#!/usr/bin/env python3
"""v12.76: Node SSH host keys are remembered on first contact and a changed key is refused."""
from __future__ import annotations
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
d = Path(tempfile.mkdtemp(prefix="sf-v1276-ssh-"))
known_hosts = d / "ssh" / "known_hosts"
os.environ["STREAMFORGE_SSH_KNOWN_HOSTS"] = str(known_hosts)
os.environ["HOME"] = str(d)  # keep the test away from the real ~/.ssh/known_hosts
from app import ssh_installer  # noqa: E402

executed: list[str] = []


class Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("root", "pw") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        executed.append(command.decode())
        def finish():
            channel.send_exit_status(0)
            channel.close()
        threading.Timer(0.05, finish).start()
        return True


def serve_once(host_key: paramiko.PKey) -> int:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run():
        conn, _ = listener.accept()
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        try:
            transport.start_server(server=Server())
            channel = transport.accept(10)
            if channel is not None:
                while transport.is_active() and not channel.closed:
                    threading.Event().wait(0.05)
        except Exception:
            pass
        finally:
            transport.close()
            listener.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def admin(port: int):
    return ssh_installer.run_node_admin_command_over_ssh(
        host="127.0.0.1", port=port, username="root", password="pw", action="service_restart")


key_a = paramiko.RSAKey.generate(2048)
key_b = paramiko.RSAKey.generate(2048)

# First contact: accepted and remembered.
port = serve_once(key_a)
admin(port)
assert len(executed) == 1
saved = paramiko.HostKeys(str(known_hosts))
assert saved.lookup(f"[127.0.0.1]:{port}") is not None, known_hosts.read_text()
assert oct(known_hosts.stat().st_mode & 0o777) == "0o600"

# Same key again: still accepted. Reuse the remembered entry on a new port.
port2 = serve_once(key_a)
saved.add(f"[127.0.0.1]:{port2}", key_a.get_name(), key_a)
saved.save(str(known_hosts))
admin(port2)
assert len(executed) == 2

# Different key for a remembered host: refused before any command runs.
port3 = serve_once(key_b)
saved.add(f"[127.0.0.1]:{port3}", key_a.get_name(), key_a)
saved.save(str(known_hosts))
try:
    admin(port3)
    raise AssertionError("changed host key must be refused")
except ssh_installer.SSHInstallError as exc:
    message = str(exc)
    assert "has changed" in message and "ssh-keygen" in message and f"[127.0.0.1]:{port3}" in message, message
assert len(executed) == 2
print("PASS: v12.76 SSH host keys remembered on first contact (0600 file), reused, and a changed key refused with recovery hint")
