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
    print("forwarding 127.0.0.1:%d -> %s:%d via %s" % (local_port, remote_host, remote_port, sshx.HOST), flush=True)
    while True:
        conn, _ = listener.accept()
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
    print("[auth=%s]" % kind, flush=True)
    serve(local, host, int(port), client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
