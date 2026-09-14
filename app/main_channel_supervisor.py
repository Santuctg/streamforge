from __future__ import annotations

# Set ownership mode before importing node_manager/ffmpeg.  This process is the
# only Main-side process allowed to instantiate the real StreamManager.
import os
os.environ["STREAMFORGE_MAIN_CHANNEL_SUPERVISOR_MODE"] = "1"

import json
import signal
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

from .audit_log import log_event
from .db import Base, engine, ensure_runtime_schema
from .ffmpeg import StreamManager, stream_manager
from .node_manager import node_controller


SOCKET_PATH = Path(os.getenv(
    "STREAMFORGE_MAIN_CHANNEL_SUPERVISOR_SOCKET",
    "/var/lib/streamforge/main-channel-supervisor.sock",
))
STOP_EVENT = threading.Event()

# STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_ENTRYPOINT_V1115


def _manager() -> StreamManager:
    if not isinstance(stream_manager, StreamManager):
        raise RuntimeError("Main channel supervisor did not acquire StreamManager ownership")
    return stream_manager


def _all_runtime_snapshots() -> dict[str, dict[str, object]]:
    manager = _manager()
    with manager._lock:  # one bounded lock acquisition for the complete UI poll
        channel_ids = list(manager._processes)
    return {str(channel_id): dict(manager.runtime_snapshot(channel_id)) for channel_id in channel_ids}


def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    manager = _manager()
    if action == "ping":
        return {"ok": True, "pong": True, "pid": os.getpid()}
    if action == "snapshots":
        return {"ok": True, "snapshots": _all_runtime_snapshots()}
    if action in {"start", "stop", "restart", "forget"}:
        channel_id = int(payload.get("channel_id") or 0)
        if channel_id <= 0:
            raise ValueError("channel_id is required")
        if action == "start":
            manager.start(channel_id, recovery=bool(payload.get("recovery")))
        elif action == "stop":
            manager.stop(channel_id)
        elif action == "restart":
            manager.restart(channel_id)
        else:
            manager.forget(channel_id)
        return {"ok": True, "snapshot": dict(manager.runtime_snapshot(channel_id))}
    if action == "reset_stale_state":
        manager.reset_stale_state()
        return {"ok": True}
    if action == "shutdown_all":
        manager.shutdown_all()
        return {"ok": True}
    if action == "stop_all":
        manager.stop_all()
        return {"ok": True}
    raise ValueError(f"Unknown supervisor action: {action or 'empty'}")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(1024 * 1024)
            if not raw:
                return
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Invalid supervisor request")
            response = _dispatch(payload)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        # STREAMFORGE_MAIN_SUPERVISOR_CLIENT_DISCONNECT_SAFE_V121:
        # Status/UI callers can time out or close the Unix socket after the
        # command already completed. A disconnected client is not a supervisor
        # failure and must not emit a traceback for every BrokenPipe.
        try:
            self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _remove_socket() -> None:
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _restore_desired_channels() -> None:
    try:
        node_controller.restore_desired_channels(startup_delay=0.5)
    except Exception as exc:
        log_event(
            "Main channel supervisor restore failed",
            scope="channel",
            level="error",
            details=str(exc),
        )


def run() -> int:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    _remove_socket()

    # The supervisor may be ordered before Gunicorn on boot/fresh install. Make
    # the additive schema available independently, then own stale-state reset
    # and desired-channel restore.
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    node_controller.reset_stale_state()

    server = _Server(str(SOCKET_PATH), _Handler)
    os.chmod(SOCKET_PATH, 0o660)

    def request_stop(_signum: int, _frame: Any) -> None:
        STOP_EVENT.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    threading.Thread(
        target=_restore_desired_channels,
        name="streamforge-main-supervisor-restore",
        daemon=True,
    ).start()
    log_event(
        "Dedicated Main channel supervisor started",
        scope="system",
        details={"pid": os.getpid(), "socket": str(SOCKET_PATH)},
    )

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        # A deliberate supervisor service stop preserves desired_running but
        # terminates its child encoders. systemd restart then restores them.
        try:
            node_controller.stop_all_local(preserve_desired=True)
        except Exception:
            pass
        _remove_socket()
        log_event("Dedicated Main channel supervisor stopped", scope="system")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
