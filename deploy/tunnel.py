"""Local port forward to the parsing service: python deploy/tunnel.py [local] [remote]

The service binds 127.0.0.1 on the GPU instance, so the pipeline reaches it through SSH.
Credentials come from sshx (password in .env, key preferred), nothing is printed.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sshx  # noqa: E402


def pump(source, target) -> None:
    try:
        while True:
            data = source.recv(32 * 1024)
            if not data:
                break
            target.sendall(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        for handle in (source, target):
            try:
                handle.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass


def serve(local_port: int, remote_host: str, remote_port: int, client) -> None:
    transport = client.get_transport()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", local_port))
    listener.listen(64)
    # 这个超时只为每 20 秒醒一次检查 SSH 传输是否还活着：网络抖动后传输会死，
    # 而进程还在 —— systemd 看不到失败，隧道就一直是个"能连上但过不去"的壳。
    # 主动退出，交给 systemd 重启。
    listener.settimeout(20)
    print("forwarding 127.0.0.1:%d -> %s:%d via %s" % (local_port, remote_host, remote_port, sshx.HOST), flush=True)
    while True:
        if not transport.is_active():
            print("ssh transport is dead; exiting so systemd can restart", flush=True)
            return
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            continue
        try:
            channel = transport.open_channel("direct-tcpip", (remote_host, remote_port), conn.getpeername())
        except Exception as exc:  # noqa: BLE001
            print("open failed", exc, flush=True)
            conn.close()
            continue
        threading.Thread(target=pump, args=(conn, channel), daemon=True).start()
        threading.Thread(target=pump, args=(channel, conn), daemon=True).start()


def main() -> int:
    local = int(sys.argv[1]) if len(sys.argv) > 1 else int(sshx.env_value("ICT_TUNNEL_LOCAL", "6008"))
    remote = sys.argv[2] if len(sys.argv) > 2 else sshx.env_value("ICT_TUNNEL_REMOTE", "127.0.0.1:6008")
    host, port = remote.split(":")
    client, kind = sshx.connect(False)
    # 保持连接活性：空闲被中间的 NAT/防火墙掐断是最常见的隧道失效原因
    client.get_transport().set_keepalive(30)
    print("[auth=%s]" % kind, flush=True)
    serve(local, host, int(port), client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
