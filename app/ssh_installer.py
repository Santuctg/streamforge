from __future__ import annotations

import io
import os
import posixpath
import secrets
import shlex
import socket
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import paramiko


class SSHInstallError(RuntimeError):
    pass


# STREAMFORGE_NODE_SSH_TAR_HYGIENE_V107:
def _node_payload_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Exclude runtime/compiler/editor artifacts from Remote Node payloads."""
    parts = Path(info.name).parts
    name = parts[-1] if parts else info.name
    if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", "__MACOSX"} for part in parts):
        return None
    if name.endswith((".pyc", ".pyo", ".swp", ".tmp", ".bak", ".orig", ".rej")) or name == ".DS_Store":
        return None
    return info


@dataclass(frozen=True)
class SSHInstallResult:
    api_url: str
    token: str
    host_key_fingerprint: str
    output: str


def _package_tarball(app_root: Path) -> bytes:
    # STREAMFORGE_NODE_SSH_COMPLETE_PAYLOAD_V65R3: keep this list aligned
    # with every SOURCE_DIR path consumed by install_node_agent.sh.  A partial
    # SSH payload can install Python dependencies successfully and then fail
    # late while copying a root-owned helper, which needlessly rolls the Node
    # back.
    required = [
        app_root / "node_agent",
        app_root / "scripts" / "install_node_agent.sh",
        app_root / "scripts" / "bootstrap_youtube_runtime.sh",
        app_root / "scripts" / "update_geoip_databases.sh",
        app_root / "scripts" / "tune_high_concurrency.py",
        app_root / "scripts" / "uninstall.sh",
        app_root / "scripts" / "cache_clear.sh",
        app_root / "deploy" / "streamforge-geoip-update.service",
        app_root / "deploy" / "streamforge-geoip-update.timer",
    ]
    for path in required:
        if not path.exists():
            raise SSHInstallError(f"Node installer payload is missing: {path}")
    buffer = io.BytesIO()
    # STREAMFORGE_NODE_UPDATE_LOW_CPU_SSH_PACKAGE_V57: this runs in a Main
    # background thread but gzip's highest compression can still contend for
    # CPU/GIL with the single Web Player worker. Level 1 is plenty for the small
    # Node payload and keeps Main playback responsive during remote updates.
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=1) as archive:
        archive.add(app_root / "node_agent", arcname="streamforge_mvp/node_agent", filter=_node_payload_filter)
        archive.add(app_root / "scripts" / "install_node_agent.sh", arcname="streamforge_mvp/scripts/install_node_agent.sh", filter=_node_payload_filter)
        # STREAMFORGE_NODE_SSH_YOUTUBE_BOOTSTRAP_PAYLOAD_V1025:
        # install_node_agent.sh consumes this helper during a fresh SSH install.
        archive.add(app_root / "scripts" / "bootstrap_youtube_runtime.sh", arcname="streamforge_mvp/scripts/bootstrap_youtube_runtime.sh", filter=_node_payload_filter)
        archive.add(app_root / "scripts" / "update_geoip_databases.sh", arcname="streamforge_mvp/scripts/update_geoip_databases.sh", filter=_node_payload_filter)
        archive.add(app_root / "scripts" / "tune_high_concurrency.py", arcname="streamforge_mvp/scripts/tune_high_concurrency.py", filter=_node_payload_filter)
        archive.add(app_root / "scripts" / "uninstall.sh", arcname="streamforge_mvp/scripts/uninstall.sh", filter=_node_payload_filter)
        archive.add(app_root / "scripts" / "cache_clear.sh", arcname="streamforge_mvp/scripts/cache_clear.sh", filter=_node_payload_filter)
        archive.add(app_root / "deploy" / "streamforge-geoip-update.service", arcname="streamforge_mvp/deploy/streamforge-geoip-update.service", filter=_node_payload_filter)
        archive.add(app_root / "deploy" / "streamforge-geoip-update.timer", arcname="streamforge_mvp/deploy/streamforge-geoip-update.timer", filter=_node_payload_filter)
    return buffer.getvalue()


def _read_channel(
    channel: paramiko.Channel,
    timeout: float = 900.0,
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    channel.settimeout(2.0)
    parts: list[str] = []
    deadline = __import__("time").monotonic() + timeout
    while True:
        if channel.recv_ready():
            chunk = channel.recv(65536).decode("utf-8", errors="replace")
            parts.append(chunk)
            if on_output and chunk:
                on_output(chunk)
        if channel.recv_stderr_ready():
            chunk = channel.recv_stderr(65536).decode("utf-8", errors="replace")
            parts.append(chunk)
            if on_output and chunk:
                on_output(chunk)
        if channel.exit_status_ready():
            while channel.recv_ready():
                chunk = channel.recv(65536).decode("utf-8", errors="replace")
                parts.append(chunk)
                if on_output and chunk:
                    on_output(chunk)
            while channel.recv_stderr_ready():
                chunk = channel.recv_stderr(65536).decode("utf-8", errors="replace")
                parts.append(chunk)
                if on_output and chunk:
                    on_output(chunk)
            return channel.recv_exit_status(), "".join(parts)[-30000:]
        if __import__("time").monotonic() >= deadline:
            channel.close()
            raise SSHInstallError("Remote installation timed out")
        __import__("time").sleep(0.15)


def install_node_over_ssh(
    *,
    app_root: Path,
    host: str,
    port: int,
    username: str,
    password: str,
    agent_port: int = 80,
    public_port: int = 80,
    token: str | None = None,
    dns_name: str | None = None,
    dns_only: bool = False,
    playlist_host: str | None = None,
    playlist_dns_only: bool = False,
    panel_url: str | None = None,
    node_slug: str | None = None,
    progress: Callable[[str, str, int, str], None] | None = None,
) -> SSHInstallResult:
    host = host.strip()
    username = username.strip()
    dns_name = (dns_name or "").strip().lower().rstrip(".")
    if not host or not username or not password:
        raise SSHInstallError("SSH host, username and password are required")
    if not (1 <= int(port) <= 65535 and 1 <= int(agent_port) <= 65535 and 1 <= int(public_port) <= 65535):
        raise SSHInstallError("SSH, control and Playlist/API ports must be between 1 and 65535")
    if dns_only and not dns_name:
        raise SSHInstallError("DNS-only mode requires a DNS hostname")

    def emit(phase: str, message: str, percent: int, detail: str = "") -> None:
        if progress:
            try:
                progress(phase, message, max(0, min(100, int(percent))), detail)
            except Exception:
                pass

    emit("packaging", "Preparing Node Agent package", 8)
    node_token = (token or secrets.token_urlsafe(32)).strip()
    payload = _package_tarball(app_root)
    emit("connecting", f"Connecting to {host}:{int(port)}", 15)
    remote_id = secrets.token_hex(6)
    remote_tar = f"/tmp/streamforge-node-{remote_id}.tar.gz"
    remote_dir = f"/tmp/streamforge-node-{remote_id}"

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # The UI explicitly shows the returned fingerprint after first connection.
    # Passwords are used only for this connection and are never persisted.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=int(port),
            username=username,
            password=password,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise SSHInstallError("SSH transport is not active")
        key = transport.get_remote_server_key()
        fingerprint = ":".join(f"{byte:02x}" for byte in key.get_fingerprint())

        emit("uploading", "Uploading Node Agent package", 25)
        with client.open_sftp() as sftp:
            with sftp.file(remote_tar, "wb") as remote_file:
                total = max(1, len(payload))
                chunk_size = 256 * 1024
                for offset in range(0, len(payload), chunk_size):
                    remote_file.write(payload[offset:offset + chunk_size])
                    uploaded = min(total, offset + chunk_size)
                    emit("uploading", f"Uploading package ({uploaded * 100 // total}%)", 25 + int((uploaded / total) * 20))
            sftp.chmod(remote_tar, 0o600)
        emit("installing", "Installing system Gunicorn/ASGI runtime and Node Agent", 50)

        install_args = [
            "bash", "scripts/install_node_agent.sh",
            "--token", node_token,
            "--port", str(int(agent_port)),
            "--public-port", str(int(public_port)),
        ]
        if dns_name:
            install_args += ["--hostname", dns_name]
        if dns_only:
            install_args += ["--dns-only"]
        if playlist_host:
            install_args += ["--playlist-host", playlist_host.strip()]
        if playlist_dns_only:
            install_args += ["--playlist-dns-only"]
        if panel_url:
            install_args += ["--panel-url", panel_url.strip().rstrip("/")]
        if node_slug:
            install_args += ["--node-slug", node_slug.strip()]

        inner = "\n".join(
            [
                "set -Eeuo pipefail",
                "export DEBIAN_FRONTEND=noninteractive",
                "if ! command -v python3 >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then if command -v apt-get >/dev/null 2>&1; then apt-get update -y; apt-get install -y python3 python3-pip ffmpeg ca-certificates; fi; fi",
                f"rm -rf {shlex.quote(remote_dir)}",
                f"mkdir -p {shlex.quote(remote_dir)}",
                f"tar -xzf {shlex.quote(remote_tar)} -C {shlex.quote(remote_dir)}",
                f"cd {shlex.quote(remote_dir)}/streamforge_mvp",
                " ".join(shlex.quote(item) for item in install_args),
                f"rm -rf {shlex.quote(remote_dir)} {shlex.quote(remote_tar)}",
            ]
        )
        if username == "root":
            command = f"bash -lc {shlex.quote(inner)}"
            stdin_prefix = ""
        else:
            command = f"sudo -S -p '' bash -lc {shlex.quote(inner)}"
            stdin_prefix = password + "\n"

        channel = transport.open_session()
        channel.exec_command(command)
        if stdin_prefix:
            channel.sendall(stdin_prefix.encode("utf-8"))
            channel.shutdown_write()
        buffered = ""
        def relay_output(chunk: str) -> None:
            nonlocal buffered
            buffered += chunk
            lines = buffered.replace("\r", "\n").split("\n")
            buffered = lines.pop() if lines else ""
            for line in lines[-8:]:
                cleaned = line.strip()
                if cleaned:
                    emit("installing", "Remote installer is running", 65, cleaned[-500:])
        code, output = _read_channel(channel, on_output=relay_output)
        if code != 0:
            raise SSHInstallError(f"Remote installer exited with code {code}:\n{output[-6000:]}")

        emit("verifying", "Remote install completed; verifying agent files", 78)
        api_host = dns_name or host
        api_url = f"http://{api_host}:{int(agent_port)}"
        return SSHInstallResult(
            api_url=api_url,
            token=node_token,
            host_key_fingerprint=fingerprint,
            output=output,
        )
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        raise SSHInstallError(f"SSH connection failed: {exc}") from exc
    finally:
        client.close()


def run_node_admin_command_over_ssh(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    action: str,
) -> SSHInstallResult:
    """Run one allow-listed Node administration action over saved SSH access."""
    commands = {
        "service_restart": "nohup sh -c 'sleep 1; systemctl restart streamforge-node' >/dev/null 2>&1 &",
        "reboot": "nohup sh -c 'sleep 1; systemctl reboot' >/dev/null 2>&1 &",
    }
    command_body = commands.get(str(action or "").strip())
    host = str(host or "").strip()
    username = str(username or "").strip()
    if not command_body:
        raise SSHInstallError("Unsupported Node administration action")
    if not host or not username or not password:
        raise SSHInstallError("Saved SSH host, username and password are required")
    if not 1 <= int(port) <= 65535:
        raise SSHInstallError("SSH port must be between 1 and 65535")

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=int(port),
            username=username,
            password=password,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise SSHInstallError("SSH transport is not active")
        key = transport.get_remote_server_key()
        fingerprint = ":".join(f"{byte:02x}" for byte in key.get_fingerprint())
        inner = f"set -Eeuo pipefail; {command_body}"
        if username == "root":
            command = f"bash -lc {shlex.quote(inner)}"
            stdin_prefix = ""
        else:
            command = f"sudo -S -p '' bash -lc {shlex.quote(inner)}"
            stdin_prefix = password + "\n"
        channel = transport.open_session()
        channel.exec_command(command)
        if stdin_prefix:
            channel.sendall(stdin_prefix.encode("utf-8"))
            channel.shutdown_write()
        code, output = _read_channel(channel, timeout=30.0)
        if code != 0:
            raise SSHInstallError(f"Remote command exited with code {code}:\n{output[-3000:]}")
        return SSHInstallResult(
            api_url="",
            token="",
            host_key_fingerprint=fingerprint,
            output=output,
        )
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        raise SSHInstallError(f"SSH connection failed: {exc}") from exc
    finally:
        client.close()


def uninstall_node_over_ssh(
    *,
    app_root: Path,
    host: str,
    port: int,
    username: str,
    password: str,
) -> str:
    """Permanently remove StreamForge Remote Node files/services over SSH.

    The uninstall script is uploaded from the current Main package so this
    also works against Nodes installed by older StreamForge versions.
    """
    host = host.strip()
    username = username.strip()
    if not host or not username or not password:
        raise SSHInstallError("Saved SSH host, username and password are required to clean the Remote Node")
    script_path = app_root / "scripts" / "uninstall.sh"
    if not script_path.is_file():
        raise SSHInstallError("StreamForge uninstall script is missing from Main Server")
    payload = script_path.read_bytes()
    remote_script = f"/tmp/streamforge-uninstall-{secrets.token_hex(6)}.sh"

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=int(port),
            username=username,
            password=password,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise SSHInstallError("SSH transport is not active")
        with client.open_sftp() as sftp:
            with sftp.file(remote_script, "wb") as remote_file:
                remote_file.write(payload)
            sftp.chmod(remote_script, 0o700)

        inner = f"bash {shlex.quote(remote_script)} node --yes; rm -f {shlex.quote(remote_script)}"
        if username == "root":
            command = f"bash -lc {shlex.quote(inner)}"
            stdin_prefix = ""
        else:
            command = f"sudo -S -p '' bash -lc {shlex.quote(inner)}"
            stdin_prefix = password + "\n"

        channel = transport.open_session()
        channel.exec_command(command)
        if stdin_prefix:
            channel.sendall(stdin_prefix.encode("utf-8"))
            channel.shutdown_write()
        code, output = _read_channel(channel)
        if code != 0:
            raise SSHInstallError(f"Remote Node uninstall exited with code {code}:\n{output[-6000:]}")
        return output
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        raise SSHInstallError(f"Remote Node uninstall SSH failed: {exc}") from exc
    finally:
        client.close()
