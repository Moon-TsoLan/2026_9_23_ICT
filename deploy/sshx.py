"""Tiny SSH runner for the parsing server: run / bg / put / get / fixkey.

    python deploy/sshx.py whoami
    python deploy/sshx.py fixkey
    python deploy/sshx.py run "bash /root/deploy/recon.sh"
    python deploy/sshx.py put deploy/parse_service.py /root/deploy/parse_service.py
    python deploy/sshx.py bg "bash /root/deploy/install_parse.sh" --log /root/setup.log

Host, port, user come from ICT_SSH_HOST / ICT_SSH_PORT / ICT_SSH_USER, else the defaults
below. The password is read from the repo .env key ssh_password and never printed. fixkey
rewrites /root/.ssh/authorized_keys over SFTP (idempotent, right modes) so plain
`ssh -i ~/.ssh/ict_parse_server` works afterwards. Needs paramiko on the path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def env_value(key: str, default: str = "") -> str:
    """Env first, then the repo .env, so a restarted instance only needs .env touched."""
    value = os.environ.get(key, "")
    if value:
        return value
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if text.startswith(key + "=") and "=" in text:
                got = text.split("=", 1)[1].strip().strip("'").strip('"')
                if got:
                    return got
    return default


HOST = env_value("ICT_SSH_HOST", "connect.bjb2.seetacloud.com")
PORT = int(env_value("ICT_SSH_PORT", "16365"))
USER = env_value("ICT_SSH_USER", "root")
KEY_PATH = Path(env_value("ICT_SSH_KEY", str(Path.home() / ".ssh" / "ict_parse_server")))
VENDOR = REPO / ".tools_py"          # paramiko lives here: pip install --target .tools_py paramiko
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
AUTHORIZED = "/root/.ssh/authorized_keys"


def key_present() -> bool:
    try:
        return KEY_PATH.exists()
    except OSError:      # sandbox may be denied access to ~/.ssh
        return False


def read_password() -> str:
    return env_value("ssh_password", os.environ.get("ICT_SSH_PASSWORD", ""))


def connect(prefer_key: bool):
    import paramiko

    secret = read_password()
    attempts = []
    if prefer_key and key_present():
        attempts.append(("key", str(KEY_PATH)))
    if secret:
        attempts.append(("password", secret))
    if not prefer_key and key_present():
        attempts.append(("key", str(KEY_PATH)))
    errors = []
    for kind, value in attempts:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                HOST,
                port=PORT,
                username=USER,
                key_filename=value if kind == "key" else None,
                password=value if kind == "password" else None,
                look_for_keys=False,
                allow_agent=False,
                timeout=25,
            )
            return client, kind
        except Exception as exc:  # noqa: BLE001
            errors.append(kind + ":" + type(exc).__name__)
            client.close()
    raise SystemExit("connect failed -> " + ("; ".join(errors) or "no credentials available"))


def stream(client, command: str, timeout):
    _, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    for line in iter(stdout.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
    code = stdout.channel.recv_exit_status()
    error = stderr.read().decode("utf-8", "replace")
    if error.strip():
        sys.stdout.write("[stderr tail] " + error[-1500:] + "\n")
    return code


def read_remote_text(sftp, path: str) -> str:
    try:
        with sftp.open(path, "r") as handle:
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def fixkey(client) -> int:
    public = Path(str(KEY_PATH) + ".pub").read_text(encoding="utf-8").strip()
    parts = public.split()
    if len(parts) < 2:
        raise SystemExit("public key file looks wrong")
    stream(client, "mkdir -p /root/.ssh && chmod 700 /root/.ssh", 30)
    sftp = client.open_sftp()
    existing = read_remote_text(sftp, AUTHORIZED)
    lines = [line.strip() for line in existing.splitlines() if line.strip()]
    kept = [line for line in lines if parts[1] not in line]
    kept.append(public)
    with sftp.open(AUTHORIZED, "w") as handle:
        handle.write("\n".join(kept) + "\n")
    sftp.chmod(AUTHORIZED, 0o600)
    sftp.close()
    print("authorized_keys entries:", len(kept))
    check = ("ls -ld /root /root/.ssh " + AUTHORIZED +
             "; sshd -T 2>/dev/null | grep -iE 'authorizedkeysfile|pubkeyauthentication|strictmodes' || true")
    return stream(client, check, 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="run things on the parsing server")
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run")
    run.add_argument("command")
    run.add_argument("--timeout", type=int, default=300)
    bg = sub.add_parser("bg")
    bg.add_argument("command")
    bg.add_argument("--log", default="/root/ict_sshx.log")
    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote")
    get = sub.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local")
    sub.add_parser("fixkey")
    sub.add_parser("whoami")
    parser.add_argument("--key", action="store_true", help="try the key before the password")
    args = parser.parse_args()

    client, kind = connect(args.key)
    print("[auth=%s host=%s:%d]" % (kind, HOST, PORT), flush=True)
    code = 0
    try:
        if args.mode == "run":
            code = stream(client, args.command, args.timeout)
        elif args.mode == "bg":
            code = stream(client, "nohup %s > %s 2>&1 </dev/null & echo started $!" % (args.command, args.log), 30)
        elif args.mode == "fixkey":
            code = fixkey(client)
        elif args.mode == "whoami":
            code = stream(client, "id; hostname; pwd; nproc; free -g | head -2", 30)
        else:
            sftp = client.open_sftp()
            if args.mode == "put":
                stream(client, "mkdir -p $(dirname %s)" % args.remote, 20)
                sftp.put(args.local, args.remote)
                print("put %s -> %s" % (args.local, args.remote))
            else:
                Path(args.local).parent.mkdir(parents=True, exist_ok=True)
                sftp.get(args.remote, args.local)
                print("get %s -> %s" % (args.remote, args.local))
            sftp.close()
    finally:
        client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
