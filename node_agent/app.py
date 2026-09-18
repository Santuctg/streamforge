from __future__ import annotations

import asyncio
import fcntl
import json
import math
import base64
import hashlib
import html
import os
import hmac
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
import re
import secrets
import string
import shutil
import sys
import tempfile
import zipfile
import signal
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    import maxminddb  # type: ignore
except Exception:
    maxminddb = None

try:
    from redis_state import NodeRedisState
except Exception:
    NodeRedisState = None  # type: ignore

from source_resolver import SourceResolveError, is_youtube_url, resolve_stream_source, youtube_cookie_configured, youtube_cookie_path, youtube_probe_payload
TOKEN = os.getenv("STREAMFORGE_NODE_TOKEN", "").strip()
FFMPEG = os.getenv("STREAMFORGE_NODE_FFMPEG", "/usr/bin/ffmpeg")
# STREAMFORGE_NODE_NVENC_DEDICATED_FFMPEG_V1117: explicit NVIDIA profiles can
# use a legacy-NVENC-compatible FFmpeg without replacing the system binary.
NVENC_FFMPEG = os.getenv("STREAMFORGE_NODE_NVENC_FFMPEG", "/opt/ffmpeg-nvenc470/bin/ffmpeg")
GUNICORN = os.getenv("STREAMFORGE_NODE_GUNICORN", "/usr/bin/gunicorn").strip() or "/usr/bin/gunicorn"
HLS_ROOT = Path(os.getenv("STREAMFORGE_NODE_HLS_ROOT", "/var/lib/streamforge-node/hls"))
STATE_FILE = Path(os.getenv("STREAMFORGE_NODE_STATE_FILE", "/var/lib/streamforge-node/state.json"))
# STREAMFORGE_NODE_UPDATE_FFMPEG_HANDOFF_V1083:
# A code-only Node update must not tear down running encoders just because the
# control Gunicorn worker reloads.  The outgoing worker snapshots the live
# FFmpeg PIDs/runtime metadata here; the replacement worker validates and
# adopts those exact processes instead of starting duplicate encoders.
UPDATE_FFMPEG_HANDOFF_FILE = Path(os.getenv(
    "STREAMFORGE_NODE_UPDATE_FFMPEG_HANDOFF_FILE",
    "/var/lib/streamforge-node/update-ffmpeg-handoff.json",
))
_NODE_UPDATE_RESTART_ARMED = threading.Event()
SHARED_STATE_FILE = Path(os.getenv("STREAMFORGE_NODE_SHARED_STATE_FILE", str(STATE_FILE.with_name("state-shared.json"))))
INDEPENDENT_STATE_FILE = Path(os.getenv("STREAMFORGE_NODE_INDEPENDENT_STATE_FILE", str(STATE_FILE.with_name("state-independent.json"))))
USERS_FILE = Path(os.getenv("STREAMFORGE_NODE_USERS_FILE", "/var/lib/streamforge-node/users.json"))
# STREAMFORGE_NODE_PLAYLIST_USER_CROSS_PROCESS_SYNC_V123: Node Panel CRUD and
# high-concurrency public workers share users.json through an atomic, locked snapshot.
USERS_LOCK_FILE = Path(os.getenv("STREAMFORGE_NODE_USERS_LOCK_FILE", "/var/lib/streamforge-node/users.lock"))
PANEL_FILE = Path(os.getenv("STREAMFORGE_NODE_PANEL_FILE", "/var/lib/streamforge-node/panel.json"))
PANEL_USERS_FILE = Path(os.getenv("STREAMFORGE_NODE_PANEL_USERS_FILE", "/var/lib/streamforge-node/panel-users.json"))
CATEGORY_FILE = Path(os.getenv("STREAMFORGE_NODE_CATEGORY_FILE", "/var/lib/streamforge-node/categories.json"))
ACCESS_FILE = Path(os.getenv("STREAMFORGE_NODE_ACCESS_FILE", "/var/lib/streamforge-node/access.json"))
# STREAMFORGE_NODE_ACCESS_CROSS_PROCESS_WRITE_LOCK_V1149: serialize all access.json
# writers across control/public/panel/supervisor processes. ACCESS_FILE itself is
# atomically replaced, so lock a stable sibling inode rather than access.json.
ACCESS_LOCK_FILE = Path(os.getenv("STREAMFORGE_NODE_ACCESS_LOCK_FILE", "/var/lib/streamforge-node/access.lock"))
# STREAMFORGE_NODE_CROSS_PROCESS_ACCESS_RECONCILE_V59: public/panel gateway
# workers and the native control worker share access.json but not Python memory.
# A local Settings save requests a post-response native-control reconcile through
# this filesystem handoff instead of recursively POSTing back into Gunicorn.
NODE_ACCESS_RECONCILE_REQUEST_FILE = Path(os.getenv("STREAMFORGE_NODE_ACCESS_RECONCILE_REQUEST_FILE", "/var/lib/streamforge-node/access-reconcile.request"))
NODE_ACCESS_RECONCILE_STATUS_FILE = Path(os.getenv("STREAMFORGE_NODE_ACCESS_RECONCILE_STATUS_FILE", "/var/lib/streamforge-node/access-reconcile.status.json"))
# STREAMFORGE_NODE_MANAGED_TLS_V39: the unprivileged Agent serves HTTP/ACME and
# requests a root-owned TLS reconciler. Public TLS termination is kept separate
# from the native Node listener so access sync never depends on port 443 being
# ready before the new policy can be saved.
NODE_ACME_WEBROOT = Path(os.getenv("STREAMFORGE_NODE_ACME_WEBROOT", "/var/lib/streamforge-node/acme-webroot"))
NODE_TLS_REQUEST_FILE = Path(os.getenv("STREAMFORGE_NODE_TLS_REQUEST_FILE", "/var/lib/streamforge-node/tls-reconcile.request"))
NODE_TLS_STATUS_FILE = Path(os.getenv("STREAMFORGE_NODE_TLS_STATUS_FILE", "/var/lib/streamforge-node/tls/status.json"))
ASN_DB_FILE = Path(os.getenv("STREAMFORGE_NODE_ASN_DB_PATH", "/opt/streamforge/GeoLite2-ASN.mmdb"))
COUNTRY_DB_FILE = Path(os.getenv("STREAMFORGE_NODE_COUNTRY_DB_PATH", "/opt/streamforge/GeoLite2-Country.mmdb"))
GEOIP_UPDATE_MARKER = ASN_DB_FILE.parent / ".geoip-last-update"
GEOIP_SETTINGS_FILE = Path(os.getenv("STREAMFORGE_GEOIP_SETTINGS_FILE", "/var/lib/streamforge-node/geoip-settings.json"))
GEOIP_AUTO_UPDATE = os.getenv("STREAMFORGE_GEOIP_AUTO_UPDATE", "0").strip().lower() in {"1", "true", "yes", "on"}
MAXMIND_CONFIGURED = bool(os.getenv("STREAMFORGE_MAXMIND_ACCOUNT_ID", "") and os.getenv("STREAMFORGE_MAXMIND_LICENSE_KEY", ""))
OPERATOR_TIMEZONE = os.getenv("STREAMFORGE_TIMEZONE", "Asia/Dhaka").strip() or "Asia/Dhaka"
LOG_FILE = Path(os.getenv("STREAMFORGE_NODE_LOG_FILE", "/var/lib/streamforge-node/agent.log"))
# STREAMFORGE_NODE_LOG_RETENTION_V88: keep Node event logs by selected age,
# with a large safety ceiling instead of the old ~2000-entry/5MB truncation.
NODE_EVENT_LOG_MAX = max(2000, int(os.getenv("STREAMFORGE_NODE_EVENT_LOG_MAX", "100000")))
NODE_EVENT_LOG_FILE_MAX_BYTES = max(10 * 1024 * 1024, int(os.getenv("STREAMFORGE_NODE_EVENT_LOG_FILE_MAX_BYTES", str(100 * 1024 * 1024))))

def _node_log_retention_days() -> int:
    try:
        raw = json.loads(OPERATIONS_SETTINGS_FILE.read_text(encoding="utf-8"))
        value = int(raw.get("log_retention_days") or 30) if isinstance(raw, dict) else 30
    except (OSError, ValueError, TypeError):
        value = 30
    return max(1, min(3650, value))
PUBLIC_GATEWAY_LOG = Path(os.getenv("STREAMFORGE_NODE_PUBLIC_LOG_FILE", "/var/lib/streamforge-node/public-gateway.log"))
PANEL_GATEWAY_LOG_DIR = Path(os.getenv("STREAMFORGE_NODE_PANEL_LOG_DIR", "/var/lib/streamforge-node"))
VIEWERS_FILE = Path(os.getenv("STREAMFORGE_NODE_VIEWERS_FILE", "/var/lib/streamforge-node/viewers.json"))
PLAYBACK_KEYS_FILE = Path(os.getenv("STREAMFORGE_NODE_PLAYBACK_KEYS_FILE", "/var/lib/streamforge-node/playback-keys.json"))
CHANNEL_LOGO_ROOT = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_LOGO_ROOT", "/var/lib/streamforge-node/channel-logos"))
NODE_LOGO_ROOT = Path(os.getenv("STREAMFORGE_NODE_LOGO_ROOT", "/opt/streamforge-node/logo"))
METRICS_HISTORY_FILE = Path(os.getenv("STREAMFORGE_NODE_METRICS_HISTORY_FILE", "/var/lib/streamforge-node/metrics-history.jsonl"))
OPERATIONS_SETTINGS_FILE = Path(os.getenv("STREAMFORGE_NODE_OPERATIONS_SETTINGS_FILE", "/var/lib/streamforge-node/operations-settings.json"))
NODE_FAVICON_ROOT = Path(os.getenv("STREAMFORGE_NODE_FAVICON_ROOT", "/var/lib/streamforge-node/favicon"))
WEBPLAYER_DOWNLOAD_FILE = Path(os.getenv("STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_FILE", "/var/lib/streamforge-node/webplayer-downloads/managed-download.bin"))
WEBPLAYER_BRAND_ROOT = Path(os.getenv("STREAMFORGE_NODE_WEBPLAYER_BRAND_ROOT", "/var/lib/streamforge-node/webplayer-brands"))
# STREAMFORGE_NODE_SELF_HOSTED_HLSJS_V1163:
WEBPLAYER_HLSJS_FILE = Path(os.getenv("STREAMFORGE_NODE_WEBPLAYER_HLSJS_FILE", "/var/lib/streamforge-node/hls.min.js"))
PANEL_URL = os.getenv("STREAMFORGE_NODE_PANEL_URL", "").strip().rstrip("/")
PANEL_NODE_SLUG = os.getenv("STREAMFORGE_NODE_SLUG", "").strip()
VIEWER_TTL = max(5, int(os.getenv("STREAMFORGE_NODE_VIEWER_TTL", "5")))
NODE_PLAYBACK_KEY_TTL_SECONDS = max(300, int(os.getenv("STREAMFORGE_NODE_PLAYBACK_KEY_TTL_SECONDS", "43200")))
NODE_RESTREAM_KEY_TTL_SECONDS = max(3600, int(os.getenv("STREAMFORGE_NODE_RESTREAM_KEY_TTL_SECONDS", "86400")))
PANEL_CACHE_SECONDS = max(2, int(os.getenv("STREAMFORGE_NODE_PANEL_CACHE_SECONDS", "8")))
# STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_V1139: keep Main liveness and targeted queue delivery
# independent of Node panel/user traffic. The control service owns exactly one lightweight sender.
NODE_MAIN_HEARTBEAT_INTERVAL_SECONDS = max(5, min(60, int(os.getenv("STREAMFORGE_NODE_MAIN_HEARTBEAT_INTERVAL_SECONDS", "15"))))
# STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210:
# Public playback workers must never synchronously call Main just to decide if
# Node user authentication is allowed. The single control worker publishes its
# periodic heartbeat result into this tiny local state file; every 8821 worker
# reads the shared last-known result with a short in-process read cache.
PANEL_CONNECTIVITY_FILE = Path(os.getenv(
    "STREAMFORGE_NODE_PANEL_CONNECTIVITY_FILE",
    "/var/lib/streamforge-node/panel-connectivity.json",
))
PANEL_CONNECTIVITY_STALE_SECONDS = max(
    20,
    min(300, int(os.getenv(
        "STREAMFORGE_NODE_PANEL_CONNECTIVITY_STALE_SECONDS",
        str(max(45, NODE_MAIN_HEARTBEAT_INTERVAL_SECONDS * 3)),
    ))),
)
PANEL_CONNECTIVITY_READ_CACHE_SECONDS = max(
    0.1,
    min(5.0, float(os.getenv("STREAMFORGE_NODE_PANEL_CONNECTIVITY_READ_CACHE_SECONDS", "0.5"))),
)
CPU_THREADS = max(1, int(os.getenv("STREAMFORGE_NODE_CPU_THREADS", "2")))
VAAPI_DEVICE = os.getenv("STREAMFORGE_NODE_VAAPI_DEVICE", "/dev/dri/renderD128")
PROGRESS_INTERVAL = max(1, int(os.getenv("STREAMFORGE_NODE_PROGRESS_INTERVAL", "3")))
STALL_SECONDS = max(15, int(os.getenv("STREAMFORGE_NODE_STALL_SECONDS", "30")))
# STREAMFORGE_NODE_RELAY_STALL_HEALTH_V111: Local-relay FFmpeg already has
# network/5xx reconnect enabled. Give it time to ride out a Main/Nginx outage,
# then restart only when the relay playlist itself is healthy but FFmpeg is not.
RELAY_STALL_GRACE_SECONDS = max(STALL_SECONDS, int(os.getenv("STREAMFORGE_NODE_RELAY_STALL_GRACE_SECONDS", "120")))
RELAY_HEALTH_PROBE_INTERVAL_SECONDS = max(10, int(os.getenv("STREAMFORGE_NODE_RELAY_HEALTH_PROBE_INTERVAL_SECONDS", "30")))
# STREAMFORGE_NODE_LOCAL_RELAY_STICKY_RECONNECT_V1141:
# v11.40 capped FFmpeg's HTTP reconnect delay at 10s. FFmpeg then retries a
# relay outage at roughly 0/1/3/7 seconds and gives up before the Node's
# 120-second relay-health watchdog can decide whether Main is actually down.
# Keep Local-relay FFmpeg alive long enough for the existing health-aware
# watchdog to arbitrate transient 404/503 misses. Main explicitly signals a
# real restart with a non-reconnectable 409, so this does not hide deliberate
# Main channel restarts.
RELAY_RECONNECT_DELAY_MAX_SECONDS = max(
    RELAY_STALL_GRACE_SECONDS + 30,
    min(600, int(os.getenv("STREAMFORGE_NODE_RELAY_RECONNECT_DELAY_MAX_SECONDS", "300"))),
)
# STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147:
# Once a Local-relay FFmpeg has actually exited, do not burn CPU by spawning a
# new FFmpeg+ffprobe every few seconds while Main's relay manifest is still
# unavailable. Poll only the tiny manifest and launch exactly once when HLS is
# healthy again. Existing running FFmpeg keeps the v11.41/v11.45 sticky HLS
# reconnect behavior, including the v11.45 rule that normal HLS EOF is not a
# reconnect trigger.
RELAY_RECOVERY_PROBE_SECONDS = max(10, min(120, int(os.getenv("STREAMFORGE_NODE_RELAY_RECOVERY_PROBE_SECONDS", "15"))))
VERSION_FILE = Path(__file__).with_name("VERSION")
VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "1.11.18"
DNS_ONLY = os.getenv("STREAMFORGE_NODE_DNS_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}

# STREAMFORGE_NODE_YOUTUBE_COOKIES_UPLOAD_V1013:
# STREAMFORGE_NODE_YOUTUBE_PROBE_TIMEOUT_V1014:
def _save_node_youtube_cookies(upload: Any) -> bool:
    filename = str(getattr(upload, "filename", "") or "").strip()
    fileobj = getattr(upload, "file", None)
    if not filename or fileobj is None:
        return False
    data = fileobj.read(4 * 1024 * 1024 + 1)
    if not data:
        raise ValueError("Selected YouTube cookies file is empty")
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("YouTube cookies file must be 4 MB or smaller")
    if b"\x00" in data:
        raise ValueError("YouTube cookies file is not valid text")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("YouTube cookies file must be UTF-8 text") from exc
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if first not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
        raise ValueError("YouTube cookies file must use Netscape cookies.txt format")
    if "youtube.com" not in text.lower() and "youtu.be" not in text.lower():
        raise ValueError("YouTube cookies file does not contain YouTube cookies")
    target = youtube_cookie_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    return True


def _remove_node_youtube_cookies() -> None:
    youtube_cookie_path().unlink(missing_ok=True)


# STREAMFORGE_NODE_YOUTUBE_FAILURE_BACKOFF_V1066:
# Automatic FFmpeg recovery can revisit an unavailable YouTube source many times.
# Keep one resolver active per page URL and exponentially defer repeated yt-dlp
# failures so bot-check errors cannot hammer Deno/yt-dlp or the Node control plane.
_NODE_YOUTUBE_RESOLVE_GUARD_LOCK = threading.RLock()
_NODE_YOUTUBE_RESOLVE_KEY_LOCKS: dict[str, threading.Lock] = {}
_NODE_YOUTUBE_RESOLVE_FAILURES: dict[str, tuple[float, int, str, tuple[int, int]]] = {}
_NODE_YOUTUBE_RESOLVE_BACKOFF_SECONDS = (30.0, 60.0, 120.0, 300.0, 600.0)


def _node_youtube_cookie_signature() -> tuple[int, int]:
    try:
        path = youtube_cookie_path()
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return 0, 0


def _guarded_resolve_stream_source(
    target: str, *, force: bool = False, bypass_backoff: bool = False
) -> str:
    if not is_youtube_url(target):
        return resolve_stream_source(target, force=force)

    key = str(target or "").strip().split("|", 1)[0].strip()
    signature = _node_youtube_cookie_signature()
    with _NODE_YOUTUBE_RESOLVE_GUARD_LOCK:
        key_lock = _NODE_YOUTUBE_RESOLVE_KEY_LOCKS.setdefault(key, threading.Lock())

    with key_lock:
        now = time.monotonic()
        with _NODE_YOUTUBE_RESOLVE_GUARD_LOCK:
            failure = _NODE_YOUTUBE_RESOLVE_FAILURES.get(key)
            if failure and failure[3] != signature:
                _NODE_YOUTUBE_RESOLVE_FAILURES.pop(key, None)
                failure = None
            if failure and not bypass_backoff and failure[0] > now:
                retry_in = max(1, int(math.ceil(failure[0] - now)))
                raise SourceResolveError(f"{failure[2]} (automatic retry deferred {retry_in}s)")

        try:
            resolved = resolve_stream_source(target, force=force)
        except SourceResolveError as exc:
            now = time.monotonic()
            detail = str(exc).strip() or "YouTube source resolution failed"
            with _NODE_YOUTUBE_RESOLVE_GUARD_LOCK:
                previous = _NODE_YOUTUBE_RESOLVE_FAILURES.get(key)
                previous_count = (
                    int(previous[1])
                    if previous and previous[3] == signature
                    else 0
                )
                count = previous_count + 1
                delay = _NODE_YOUTUBE_RESOLVE_BACKOFF_SECONDS[
                    min(count - 1, len(_NODE_YOUTUBE_RESOLVE_BACKOFF_SECONDS) - 1)
                ]
                _NODE_YOUTUBE_RESOLVE_FAILURES[key] = (
                    now + delay, count, detail, signature
                )
                if len(_NODE_YOUTUBE_RESOLVE_FAILURES) > 256:
                    stale = sorted(
                        _NODE_YOUTUBE_RESOLVE_FAILURES.items(),
                        key=lambda item: item[1][0],
                    )[:64]
                    for stale_key, _value in stale:
                        _NODE_YOUTUBE_RESOLVE_FAILURES.pop(stale_key, None)
                        _NODE_YOUTUBE_RESOLVE_KEY_LOCKS.pop(stale_key, None)
            raise

        with _NODE_YOUTUBE_RESOLVE_GUARD_LOCK:
            _NODE_YOUTUBE_RESOLVE_FAILURES.pop(key, None)
        return resolved
ALLOWED_HOST = os.getenv("STREAMFORGE_NODE_ALLOWED_HOST", "").strip().lower().rstrip(".")
STREAM_DNS_ONLY = os.getenv("STREAMFORGE_NODE_STREAM_DNS_ONLY", "1" if DNS_ONLY else "0").strip().lower() in {"1", "true", "yes", "on"}
STREAM_ALLOWED_HOST = os.getenv("STREAMFORGE_NODE_STREAM_HOST", ALLOWED_HOST).strip().lower().rstrip(".")
NODE_MODE = os.getenv("STREAMFORGE_NODE_MODE", "control").strip().lower() or "control"
# STREAMFORGE_NODE_DEDICATED_CHANNEL_SUPERVISOR_V115:
# Keep FFmpeg lifecycle, Local Relay recovery and channel start/stop/restart out
# of every HTTP/Gunicorn worker.  The existing Node services stay unchanged;
# the single control worker only ensures one detached supervisor process and
# communicates with it over a private Unix-domain socket.
NODE_DEDICATED_CHANNEL_SUPERVISOR = os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR", "1").strip().lower() in {"1", "true", "yes", "on"}
NODE_CHANNEL_SUPERVISOR_SOCKET = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR_SOCKET", "/var/lib/streamforge-node/channel-supervisor.sock"))
NODE_CHANNEL_SUPERVISOR_PID_FILE = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR_PID_FILE", "/var/lib/streamforge-node/channel-supervisor.pid"))
NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE", "/var/lib/streamforge-node/channel-supervisor-runtime.json"))
NODE_CHANNEL_SUPERVISOR_LOG = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR_LOG", "/var/lib/streamforge-node/channel-supervisor.log"))
NODE_CHANNEL_SUPERVISOR_IO_ROOT = Path(os.getenv("STREAMFORGE_NODE_CHANNEL_SUPERVISOR_IO_ROOT", "/var/lib/streamforge-node/supervisor-io"))
NODE_CHANNEL_OWNER = bool(NODE_MODE == "supervisor" or (NODE_MODE == "control" and not NODE_DEDICATED_CHANNEL_SUPERVISOR))
NODE_PUBLIC_WORKER_COUNT = max(1, int(os.getenv("STREAMFORGE_NODE_PUBLIC_WORKER_COUNT", "1")))
# STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_V79: modern Remote Nodes run the high-volume
# Playlist/API pool in streamforge-node-public.service, never as a child of the
# single-worker control service.  The flag is set by the root installer.
NODE_PUBLIC_MANAGED_BY_SYSTEMD = os.getenv("STREAMFORGE_NODE_PUBLIC_MANAGED_BY_SYSTEMD", "0").strip().lower() in {"1", "true", "yes", "on"}
EXTERNAL_PROXY_MODE = os.getenv("STREAMFORGE_NODE_EXTERNAL_PROXY", "0").strip().lower() in {"1", "true", "yes", "on"}
CONTROL_PORT = max(1, min(65535, int(os.getenv("STREAMFORGE_NODE_PORT", "80"))))
CONTROL_BACKEND_PORT = max(1024, min(65535, int(os.getenv("STREAMFORGE_NODE_CONTROL_BACKEND_PORT", "8810"))))
PUBLIC_PORT_DEFAULT = max(1, min(65535, int(os.getenv("STREAMFORGE_NODE_PUBLIC_PORT", str(CONTROL_PORT)))))
NODE_PUBLIC_BACKEND_PORT = max(1024, min(65535, int(os.getenv("STREAMFORGE_NODE_PUBLIC_BACKEND_PORT", "8821"))))
NODE_PUBLIC_WORKERS_MODE = os.getenv("STREAMFORGE_NODE_PUBLIC_WORKERS", "auto").strip().lower() or "auto"
NODE_PUBLIC_WORKERS_MAX_RAW = os.getenv("STREAMFORGE_NODE_PUBLIC_WORKERS_MAX", "auto").strip().lower() or "auto"
NODE_REDIS_ENABLED = os.getenv("STREAMFORGE_NODE_REDIS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
NODE_REDIS_URL = os.getenv("STREAMFORGE_NODE_REDIS_URL", "redis://127.0.0.1:6379/1").strip()
NODE_REDIS_NAMESPACE = os.getenv("STREAMFORGE_NODE_REDIS_NAMESPACE", "").strip() or hashlib.sha256((PANEL_NODE_SLUG or TOKEN or socket.gethostname()).encode("utf-8", errors="ignore")).hexdigest()[:16]
NODE_MEDIA_AUTH_CACHE_SECONDS = max(5, min(300, int(os.getenv("STREAMFORGE_NODE_MEDIA_AUTH_CACHE_SECONDS", "60"))))
NODE_MEDIA_HEARTBEAT_TTL = max(NODE_MEDIA_AUTH_CACHE_SECONDS * 2 + 15, int(os.getenv("STREAMFORGE_NODE_MEDIA_HEARTBEAT_TTL", "150")))
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
SPEED_RE = re.compile(r"([0-9.]+)x")

# STREAMFORGE_NODE_REDIS_SHARED_STATE_V65: multi-worker public traffic shares
# playback grants, connection reservations and viewer heartbeats through Redis.
node_redis = (
    NodeRedisState(NODE_REDIS_URL, f"streamforge:node:{NODE_REDIS_NAMESPACE}", enabled=NODE_REDIS_ENABLED)
    if NodeRedisState is not None else None
)
BITRATE_RE = re.compile(r"([0-9.]+)kbits/s")
VIDEO_DIMENSION_RE = re.compile(r"(?<!\d)([1-9]\d{1,4})x([1-9]\d{1,4})(?!\d)")
VIDEO_FPS_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*fps", re.IGNORECASE)


# STREAMFORGE_NODE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041:
def _normalized_webplayer_login_mode(value: object) -> str:
    mode = str(value or "manual").strip().lower()
    return mode if mode in {"manual", "auto", "quick"} else "manual"


_CURRENT_PANEL_PREFIX: ContextVar[str | None] = ContextVar("streamforge_panel_prefix", default=None)
_CURRENT_STREAM_PREFIX: ContextVar[str | None] = ContextVar("streamforge_stream_prefix", default=None)

# Compatibility markers retained for updater lineage; the old in-place
# fetch/document.write implementation itself is replaced by v5.0 native reloads.
# STREAMFORGE_NODE_FIXED_PANEL_ADDRESS_BAR_V3059
# STREAMFORGE_NODE_ZERO_FLASH_PANEL_NAV_V3060
# STREAMFORGE_NODE_RELOAD_SAFE_HIDDEN_ROUTE_V3060R2
# STREAMFORGE_NODE_HIDE_HOVER_URLS_V2255
# STREAMFORGE_NODE_HIDDEN_NATIVE_ROUTE_V50:
# Node Panel navigation uses normal full-document reloads while only the
# configured Panel/API root remains browser-visible. The cookie contains only
# an allow-listed, non-sensitive internal GET route.
STREAMFORGE_NODE_PANEL_ROUTE_COOKIE = "streamforge_node_panel_route"
STREAMFORGE_NODE_HIDDEN_PANEL_PREFIXES = (
    "/panel", "/panel/login", "/panel/logout", "/panel/account",
    "/panel/manage", "/panel/channels", "/panel/playlists",
    "/panel/users", "/panel/sessions",
)

def _normalize_hidden_node_panel_target(raw: str | None) -> str:
    value = urllib.parse.unquote(str(raw or "").strip())
    if not value or len(value) > 3500 or not value.startswith("/") or value.startswith("//"):
        return ""
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme or parts.netloc or parts.fragment:
        return ""
    path = parts.path or "/panel"
    if path != "/panel" and not any(path == prefix or path.startswith(prefix + "/") for prefix in STREAMFORGE_NODE_HIDDEN_PANEL_PREFIXES[1:]):
        return ""
    target = path
    if parts.query:
        target += "?" + parts.query
    return target


def _node_public_backend_port() -> int:
    port = int(NODE_PUBLIC_BACKEND_PORT)
    if port == CONTROL_PORT:
        port = 8822 if CONTROL_PORT != 8822 else 8823
    return max(1024, min(65535, port))


def _node_cpu_limit() -> int:
    try:
        detected = max(1, len(os.sched_getaffinity(0)))
    except Exception:
        detected = max(1, int(os.cpu_count() or 1))
    try:
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().strip().split()
        if len(raw) == 2 and raw[0] != "max":
            quota, period = int(raw[0]), int(raw[1])
            if quota > 0 and period > 0:
                detected = min(detected, max(1, math.ceil(quota / period)))
    except Exception:
        pass
    return detected


def _node_available_memory_mb() -> int:
    available = 1024
    try:
        for row in Path("/proc/meminfo").read_text().splitlines():
            if row.startswith("MemAvailable:"):
                available = max(256, int(row.split()[1]) // 1024)
                break
    except Exception:
        pass
    try:
        limit_raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        current_raw = Path("/sys/fs/cgroup/memory.current").read_text().strip()
        if limit_raw != "max":
            remaining = max(0, int(limit_raw) - int(current_raw)) // (1024 * 1024)
            if remaining:
                available = min(available, remaining)
    except Exception:
        pass
    return max(256, available)


def _node_ffmpeg_process_count() -> int:
    count = 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if raw:
            first = raw.split(b"\0", 1)[0].decode("utf-8", errors="ignore")
            if Path(first).name == "ffmpeg":
                count += 1
    return count


def _node_tier_auto_target(cpu: int) -> int:
    cpu = max(1, int(cpu))
    if cpu <= 16:
        return cpu
    if cpu <= 32:
        return max(16, math.ceil(cpu * 0.75))
    if cpu <= 64:
        return max(24, math.ceil(cpu * 0.625))
    return max(32, math.ceil(cpu * 4.0 / 7.0))


def _node_worker_ceiling(cpu: int) -> tuple[int, str]:
    raw = NODE_PUBLIC_WORKERS_MAX_RAW
    # 32 was the generated v6.5 default.  In Auto mode interpret that legacy
    # value as a hardware ceiling so an upgraded 84-core node can reach the
    # new 48-worker target without requiring an env edit.
    if raw in {"", "auto"} or (raw == "32" and NODE_PUBLIC_WORKERS_MODE in {"", "auto"}):
        return max(1, min(cpu, 256)), "auto-cpu"
    try:
        return max(1, min(int(raw), cpu, 256)), "configured"
    except ValueError:
        return max(1, min(cpu, 256)), "invalid-auto-fallback"


def _node_public_worker_count() -> int:
    """Choose a stable hardware-aware public worker count, gated by Redis.

    STREAMFORGE_NODE_HARDWARE_AWARE_WORKERS_V65R1
    """
    redis_ok = bool(node_redis is not None and node_redis.available)
    cpu = _node_cpu_limit()
    mem_mb = _node_available_memory_mb()
    max_workers, max_mode = _node_worker_ceiling(cpu)
    reserve_mb = min(4096, max(512, mem_mb // 8))
    usable_mb = max(256, mem_mb - reserve_mb)
    ram_workers = max(1, usable_mb // 256)
    ffmpeg_processes = _node_ffmpeg_process_count()
    ffmpeg_cpu_reserve = min(max(0, cpu // 4), max(0, ffmpeg_processes // 2))
    effective_cpu = max(1, cpu - ffmpeg_cpu_reserve)
    auto_target = _node_tier_auto_target(effective_cpu)

    if not redis_ok:
        workers = 1
        reason = "redis-unavailable"
    else:
        manual = NODE_PUBLIC_WORKERS_MODE
        if manual not in {"", "auto"}:
            try:
                workers = max(1, min(max_workers, ram_workers, int(manual)))
                reason = "manual"
            except ValueError:
                workers = max(1, min(auto_target, max_workers, ram_workers))
                reason = "invalid-manual-auto-fallback"
        else:
            workers = max(1, min(auto_target, max_workers, ram_workers))
            reason = f"auto-cpu{cpu}-effective{effective_cpu}-ram{mem_mb // 1024}g"
    try:
        out = Path("/var/lib/streamforge-node/public-workers.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "workers": int(workers),
            "mode": NODE_PUBLIC_WORKERS_MODE,
            "redis": redis_ok,
            "reason": reason,
            "cpu": cpu,
            "effective_cpu": effective_cpu,
            "mem_available_mb": mem_mb,
            "ram_worker_ceiling": ram_workers,
            "ffmpeg_processes": ffmpeg_processes,
            "ffmpeg_cpu_reserve": ffmpeg_cpu_reserve,
            "auto_target": auto_target,
            "max_workers": max_workers,
            "max_mode": max_mode,
            "updated_at": int(time.time()),
        }, separators=(",", ":")) + "\n", encoding="utf-8")
    except OSError:
        pass
    return int(workers)


def _gunicorn_asgi_command(app_ref: str, host: str, port: int, *, workers: int = 1) -> list[str]:
    """Build the Gunicorn command for a Node HTTP listener."""
    worker_count = max(1, int(workers))
    return [
        GUNICORN,
        app_ref,
        "--bind", f"{host}:{int(port)}",
        "--workers", str(worker_count),
        "--worker-class", "uvicorn_worker.UvicornWorker",
        "--timeout", "120" if worker_count == 1 else "45",
        "--graceful-timeout", "30" if worker_count == 1 else "15",
        "--keep-alive", "5" if worker_count == 1 else "10",
        "--backlog", "65535",
        "--access-logfile", "-",
        "--error-logfile", "-",
    ]


class ChannelConfig(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    name: str = "Channel"
    slug: str = "channel"
    category: str = "Uncategorized"
    categories: list[str] = Field(default_factory=list)
    category_order: int = 100000
    channel_order: int = 100000
    logo_url: str = ""
    input_url: str
    input_urls: list[str] = Field(default_factory=list)
    input_program_ids: list[int | None] = Field(default_factory=list)
    input_mode: str = "source"
    active_input_index: int = 0
    failback_enabled: bool = True
    failback_interval: int = 30
    program_id: int | None = None
    enabled: bool = True
    auto_restart: bool = True
    video_codec: str = "copy"
    video_bitrate: str = "2500k"
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    preset: str = "ultrafast"
    audio_codec: str = "copy"
    audio_bitrate: str = "128k"
    output_type: str = "hls"
    output_url: str | None = None
    hls_segment_time: int = 1
    # Main catalogue number (101+). Node-local rows calculate their own 1+ ID.
    display_id: int | None = None
    # main = synchronized from Main Server; local = created on this Independent Node.
    catalog_owner: str = "main"


def _remote_output_urls(raw: str | None) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in str(raw or "").splitlines() if line.strip()))


def _tee_escape_filename(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _remote_tee_slave(url: str, *, http_put: bool = False, onfail_ignore: bool = True) -> str:
    options = ["f=mpegts", "mpegts_flags=+resend_headers"]
    if onfail_ignore:
        options.append("onfail=ignore")
    if url.lower().startswith(("http://", "https://")):
        options.extend(["content_type=video/mp2t", f"method={'PUT' if http_put else 'POST'}"])
    return "[" + ":".join(options) + "]" + _tee_escape_filename(url)


# STREAMFORGE_NODE_BULK_CONFIG_PAYLOAD_V1127:
class BulkChannelSyncPayload(BaseModel):
    channels: list[ChannelConfig] = Field(default_factory=list, max_length=500)


class ChannelLogoSyncPayload(BaseModel):
    content_base64: str = Field(min_length=4, max_length=3_000_000)


class NodeCategoryConfig(BaseModel):
    category_id: int | None = None
    name: str
    slug: str = ""
    sort_order: int = 100000
    owner: str = "main"


class NodeUserChannel(BaseModel):
    stream_id: int | None = None
    key: str
    name: str
    slug: str
    category: str = "Uncategorized"
    categories: list[str] = Field(default_factory=list)
    category_order: int = 100000
    channel_order: int = 100000
    logo_url: str = ""


class NodePlaylistConfig(BaseModel):
    token: str
    name: str
    description: str = ""
    logo_url: str = ""
    enabled: bool = True
    all_node_channels: bool = False
    channels: list[NodeUserChannel] = Field(default_factory=list)
    playlist_order: list[str] = Field(default_factory=list)
    playlist_category_order: list[str] = Field(default_factory=list)


class NodeUserConfig(BaseModel):
    user_id: int | None = None
    source: str = "direct_node"
    name: str
    username: str = ""
    password_hash: str = ""
    token: str
    user_type: str = "viewer"
    restream_allowed_ips: str = ""
    playlist_name: str = ""
    playlist_logo_url: str = ""
    playlist_profile_id: str = ""
    playlist_order: list[str] = Field(default_factory=list)
    playlist_category_order: list[str] = Field(default_factory=list)
    xtream_output_url: str = ""
    enabled: bool = True
    expires_at: str | None = None
    max_connections: int = 1
    all_node_channels: bool = False
    channels: list[NodeUserChannel] = Field(default_factory=list)


class UserSyncPayload(BaseModel):
    panel_url: str
    node_slug: str
    independent_mode: bool = False
    users: list[NodeUserConfig] = Field(default_factory=list)


class PanelAccessUser(BaseModel):
    username: str
    # Password hashes are no longer cached on Nodes. The field remains for
    # backward-compatible parsing of old panel-users.json files and is erased
    # during startup/synchronization.
    password_hash: str = ""
    auth_version: str = ""
    enabled: bool = True
    permissions: list[str] = Field(default_factory=list)


class PanelUserSyncPayload(BaseModel):
    panel_url: str
    node_slug: str
    node_name: str = ""
    node_logo_url: str = ""
    node_logo_filename: str = ""
    node_logo_content_base64: str = Field(default="", max_length=3_000_000)
    node_favicon_filename: str = ""
    node_favicon_content_base64: str = Field(default="", max_length=3_000_000)
    remove_node_favicon: bool = False
    independent_mode: bool = False
    users: list[PanelAccessUser] = Field(default_factory=list)


class ModeSyncPayload(BaseModel):
    independent_mode: bool = False
    desired_channel_keys: list[str] = Field(default_factory=list)
    local_channel_limit: int = 0
    total_max_connections: int = 0
    categories: list[NodeCategoryConfig] = Field(default_factory=list)


class KillViewerPayload(BaseModel):
    session_id: str


class GeoSettingsPayload(BaseModel):
    provider: str = "auto"
    auto_update: bool = False
    maxmind_account_id: str = ""
    maxmind_license_key: str = ""
    ipinfo_token: str = ""
    remove_maxmind_key: bool = False
    remove_ipinfo_token: bool = False


class AccessSettingsPayload(BaseModel):
    # Legacy fields remain accepted by old Main Panels.
    dns_only: bool = False
    allowed_host: str = ""
    panel_dns_only: bool | None = None
    panel_host: str = ""
    panel_urls: list[str] = Field(default_factory=list)
    stream_dns_only: bool | None = None
    stream_host: str = ""
    stream_urls: list[str] = Field(default_factory=list)
    access_slug: str = ""
    stream_slug: str = ""
    main_panel_url: str = ""
    # STREAMFORGE_MAIN_NODE_NAME_SYNC_V304:
    node_name: str = ""
    stream_port: int | None = None
    total_max_connections: int | None = None
    panel_ip_whitelist: str = ""
    panel_ip_blacklist: str = ""
    panel_asn_whitelist: str = ""
    panel_asn_blacklist: str = ""
    ip_whitelist: str = ""
    ip_blacklist: str = ""
    asn_whitelist: str = ""
    asn_blacklist: str = ""
    # STREAMFORGE_NODE_WEBPLAYER_MANAGE_V2219:
    webplayer_show_user_info: bool | None = None
    webplayer_show_connection_info: bool | None = None
    webplayer_login_mode: str | None = None
    webplayer_auto_user_id: int | None = None
    webplayer_auto_user_token: str | None = None
    webplayer_page_color: str | None = None
    webplayer_page_alpha: int | None = None
    webplayer_panel_color: str | None = None
    webplayer_panel_alpha: int | None = None
    webplayer_accent_color: str | None = None
    webplayer_accent_alpha: int | None = None
    webplayer_text_color: str | None = None
    webplayer_text_alpha: int | None = None
    viewer_ttl_seconds: int | None = None
    client_session_reset_offline_minutes: int | None = None
    hide_panel_hover_urls: bool | None = None
    # STREAMFORGE_NODE_ANDROID_UPDATE_SYNC_V3061:
    android_version_name: str | None = None
    android_description: str | None = None
    # STREAMFORGE_NODE_WEBPLAYER_MULTI_BRAND_V1155:
    webplayer_brands: list[dict[str, Any]] | None = None


class AdoptedProcess:
    """Small Popen-compatible handle for an FFmpeg inherited across an API update.

    The encoder is no longer a child of the replacement Gunicorn worker, so
    waitpid() is unavailable.  PID + /proc start-time validation prevents PID
    reuse from ever being mistaken for the original encoder.
    """

    def __init__(self, pid: int, start_ticks: int) -> None:
        self.pid = max(0, int(pid or 0))
        self.start_ticks = max(0, int(start_ticks or 0))
        self.stdout = None
        self.stderr = None
        self.returncode: int | None = None

    @staticmethod
    def _proc_start_ticks(pid: int) -> int:
        try:
            raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
            # comm may contain spaces/parentheses; fields after the final ')'
            # begin with state (field 3), so starttime (field 22) is index 19.
            tail = raw.rsplit(")", 1)[1].strip().split()
            return int(tail[19]) if len(tail) > 19 else 0
        except (OSError, ValueError, IndexError):
            return 0

    def _alive(self) -> bool:
        if self.pid <= 1 or self.start_ticks <= 0:
            return False
        if self._proc_start_ticks(self.pid) != self.start_ticks:
            return False
        try:
            exe = Path(f"/proc/{self.pid}/exe").resolve()
            return exe.name == "ffmpeg"
        except OSError:
            return False

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._alive():
            return None
        self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(["ffmpeg", f"pid={self.pid}"], timeout)
            time.sleep(0.2)


class Runtime:
    def __init__(self, config: ChannelConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | AdoptedProcess | None = None
        self.status = "stopped"
        self.last_error: str | None = None
        self.bitrate_kbps = 0
        self.speed_x = 0.0
        self.live_width = 0
        self.live_height = 0
        self.live_fps = 0.0
        self.started_at = 0.0
        self.updated_at = 0.0
        # STREAMFORGE_NODE_WAITING_TIMER_V51: track delivery waiting time
        # independently from the current FFmpeg process uptime. This survives
        # automatic process restarts while the same Runtime stays desired-running.
        self.waiting_since = 0.0
        self.intentional_stop = False
        self.desired_running = False
        self.restart_attempt = 0
        self.restart_timer: threading.Timer | None = None
        self.active_input_index = max(0, int(config.active_input_index or 0))
        self.relay_health_checked_at = 0.0
        self.relay_health_ready = False
        self.relay_health_detail = ""
        self.relay_health_log_at = 0.0
        self.error_log_path: Path | None = None
        self.error_log_handle: Any | None = None




# STREAMFORGE_NODE_GPU_BACKGROUND_METRICS_V1069:
# GPU discovery/sampling is intentionally isolated from dashboard request paths.
# The sampler runs in one low-rate background thread and snapshot() only copies
# the most recent cached result. NVIDIA uses nvidia-smi when available; Intel/
# AMD fall back to Linux DRM/sysfs metrics without adding Python dependencies.
class GPUMetricsSampler:
    def __init__(self, interval_seconds: float = 5.0) -> None:
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device_names: dict[str, str] = {}
        self._snapshot: dict[str, Any] = self._empty_snapshot()

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return {
            "available": False,
            "count": 0,
            "vendor": "",
            "name": "",
            "encoder": "",
            "usage_percent": None,
            "encoder_percent": None,
            "memory_used_bytes": 0,
            "memory_total_bytes": 0,
            "memory_percent": None,
            "temperature_c": None,
            "power_w": None,
            "active_streams": 0,
            "devices": [],
            "captured_at": 0.0,
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            text = str(value).strip()
            if not text or text.lower() in {"n/a", "na", "not supported", "[not supported]"}:
                return None
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_number(path: Path, divisor: float = 1.0) -> float | None:
        try:
            return float(path.read_text(encoding="utf-8", errors="replace").strip()) / divisor
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _active_gpu_streams() -> int:
        count = 0
        markers = (
            "_nvenc", "_qsv", "_vaapi", "h264_amf", "hevc_amf",
            "-hwaccel cuda", "-hwaccel qsv", "-vaapi_device",
            "hwupload_cuda", "hwupload=", "scale_cuda", "scale_qsv", "scale_vaapi",
        )
        try:
            proc_root = Path("/proc")
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore").lower()
                except OSError:
                    continue
                if "ffmpeg" not in raw:
                    continue
                if any(marker in raw for marker in markers):
                    count += 1
        except OSError:
            pass
        return count

    @staticmethod
    def _aggregate(devices: list[dict[str, Any]], active_streams: int) -> dict[str, Any]:
        if not devices:
            result = GPUMetricsSampler._empty_snapshot()
            result["captured_at"] = time.time()
            return result

        def avg(field: str) -> float | None:
            values = [float(item[field]) for item in devices if item.get(field) is not None]
            return round(sum(values) / len(values), 1) if values else None

        def maximum(field: str) -> float | None:
            values = [float(item[field]) for item in devices if item.get(field) is not None]
            return round(max(values), 1) if values else None

        total_memory = sum(max(0, int(item.get("memory_total_bytes") or 0)) for item in devices)
        used_memory = sum(max(0, int(item.get("memory_used_bytes") or 0)) for item in devices)
        power_values = [float(item["power_w"]) for item in devices if item.get("power_w") is not None]
        vendors = sorted({str(item.get("vendor") or "").strip() for item in devices if str(item.get("vendor") or "").strip()})
        encoders = sorted({str(item.get("encoder") or "").strip() for item in devices if str(item.get("encoder") or "").strip()})
        first_name = str(devices[0].get("name") or "GPU")
        name = first_name if len(devices) == 1 else f"{len(devices)} GPUs Â· {first_name}"
        return {
            "available": True,
            "count": len(devices),
            "vendor": vendors[0] if len(vendors) == 1 else "Mixed",
            "name": name,
            "encoder": "/".join(encoders),
            "usage_percent": avg("usage_percent"),
            "encoder_percent": avg("encoder_percent"),
            "memory_used_bytes": used_memory,
            "memory_total_bytes": total_memory,
            "memory_percent": round(used_memory / total_memory * 100.0, 1) if total_memory else None,
            "temperature_c": maximum("temperature_c"),
            "power_w": round(sum(power_values), 1) if power_values else None,
            "active_streams": max(0, int(active_streams)),
            "devices": devices,
            "captured_at": time.time(),
        }

    def _sample_nvidia(self) -> list[dict[str, Any]]:
        binary = shutil.which("nvidia-smi")
        if not binary:
            return []
        queries = [
            (
                "index,name,utilization.gpu,utilization.encoder,memory.used,memory.total,temperature.gpu,power.draw",
                True,
            ),
            (
                "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                False,
            ),
        ]
        for query, extended in queries:
            try:
                result = subprocess.run(
                    [binary, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                    text=True,
                    capture_output=True,
                    timeout=2.5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return []
            if result.returncode != 0:
                continue
            devices: list[dict[str, Any]] = []
            for line in result.stdout.splitlines():
                parts = [item.strip() for item in line.split(",")]
                expected = 8 if extended else 6
                if len(parts) < expected:
                    continue
                if extended:
                    index, name, usage, encoder, mem_used, mem_total, temperature, power = parts[:8]
                else:
                    index, name, usage, mem_used, mem_total, temperature = parts[:6]
                    encoder, power = "", ""
                used_mib = self._number(mem_used) or 0.0
                total_mib = self._number(mem_total) or 0.0
                devices.append({
                    "id": str(index),
                    "vendor": "NVIDIA",
                    "name": name or f"NVIDIA GPU {index}",
                    "encoder": "NVENC",
                    "usage_percent": self._number(usage),
                    "encoder_percent": self._number(encoder),
                    "memory_used_bytes": int(max(0.0, used_mib) * 1024 * 1024),
                    "memory_total_bytes": int(max(0.0, total_mib) * 1024 * 1024),
                    "temperature_c": self._number(temperature),
                    "power_w": self._number(power),
                })
            if devices:
                return devices
        return []

    def _sysfs_name(self, card: Path, vendor_name: str) -> str:
        cached = self._device_names.get(card.name)
        if cached:
            return cached
        device = card / "device"
        slot = ""
        driver = ""
        try:
            for line in (device / "uevent").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PCI_SLOT_NAME="):
                    slot = line.split("=", 1)[1].strip()
                elif line.startswith("DRIVER="):
                    driver = line.split("=", 1)[1].strip()
        except OSError:
            pass
        name = ""
        lspci = shutil.which("lspci")
        if lspci and slot:
            try:
                result = subprocess.run([lspci, "-s", slot], text=True, capture_output=True, timeout=1.5, check=False)
                text = result.stdout.strip()
                if ": " in text:
                    name = text.split(": ", 1)[1].strip()
            except (OSError, subprocess.SubprocessError):
                pass
        if not name:
            suffix = f" ({driver})" if driver else ""
            name = f"{vendor_name} GPU{suffix}"
        self._device_names[card.name] = name
        return name

    def _sample_sysfs(self) -> list[dict[str, Any]]:
        drm = Path("/sys/class/drm")
        if not drm.is_dir():
            return []
        vendors = {
            "0x10de": ("NVIDIA", "NVENC"),
            "0x8086": ("Intel", "QSV/VAAPI"),
            "0x1002": ("AMD", "VAAPI"),
        }
        devices: list[dict[str, Any]] = []
        for card in sorted(drm.glob("card[0-9]*")):
            if not re.fullmatch(r"card\d+", card.name):
                continue
            device = card / "device"
            try:
                vendor_id = (device / "vendor").read_text(encoding="utf-8", errors="replace").strip().lower()
            except OSError:
                continue
            if vendor_id not in vendors:
                continue
            vendor_name, encoder_name = vendors[vendor_id]
            usage = self._read_number(device / "gpu_busy_percent")
            memory_used = self._read_number(device / "mem_info_vram_used")
            memory_total = self._read_number(device / "mem_info_vram_total")
            temperature = None
            power = None
            for hwmon in sorted((device / "hwmon").glob("hwmon*")) if (device / "hwmon").is_dir() else []:
                if temperature is None:
                    temperature = self._read_number(hwmon / "temp1_input", 1000.0)
                if power is None:
                    power = self._read_number(hwmon / "power1_average", 1_000_000.0)
            devices.append({
                "id": card.name,
                "vendor": vendor_name,
                "name": self._sysfs_name(card, vendor_name),
                "encoder": encoder_name,
                "usage_percent": usage,
                "encoder_percent": None,
                "memory_used_bytes": int(max(0.0, memory_used or 0.0)),
                "memory_total_bytes": int(max(0.0, memory_total or 0.0)),
                "temperature_c": temperature,
                "power_w": power,
            })
        return devices

    def _sample(self) -> dict[str, Any]:
        devices = self._sample_nvidia()
        if not devices:
            devices = self._sample_sysfs()
        return self._aggregate(devices, self._active_gpu_streams())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                fresh = self._sample()
                with self._lock:
                    self._snapshot = fresh
            except Exception:
                pass
            if self._stop.wait(self.interval_seconds):
                break

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="streamforge-gpu-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._snapshot)
            result["devices"] = [dict(item) for item in self._snapshot.get("devices", []) if isinstance(item, dict)]
            return result


_node_gpu_metrics = GPUMetricsSampler()

class AgentManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        # STREAMFORGE_NODE_ATOMIC_STATE_WRITE_V65R4: serialize state-file
        # commits from concurrent FFmpeg watcher threads. A shared fixed .tmp
        # pathname allowed one thread to rename another thread's staging file.
        self.state_write_lock = threading.RLock()
        self.channels: dict[str, Runtime] = {}
        self._pending_update_handoff: dict[str, dict[str, Any]] = {}
        self.channel_supervisor_process: subprocess.Popen[Any] | None = None
        self.channel_supervisor_log_handle: Any | None = None
        self._channel_supervisor_spawn_lock = threading.Lock()
        self._channel_supervisor_status_lock = threading.Lock()
        self._channel_supervisor_status_cache_at = 0.0
        self._channel_supervisor_status_cache: dict[str, dict[str, Any]] = {}
        # STREAMFORGE_NODE_STAGGERED_CHANNEL_AUTOSTART_V1051:
        # A control-worker restart used to launch every desired FFmpeg in parallel.
        # On large Nodes that creates a short reconnect storm against Main relay.
        # Keep not-yet-started channels out of the watchdog until their batch is due.
        self._startup_pending: set[str] = set()
        # STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052:
        # v10.51 paced only control-worker startup. Normal FFmpeg exit timers and
        # the desired-state watchdog could still wake many channels together after
        # a shared source/relay interruption. Serialize every automatic FFmpeg start
        # through one Node-wide pacer so startup, recovery timers and watchdog repair
        # cannot create a second reconnect storm. Manual Start/Restart stays immediate.
        self._automatic_start_lock = threading.Lock()
        self._automatic_start_next = 0.0
        self._automatic_starts_per_second = max(1, min(32, int(os.getenv("STREAMFORGE_NODE_AUTO_STARTS_PER_SECOND", "8"))))
        self.encoder_cache: dict[str, str] = {}
        self.encoder_probe_cache: dict[tuple[str, str], bool] = {}
        # Network rates are shared by the heartbeat, quick-status API and the
        # Node dashboard. Keep one cached sampler so a second caller cannot
        # reset the byte baseline and incorrectly turn active traffic into 0.
        self.metrics_lock = threading.RLock()
        # STREAMFORGE_NODE_REAL_CPU_PROC_STAT_V1148:
        # Keep one shared /proc/stat baseline for the control worker.  Linux load
        # average measures runnable/uninterruptible work and is not CPU utilization;
        # the old load1/cores formula could therefore show 70-100% on an almost-idle
        # high-I/O Node.  Mirror Main's delta sampler without blocking dashboard calls.
        self.last_cpu_sample_at = time.monotonic()
        self.last_cpu: tuple[int, int] = self._cpu_counters()
        self.cached_cpu_percent = 0.0
        self.last_net: tuple[float, dict[str, tuple[int, int]]] | None = None
        self.cached_network_metrics: dict[str, Any] = {
            "network_interface": "",
            "network_interfaces": [],
            "network_download_mbps": 0.0,
            "network_upload_mbps": 0.0,
            "network_sample_seconds": 0.0,
        }
        self.users: dict[str, NodeUserConfig] = {}
        self.playlists: dict[str, NodePlaylistConfig] = {}
        self.panel_users: dict[str, PanelAccessUser] = {}
        self.viewer_sessions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        # STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOCAL_V1046: single-worker /
        # Redis-fallback Nodes keep completed SID generations in memory too, so
        # the Client-log Session age freezes after playback goes offline.
        self.viewer_session_history: dict[str, list[dict[str, float]]] = {}
        self._viewer_session_history_force_new: set[str] = set()
        self._client_session_history_ttl_seconds = 30 * 86400
        self._client_session_history_ttl_refresh_at = 0.0
        # STREAMFORGE_NODE_SESSION_OBSERVER_STATE_V1049: persistent Client-log
        # Session age and reconnect logging are owned by the single control
        # worker. Public playback workers carry no history/logging daemon and
        # no per-heartbeat history queue.
        self._viewer_observer_previous: dict[tuple[str, str], tuple[float, float]] = {}
        self._viewer_observer_warmed = False
        self._viewer_observer_thread_started = False
        self.viewer_session_index: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        self.connection_reservations: dict[tuple[str, str], float] = {}
        self._shared_viewers_loaded = False
        self._shared_viewers_mtime_ns = -1
        self._shared_viewers_save_timer: threading.Timer | None = None
        self._viewer_next_prune = 0.0
        self.proxied_viewer_sessions: list[dict[str, Any]] = []
        self.panel_url = PANEL_URL
        self.panel_node_slug = PANEL_NODE_SLUG
        self.node_name = "Node"
        self.node_logo_url = ""
        self.independent_mode = False
        self.local_channel_limit = 0
        self.total_max_connections = 0
        self.main_categories: list[NodeCategoryConfig] = []
        self.category_order_overrides: list[str] = []
        self.panel_dns_only = True
        self.panel_host = ALLOWED_HOST
        self.panel_urls: list[str] = []
        self.stream_dns_only = True
        self.stream_host = STREAM_ALLOWED_HOST
        self.stream_urls: list[str] = []
        self.access_slug = ""
        self.stream_slug = ""
        self.stream_port = PUBLIC_PORT_DEFAULT
        self.public_process: subprocess.Popen[Any] | None = None
        self.public_process_port = 0
        self.public_process_workers = 0
        self.public_process_log_handle: Any | None = None
        self.public_gateway_error = ""
        self.panel_gateway_processes: dict[int, subprocess.Popen[Any]] = {}
        self.panel_gateway_log_handles: dict[int, Any] = {}
        self.panel_gateway_errors: dict[int, str] = {}
        self.access_reconcile_request_mtime_ns = -1
        # STREAMFORGE_NODE_VIEWER_TTL_RUNTIME_RELOAD_V90R3:
        # Every public Gunicorn worker watches access.json independently so a
        # Main-side Online Session Timeout change applies without restarting the
        # high-concurrency public pool.
        # STREAMFORGE_NODE_ACCESS_SIGNATURE_RELOAD_V124:
        # access.json is atomically replaced by the control plane. Track full
        # file identity so every long-lived public worker sees brand/access CRUD
        # even when mtimes repeat or the wall clock moves backwards.
        self.access_file_signature: tuple[int, int, int, int] | None = None
        self.access_file_mtime_ns = -1
        self.users_file_mtime = 0.0
        # STREAMFORGE_NODE_PLAYLIST_USER_SIGNATURE_RELOAD_V123: atomic replace can
        # change inode without a strictly increasing wall-clock mtime. Track the
        # complete file identity so every public worker sees add/edit/delete.
        self.users_file_signature: tuple[int, int, int, int] | None = None
        self.state_file_mtime = 0.0
        self.panel_ip_whitelist = ""
        self.panel_ip_blacklist = ""
        self.panel_asn_whitelist = ""
        self.panel_asn_blacklist = ""
        self.ip_whitelist = ""
        self.ip_blacklist = ""
        self.asn_whitelist = ""
        self.asn_blacklist = ""
        self.webplayer_show_user_info = True
        self.webplayer_show_connection_info = True
        self.viewer_ttl_seconds = VIEWER_TTL
        self.client_session_reset_offline_minutes = 60
        self.hide_panel_hover_urls = True
        self.webplayer_login_mode = "manual"
        self.webplayer_auto_user_id = 0
        self.webplayer_auto_user_token = ""
        self.webplayer_page_color = "#04080d"
        self.webplayer_page_alpha = 100
        self.webplayer_panel_color = "#071019"
        self.webplayer_panel_alpha = 100
        self.webplayer_accent_color = "#ff2020"
        self.webplayer_accent_alpha = 100
        self.webplayer_text_color = "#f6fbff"
        self.webplayer_text_alpha = 100
        self.webplayer_brands: list[dict[str, Any]] = []
        self.webplayer_download_name = ""
        self.android_version_name = ""
        self.android_description = ""
        self._asn_reader = None
        self._country_reader = None
        self._geo_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # STREAMFORGE_NODE_VIEWER_GEO_BACKGROUND_V1126: direct-session APIs
        # never wait on IPinfo. Missing GeoIP records resolve in the background
        # and subsequent Main/Node Live Sessions refreshes reuse the cache.
        self._geo_background_lock = threading.RLock()
        self._geo_background_pending: set[str] = set()
        self._geo_background_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sf-node-viewer-geo")
        self._asn_status_cache: tuple[float, dict[str, Any]] | None = None
        self.geo_provider = "auto"
        self.geo_auto_update = GEOIP_AUTO_UPDATE
        self.maxmind_account_id = os.getenv("STREAMFORGE_MAXMIND_ACCOUNT_ID", "").strip()
        self.maxmind_license_key = os.getenv("STREAMFORGE_MAXMIND_LICENSE_KEY", "").strip()
        self.ipinfo_token = os.getenv("STREAMFORGE_IPINFO_TOKEN", "").strip()
        self.panel_last_check = 0.0
        self.panel_last_ok = False
        # STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210:
        # These fields cache only the shared control-worker heartbeat snapshot;
        # they are intentionally separate from panel_last_check/panel_last_ok,
        # which remain the control worker's live network heartbeat cache.
        self._panel_shared_last_check = 0.0
        self._panel_shared_last_ok = False
        self._panel_connectivity_write_lock = threading.Lock()
        self.event_logs: deque[dict[str, Any]] = deque(maxlen=NODE_EVENT_LOG_MAX)
        self._last_event_log_prune = 0.0
        # STREAMFORGE_NODE_PUBLIC_CLIENT_LOG_SYNC_V1045: Public Playlist/WebPlayer
        # workers append client events to one shared JSONL file while the single
        # control worker owns the panel/API ring buffer. Incrementally ingest those
        # cross-process events so manual logins and playlist requests appear live.
        self._event_log_sync_lock = threading.Lock()
        self._event_log_file_offset = 0
        self._event_log_file_identity: tuple[int, int] | None = None
        self._event_log_seen_order: deque[str] = deque()
        self._event_log_seen: set[str] = set()

    @staticmethod
    def _event_log_fingerprint(item: dict[str, Any]) -> str:
        payload = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()

    def _remember_event_log(self, item: dict[str, Any]) -> bool:
        fingerprint = self._event_log_fingerprint(item)
        if fingerprint in self._event_log_seen:
            return False
        self._event_log_seen.add(fingerprint)
        self._event_log_seen_order.append(fingerprint)
        ceiling = max(4000, NODE_EVENT_LOG_MAX * 2)
        while len(self._event_log_seen_order) > ceiling:
            expired = self._event_log_seen_order.popleft()
            self._event_log_seen.discard(expired)
        return True

    def sync_event_logs_from_file(self) -> None:
        # STREAMFORGE_NODE_PUBLIC_CLIENT_LOG_SYNC_V1045
        if NODE_MODE != "control":
            return
        with self._event_log_sync_lock:
            try:
                stat = LOG_FILE.stat()
            except OSError:
                return
            identity = (int(stat.st_dev), int(stat.st_ino))
            if self._event_log_file_identity != identity or int(stat.st_size) < int(self._event_log_file_offset or 0):
                self._event_log_file_identity = identity
                self._event_log_file_offset = 0
            cutoff = time.time() - (_node_log_retention_days() * 86400)
            try:
                with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(max(0, int(self._event_log_file_offset or 0)))
                    for line in handle:
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(item, dict):
                            continue
                        raw = str(item.get("time") or "").strip()
                        try:
                            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                        except (TypeError, ValueError):
                            stamp = time.time()
                        if stamp < cutoff or not self._remember_event_log(item):
                            continue
                        with self.lock:
                            self.event_logs.appendleft(item)
                    self._event_log_file_offset = int(handle.tell())
                    self._event_log_file_identity = identity
            except OSError:
                return

    # STREAMFORGE_NODE_LOG_DEQUE_SNAPSHOT_V104:
    # event_logs is written from channel/runtime threads while panel/API readers
    # build filters and pagination. Always take a locked immutable snapshot so a
    # concurrent append/prune cannot raise ``deque mutated during iteration``.
    def event_logs_snapshot(self) -> list[dict[str, Any]]:
        self.sync_event_logs_from_file()
        with self.lock:
            return [dict(item) for item in self.event_logs]

    def log(self, message: str, *, scope: str = "system", level: str = "info", key: str = "", details: str = "", user: str = "") -> None:
        from datetime import datetime, timezone
        item = {
            "time": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "level": level,
            "channel": key,
            "user": str(user or "")[:120],
            "message": str(message)[-4000:],
            "details": str(details)[-8000:] if details else "",
        }
        if self._remember_event_log(item):
            with self.lock:
                self.event_logs.appendleft(item)
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass
        self.prune_event_logs()

    def prune_event_logs(self, *, force: bool = False) -> None:
        # STREAMFORGE_NODE_LOG_RETENTION_V88:
        # Prune by age every five minutes (or immediately after Settings save).
        now_mono = time.monotonic()
        if not force and now_mono - float(self._last_event_log_prune or 0.0) < 300.0:
            return
        self._last_event_log_prune = now_mono
        if NODE_MODE == "control":
            self.sync_event_logs_from_file()
        cutoff = time.time() - (_node_log_retention_days() * 86400)
        with self.lock:
            previous = list(self.event_logs)
            kept: list[dict[str, Any]] = []
            for entry in previous:
                raw = str(entry.get("time") or "").strip()
                try:
                    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    stamp = time.time()
                if stamp >= cutoff:
                    kept.append(entry)
            if len(kept) > NODE_EVENT_LOG_MAX:
                kept = kept[:NODE_EVENT_LOG_MAX]
            changed = len(kept) != len(previous)
            self.event_logs = deque(kept, maxlen=NODE_EVENT_LOG_MAX)
        # STREAMFORGE_NODE_PUBLIC_LOG_SINGLE_WRITER_RETENTION_V1045
        # STREAMFORGE_NODE_LOG_SINGLE_WRITER_SUPERVISOR_V115: every child
        # process, including the channel supervisor, appends only.  The single
        # control worker remains the only process allowed to truncate/rewrite
        # the shared event log for retention.
        if NODE_MODE != "control":
            return
        try:
            oversized = LOG_FILE.is_file() and LOG_FILE.stat().st_size > NODE_EVENT_LOG_FILE_MAX_BYTES
        except OSError:
            oversized = False
        if force or changed or oversized:
            try:
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with self.lock:
                    rows = list(reversed(self.event_logs))
                LOG_FILE.write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in rows), encoding="utf-8")
            except OSError:
                pass

    @staticmethod
    def config_inputs(config: ChannelConfig) -> list[str]:
        values: list[str] = []
        for item in ([config.input_url] + list(config.input_urls or [])):
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
        return values

    @classmethod
    def config_program_ids(cls, config: ChannelConfig) -> list[int | None]:
        inputs = cls.config_inputs(config)
        values: list[int | None] = []
        for item in list(config.input_program_ids or [])[:len(inputs)]:
            try:
                parsed = int(item) if item not in (None, "") else None
            except (TypeError, ValueError):
                parsed = None
            values.append(parsed if parsed and 1 <= parsed <= 65535 else None)
        while len(values) < len(inputs):
            values.append(None)
        if values and values[0] is None and config.program_id:
            values[0] = int(config.program_id)
        return values

    @staticmethod
    def safe_key(key: str) -> str:
        if not KEY_RE.fullmatch(key):
            raise ValueError("Invalid channel key")
        return key

    @staticmethod
    def normalize_channel_config(config: ChannelConfig) -> ChannelConfig:
        """Upgrade legacy Node UI values without changing the stream intent."""
        video_aliases = {
            "h264_auto": "auto_h264", "h264": "auto_h264",
            "h265_auto": "auto_h265", "hevc_auto": "auto_h265",
            "h265": "auto_h265", "hevc": "auto_h265",
        }
        audio_aliases = {"mp3": "libmp3lame", "mp3lame": "libmp3lame", "opus": "libopus"}
        output_aliases = {"http": "http_post", "rtp": "udp"}
        video = str(config.video_codec or "auto").strip().lower()
        audio = str(config.audio_codec or "auto").strip().lower()
        output = str(config.output_type or "hls").strip().lower()
        owner = str(getattr(config, "catalog_owner", "main") or "main").strip().lower()
        program_ids = []
        for item in list(getattr(config, "input_program_ids", []) or []):
            try:
                parsed = int(item) if item not in (None, "") else None
            except (TypeError, ValueError):
                parsed = None
            program_ids.append(parsed if parsed and 1 <= parsed <= 65535 else None)
        input_count = len([item for item in ([config.input_url] + list(config.input_urls or [])) if str(item or "").strip()])
        while len(program_ids) < input_count:
            program_ids.append(None)
        program_ids = program_ids[:input_count]
        if program_ids and program_ids[0] is None and config.program_id:
            program_ids[0] = int(config.program_id)
        updates = {
            "input_program_ids": program_ids,
            "program_id": program_ids[0] if program_ids else None,
            "video_codec": video_aliases.get(video, video),
            "audio_codec": audio_aliases.get(audio, audio),
            "output_type": output_aliases.get(output, output),
            "hls_segment_time": max(1, min(20, int(getattr(config, "hls_segment_time", 1) or 1))),
            "catalog_owner": owner if owner in {"main", "local"} else "main",
            "category_order": max(0, int(getattr(config, "category_order", 100000) or 100000)),
            "channel_order": max(0, int(getattr(config, "channel_order", 100000) or 100000)),
        }
        return config.model_copy(update=updates)

    @classmethod
    def _normalize_v200_state_payload(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Persist the v2.0 two-second HLS profile in every Node state archive."""
        rows: list[dict[str, Any]] = []
        for item in raw.get("channels", []):
            if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                continue
            copied = dict(item)
            cfg = dict(item["config"])
            cfg["hls_segment_time"] = max(1, min(20, int(cfg.get("hls_segment_time") or 1)))
            copied["config"] = cfg
            rows.append(copied)
        return {"channels": rows}

    def _state_payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "channels": [
                    {"config": rt.config.model_dump(), "desired_running": bool(rt.desired_running)}
                    for rt in self.channels.values()
                ]
            }

    def _write_state(self, path: Path, payload: dict[str, Any]) -> None:
        # STREAMFORGE_NODE_ATOMIC_STATE_WRITE_V65R4
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, separators=(",", ":"))
        with self.state_write_lock:
            temp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
            try:
                temp.write_text(encoded, encoding="utf-8")
                os.replace(temp, path)
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _read_state(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text())
            return raw if isinstance(raw, dict) else {"channels": []}
        except (OSError, ValueError, TypeError):
            return {"channels": []}

    @staticmethod
    def _raw_channel_signature(item: dict[str, Any]) -> tuple[str, str, str, str]:
        cfg = item.get("config") if isinstance(item, dict) else {}
        cfg = cfg if isinstance(cfg, dict) else {}
        return (
            str(cfg.get("key") or ""),
            str(cfg.get("name") or ""),
            str(cfg.get("slug") or ""),
            str(cfg.get("input_url") or ""),
        )

    def _prepare_shared_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Shared catalogue while preserving explicit Node-owned rows.

        Older Shared catalogues had no owner marker, so those rows remain
        Main-owned. A local row always wins a duplicate key to prevent a Main
        synchronization from overwriting a channel created on the node.
        """
        by_key: dict[str, dict[str, Any]] = {}
        for item in raw.get("channels", []):
            if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                continue
            copied = dict(item)
            cfg = dict(item["config"])
            key = str(cfg.get("key") or "").strip()
            if not key:
                continue
            owner = str(cfg.get("catalog_owner") or "").strip().lower()
            cfg["catalog_owner"] = owner if owner in {"main", "local"} else "main"
            copied["config"] = cfg
            previous = by_key.get(key)
            if previous and str((previous.get("config") or {}).get("catalog_owner")) == "local" and cfg["catalog_owner"] == "main":
                continue
            by_key[key] = copied
        return {"channels": list(by_key.values())}

    def _prepare_independent_payload(
        self, raw: dict[str, Any], shared_raw: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Keep only Node-owned channels and migrate old unmarked catalogues safely.

        v1.11.9 initially cloned the Shared catalogue when Independent mode was
        enabled for the first time. Old rows have no catalog_owner marker. Such
        rows are removed when their key exists in the Shared archive; other
        unmarked rows are preserved as genuine local channels.
        """
        shared_rows = (shared_raw or {}).get("channels", [])
        shared_by_key = {
            self._raw_channel_signature(item)[0]: self._raw_channel_signature(item)
            for item in shared_rows if isinstance(item, dict)
        }
        rows: list[dict[str, Any]] = []
        for item in raw.get("channels", []):
            if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                continue
            cfg = dict(item["config"])
            owner_raw = str(cfg.get("catalog_owner") or "").strip().lower()
            signature = self._raw_channel_signature(item)
            if owner_raw == "main":
                continue
            if not owner_raw and signature[0] in shared_by_key:
                # This key belongs to the Shared/Main catalogue, even if an old
                # Independent editor changed some of its copied fields.
                continue
            cfg["catalog_owner"] = "local"
            copied = dict(item)
            copied["config"] = cfg
            rows.append(copied)
        return {"channels": rows}

    def _prepare_independent_active_payload(
        self, raw: dict[str, Any], shared_raw: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Normalize the live Independent catalogue without mixing ownership.

        Local rows remain editable. Main rows are synchronized from the Main
        Panel and are read-only in the Node UI. Legacy unmarked rows are
        classified using the Shared archive; unknown rows are treated as local.
        """
        shared_keys = {
            self._raw_channel_signature(item)[0]
            for item in (shared_raw or {}).get("channels", [])
            if isinstance(item, dict)
        }
        by_key: dict[str, dict[str, Any]] = {}
        for item in raw.get("channels", []):
            if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                continue
            copied = dict(item)
            cfg = dict(item["config"])
            key = str(cfg.get("key") or "").strip()
            if not key:
                continue
            owner = str(cfg.get("catalog_owner") or "").strip().lower()
            if owner not in {"main", "local"}:
                owner = "main" if key in shared_keys else "local"
            cfg["catalog_owner"] = owner
            copied["config"] = cfg
            previous = by_key.get(key)
            # Never let a synchronized Main row silently overwrite an existing
            # local channel with the same key.
            if previous and str((previous.get("config") or {}).get("catalog_owner")) == "local":
                continue
            by_key[key] = copied
        return {"channels": list(by_key.values())}

    @staticmethod
    def _select_owner(raw: dict[str, Any], owner: str) -> dict[str, Any]:
        return {
            "channels": [
                item for item in raw.get("channels", [])
                if isinstance(item, dict)
                and str((item.get("config") or {}).get("catalog_owner") or "").strip().lower() == owner
            ]
        }

    def _load_state_payload(
        self, raw: dict[str, Any], *, autostart: bool = True, expected_owner: str | None = None
    ) -> int:
        loaded = 0
        autostart_rows: list[tuple[str, ChannelConfig]] = []
        adopted_rows: list[tuple[str, Runtime, AdoptedProcess]] = []
        for item in raw.get("channels", []):
            try:
                cfg = self.normalize_channel_config(ChannelConfig.model_validate(item["config"]))
                if expected_owner and cfg.catalog_owner != expected_owner:
                    continue
                runtime = Runtime(cfg)
                runtime.desired_running = bool(item.get("desired_running"))
                self.channels[cfg.key] = runtime
                loaded += 1
                handoff = self._pending_update_handoff.get(cfg.key) if NODE_CHANNEL_OWNER else None
                if autostart and runtime.desired_running and cfg.enabled and isinstance(handoff, dict):
                    adopted = AdoptedProcess(
                        int(handoff.get("pid") or 0),
                        int(handoff.get("proc_start_ticks") or 0),
                    )
                    if adopted.poll() is None:
                        runtime.process = adopted
                        runtime.status = str(handoff.get("status") or "running")
                        runtime.last_error = handoff.get("last_error") or None
                        runtime.bitrate_kbps = max(0, int(handoff.get("bitrate_kbps") or 0))
                        runtime.speed_x = max(0.0, float(handoff.get("speed_x") or 0.0))
                        runtime.live_width = max(0, int(handoff.get("live_width") or 0))
                        runtime.live_height = max(0, int(handoff.get("live_height") or 0))
                        runtime.live_fps = max(0.0, float(handoff.get("live_fps") or 0.0))
                        runtime.started_at = max(0.0, float(handoff.get("started_at") or time.monotonic()))
                        runtime.updated_at = time.monotonic()
                        runtime.waiting_since = max(0.0, float(handoff.get("waiting_since") or 0.0))
                        runtime.restart_attempt = max(0, int(handoff.get("restart_attempt") or 0))
                        runtime.active_input_index = max(0, int(handoff.get("active_input_index") or 0))
                        runtime.config.active_input_index = runtime.active_input_index
                        adopted_rows.append((cfg.key, runtime, adopted))
                # Compatibility ownership guard: only the dedicated channel owner may autostart
                # Only the native control listener owns FFmpeg processes. Public,
                # panel and shared child listeners are read-only HTTP workers and
                # must never duplicate encoders when they load the same state file.
                if (
                    autostart and NODE_CHANNEL_OWNER and runtime.desired_running and cfg.enabled
                    and runtime.process is None
                ):
                    autostart_rows.append((cfg.key, cfg))
            except Exception:
                continue
        for key, runtime, process in adopted_rows:
            threading.Thread(
                target=self.watch_adopted_progress, args=(key, process),
                name=f"streamforge-adopted-progress-{key}", daemon=True,
            ).start()
            threading.Thread(
                target=self.wait_process, args=(key, process),
                name=f"streamforge-adopted-wait-{key}", daemon=True,
            ).start()
            if runtime.config.auto_restart:
                threading.Thread(
                    target=self.watch_stall, args=(key, process),
                    name=f"streamforge-adopted-stall-{key}", daemon=True,
                ).start()
            if runtime.config.failback_enabled and runtime.active_input_index > 0:
                threading.Thread(
                    target=self.watch_failback, args=(key, process),
                    name=f"streamforge-adopted-failback-{key}", daemon=True,
                ).start()
            self.log(
                "Running FFmpeg adopted across Node Agent update",
                scope="update", key=key, details=f"pid={process.pid}",
            )
        if autostart_rows:
            # STREAMFORGE_NODE_STAGGERED_CHANNEL_AUTOSTART_V1051:
            # Spread control-worker recovery over small batches instead of making
            # every FFmpeg reconnect to Main in the same second.  Eight starts per
            # second keeps a 135-channel Node back online in ~17 seconds while
            # avoiding the old 100+ channel reconnect burst.
            with self.lock:
                self._startup_pending.update(key for key, _cfg in autostart_rows)
            threading.Thread(
                target=self._staggered_autostart,
                args=(tuple(autostart_rows),),
                name="streamforge-node-channel-autostart",
                daemon=True,
            ).start()
        return loaded

    def _paced_automatic_start(self, key: str, config: ChannelConfig) -> dict[str, Any]:
        # STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052:
        # One shared lock/time cursor caps aggregate automatic starts even when
        # dozens of independent restart Timer threads fire in the same second.
        gap = 1.0 / float(self._automatic_starts_per_second)
        with self._automatic_start_lock:
            now = time.monotonic()
            slot = max(now, float(self._automatic_start_next or 0.0))
            self._automatic_start_next = slot + gap
        wait = max(0.0, slot - time.monotonic())
        if wait > 0:
            time.sleep(wait)
        # Re-check intent after the wait so a manual Stop issued while many
        # recoveries are queued cannot be undone by an old reserved slot.
        with self.lock:
            rt = self.channels.get(key)
            if not rt or rt.intentional_stop or not rt.desired_running or not rt.config.enabled:
                return self.status(key) if rt else {"key": key, "status": "stopped"}
            config = rt.config
        return self.start(key, config)

    def _staggered_autostart(self, rows: tuple[tuple[str, ChannelConfig], ...]) -> None:
        for key, config in rows:
            try:
                self._paced_automatic_start(key, config)
            except Exception as exc:
                with self.lock:
                    rt = self.channels.get(key)
                    if rt:
                        rt.status = "restarting"
                        rt.last_error = str(exc)[-4000:]
            finally:
                with self.lock:
                    self._startup_pending.discard(key)


    @staticmethod
    def _proc_start_ticks(pid: int) -> int:
        return AdoptedProcess._proc_start_ticks(pid)

    def prepare_update_ffmpeg_handoff(self, target_version: str = "") -> int:
        """Snapshot live encoders so a code-only control-worker reload is seamless."""
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            result = self._delegate_channel_command(
                "prepare_update", target_version=target_version, timeout=12.0
            )
            return max(0, int(result.get("preserved_encoders") or 0))
        rows: list[dict[str, Any]] = []
        with self.lock:
            for key, rt in self.channels.items():
                process = rt.process
                if not process or process.poll() is not None:
                    continue
                start_ticks = self._proc_start_ticks(process.pid)
                if start_ticks <= 0:
                    continue
                rows.append({
                    "key": key,
                    "pid": int(process.pid),
                    "proc_start_ticks": start_ticks,
                    "status": str(rt.status or "running"),
                    "last_error": rt.last_error or "",
                    "bitrate_kbps": int(rt.bitrate_kbps or 0),
                    "speed_x": float(rt.speed_x or 0.0),
                    "live_width": int(rt.live_width or 0),
                    "live_height": int(rt.live_height or 0),
                    "live_fps": float(rt.live_fps or 0.0),
                    "started_at": float(rt.started_at or time.monotonic()),
                    "waiting_since": float(rt.waiting_since or 0.0),
                    "restart_attempt": int(rt.restart_attempt or 0),
                    "active_input_index": int(rt.active_input_index or 0),
                })
        payload = {
            "created_epoch": time.time(),
            "from_version": VERSION,
            "target_version": str(target_version or ""),
            "channels": rows,
        }
        UPDATE_FFMPEG_HANDOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = UPDATE_FFMPEG_HANDOFF_FILE.with_name(
            f".{UPDATE_FFMPEG_HANDOFF_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, UPDATE_FFMPEG_HANDOFF_FILE)
        return len(rows)

    def load_update_ffmpeg_handoff(self) -> dict[str, dict[str, Any]]:
        if not NODE_CHANNEL_OWNER:
            return {}

        def read_rows(path: Path, *, max_age: float | None) -> dict[str, dict[str, Any]]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                created = float(raw.get("created_epoch") or 0.0)
                if max_age is not None and (created <= 0 or time.time() - created > max_age):
                    path.unlink(missing_ok=True)
                    return {}
                rows = raw.get("channels") or []
                return {
                    str(item.get("key") or ""): item
                    for item in rows if isinstance(item, dict) and str(item.get("key") or "")
                }
            except (OSError, ValueError, TypeError):
                return {}

        # A code update from v10.83+ writes the short-lived update handoff.
        result = read_rows(UPDATE_FFMPEG_HANDOFF_FILE, max_age=180.0)
        if result:
            cleanup_timer = threading.Timer(180.0, lambda: UPDATE_FFMPEG_HANDOFF_FILE.unlink(missing_ok=True))
            cleanup_timer.daemon = True
            cleanup_timer.start()
            return result

        # STREAMFORGE_NODE_SUPERVISOR_CRASH_ADOPTION_V115: the supervisor also
        # keeps a lightweight PID/start-tick snapshot.  A replacement supervisor
        # can validate and adopt those exact process IDs after an unexpected
        # supervisor exit without creating duplicate encoders.
        return read_rows(NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE, max_age=None)

    def persist_channel_supervisor_runtime(self) -> int:
        if not NODE_CHANNEL_OWNER:
            return 0
        rows: list[dict[str, Any]] = []
        with self.lock:
            for key, rt in self.channels.items():
                process = rt.process
                if not process or process.poll() is not None:
                    continue
                start_ticks = self._proc_start_ticks(process.pid)
                if start_ticks <= 0:
                    continue
                rows.append({
                    "key": key,
                    "pid": int(process.pid),
                    "proc_start_ticks": start_ticks,
                    "status": str(rt.status or "running"),
                    "last_error": rt.last_error or "",
                    "bitrate_kbps": int(rt.bitrate_kbps or 0),
                    "speed_x": float(rt.speed_x or 0.0),
                    "live_width": int(rt.live_width or 0),
                    "live_height": int(rt.live_height or 0),
                    "live_fps": float(rt.live_fps or 0.0),
                    "started_at": float(rt.started_at or time.monotonic()),
                    "waiting_since": float(rt.waiting_since or 0.0),
                    "restart_attempt": int(rt.restart_attempt or 0),
                    "active_input_index": int(rt.active_input_index or 0),
                })
        payload = {"created_epoch": time.time(), "version": VERSION, "channels": rows}
        NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE.with_name(
            f".{NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, NODE_CHANNEL_SUPERVISOR_RUNTIME_FILE)
        finally:
            temporary.unlink(missing_ok=True)
        return len(rows)

    def channel_supervisor_runtime_loop(self) -> None:
        while True:
            try:
                self.persist_channel_supervisor_runtime()
            except Exception as exc:
                self.log("Channel supervisor runtime snapshot failed", scope="channel", level="warning", details=str(exc))
            time.sleep(5.0)

    def watch_adopted_progress(self, key: str, process: AdoptedProcess) -> None:
        """Refresh health/bitrate for an FFmpeg whose original parent reloaded."""
        previous_counter = self.process_write_counter(process.pid)
        previous_time = time.monotonic()
        while process.poll() is None:
            time.sleep(1.0)
            now = time.monotonic()
            counter = self.process_write_counter(process.pid)
            bitrate = None
            if counter is not None and previous_counter is not None and counter >= previous_counter:
                elapsed = max(0.001, now - previous_time)
                bitrate = int(round((counter - previous_counter) * 8 / elapsed / 1000))
            previous_counter = counter
            previous_time = now
            with self.lock:
                rt = self.channels.get(key)
                if not rt or rt.process is not process:
                    return
                segment_time = int(rt.config.hls_segment_time or 1)
                output_type = str(rt.config.output_type or "hls").strip().lower()
            ready, fresh_hls = self._hls_output_state(key, segment_time)
            active = bool((counter is not None and bitrate is not None and bitrate > 0) or (output_type == "hls" and ready and fresh_hls))
            if active:
                with self.lock:
                    rt = self.channels.get(key)
                    if not rt or rt.process is not process:
                        return
                    rt.updated_at = now
                    rt.status = "running"
                    rt.last_error = None
                    if bitrate is not None and bitrate > 0:
                        rt.bitrate_kbps = bitrate

    def reload_http_workers_after_code_update(self) -> None:
        """Reload non-supervisor Gunicorn workers after app.py is replaced."""
        pids: set[int] = set()
        for process in [self.public_process, *list(self.panel_gateway_processes.values())]:
            try:
                if process and process.poll() is None:
                    pids.add(int(process.pid))
            except Exception:
                continue
        try:
            result = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "--value", "streamforge-node-public.service"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            pid = int((result.stdout or "0").strip() or 0)
            if pid > 1:
                pids.add(pid)
        except Exception:
            pass
        for pid in sorted(pids):
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                continue

    def load(self) -> None:
        self._pending_update_handoff = self.load_update_ffmpeg_handoff()
        raw = self._normalize_v200_state_payload(self._read_state(STATE_FILE))
        if self.independent_mode:
            shared_raw = self._normalize_v200_state_payload(self._read_state(SHARED_STATE_FILE))
            if not raw.get("channels"):
                raw = {
                    "channels": list(self._normalize_v200_state_payload(self._read_state(INDEPENDENT_STATE_FILE)).get("channels", []))
                    + list(shared_raw.get("channels", []))
                }
            raw = self._normalize_v200_state_payload(raw)
            raw = self._prepare_independent_active_payload(raw, shared_raw)
            self._write_state(STATE_FILE, raw)
            self._write_state(INDEPENDENT_STATE_FILE, self._select_owner(raw, "local"))
            self._write_state(SHARED_STATE_FILE, self._select_owner(raw, "main"))
            self._load_state_payload(raw, autostart=True)
        else:
            raw = self._prepare_shared_payload(raw)
            self._write_state(STATE_FILE, raw)
            self._write_state(INDEPENDENT_STATE_FILE, self._select_owner(raw, "local"))
            self._write_state(SHARED_STATE_FILE, self._select_owner(raw, "main"))
            self._load_state_payload(raw, autostart=True)
        self._pending_update_handoff = {}
        try:
            self.state_file_mtime = STATE_FILE.stat().st_mtime_ns
        except OSError:
            self.state_file_mtime = 0

    def reload_channel_catalog_if_changed(self, *, force: bool = False) -> None:
        """Refresh read-only listener metadata from the control process state.

        Separate Playlist/API and shared Panel/Playlist Gunicorn listeners do
        not own FFmpeg subprocess objects. They therefore reload channel rows
        from ``state.json`` whenever the control process changes it, while the
        native control listener keeps its in-memory Runtime/process objects.
        """
        if NODE_CHANNEL_OWNER:
            return
        try:
            current_mtime = STATE_FILE.stat().st_mtime_ns
        except OSError:
            current_mtime = 0
        if not force and current_mtime and current_mtime == self.state_file_mtime:
            return
        raw = self._normalize_v200_state_payload(self._read_state(STATE_FILE))
        refreshed: dict[str, Runtime] = {}
        for item in raw.get("channels", []):
            try:
                cfg = self.normalize_channel_config(ChannelConfig.model_validate(item["config"]))
                runtime = Runtime(cfg)
                runtime.desired_running = bool(item.get("desired_running"))
                refreshed[cfg.key] = runtime
            except Exception:
                continue
        with self.lock:
            self.channels = refreshed
            self.state_file_mtime = current_mtime

    def _quiesce_channels_for_mode_switch(self) -> None:
        with self.lock:
            runtimes = list(self.channels.values())
            processes = []
            for rt in runtimes:
                rt.intentional_stop = True
                if rt.restart_timer:
                    rt.restart_timer.cancel()
                    rt.restart_timer = None
                if rt.process and rt.process.poll() is None:
                    processes.append(rt.process)
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
        with self.lock:
            for rt in runtimes:
                rt.process = None
                rt.status = "stopped"
                rt.bitrate_kbps = 0
                rt.speed_x = 0.0
                rt.waiting_since = 0.0

    def sync_mode(self, payload: ModeSyncPayload) -> dict[str, Any]:
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            result = self._channel_supervisor_rpc(
                {"action": "sync_mode", "payload": payload.model_dump()}, timeout=30.0
            )
            self.load_access()
            self.load_users()
            self.reload_channel_catalog_if_changed(force=True)
            self._invalidate_supervisor_status_cache()
            return result
        target = bool(payload.independent_mode)
        self.local_channel_limit = max(0, min(100000, int(payload.local_channel_limit or 0)))
        self.total_max_connections = max(0, min(1000000, int(payload.total_max_connections or 0)))
        # STREAMFORGE_NODE_MODE_SYNC_NO_ACCESS_ALIAS_CLOBBER_V1149:
        # STREAMFORGE_NODE_ACCESS_SINGLE_OWNER_WRITES_V1150:
        # Mode synchronization owns only total_max_connections in access.json.
        # Never rewrite the full access policy from this process: control, panel,
        # public and supervisor processes can all hold an older in-memory alias
        # snapshot.  Merge the one owned field into the newest on-disk document.
        self.patch_access_file({"total_max_connections": int(self.total_max_connections)})
        seen_categories: set[str] = set()
        synced_categories: list[NodeCategoryConfig] = []
        for item in sorted(payload.categories, key=lambda row: (int(row.sort_order or 100000), row.name.lower())):
            name = str(item.name or "").strip()[:120]
            marker = name.lower()
            if not name or marker in seen_categories:
                continue
            seen_categories.add(marker)
            synced_categories.append(item.model_copy(update={"name": name, "owner": "main", "sort_order": max(0, int(item.sort_order or 100000))}))
        self.main_categories = synced_categories
        desired = {self.safe_key(str(key)) for key in payload.desired_channel_keys if str(key).strip()}
        previous = bool(self.independent_mode)
        removed: list[str] = []

        if target == previous:
            # In both modes, reconciliation removes only stale Main-owned rows.
            # Node-owned channels remain local and are governed by the limit.
            with self.lock:
                stale = [
                    key for key, rt in self.channels.items()
                    if rt.config.catalog_owner == "main" and key not in desired
                ]
            for key in stale:
                self.delete(key)
                removed.append(key)
            self.save()
            # STREAMFORGE_NODE_TEST_SYNC_MODE_USER_IMMUTABLE_V127:
            self.save_categories()
            return {
                "ok": True, "changed": False, "independent_mode": target,
                "removed_channels": removed, "restored_channels": 0,
                "local_channel_limit": int(self.local_channel_limit),
                "total_max_connections": int(self.total_max_connections),
            }

        current_payload = self._state_payload()
        active = self._prepare_independent_active_payload(current_payload, self._read_state(SHARED_STATE_FILE))
        self._write_state(INDEPENDENT_STATE_FILE, self._select_owner(active, "local"))
        self._write_state(SHARED_STATE_FILE, self._select_owner(active, "main"))
        current_archive = INDEPENDENT_STATE_FILE if previous else SHARED_STATE_FILE
        self._quiesce_channels_for_mode_switch()
        with self.lock:
            self.channels.clear()

        shared_payload = self._prepare_shared_payload(self._read_state(SHARED_STATE_FILE))
        shared_payload = {
            "channels": [
                item for item in shared_payload.get("channels", [])
                if str((item.get("config") or {}).get("key") or "") in desired
            ]
        }
        local_payload = self._prepare_independent_payload(
            self._read_state(INDEPENDENT_STATE_FILE), shared_payload
        )
        target_payload = self._prepare_independent_active_payload(
            {"channels": list(local_payload.get("channels", [])) + list(shared_payload.get("channels", []))},
            shared_payload,
        )
        expected_owner = None

        self.independent_mode = target
        restored = self._load_state_payload(target_payload, autostart=True, expected_owner=expected_owner)
        self.save()
        # STREAMFORGE_NODE_TEST_SYNC_MODE_USER_IMMUTABLE_V127:
        self.save_categories()
        self.save_panel_users()
        self.panel_last_check = 0.0
        self.log(
            "Node operating mode changed", scope="system", level="warning",
            details=f"{'independent' if previous else 'shared'} -> {'independent' if target else 'shared'}; restored={restored}",
        )
        return {
            "ok": True, "changed": True, "independent_mode": target,
            "removed_channels": removed, "restored_channels": restored,
            "archive": str(current_archive), "local_channel_limit": int(self.local_channel_limit),
            "total_max_connections": int(self.total_max_connections),
        }

    @staticmethod
    def _users_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
        return (
            int(stat_result.st_dev), int(stat_result.st_ino),
            int(stat_result.st_size), int(stat_result.st_mtime_ns),
        )

    def load_users(self) -> None:
        # STREAMFORGE_NODE_PLAYLIST_USER_SIGNATURE_RELOAD_V123:
        # Read and fstat the same opened inode. This avoids binding stale content
        # to the signature of a newer atomic replacement during concurrent CRUD.
        try:
            with USERS_FILE.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
                loaded_signature = self._users_signature(os.fstat(handle.fileno()))
            loaded_users = {item["token"]: NodeUserConfig.model_validate(item) for item in raw.get("users", [])}
            loaded_playlists = {
                item["token"]: NodePlaylistConfig.model_validate(item)
                for item in raw.get("playlists", [])
                if str((item or {}).get("token") or "").strip()
            }
            # Since v1.11.22 playlist accounts are authoritative on the Node.
            # Drop legacy Main-synchronized accounts while keeping every
            # Node-local account, including accounts copied from Main playlists.
            loaded_local_users = {
                token: item for token, item in loaded_users.items()
                if str(item.source or "").strip().lower() == "node_local"
            }
            with self.lock:
                self.users = loaded_local_users
                self.playlists = loaded_playlists
                self.users_file_signature = loaded_signature
                self.users_file_mtime = float(loaded_signature[3]) / 1_000_000_000.0
        except (OSError, ValueError, TypeError, KeyError):
            with self.lock:
                self.users = {}
                self.playlists = {}
                self.users_file_signature = None
                self.users_file_mtime = 0.0
        try:
            panel = json.loads(PANEL_FILE.read_text())
            self.panel_url = str(panel.get("panel_url") or self.panel_url).strip().rstrip("/")
            self.panel_node_slug = str(panel.get("node_slug") or self.panel_node_slug).strip()
            self.independent_mode = bool(panel.get("independent_mode", self.independent_mode))
            self.local_channel_limit = max(0, int(panel.get("local_channel_limit", self.local_channel_limit) or 0))
        except (OSError, ValueError, TypeError):
            pass
        try:
            category_raw = json.loads(CATEGORY_FILE.read_text())
            self.main_categories = [
                NodeCategoryConfig.model_validate(item)
                for item in category_raw.get("categories", [])
                if str((item or {}).get("name") or "").strip()
            ]
            self.category_order_overrides = [
                str(item or "").strip().lower()
                for item in category_raw.get("order_override", [])
                if str(item or "").strip()
            ]
        except (OSError, ValueError, TypeError, KeyError):
            self.main_categories = []
            self.category_order_overrides = []
        try:
            raw_panel_users = json.loads(PANEL_USERS_FILE.read_text())
            cached_panel_users = {
                item["username"]: PanelAccessUser.model_validate(item)
                for item in raw_panel_users.get("users", [])
            }
            cached_hashes_present = any(bool(item.password_hash) for item in cached_panel_users.values())
            self.panel_users = {
                username: item.model_copy(update={"password_hash": "", "auth_version": ""})
                for username, item in cached_panel_users.items()
            }
            self.node_name = str(raw_panel_users.get("node_name") or self.node_name)
            self.node_logo_url = str(raw_panel_users.get("node_logo_url") or "")
            self.independent_mode = bool(raw_panel_users.get("independent_mode", self.independent_mode))
            if cached_hashes_present:
                self.save_panel_users()
                self.log("Removed legacy cached panel-user password hashes", scope="auth", level="warning")
        except (OSError, ValueError, TypeError, KeyError):
            self.panel_users = {}
        # STREAMFORGE_NODE_PLAYLIST_USER_NO_READ_SIDE_WRITE_V125:
        # Loading a shared users.json snapshot must never write it back. A public
        # or stale control worker could otherwise erase a Node-local account that
        # another worker created milliseconds earlier. Xtream URLs are rebased in
        # memory and rendered through current_xtream_output_url(), so persistence
        # is unnecessary here.
        self.refresh_xtream_output_urls()

    @staticmethod
    def normalize_allowed_host(value: str) -> str:
        raw = str(value or "").strip().lower().rstrip(".")
        if not raw:
            return ""
        parsed = urllib.parse.urlsplit(raw if "://" in raw else f"//{raw}")
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host or any(ch in host for ch in "/?#\\"):
            raise ValueError("A valid hostname or IP address is required")
        return host

    @staticmethod
    def normalize_access_slug(value: str) -> str:
        raw = str(value or "").strip().strip("/").lower()
        if not raw:
            return ""
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")[:120]
        if not cleaned:
            raise ValueError("Access slug is invalid")
        return cleaned

    @classmethod
    def normalize_access_urls(
        cls, values: Any, label: str, legacy_slug: str = "", fallback_port: int = 0
    ) -> tuple[list[str], str]:
        """Normalize aliases while preserving every URL's own optional slug."""
        raw_values = values if isinstance(values, list) else str(values or "").replace("\r", "").split("\n")
        parsed_rows: list[tuple[urllib.parse.SplitResult, str]] = []
        for raw in raw_values:
            item = str(raw or "").strip().rstrip("/")
            if not item:
                continue
            parsed = urllib.parse.urlsplit(item)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"{label} must contain one http:// or https:// URL per line")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"{label} cannot contain credentials, query or fragment")
            raw_path = parsed.path.strip("/")
            if "/" in raw_path:
                raise ValueError(f"Each {label} URL may use only one access-path segment")
            path_slug = cls.normalize_access_slug(raw_path) if raw_path else ""
            parsed_rows.append((parsed, path_slug))

        legacy = cls.normalize_access_slug(legacy_slug)
        apply_legacy = bool(legacy and parsed_rows and not any(path_slug for _parsed, path_slug in parsed_rows))
        result: list[str] = []
        preserved_port = max(0, min(65535, int(fallback_port or 0)))
        for parsed, explicit_slug in parsed_rows:
            path_slug = legacy if apply_legacy else explicit_slug
            path = f"/{path_slug}" if path_slug else ""
            # STREAMFORGE_NODE_PUBLIC_URL_SCHEME_PORT_NORMALIZATION_V36:
            # The public URL's scheme owns its default port.  The Agent's
            # internal control/stream listener port is separate and must not be
            # appended to a URL that omitted a public port.  Repair the v3.5
            # https://host:80 / http://host:443 artifacts at load/sync time.
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            explicit_port = parsed.port
            if (parsed.scheme == "https" and explicit_port == 80) or (parsed.scheme == "http" and explicit_port == 443):
                explicit_port = None
            default_port = 443 if parsed.scheme == "https" else 80
            netloc = host if explicit_port in {None, default_port} else f"{host}:{explicit_port}"
            normalized = urllib.parse.urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")
            if normalized not in result:
                result.append(normalized)
        primary_slug = cls.normalize_access_slug(urllib.parse.urlsplit(result[0]).path.strip("/")) if result else ""
        return result, primary_slug

    @staticmethod
    def first_url_host(values: list[str]) -> str:
        return (urllib.parse.urlsplit(values[0]).hostname or "").lower().rstrip(".") if values else ""


    def load_geo_settings(self) -> None:
        try:
            raw = json.loads(GEOIP_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        provider = str(raw.get("provider") or self.geo_provider or "auto").strip().lower()
        self.geo_provider = provider if provider in {"auto", "maxmind", "ipinfo"} else "auto"
        self.geo_auto_update = bool(raw.get("auto_update", self.geo_auto_update))
        self.maxmind_account_id = str(raw.get("maxmind_account_id") or self.maxmind_account_id or "").strip()
        self.maxmind_license_key = str(raw.get("maxmind_license_key") or self.maxmind_license_key or "").strip()
        self.ipinfo_token = str(raw.get("ipinfo_token") or self.ipinfo_token or "").strip()

    def save_geo_settings(self, payload: GeoSettingsPayload) -> dict[str, Any]:
        provider = str(payload.provider or "auto").strip().lower()
        if provider not in {"auto", "maxmind", "ipinfo"}:
            raise ValueError("GeoIP provider must be auto, maxmind or ipinfo")
        self.load_geo_settings()
        self.geo_provider = provider
        self.geo_auto_update = bool(payload.auto_update)
        self.maxmind_account_id = str(payload.maxmind_account_id or "").strip()
        if payload.remove_maxmind_key:
            self.maxmind_license_key = ""
        elif str(payload.maxmind_license_key or "").strip():
            self.maxmind_license_key = str(payload.maxmind_license_key).strip()
        if payload.remove_ipinfo_token:
            self.ipinfo_token = ""
        elif str(payload.ipinfo_token or "").strip():
            self.ipinfo_token = str(payload.ipinfo_token).strip()
        GEOIP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = GEOIP_SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "provider": self.geo_provider,
            "auto_update": self.geo_auto_update,
            "maxmind_account_id": self.maxmind_account_id,
            "maxmind_license_key": self.maxmind_license_key,
            "ipinfo_token": self.ipinfo_token,
        }, separators=(",", ":")), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(GEOIP_SETTINGS_FILE)
        self._geo_cache.clear()
        self._asn_status_cache = None
        return self.geo_settings_status()

    def geo_settings_status(self) -> dict[str, Any]:
        self.load_geo_settings()
        def masked(value: str) -> str:
            normalized = str(value or "").strip()
            if not normalized:
                return "Not saved"
            suffix = normalized[-4:] if len(normalized) >= 4 else normalized
            return f"Saved â€” â€¢â€¢â€¢â€¢{suffix}"
        return {
            "provider": self.geo_provider,
            "auto_update": bool(self.geo_auto_update),
            "maxmind_account_id": self.maxmind_account_id,
            "maxmind_configured": bool(self.maxmind_account_id and self.maxmind_license_key),
            "ipinfo_configured": bool(self.ipinfo_token),
            "maxmind_saved_display": masked(self.maxmind_license_key),
            "ipinfo_saved_display": masked(self.ipinfo_token),
        }

    def run_geo_update(self) -> dict[str, Any]:
        self.load_geo_settings()
        if self.geo_provider == "ipinfo":
            return {
                "ok": True,
                "output": "IPinfo is a live lookup API. No database update is required; use Test an IP to verify the saved token.",
                "provider": "ipinfo",
                "database_update_required": False,
            }
        script = Path("/opt/streamforge/scripts/update_geoip_databases.sh")
        if not script.is_file():
            raise RuntimeError("GeoIP update script is missing. Run the manual/SSH Node updater once to install MaxMind update support.")
        try:
            completed = subprocess.run([str(script), "--force"], text=True, capture_output=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GeoIP update timed out") from exc
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())[-8000:]
        self._asn_status_cache = None
        self._geo_cache.clear()
        if completed.returncode != 0:
            raise RuntimeError(output or "GeoIP update failed")
        return {"ok": True, "output": output or "GeoIP databases updated"}

    @staticmethod
    def _webplayer_color(value: Any, default: str) -> str:
        cleaned = str(value or "").strip()
        return cleaned.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", cleaned) else default

    @staticmethod
    def _webplayer_alpha(value: Any, default: int = 100) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = int(default)
        return max(0, min(100, parsed))

    @staticmethod
    def _normalize_webplayer_brand_domain(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urllib.parse.urlsplit(raw if "://" in raw else "//" + raw)
            host = str(parsed.hostname or "").strip().lower().rstrip(".")
        except Exception:
            host = ""
        return host if host and len(host) <= 253 else ""

    @classmethod
    def _normalize_webplayer_brands(cls, raw_items: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        seen_hosts: set[str] = set()
        for raw in raw_items[:16]:
            if not isinstance(raw, dict):
                continue
            brand_id = re.sub(r"[^a-z0-9_-]+", "", str(raw.get("id") or "").strip().lower())[:40]
            name = str(raw.get("name") or "").strip()[:120]
            domains_raw = raw.get("domains") or []
            if isinstance(domains_raw, str):
                domains_raw = re.split(r"[\s,;]+", domains_raw)
            domains: list[str] = []
            for value in list(domains_raw or []):
                host = cls._normalize_webplayer_brand_domain(value)
                if host and host not in domains and host not in seen_hosts:
                    domains.append(host)
            if not brand_id or not name or not domains:
                continue
            def clean_url(key: str) -> str:
                value = str(raw.get(key) or "").strip()[:1000]
                if not value:
                    return ""
                try:
                    parsed = urllib.parse.urlsplit(value)
                except Exception:
                    return ""
                return value if parsed.scheme in {"http", "https"} and parsed.hostname else ""
            def clean_asset(key: str) -> str:
                value = Path(str(raw.get(key) or "")).name
                return value if value and value == str(raw.get(key) or "") else ""
            # STREAMFORGE_NODE_WEBPLAYER_BRAND_FULL_SETTINGS_PARITY_V1159:
            # Keep the host profile self-contained so every Public worker can
            # apply login/viewer/theme behavior from the synchronized access.json.
            item = {
                "id": brand_id,
                "name": name,
                "domains": domains,
                "access_urls": [str(v).strip() for v in list(raw.get("access_urls") or []) if str(v).strip()][:32],
                "logo_url": clean_url("logo_url"),
                "favicon_url": clean_url("favicon_url"),
                "logo_asset": clean_asset("logo_asset"),
                "favicon_asset": clean_asset("favicon_asset"),
                "show_user_info": bool(raw.get("show_user_info", True)),
                "show_connection_info": bool(raw.get("show_connection_info", True)),
                "login_mode": _normalized_webplayer_login_mode(raw.get("login_mode")),
                "auto_user_id": max(0, int(raw.get("auto_user_id") or 0)),
                "auto_user_token": str(raw.get("auto_user_token") or "").strip()[:256],
                "download_name": Path(str(raw.get("download_name") or "")).name,
                "download_asset": clean_asset("download_asset"),
                "android_version_name": str(raw.get("android_version_name") or "").strip()[:64],
                "android_description": str(raw.get("android_description") or "").strip()[:2000],
                "page_color": cls._webplayer_color(raw.get("page_color"), "#04080d"),
                "page_alpha": cls._webplayer_alpha(raw.get("page_alpha"), 100),
                "panel_color": cls._webplayer_color(raw.get("panel_color"), "#071019"),
                "panel_alpha": cls._webplayer_alpha(raw.get("panel_alpha"), 100),
                "accent_color": cls._webplayer_color(raw.get("accent_color"), "#ff2020"),
                "accent_alpha": cls._webplayer_alpha(raw.get("accent_alpha"), 100),
                "text_color": cls._webplayer_color(raw.get("text_color"), "#f6fbff"),
                "text_alpha": cls._webplayer_alpha(raw.get("text_alpha"), 100),
            }
            seen_hosts.update(domains)
            items.append(item)
        return items

    @classmethod
    def _webplayer_rgba(cls, color: str, alpha: int) -> str:
        cleaned = cls._webplayer_color(color, "#000000")
        r, g, b = int(cleaned[1:3], 16), int(cleaned[3:5], 16), int(cleaned[5:7], 16)
        return f"rgba({r},{g},{b},{cls._webplayer_alpha(alpha)/100.0:.3f})"

    @staticmethod
    def _access_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
        return (
            int(stat_result.st_dev), int(stat_result.st_ino),
            int(stat_result.st_size), int(stat_result.st_mtime_ns),
        )

    def load_access(self) -> None:
        self.panel_dns_only = True
        self.stream_dns_only = True
        loaded_signature: tuple[int, int, int, int] | None = None
        try:
            # STREAMFORGE_NODE_ACCESS_SIGNATURE_RELOAD_V124:
            # Read and fstat the same inode so a concurrent atomic replacement
            # cannot pair stale JSON with the signature of a newer file.
            with ACCESS_FILE.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
                loaded_signature = self._access_signature(os.fstat(handle.fileno()))
        except (OSError, ValueError, TypeError):
            raw = {}
        try:
            legacy_access_slug = self.normalize_access_slug(raw.get("access_slug") or "")
            saved_node_name = str(raw.get("node_name") or "").strip()[:120]
            if saved_node_name:
                self.node_name = saved_node_name
            legacy_stream_slug = self.normalize_access_slug(raw.get("stream_slug") or raw.get("playlist_access_slug") or "")
            panel_values = raw.get("panel_urls") or []
            stream_values = raw.get("stream_urls") or []
            if not panel_values:
                legacy_host = str(raw.get("panel_host") or raw.get("allowed_host") or ALLOWED_HOST or "").strip()
                if legacy_host:
                    panel_values = [f"http://{legacy_host}:{CONTROL_PORT}"]
            if not stream_values:
                legacy_host = str(raw.get("stream_host") or STREAM_ALLOWED_HOST or "").strip()
                if legacy_host:
                    legacy_port = max(1, min(65535, int(raw.get("stream_port") or PUBLIC_PORT_DEFAULT)))
                    stream_values = [f"http://{legacy_host}:{legacy_port}"]
            self.panel_urls, self.access_slug = self.normalize_access_urls(
                panel_values, "Panel/API access URLs", legacy_access_slug, CONTROL_PORT
            )
            self.stream_urls, self.stream_slug = self.normalize_access_urls(
                stream_values, "Playlist/App access URLs", legacy_stream_slug,
                int(raw.get("stream_port") or self.stream_port or PUBLIC_PORT_DEFAULT),
            )
            self.panel_host = self.first_url_host(self.panel_urls)
            self.stream_host = self.first_url_host(self.stream_urls)
            first_stream = urllib.parse.urlsplit(self.stream_urls[0]) if self.stream_urls else None
            self.stream_port = max(1, min(65535, int(raw.get("stream_port") or (first_stream.port if first_stream else 0) or PUBLIC_PORT_DEFAULT)))
            self.total_max_connections = max(0, min(1000000, int(raw.get("total_max_connections") or 0)))
            self.panel_ip_whitelist = str(raw.get("panel_ip_whitelist") or "")
            self.panel_ip_blacklist = str(raw.get("panel_ip_blacklist") or "")
            self.panel_asn_whitelist = str(raw.get("panel_asn_whitelist") or "")
            self.panel_asn_blacklist = str(raw.get("panel_asn_blacklist") or "")
            self.ip_whitelist = str(raw.get("ip_whitelist") or "")
            self.ip_blacklist = str(raw.get("ip_blacklist") or "")
            self.asn_whitelist = str(raw.get("asn_whitelist") or "")
            self.asn_blacklist = str(raw.get("asn_blacklist") or "")
            self.webplayer_show_user_info = bool(raw.get("webplayer_show_user_info", True))
            self.webplayer_show_connection_info = bool(raw.get("webplayer_show_connection_info", True))
            self.viewer_ttl_seconds = max(5, min(3600, int(raw.get("viewer_ttl_seconds") or VIEWER_TTL)))
            self.client_session_reset_offline_minutes = max(1, min(10080, int(raw.get("client_session_reset_offline_minutes") or 60)))
            # STREAMFORGE_NODE_VIEWER_TTL_LOAD_GLOBAL_V90R3:
            # VIEWER_TTL is consumed by auth, Redis session pruning and live
            # session reads. load_access() previously updated only the manager
            # attribute, leaving freshly started public workers on the env
            # default (usually 5s) even when this Node was configured for 10s+.
            globals()["VIEWER_TTL"] = self.viewer_ttl_seconds
            self.hide_panel_hover_urls = bool(raw.get("hide_panel_hover_urls", True))
            self.webplayer_login_mode = _normalized_webplayer_login_mode(raw.get("webplayer_login_mode"))
            try:
                self.webplayer_auto_user_id = int(raw.get("webplayer_auto_user_id") or 0)
            except (TypeError, ValueError):
                self.webplayer_auto_user_id = 0
            self.webplayer_auto_user_token = str(raw.get("webplayer_auto_user_token") or "").strip()
            self.webplayer_page_color = self._webplayer_color(raw.get("webplayer_page_color"), "#04080d")
            self.webplayer_page_alpha = self._webplayer_alpha(raw.get("webplayer_page_alpha"), 100)
            self.webplayer_panel_color = self._webplayer_color(raw.get("webplayer_panel_color"), "#071019")
            self.webplayer_panel_alpha = self._webplayer_alpha(raw.get("webplayer_panel_alpha"), 100)
            self.webplayer_accent_color = self._webplayer_color(raw.get("webplayer_accent_color"), "#ff2020")
            self.webplayer_accent_alpha = self._webplayer_alpha(raw.get("webplayer_accent_alpha"), 100)
            self.webplayer_text_color = self._webplayer_color(raw.get("webplayer_text_color"), "#f6fbff")
            self.webplayer_text_alpha = self._webplayer_alpha(raw.get("webplayer_text_alpha"), 100)
            self.webplayer_brands = self._normalize_webplayer_brands(raw.get("webplayer_brands"))
            self.webplayer_download_name = Path(str(raw.get("webplayer_download_name") or "")).name
            self.android_version_name = str(raw.get("android_version_name") or "").strip()[:64]
            self.android_description = str(raw.get("android_description") or "").strip()[:2000]
        except (ValueError, TypeError):
            self.panel_urls = []
            self.stream_urls = []
            self.access_slug = ""
            self.stream_slug = ""
            self.panel_host = ""
            self.stream_host = ""
        self.access_file_signature = loaded_signature
        self.access_file_mtime_ns = int(loaded_signature[3]) if loaded_signature else -1

    def refresh_runtime_access_if_changed(self) -> None:
        """Reload shared access/session settings inside long-lived public workers."""
        # STREAMFORGE_NODE_WEBPLAYER_BRAND_CROSS_PROCESS_RELOAD_V124:
        # Compare complete file identity rather than only mtime. Brand add/edit/
        # delete must become visible on every public worker without Main re-sync.
        try:
            current_signature = self._access_signature(ACCESS_FILE.stat())
        except OSError:
            current_signature = None
        if current_signature == self.access_file_signature:
            return
        with self.lock:
            try:
                current_signature = self._access_signature(ACCESS_FILE.stat())
            except OSError:
                current_signature = None
            if current_signature == self.access_file_signature:
                return
            self.load_access()

    def save_access(self) -> None:
        # STREAMFORGE_NODE_ACCESS_AUTHORITATIVE_FULL_WRITE_V1150:
        # Full access-policy writes are reserved for sync_access() (and its
        # rollback). Unrelated state changes use patch_access_file() so stale
        # worker memory cannot remove newly synchronized URL aliases.
        ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCESS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        # STREAMFORGE_NODE_ACCESS_ATOMIC_UNIQUE_TEMP_V1033: preserve unique temp files for atomic replaces.
        # STREAMFORGE_NODE_ACCESS_CROSS_PROCESS_WRITE_LOCK_V1149:
        # access.json is shared by the native control worker, managed panel/public
        # workers and the dedicated channel supervisor. Atomic os.replace protects
        # readers from partial JSON, but it does not prevent a stale process from
        # overwriting a newer process. Serialize writers on a stable sibling lock.
        with ACCESS_LOCK_FILE.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            temp = ACCESS_FILE.with_name(f".{ACCESS_FILE.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
            try:
                temp.write_text(
                    json.dumps(
                        {
                            "node_name": self.node_name,
                            "dns_only": True,
                            "allowed_host": self.panel_host,
                            "panel_dns_only": True,
                            "panel_host": self.panel_host,
                            "panel_urls": list(self.panel_urls),
                            "stream_dns_only": True,
                            "stream_host": self.stream_host,
                            "stream_urls": list(self.stream_urls),
                            "access_slug": self.access_slug,
                            "stream_slug": self.stream_slug,
                            "stream_port": int(self.stream_port),
                            "control_port": int(CONTROL_PORT),
                            "total_max_connections": int(self.total_max_connections),
                            "panel_ip_whitelist": self.panel_ip_whitelist,
                            "panel_ip_blacklist": self.panel_ip_blacklist,
                            "panel_asn_whitelist": self.panel_asn_whitelist,
                            "panel_asn_blacklist": self.panel_asn_blacklist,
                            "ip_whitelist": self.ip_whitelist,
                            "ip_blacklist": self.ip_blacklist,
                            "asn_whitelist": self.asn_whitelist,
                            "asn_blacklist": self.asn_blacklist,
                            "webplayer_show_user_info": bool(self.webplayer_show_user_info),
                            "webplayer_show_connection_info": bool(self.webplayer_show_connection_info),
                            "viewer_ttl_seconds": int(self.viewer_ttl_seconds),
                            "client_session_reset_offline_minutes": int(self.client_session_reset_offline_minutes),
                            "hide_panel_hover_urls": bool(self.hide_panel_hover_urls),
                            "webplayer_login_mode": self.webplayer_login_mode,
                            "webplayer_auto_user_id": int(self.webplayer_auto_user_id),
                            "webplayer_auto_user_token": self.webplayer_auto_user_token,
                            "webplayer_page_color": self.webplayer_page_color,
                            "webplayer_page_alpha": int(self.webplayer_page_alpha),
                            "webplayer_panel_color": self.webplayer_panel_color,
                            "webplayer_panel_alpha": int(self.webplayer_panel_alpha),
                            "webplayer_accent_color": self.webplayer_accent_color,
                            "webplayer_accent_alpha": int(self.webplayer_accent_alpha),
                            "webplayer_text_color": self.webplayer_text_color,
                            "webplayer_text_alpha": int(self.webplayer_text_alpha),
                            "webplayer_brands": list(self.webplayer_brands),
                            "webplayer_download_name": self.webplayer_download_name,
                            "android_version_name": self.android_version_name,
                            "android_description": self.android_description,
                        },
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                temp.replace(ACCESS_FILE)
                try:
                    current_stat = ACCESS_FILE.stat()
                    self.access_file_signature = self._access_signature(current_stat)
                    self.access_file_mtime_ns = int(current_stat.st_mtime_ns)
                except OSError:
                    self.access_file_signature = None
                    self.access_file_mtime_ns = -1
            finally:
                temp.unlink(missing_ok=True)
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def patch_access_file(self, updates: dict[str, Any]) -> None:
        """Merge a small cross-process update without clobbering newer aliases."""
        ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCESS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ACCESS_LOCK_FILE.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    raw = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                raw.update(updates)
                temp = ACCESS_FILE.with_name(f".{ACCESS_FILE.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
                try:
                    temp.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
                    temp.replace(ACCESS_FILE)
                    try:
                        current_stat = ACCESS_FILE.stat()
                        self.access_file_signature = self._access_signature(current_stat)
                        self.access_file_mtime_ns = int(current_stat.st_mtime_ns)
                    except OSError:
                        self.access_file_signature = None
                        self.access_file_mtime_ns = -1
                finally:
                    temp.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def access_sync_payload(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "panel_urls": list(self.panel_urls),
            "stream_urls": list(self.stream_urls),
            "access_slug": self.access_slug,
            "stream_slug": self.stream_slug,
            "stream_port": int(self.stream_port),
            "control_port": int(CONTROL_PORT),
            "total_max_connections": int(self.total_max_connections),
            "panel_ip_whitelist": self.panel_ip_whitelist,
            "panel_ip_blacklist": self.panel_ip_blacklist,
            "panel_asn_whitelist": self.panel_asn_whitelist,
            "panel_asn_blacklist": self.panel_asn_blacklist,
            "ip_whitelist": self.ip_whitelist,
            "ip_blacklist": self.ip_blacklist,
            "asn_whitelist": self.asn_whitelist,
            "asn_blacklist": self.asn_blacklist,
        }

    def sync_access(self, payload: AccessSettingsPayload, *, apply_listeners: bool = True) -> dict[str, Any]:
        legacy_access_slug = self.normalize_access_slug(payload.access_slug or "")
        legacy_stream_slug = self.normalize_access_slug(payload.stream_slug or "")
        panel_values: Any = payload.panel_urls
        stream_values: Any = payload.stream_urls
        if not panel_values:
            panel_raw = str(payload.panel_host or payload.allowed_host or "").strip()
            panel_values = [f"http://{panel_raw}:{CONTROL_PORT}"] if panel_raw else []
        if not stream_values:
            stream_raw = str(payload.stream_host or payload.panel_host or payload.allowed_host or "").strip()
            port = max(1, min(65535, int(payload.stream_port or self.stream_port or PUBLIC_PORT_DEFAULT)))
            stream_values = [f"http://{stream_raw}:{port}"] if stream_raw else []
        panel_urls, access_slug = self.normalize_access_urls(
            panel_values, "Panel/API access URLs", legacy_access_slug, CONTROL_PORT
        )
        primary_panel = urllib.parse.urlsplit(panel_urls[0]) if panel_urls else None
        shared_listener_port = int(
            (primary_panel.port if primary_panel else 0)
            or (443 if primary_panel and primary_panel.scheme == "https" else CONTROL_PORT)
        )
        # A path-only Playlist/App URL shares the canonical Panel/API listener.
        # A separate listener remains available by entering an explicit port.
        stream_urls, stream_slug = self.normalize_access_urls(
            stream_values, "Playlist/App access URLs", legacy_stream_slug,
            int(payload.stream_port or shared_listener_port),
        )
        if not panel_urls:
            raise ValueError("At least one Panel/API access URL is required")
        if not stream_urls:
            raise ValueError("At least one Playlist/App access URL is required")
        first_stream = urllib.parse.urlsplit(stream_urls[0])
        stream_port = max(1, min(65535, int(
            first_stream.port
            or payload.stream_port
            or (443 if first_stream.scheme == "https" else shared_listener_port)
        )))
        total_max_connections = max(0, min(1000000, int(payload.total_max_connections if payload.total_max_connections is not None else self.total_max_connections or 0)))

        snapshot = {
            "node_name": self.node_name,
            "panel_dns_only": self.panel_dns_only,
            "stream_dns_only": self.stream_dns_only,
            "panel_urls": list(self.panel_urls),
            "stream_urls": list(self.stream_urls),
            "access_slug": self.access_slug,
            "stream_slug": self.stream_slug,
            "panel_host": self.panel_host,
            "stream_host": self.stream_host,
            "stream_port": int(self.stream_port),
            "total_max_connections": int(self.total_max_connections),
            "panel_ip_whitelist": self.panel_ip_whitelist,
            "panel_ip_blacklist": self.panel_ip_blacklist,
            "panel_asn_whitelist": self.panel_asn_whitelist,
            "panel_asn_blacklist": self.panel_asn_blacklist,
            "ip_whitelist": self.ip_whitelist,
            "ip_blacklist": self.ip_blacklist,
            "asn_whitelist": self.asn_whitelist,
            "asn_blacklist": self.asn_blacklist,
            "panel_url": self.panel_url,
            "webplayer_show_user_info": bool(self.webplayer_show_user_info),
            "webplayer_show_connection_info": bool(self.webplayer_show_connection_info),
            "viewer_ttl_seconds": int(self.viewer_ttl_seconds),
            "client_session_reset_offline_minutes": int(self.client_session_reset_offline_minutes),
            "hide_panel_hover_urls": bool(self.hide_panel_hover_urls),
            "webplayer_login_mode": self.webplayer_login_mode,
            "webplayer_auto_user_id": int(self.webplayer_auto_user_id),
            "webplayer_auto_user_token": self.webplayer_auto_user_token,
            "webplayer_page_color": self.webplayer_page_color,
            "webplayer_page_alpha": int(self.webplayer_page_alpha),
            "webplayer_panel_color": self.webplayer_panel_color,
            "webplayer_panel_alpha": int(self.webplayer_panel_alpha),
            "webplayer_accent_color": self.webplayer_accent_color,
            "webplayer_accent_alpha": int(self.webplayer_accent_alpha),
            "webplayer_text_color": self.webplayer_text_color,
            "webplayer_text_alpha": int(self.webplayer_text_alpha),
            "webplayer_brands": list(self.webplayer_brands),
            "android_version_name": self.android_version_name,
            "android_description": self.android_description,
        }
        with self.lock:
            synced_node_name = str(payload.node_name or "").strip()[:120]
            if synced_node_name:
                self.node_name = synced_node_name
            self.panel_dns_only = True
            self.stream_dns_only = True
            self.panel_urls = panel_urls
            self.stream_urls = stream_urls
            self.access_slug = access_slug
            self.stream_slug = stream_slug
            self.panel_host = self.first_url_host(panel_urls)
            self.stream_host = self.first_url_host(stream_urls)
            self.stream_port = stream_port
            self.total_max_connections = total_max_connections
            self.panel_ip_whitelist = str(payload.panel_ip_whitelist or "").strip()
            self.panel_ip_blacklist = str(payload.panel_ip_blacklist or "").strip()
            self.panel_asn_whitelist = str(payload.panel_asn_whitelist or "").strip()
            self.panel_asn_blacklist = str(payload.panel_asn_blacklist or "").strip()
            self.ip_whitelist = str(payload.ip_whitelist or "").strip()
            self.ip_blacklist = str(payload.ip_blacklist or "").strip()
            self.asn_whitelist = str(payload.asn_whitelist or "").strip()
            self.asn_blacklist = str(payload.asn_blacklist or "").strip()
            if payload.webplayer_show_user_info is not None:
                self.webplayer_show_user_info = bool(payload.webplayer_show_user_info)
            if payload.webplayer_show_connection_info is not None:
                self.webplayer_show_connection_info = bool(payload.webplayer_show_connection_info)
            if payload.viewer_ttl_seconds is not None:
                self.viewer_ttl_seconds = max(5, min(3600, int(payload.viewer_ttl_seconds or 5)))
                globals()["VIEWER_TTL"] = self.viewer_ttl_seconds
            if payload.client_session_reset_offline_minutes is not None:
                self.client_session_reset_offline_minutes = max(1, min(10080, int(payload.client_session_reset_offline_minutes or 60)))
            if payload.hide_panel_hover_urls is not None:
                self.hide_panel_hover_urls = bool(payload.hide_panel_hover_urls)
            if payload.webplayer_login_mode is not None:
                self.webplayer_login_mode = _normalized_webplayer_login_mode(payload.webplayer_login_mode)
            if payload.webplayer_auto_user_id is not None:
                try:
                    self.webplayer_auto_user_id = max(0, int(payload.webplayer_auto_user_id))
                except (TypeError, ValueError):
                    self.webplayer_auto_user_id = 0
            if payload.webplayer_auto_user_token is not None:
                self.webplayer_auto_user_token = str(payload.webplayer_auto_user_token or "").strip()
            if payload.webplayer_page_color is not None:
                self.webplayer_page_color = self._webplayer_color(payload.webplayer_page_color, self.webplayer_page_color)
            if payload.webplayer_page_alpha is not None:
                self.webplayer_page_alpha = self._webplayer_alpha(payload.webplayer_page_alpha, self.webplayer_page_alpha)
            if payload.webplayer_panel_color is not None:
                self.webplayer_panel_color = self._webplayer_color(payload.webplayer_panel_color, self.webplayer_panel_color)
            if payload.webplayer_panel_alpha is not None:
                self.webplayer_panel_alpha = self._webplayer_alpha(payload.webplayer_panel_alpha, self.webplayer_panel_alpha)
            if payload.webplayer_accent_color is not None:
                self.webplayer_accent_color = self._webplayer_color(payload.webplayer_accent_color, self.webplayer_accent_color)
            if payload.webplayer_accent_alpha is not None:
                self.webplayer_accent_alpha = self._webplayer_alpha(payload.webplayer_accent_alpha, self.webplayer_accent_alpha)
            if payload.webplayer_text_color is not None:
                self.webplayer_text_color = self._webplayer_color(payload.webplayer_text_color, self.webplayer_text_color)
            if payload.webplayer_text_alpha is not None:
                self.webplayer_text_alpha = self._webplayer_alpha(payload.webplayer_text_alpha, self.webplayer_text_alpha)
            if payload.webplayer_brands is not None:
                self.webplayer_brands = self._normalize_webplayer_brands(payload.webplayer_brands)
                # STREAMFORGE_NODE_WEBPLAYER_BRAND_ASSET_PRUNE_V1158:
                # Access sync is authoritative for the active brand set. Remove
                # files belonging to deleted profiles so an offline-then-recovered
                # Node does not accumulate orphaned per-brand assets forever.
                active_brand_ids = {str(item.get("id") or "") for item in self.webplayer_brands}
                try:
                    if WEBPLAYER_BRAND_ROOT.is_dir():
                        for target in WEBPLAYER_BRAND_ROOT.iterdir():
                            if not target.is_file():
                                continue
                            match = re.match(r"^([a-z0-9_-]{1,40})-(?:logo|favicon|download)(?:\..*)?$", target.name)
                            if match and match.group(1) not in active_brand_ids:
                                target.unlink(missing_ok=True)
                except OSError:
                    pass
            if payload.android_version_name is not None:
                self.android_version_name = str(payload.android_version_name or "").strip()[:64]
            if payload.android_description is not None:
                self.android_description = str(payload.android_description or "").strip()[:2000]
            if str(payload.main_panel_url or "").strip():
                self.panel_url = str(payload.main_panel_url).strip().rstrip("/")
            self._geo_cache.clear()
            self._asn_status_cache = None
            for reader_name in ("_asn_reader", "_country_reader"):
                reader = getattr(self, reader_name, None)
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:
                        pass
                setattr(self, reader_name, None)
            self.save_access()
            self.save_panel_users()
            # STREAMFORGE_NODE_ACCESS_SYNC_USER_IMMUTABLE_V125:
            # Access/WebPlayer synchronization owns access.json only. Never
            # rewrite users.json from this worker's process-local snapshot.
            # Reload the authoritative Node-local registry, then rebase only the
            # in-memory display URL against the newly synchronized stream base.
            self.reload_users_if_changed()
            self.refresh_xtream_output_urls()

        try:
            if NODE_MODE == "control" and apply_listeners:
                # STREAMFORGE_NODE_TLS_ASYNC_ACCESS_SYNC_V39: accept/save the
                # public HTTPS policy over authenticated native HTTP first,
                # then let the root helper provision TLS asynchronously. This
                # keeps Main->Node config/channel sync alive while ACME is pending.
                self.request_tls_reconcile()
                self.ensure_public_gateway()
                # Restart managed HTTP Panel/API listeners so host/path changes
                # are loaded by their child processes immediately. HTTPS ports
                # are intentionally excluded and belong to the TLS helper.
                self.ensure_panel_gateways(force_restart=True)
        except Exception as exc:
            with self.lock:
                for key, value in snapshot.items():
                    setattr(self, key, value)
                self.save_access()
                self.save_panel_users()
                # STREAMFORGE_NODE_ACCESS_ROLLBACK_USER_IMMUTABLE_V125:
                # Listener rollback must not touch the authoritative playlist
                # user registry either.
                self.reload_users_if_changed()
                self.refresh_xtream_output_urls()
            try:
                if NODE_MODE == "control":
                    self.ensure_public_gateway()
                    self.ensure_panel_gateways(force_restart=True)
            except Exception as rollback_exc:
                self.log(
                    f"Panel/API listener rollback also failed: {rollback_exc}",
                    scope="settings", level="error",
                )
            self.log(
                f"Rejected access settings because the requested listener could not be activated: {exc}",
                scope="settings", level="error",
            )
            raise ValueError(str(exc)) from exc

        self.log(
            "Node Panel/API and Playlist/App URL policy synchronized",
            scope="settings",
            details=f"panel_urls={len(self.panel_urls)}; stream_urls={len(self.stream_urls)}; panel_slug={self.access_slug or '-'}; stream_slug={self.stream_slug or '-'}; stream_port={self.stream_port}",
        )
        return {
            "ok": True,
            "node_name": self.node_name,
            "dns_only": True,
            "allowed_host": self.panel_host or None,
            "panel_dns_only": True,
            "panel_host": self.panel_host or None,
            "panel_urls": list(self.panel_urls),
            "stream_dns_only": True,
            "stream_host": self.stream_host or None,
            "stream_urls": list(self.stream_urls),
            "access_slug": self.access_slug,
            "stream_slug": self.stream_slug,
            "stream_port": int(self.stream_port),
            "control_port": int(CONTROL_PORT),
            "total_max_connections": int(self.total_max_connections),
            "panel_ip_whitelist": self.panel_ip_whitelist,
            "panel_ip_blacklist": self.panel_ip_blacklist,
            "panel_asn_whitelist": self.panel_asn_whitelist,
            "panel_asn_blacklist": self.panel_asn_blacklist,
            "ip_whitelist": self.ip_whitelist,
            "ip_blacklist": self.ip_blacklist,
            "asn_whitelist": self.asn_whitelist,
            "asn_blacklist": self.asn_blacklist,
            # STREAMFORGE_NODE_WEBPLAYER_THEME_SYNC_ECHO_V2229:
            "webplayer": {
                "show_user_info": bool(self.webplayer_show_user_info),
                "show_connection_info": bool(self.webplayer_show_connection_info),
                "login_mode": self.webplayer_login_mode,
                "auto_user_id": int(self.webplayer_auto_user_id),
                "auto_user_token": self.webplayer_auto_user_token,
                "page_color": self.webplayer_page_color,
                "page_alpha": int(self.webplayer_page_alpha),
                "panel_color": self.webplayer_panel_color,
                "panel_alpha": int(self.webplayer_panel_alpha),
                "accent_color": self.webplayer_accent_color,
                "accent_alpha": int(self.webplayer_accent_alpha),
                "text_color": self.webplayer_text_color,
                "text_alpha": int(self.webplayer_text_alpha),
                "viewer_ttl_seconds": int(self.viewer_ttl_seconds),
                "client_session_reset_offline_minutes": int(self.client_session_reset_offline_minutes),
                "hide_hover_urls": bool(self.hide_panel_hover_urls),
                "android_version_name": self.android_version_name,
                "android_description": self.android_description,
                "brands": list(self.webplayer_brands),
            },
            "panel_gateways": self.panel_gateway_status(),
            "managed_tls": self.tls_status(),
            "tls_reconcile_queued": bool(not EXTERNAL_PROXY_MODE),
            "applied_immediately": True,
        }

    # STREAMFORGE_NODE_SETTINGS_POST_RESPONSE_RECONCILE_V52:
    # Standalone Settings saves may be served through the same TLS/Nginx path
    # being reconciled. Apply public listeners only after the POST/redirect has
    # been returned so the browser never sees a transient 502 from its own save.
    def apply_access_listeners(self) -> None:
        if NODE_MODE != "control":
            return
        self.request_tls_reconcile()
        self.ensure_public_gateway()
        self.ensure_panel_gateways(force_restart=True)

    # STREAMFORGE_NODE_LOCAL_SAVE_ACCESS_APPLY_V56: compatibility marker; v5.9 supersedes the in-worker apply with a cross-process native-control watcher.
    # STREAMFORGE_NODE_LOCAL_SAVE_CONTROL_SYNC_V58: compatibility marker retained.
    # STREAMFORGE_NODE_CROSS_PROCESS_ACCESS_RECONCILE_V59: Settings can be
    # served by control, panel, or public gateway workers.  They all share the
    # persisted access.json, but only the native control worker owns child HTTP
    # listeners.  After the browser response is complete, write a tiny request
    # file.  The control worker watcher reloads access.json and applies the full
    # listener/TLS pipeline.  This avoids recursive loopback HTTP and works even
    # when the request-serving worker is about to be restarted.
    def queue_control_access_reconcile_after_response(self) -> None:
        try:
            NODE_ACCESS_RECONCILE_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = NODE_ACCESS_RECONCILE_REQUEST_FILE.with_name(
                NODE_ACCESS_RECONCILE_REQUEST_FILE.name + f".{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps({
                    "requested_at": time.time(),
                    "pid": os.getpid(),
                    "version": VERSION,
                }, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            temporary.replace(NODE_ACCESS_RECONCILE_REQUEST_FILE)
            self.log(
                "Node local Settings queued native access reconcile",
                scope="settings",
                details=f"panel_urls={len(self.panel_urls)}; stream_urls={len(self.stream_urls)}",
            )
        except OSError as exc:
            self.log(
                f"Node local Settings could not queue native access reconcile: {exc}",
                scope="settings", level="error",
            )

    def _write_access_reconcile_status(self, *, ok: bool, detail: str = "") -> None:
        try:
            NODE_ACCESS_RECONCILE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = NODE_ACCESS_RECONCILE_STATUS_FILE.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({
                    "ok": bool(ok),
                    "updated_at": time.time(),
                    "detail": str(detail or "")[-2000:],
                    "panel_urls": list(self.panel_urls),
                    "stream_urls": list(self.stream_urls),
                    "panel_gateways": self.panel_gateway_status(),
                    "public_gateway": self.public_gateway_status(),
                }, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(NODE_ACCESS_RECONCILE_STATUS_FILE)
        except OSError:
            pass

    def watch_access_reconcile_requests(self) -> None:
        if NODE_MODE != "control":
            return
        try:
            self.access_reconcile_request_mtime_ns = NODE_ACCESS_RECONCILE_REQUEST_FILE.stat().st_mtime_ns
        except OSError:
            self.access_reconcile_request_mtime_ns = -1
        while True:
            try:
                current_mtime = NODE_ACCESS_RECONCILE_REQUEST_FILE.stat().st_mtime_ns
            except OSError:
                current_mtime = -1
            if current_mtime >= 0 and current_mtime != self.access_reconcile_request_mtime_ns:
                self.access_reconcile_request_mtime_ns = current_mtime
                # Give the request-serving worker a short grace period to finish
                # its redirect before a custom Panel/API child is restarted.
                time.sleep(0.20)
                try:
                    with self.lock:
                        self.load_access()
                        # STREAMFORGE_NODE_ACCESS_WATCHER_USER_IMMUTABLE_V125:
                        # The native access watcher is a listener reconciler, not
                        # a playlist-user writer. Reload users from disk and keep
                        # any URL rebase in memory only.
                        self.reload_users_if_changed()
                        self.refresh_xtream_output_urls()
                    self.request_tls_reconcile()
                    self.ensure_public_gateway()
                    self.ensure_panel_gateways(force_restart=True)
                    detail = (
                        f"panel_urls={len(self.panel_urls)}; stream_urls={len(self.stream_urls)}; "
                        f"stream_ports={sorted(self.stream_gateway_ports())}"
                    )
                    self._write_access_reconcile_status(ok=True, detail=detail)
                    self.log(
                        "Node local Settings applied by native control watcher",
                        scope="settings", details=detail,
                    )
                except Exception as exc:
                    self._write_access_reconcile_status(ok=False, detail=str(exc))
                    self.log(
                        f"Node local Settings native access reconcile failed: {exc}",
                        scope="settings", level="error",
                    )
            time.sleep(0.25)

    def request_tls_reconcile(self, host: str = "", *, force_retry: bool = False) -> None:
        """Ask the root Node TLS helper to reconcile certificates/listeners."""
        if EXTERNAL_PROXY_MODE:
            return
        try:
            # STREAMFORGE_NODE_TLS_FORCE_RETRY_V1028: explicit certificate Retry
            # actions may clear the helper's prior ACME backoff for one validated
            # hostname. Ordinary access-sync/path triggers remain non-forcing.
            clean_host = str(host or "").strip().lower().rstrip(".")
            payload = {"requested_at": int(time.time()), "force_retry": bool(force_retry)}
            if clean_host:
                payload["host"] = clean_host
            NODE_TLS_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
            NODE_TLS_REQUEST_FILE.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not queue managed TLS reconciliation: {exc}", scope="settings", level="warning")

    def tls_status(self) -> dict[str, Any]:
        try:
            data = json.loads(NODE_TLS_STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        return data if isinstance(data, dict) else {}

    def back_sync_access_to_main(self) -> dict[str, Any]:
        if not self.panel_url or not self.panel_node_slug or not TOKEN:
            raise RuntimeError("Main Panel connection is not configured")
        url = f"{self.panel_url.rstrip('/')}/api/v1/node-access-back-sync/{urllib.parse.quote(self.panel_node_slug)}"
        body = json.dumps(self.access_sync_payload(), separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "X-Node-Token": TOKEN, "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": f"StreamForge-Node/{VERSION}",
        })
        try:
            with urllib.request.urlopen(request, timeout=8.0) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
                if response.status != 200 or not data.get("ok"):
                    raise RuntimeError(str(data.get("detail") or "Main Panel rejected Node access settings"))
                return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1200:]
            raise RuntimeError(detail or str(exc.reason)) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError(f"Main Panel back-sync failed: {exc}") from exc

    @staticmethod
    def _url_listener_port(value: str) -> int:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
        if parsed.port is not None:
            return max(1, min(65535, int(parsed.port)))
        return 443 if parsed.scheme == "https" else 80

    def panel_gateway_targets(self) -> dict[int, list[str]]:
        # A co-located Main Nginx proxy owns the public listeners and forwards
        # every configured Node alias to this control listener.
        if EXTERNAL_PROXY_MODE:
            return {}
        targets: dict[int, list[str]] = {}
        # Every configured Panel/API and Playlist/App alias owns its explicit
        # public listener.  Previously only Panel/API aliases were considered,
        # so a secondary stream URL such as http://node-ip:802 was saved and
        # advertised but no process ever bound port 802.
        for value in [*self.panel_urls, *self.stream_urls]:
            parsed = urllib.parse.urlsplit(str(value or "").strip())
            if not parsed.hostname:
                continue
            # STREAMFORGE_NODE_TLS_PORT_SEPARATION_V39: HTTPS listeners belong
            # to the root-managed TLS proxy, never to a plain Gunicorn child.
            # HTTP custom ports remain Agent-managed as before.
            if parsed.scheme == "https":
                continue
            port = self._url_listener_port(value)
            if port == CONTROL_PORT:
                continue
            port_urls = targets.setdefault(port, [])
            if value not in port_urls:
                port_urls.append(value)
        return targets

    def stream_gateway_ports(self) -> set[int]:
        """Return every listener port declared by a Playlist/App alias."""
        return {
            self._url_listener_port(value)
            for value in self.stream_urls
            if urllib.parse.urlsplit(str(value or "").strip()).hostname
        }

    def managed_tls_ports(self) -> set[int]:
        """Return public HTTPS ports owned by the root-managed TLS proxy."""
        ports: set[int] = set()
        for value in [*self.panel_urls, *self.stream_urls]:
            parsed = urllib.parse.urlsplit(str(value or "").strip())
            if parsed.scheme == "https" and parsed.hostname:
                ports.add(self._url_listener_port(value))
        # Port 443 is also needed for HTTPS -> HTTP canonical redirects on any
        # DNS alias; the helper provisions it even when the saved URL is HTTP.
        return ports

    def _verify_http_panel_url(self, value: str) -> None:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
        if parsed.scheme != "http" or not parsed.hostname:
            return
        port = self._url_listener_port(value)
        prefix = parsed.path.rstrip("/")
        stream_only = value in self.stream_urls and value not in self.panel_urls
        probe_path = "/get.php" if stream_only else "/api/v1/health"
        probe_url = f"http://127.0.0.1:{port}{prefix}{probe_path}"
        host_header = parsed.hostname
        default_port = 80
        if port != default_port:
            host_header = f"{host_header}:{port}"
        request = urllib.request.Request(
            probe_url,
            headers={
                "X-Node-Token": TOKEN,
                "Host": host_header,
                "Accept": "application/json",
                "User-Agent": f"StreamForge-Node/{VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
                if response.status != 200 or not data.get("ok"):
                    raise RuntimeError("health response was not successful")
        except urllib.error.HTTPError as exc:
            # A stream-only probe intentionally has no credentials. FastAPI's
            # 4xx response proves that the configured Host, path and listener
            # reached the Playlist/App route instead of being silently dropped.
            if stream_only and 400 <= int(exc.code) < 500:
                return
            raise RuntimeError(f"Panel/API URL did not become usable: {value}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Panel/API URL did not become usable: {value}: {exc}") from exc

    def _stop_panel_gateway(self, port: int) -> None:
        process = self.panel_gateway_processes.pop(int(port), None)
        handle = self.panel_gateway_log_handles.pop(int(port), None)
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def stop_panel_gateways(self, keep_ports: set[int] | None = None) -> None:
        keep = set(keep_ports or set())
        for port in list(self.panel_gateway_processes):
            if port not in keep:
                self._stop_panel_gateway(port)
        for port in list(self.panel_gateway_errors):
            if port not in keep:
                self.panel_gateway_errors.pop(port, None)

    def panel_gateway_status(self) -> dict[str, Any]:
        targets = self.panel_gateway_targets()
        rows: list[dict[str, Any]] = []
        for port, urls in sorted(targets.items()):
            process = self.panel_gateway_processes.get(port)
            alive = bool(process and process.poll() is None)
            managed = bool(process)
            ready = bool(self._port_accepting_connections(port))
            schemes = sorted({urllib.parse.urlsplit(value).scheme for value in urls})
            rows.append({
                "port": port,
                "urls": list(urls),
                "schemes": schemes,
                "managed": managed,
                "ready": ready,
                "pid": process.pid if alive and process else None,
                "mode": (
                    "managed-shared-panel-stream-listener"
                    if managed and port in self.stream_gateway_ports()
                    else "managed-http-listener" if managed else "external-listener"
                ),
                "error": self.panel_gateway_errors.get(port, ""),
            })
        return {"ready": all(item["ready"] for item in rows), "listeners": rows}

    def ensure_panel_gateways(self, *, force_restart: bool = False) -> None:
        if NODE_MODE != "control":
            return
        targets = self.panel_gateway_targets()
        desired_ports = set(targets)
        self.stop_panel_gateways(keep_ports=desired_ports)

        for port, urls in sorted(targets.items()):
            shared_panel_stream = port in self.stream_gateway_ports()

            existing = self.panel_gateway_processes.get(port)
            if existing and existing.poll() is None and not force_restart:
                if self._port_accepting_connections(port):
                    self.panel_gateway_errors.pop(port, None)
                    continue
            if existing:
                self._stop_panel_gateway(port)

            # Respect an already-running local reverse proxy or web server,
            # but verify that it really serves every requested URL.
            if self._port_accepting_connections(port):
                for value in urls:
                    self._verify_http_panel_url(value)
                self.panel_gateway_errors.pop(port, None)
                continue

            PANEL_GATEWAY_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = PANEL_GATEWAY_LOG_DIR / f"panel-gateway-{port}.log"
            try:
                log_handle = log_path.open("ab", buffering=0)
            except OSError as exc:
                raise RuntimeError(f"Cannot open Panel/API gateway log for port {port}: {exc}") from exc

            env = os.environ.copy()
            # One single-worker Gunicorn ASGI listener safely serves both the
            # Node Panel/API and Playlist/App routes when they share a port.
            env["STREAMFORGE_NODE_MODE"] = "shared" if shared_panel_stream else "panel"
            env["STREAMFORGE_NODE_PANEL_GATEWAY_PORT"] = str(port)
            cmd = _gunicorn_asgi_command(
                "app:app", os.getenv("STREAMFORGE_NODE_BIND", "0.0.0.0"), port
            )
            try:
                process = subprocess.Popen(
                    cmd, cwd=str(Path(__file__).resolve().parent), env=env,
                    stdout=log_handle, stderr=log_handle, close_fds=True,
                )
            except Exception as exc:
                log_handle.close()
                raise RuntimeError(f"Could not launch Panel/API listener on port {port}: {exc}") from exc

            self.panel_gateway_processes[port] = process
            self.panel_gateway_log_handles[port] = log_handle
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self._port_accepting_connections(port):
                    self.panel_gateway_errors.pop(port, None)
                    self.log(f"Panel/API gateway listening on port {port}", scope="settings")
                    break
                time.sleep(0.1)
            else:
                pass

            if self._port_accepting_connections(port):
                try:
                    for value in urls:
                        self._verify_http_panel_url(value)
                except Exception:
                    self._stop_panel_gateway(port)
                    raise

            if not self._port_accepting_connections(port):
                rc = process.poll()
                self._stop_panel_gateway(port)
                tail = ""
                try:
                    tail = " | ".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-6:])
                except OSError:
                    pass
                hint = "The port may already be reserved by another service."
                if port < 1024:
                    hint += " Low ports also require CAP_NET_BIND_SERVICE on streamforge-node.service."
                error = (
                    f"Panel/API listener could not bind port {port}"
                    + (f" (exit {rc})" if rc is not None else "")
                    + f". {hint}"
                    + (f" Log: {tail[-900:]}" if tail else "")
                )
                self.panel_gateway_errors[port] = error
                raise RuntimeError(error)

    def stop_public_gateway(self) -> None:
        process = self.public_process
        handle = self.public_process_log_handle
        self.public_process = None
        self.public_process_port = 0
        self.public_process_workers = 0
        self.public_process_log_handle = None
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    @staticmethod
    def _port_accepting_connections(port: int) -> bool:
        for host in ("127.0.0.1", "::1"):
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.25)
                    if sock.connect_ex((host, int(port))) == 0:
                        return True
            except OSError:
                continue
        return False

    def _canonical_stream_listener_port(self) -> int:
        # STREAMFORGE_NODE_CANONICAL_STREAM_PORT_V65R5: the canonical URL is
        # authoritative. A stale legacy scalar must never make a http:// URL
        # bind plaintext Gunicorn to 443.
        for value in self.stream_urls:
            try:
                parsed = urllib.parse.urlsplit(str(value or "").strip())
            except ValueError:
                continue
            if parsed.hostname and parsed.scheme in {"http", "https"}:
                return self._url_listener_port(value)
        return max(1, min(65535, int(self.stream_port or CONTROL_PORT)))

    def _managed_http_stream_ports(self) -> set[int]:
        ports: set[int] = set()
        for value in self.stream_urls:
            try:
                parsed = urllib.parse.urlsplit(str(value or "").strip())
            except ValueError:
                continue
            if parsed.scheme == "http" and parsed.hostname:
                ports.add(self._url_listener_port(value))
        return ports

    def _managed_tls_stream_ports(self) -> set[int]:
        # STREAMFORGE_NODE_SHARED_PORT_PUBLIC_POOL_V65R4: the persisted legacy
        # stream_port can remain 80 even while the canonical Playlist/App URL
        # is HTTPS. Nginx owns the HTTPS listener and proxies public routes to
        # the private multi-worker backend.
        ports: set[int] = set()
        for value in self.stream_urls:
            parsed = urllib.parse.urlsplit(str(value or "").strip())
            if parsed.scheme == "https" and parsed.hostname:
                ports.add(self._url_listener_port(value))
        return ports

    def _systemd_public_worker_state(self) -> dict[str, Any]:
        # STREAMFORGE_NODE_PUBLIC_SYSTEMD_STATUS_V79: the public service writes
        # its hardware-aware startup decision atomically enough for status/UI.
        path = Path("/var/lib/streamforge-node/public-workers.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def public_gateway_status(self) -> dict[str, Any]:
        desired = self._canonical_stream_listener_port()
        backend_port = _node_public_backend_port()
        public_state = self._systemd_public_worker_state() if NODE_PUBLIC_MANAGED_BY_SYSTEMD else {}
        target_workers = int(public_state.get("workers") or _node_public_worker_count())
        if EXTERNAL_PROXY_MODE:
            return {
                "enabled": True, "ready": True, "port": desired,
                "mode": "external-main-nginx-proxy", "pid": os.getpid(), "error": "",
                "backend_port": int(CONTROL_PORT), "workers": 1,
            }
        tls_stream_ports = self._managed_tls_stream_ports()
        if tls_stream_ports:
            tls = self.tls_status()
            ready_ports = {int(port) for ports in (tls.get("ready_hosts") or {}).values() for port in (ports or []) if str(port).isdigit()}
            process = self.public_process
            alive = bool(process and process.poll() is None)
            backend_ready = (
                bool(self._port_accepting_connections(backend_port))
                if NODE_PUBLIC_MANAGED_BY_SYSTEMD
                else bool(alive and self.public_process_port == backend_port and self._port_accepting_connections(backend_port))
            )
            public_tls_port = min(tls_stream_ports)
            return {
                "enabled": True, "ready": bool(public_tls_port in ready_ports and backend_ready), "port": public_tls_port,
                "mode": "managed-node-tls-systemd-public-pool" if NODE_PUBLIC_MANAGED_BY_SYSTEMD else "managed-node-tls-public-pool",
                "pid": None if NODE_PUBLIC_MANAGED_BY_SYSTEMD else (process.pid if alive and process else None),
                "error": str((tls.get("errors") or {}).get("nginx") or self.public_gateway_error or ""),
                "backend_port": int(backend_port),
                "workers": int(target_workers if NODE_PUBLIC_MANAGED_BY_SYSTEMD else (self.public_process_workers if alive else target_workers)),
                "auto_target_workers": int(public_state.get("auto_target") or target_workers),
            }
        http_stream_ports = self._managed_http_stream_ports()
        if desired in http_stream_ports:
            process = self.public_process
            alive = bool(process and process.poll() is None)
            backend_ready = (
                bool(self._port_accepting_connections(backend_port))
                if NODE_PUBLIC_MANAGED_BY_SYSTEMD
                else bool(alive and self.public_process_port == backend_port and self._port_accepting_connections(backend_port))
            )
            front_ready = bool(self._port_accepting_connections(desired))
            tls = self.tls_status()
            return {
                "enabled": True, "ready": bool(front_ready and backend_ready), "port": desired,
                "mode": "managed-node-http-systemd-public-pool" if NODE_PUBLIC_MANAGED_BY_SYSTEMD else "managed-node-http-public-pool",
                "pid": None if NODE_PUBLIC_MANAGED_BY_SYSTEMD else (process.pid if alive and process else None),
                "error": str((tls.get("errors") or {}).get("nginx") or self.public_gateway_error or ""), "backend_port": int(backend_port),
                "workers": int(target_workers if NODE_PUBLIC_MANAGED_BY_SYSTEMD else (self.public_process_workers if alive else target_workers)),
                "auto_target_workers": int(public_state.get("auto_target") or target_workers),
            }
        if desired != CONTROL_PORT and desired in self.panel_gateway_targets():
            return {
                "enabled": True,
                "ready": bool(self._port_accepting_connections(desired)),
                "port": desired,
                "mode": "shared-panel-stream-listener",
                "pid": (
                    self.panel_gateway_processes[desired].pid
                    if desired in self.panel_gateway_processes
                    and self.panel_gateway_processes[desired].poll() is None
                    else None
                ),
                "error": self.panel_gateway_errors.get(desired, ""),
                "workers": 1,
            }
        if desired == CONTROL_PORT:
            return {
                "enabled": False, "ready": True, "port": desired,
                "mode": "shared-control-port", "pid": os.getpid(), "error": "",
                "workers": 1, "auto_target_workers": int(target_workers),
            }
        process = self.public_process
        alive = bool(process and process.poll() is None)
        if NODE_PUBLIC_MANAGED_BY_SYSTEMD:
            ready = bool(self._port_accepting_connections(backend_port))
            return {
                "enabled": True, "ready": ready, "port": desired,
                "mode": "systemd-public-multiworker", "pid": None,
                "error": self.public_gateway_error, "backend_port": int(backend_port),
                "workers": int(target_workers),
                "auto_target_workers": int(public_state.get("auto_target") or target_workers),
            }
        ready = bool(alive and self.public_process_port == desired and self._port_accepting_connections(desired))
        return {
            "enabled": True, "ready": ready, "port": desired,
            "mode": "separate-public-multiworker", "pid": process.pid if alive and process else None,
            "error": self.public_gateway_error,
            "workers": int(self.public_process_workers if alive else target_workers),
            "auto_target_workers": int(target_workers),
        }

    def ensure_public_gateway(self) -> None:
        if NODE_MODE != "control":
            return
        if EXTERNAL_PROXY_MODE:
            self.stop_public_gateway()
            self.public_gateway_error = ""
            return
        desired = self._canonical_stream_listener_port()
        tls_stream_ports = self._managed_tls_stream_ports()
        managed_tls = bool(tls_stream_ports)
        backend_port = _node_public_backend_port()
        if NODE_PUBLIC_MANAGED_BY_SYSTEMD:
            # STREAMFORGE_NODE_PUBLIC_SYSTEMD_OWNER_V79: the control process
            # never spawns/owns the public Gunicorn pool.  Nginx listener
            # reconciliation stays here; systemd owns 8821 independently.
            self.stop_public_gateway()
            if managed_tls or desired in self._managed_http_stream_ports():
                self.request_tls_reconcile()
            if self._port_accepting_connections(backend_port):
                self.public_gateway_error = ""
            else:
                self.public_gateway_error = f"Systemd public pool is not ready on 127.0.0.1:{backend_port}"
            return
        if managed_tls:
            # STREAMFORGE_NODE_TLS_PUBLIC_GATEWAY_SPLIT_V39: never launch a
            # plaintext Gunicorn child on the public HTTPS port itself.
            # STREAMFORGE_NODE_PUBLIC_POOL_TLS_BACKEND_V65: Nginx owns the
            # public TLS listener while a private multi-worker Gunicorn pool
            # serves playlist/auth requests on loopback.
            listen_port = backend_port
            bind_host = "127.0.0.1"
            self.request_tls_reconcile()
        else:
            # STREAMFORGE_NODE_HTTP_SHARED_PUBLIC_POOL_V65R5: Nginx owns the
            # external shared HTTP listener and routes public paths to the
            # loopback multi-worker backend, while control/API paths go to the
            # loopback single-worker control backend.
            http_stream_ports = self._managed_http_stream_ports()
            if desired in http_stream_ports:
                listen_port = backend_port
                bind_host = "127.0.0.1"
                self.request_tls_reconcile()
            else:
                if desired != CONTROL_PORT and desired in self.panel_gateway_targets():
                    self.stop_public_gateway()
                    self.public_gateway_error = ""
                    return
                if desired == CONTROL_PORT:
                    self.stop_public_gateway()
                    self.public_gateway_error = ""
                    return
                listen_port = desired
                bind_host = os.getenv("STREAMFORGE_NODE_BIND", "0.0.0.0")

        if self.public_process and self.public_process.poll() is None and self.public_process_port == listen_port:
            if self._port_accepting_connections(listen_port):
                self.public_gateway_error = ""
                return
            self.stop_public_gateway()
        elif self.public_process and self.public_process.poll() is not None:
            self.stop_public_gateway()

        PUBLIC_GATEWAY_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_handle = PUBLIC_GATEWAY_LOG.open("ab", buffering=0)
        except OSError as exc:
            self.public_gateway_error = f"Cannot open public gateway log: {exc}"
            raise RuntimeError(self.public_gateway_error) from exc

        workers = _node_public_worker_count()
        env = os.environ.copy()
        env["STREAMFORGE_NODE_MODE"] = "public"
        env["STREAMFORGE_NODE_PUBLIC_PORT"] = str(desired)
        env["STREAMFORGE_NODE_PUBLIC_BACKEND_PORT"] = str(backend_port)
        env["STREAMFORGE_NODE_PUBLIC_WORKER_COUNT"] = str(workers)
        cmd = _gunicorn_asgi_command(
            "app:app", bind_host, listen_port, workers=workers
        )
        try:
            process = subprocess.Popen(
                cmd, cwd=str(Path(__file__).resolve().parent), env=env,
                stdout=log_handle, stderr=log_handle, close_fds=True,
            )
        except Exception as exc:
            log_handle.close()
            self.public_gateway_error = f"Could not launch Playlist/API public worker pool on port {listen_port}: {exc}"
            raise RuntimeError(self.public_gateway_error) from exc

        self.public_process = process
        self.public_process_log_handle = log_handle
        self.public_process_port = listen_port
        self.public_process_workers = int(workers)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if self._port_accepting_connections(listen_port):
                self.public_gateway_error = ""
                self.log(
                    f"Playlist/API public pool listening on {bind_host}:{listen_port} with {workers} worker(s)",
                    scope="settings",
                )
                return
            time.sleep(0.1)

        rc = process.poll()
        self.stop_public_gateway()
        tail = ""
        try:
            tail = " | ".join(PUBLIC_GATEWAY_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-6:])
        except OSError:
            pass
        hint = "Check whether another service is already using this port."
        if listen_port < 1024:
            hint = "Low ports require CAP_NET_BIND_SERVICE on streamforge-node.service."
        self.public_gateway_error = (
            f"Playlist/API public pool could not listen on port {listen_port}"
            + (f" (exit {rc})" if rc is not None else "")
            + f". {hint}"
            + (f" Log: {tail[-900:]}" if tail else "")
        )
        self.log(self.public_gateway_error, scope="settings", level="error")
        raise RuntimeError(self.public_gateway_error)

    @staticmethod
    def _ip_in_rules(ip_text: str, rules: str) -> bool:
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        for raw in str(rules or "").splitlines():
            item = raw.strip()
            if not item:
                continue
            try:
                if address in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _lookup_geo(self, ip_text: str) -> dict[str, Any]:
        try:
            address = ipaddress.ip_address(ip_text)
            normalized = str(address)
        except ValueError:
            return {"ip": ip_text, "asn": None, "business_name": "", "country_name": "", "country_code": "", "provider": ""}
        cached = self._geo_cache.get(normalized)
        if cached is not None:
            cache_ttl = 30 if cached[1].get("ipinfo_error") else 21600
            if time.monotonic() - cached[0] < cache_ttl:
                return dict(cached[1])
            self._geo_cache.pop(normalized, None)
        self.load_geo_settings()
        result: dict[str, Any] = {"ip": normalized, "asn": None, "business_name": "", "country_name": "", "country_code": "", "provider": ""}
        # Provider order:
        #   auto     -> IPinfo first, then MaxMind only for missing fields
        #   ipinfo   -> IPinfo only
        #   maxmind  -> MaxMind databases only
        if self.geo_provider in {"auto", "ipinfo"} and self.ipinfo_token and not address.is_private and not address.is_loopback:
            # STREAMFORGE_NODE_IPINFO_COMPATIBLE_LOOKUP_V3059:
            # Prefer the current Lite API, then try IPinfo's legacy hostname.
            # Parse Lite, Core/nested and legacy response shapes, and return a
            # precise failure reason to Main instead of swallowing every error.
            quoted_ip = urllib.parse.quote(normalized, safe="")
            quoted_token = urllib.parse.quote(self.ipinfo_token, safe="")
            urls = (
                # STREAMFORGE_NODE_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060: IPinfo officially provides an IPv4 transport hostname.
                # Try it before the legacy endpoint for hosts with broken outbound IPv6.
                f"https://api.ipinfo.io/lite/{quoted_ip}?token={quoted_token}",
                f"https://v4.api.ipinfo.io/lite/{quoted_ip}?token={quoted_token}",
                f"https://ipinfo.io/{quoted_ip}/json?token={quoted_token}",
            )
            errors: list[str] = []
            for url in urls:
                req = urllib.request.Request(
                    url,
                    headers={"Accept": "application/json", "User-Agent": "StreamForge-Node-GeoIP/3.2"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=6.0) as response:
                        payload = json.loads(response.read().decode("utf-8", errors="replace"))
                    if not isinstance(payload, dict):
                        raise ValueError("IPinfo returned a non-object response")
                    as_record = payload.get("as") if isinstance(payload.get("as"), dict) else {}
                    geo_record = payload.get("geo") if isinstance(payload.get("geo"), dict) else {}
                    org_text = str(payload.get("org") or "").strip()
                    asn_value = payload.get("asn") or as_record.get("asn") or ""
                    if not asn_value and org_text:
                        match = re.match(r"^AS(\d+)(?:\s+|$)", org_text, re.IGNORECASE)
                        asn_value = match.group(1) if match else ""
                    asn_text = str(asn_value or "").upper().removeprefix("AS")
                    country_name = str(payload.get("country_name") or geo_record.get("country") or "").strip()
                    country_value = str(payload.get("country") or "").strip()
                    country_code = str(payload.get("country_code") or geo_record.get("country_code") or "").strip()
                    if not country_code and len(country_value) == 2:
                        country_code = country_value.upper()
                    elif not country_name and country_value and len(country_value) != 2:
                        country_name = country_value
                    remote = {
                        "asn": int(asn_text) if asn_text.isdigit() else None,
                        "business_name": str(
                            payload.get("as_name")
                            or as_record.get("name")
                            or re.sub(r"^AS\d+\s*", "", org_text, flags=re.IGNORECASE)
                            or org_text
                            or ""
                        ).strip(),
                        "country_name": country_name,
                        "country_code": country_code,
                    }
                    for key in ("asn", "business_name", "country_name", "country_code"):
                        if remote.get(key) not in (None, ""):
                            result[key] = remote.get(key)
                    if any(result.get(key) not in (None, "") for key in ("asn", "business_name", "country_name", "country_code")):
                        result["provider"] = "ipinfo"
                        errors.clear()
                        break
                    errors.append("IPinfo returned no GeoIP fields")
                except urllib.error.HTTPError as exc:
                    if exc.code in {401, 403}:
                        errors.append(f"IPinfo rejected the saved token (HTTP {exc.code})")
                    elif exc.code == 429:
                        errors.append("IPinfo rate limit reached (HTTP 429)")
                    else:
                        errors.append(f"IPinfo HTTP {exc.code}")
                except urllib.error.URLError as exc:
                    reason = str(getattr(exc, "reason", "") or exc)
                    errors.append(f"IPinfo connection failed: {reason[:180]}")
                except TimeoutError:
                    errors.append("IPinfo request timed out")
                except (ValueError, OSError) as exc:
                    errors.append(f"IPinfo response error: {str(exc)[:180]}")
            if errors and not result.get("provider"):
                result["ipinfo_error"] = "; ".join(dict.fromkeys(item for item in errors if item)) or "IPinfo lookup failed"

        if self.geo_provider in {"auto", "maxmind"}:
            fallback_used = False
            if maxminddb is not None and ASN_DB_FILE.is_file():
                try:
                    if self._asn_reader is None:
                        self._asn_reader = maxminddb.open_database(str(ASN_DB_FILE))
                    record = self._asn_reader.get(normalized) or {}
                    local_asn = int(record["autonomous_system_number"]) if record.get("autonomous_system_number") else None
                    local_business = str(record.get("autonomous_system_organization") or "")
                    for key, value in (("asn", local_asn), ("business_name", local_business)):
                        if self.geo_provider == "maxmind" or result.get(key) in (None, ""):
                            if value not in (None, ""):
                                result[key] = value
                                fallback_used = True
                except Exception:
                    pass
            if maxminddb is not None and COUNTRY_DB_FILE.is_file():
                try:
                    if self._country_reader is None:
                        self._country_reader = maxminddb.open_database(str(COUNTRY_DB_FILE))
                    record = self._country_reader.get(normalized) or {}
                    country = record.get("country") or record.get("registered_country") or {}
                    local_country = str((country.get("names") or {}).get("en") or "")
                    local_code = str(country.get("iso_code") or "")
                    for key, value in (("country_name", local_country), ("country_code", local_code)):
                        if self.geo_provider == "maxmind" or result.get(key) in (None, ""):
                            if value not in (None, ""):
                                result[key] = value
                                fallback_used = True
                except Exception:
                    pass
            if fallback_used:
                result["provider"] = "maxmind" if self.geo_provider == "maxmind" or not result.get("provider") else "ipinfo+maxmind"
        if len(self._geo_cache) > 10000:
            self._geo_cache.clear()
        self._geo_cache[normalized] = (time.monotonic(), dict(result))
        return result

    def _lookup_geo_background_worker(self, normalized: str) -> None:
        try:
            self._lookup_geo(normalized)
        finally:
            with self._geo_background_lock:
                self._geo_background_pending.discard(normalized)

    def _lookup_geo_cached_nonblocking(self, ip_text: str) -> dict[str, Any]:
        try:
            normalized = str(ipaddress.ip_address(str(ip_text or "").strip()))
        except ValueError:
            return {}
        cached = self._geo_cache.get(normalized)
        if cached is not None:
            age = time.monotonic() - float(cached[0])
            value = dict(cached[1])
            ttl = 30 if value.get("ipinfo_error") else 21600
            if age < ttl:
                return value
        with self._geo_background_lock:
            if normalized not in self._geo_background_pending:
                self._geo_background_pending.add(normalized)
                self._geo_background_executor.submit(self._lookup_geo_background_worker, normalized)
        return dict(cached[1]) if cached is not None else {}

    def _lookup_asn(self, ip_text: str) -> int | None:
        value = self._lookup_geo(ip_text).get("asn")
        return int(value) if value is not None else None

    @staticmethod
    def _database_status(path: Path, missing_message: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": str(path),
            "exists": path.is_file(),
            "module_loaded": maxminddb is not None,
            "loaded": False,
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "error": "",
        }
        if maxminddb is None:
            result["error"] = "Python maxminddb module is not installed"
            return result
        if not path.is_file():
            result["error"] = missing_message
            return result
        try:
            reader = maxminddb.open_database(str(path))
            metadata = reader.metadata()
            result.update({
                "loaded": True,
                "database_type": getattr(metadata, "database_type", ""),
                "build_epoch": int(getattr(metadata, "build_epoch", 0) or 0),
                "ip_version": int(getattr(metadata, "ip_version", 0) or 0),
            })
            reader.close()
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def asn_status(self) -> dict[str, Any]:
        self.load_geo_settings()
        now = time.monotonic()
        if self._asn_status_cache and now - self._asn_status_cache[0] < 30:
            return dict(self._asn_status_cache[1])
        asn = self._database_status(ASN_DB_FILE, "GeoLite2-ASN.mmdb was not found")
        country = self._database_status(COUNTRY_DB_FILE, "GeoLite2-Country.mmdb was not found")
        result = dict(asn)
        result.update({
            "country_path": country["path"],
            "country_exists": country["exists"],
            "country_loaded": country["loaded"],
            "country_size_bytes": country["size_bytes"],
            "country_database_type": country.get("database_type", ""),
            "country_error": country.get("error", ""),
            "auto_update_enabled": bool(self.geo_auto_update),
            "auto_update_configured": bool(self.maxmind_account_id and self.maxmind_license_key),
            "provider": self.geo_provider,
            "ipinfo_configured": bool(self.ipinfo_token),
            "last_auto_update": GEOIP_UPDATE_MARKER.read_text(encoding="utf-8").strip() if GEOIP_UPDATE_MARKER.is_file() else "",
        })
        self._asn_status_cache = (now, dict(result))
        return result


    def access_allowed(self, request: Request) -> tuple[bool, str]:
        ip_text = self._client_ip(request)
        if self._ip_in_rules(ip_text, self.ip_blacklist):
            return False, "Client IP is blacklisted"
        if self.ip_whitelist.strip() and not self._ip_in_rules(ip_text, self.ip_whitelist):
            return False, "Client IP is not in the whitelist"
        needs_asn = bool(self.asn_whitelist.strip() or self.asn_blacklist.strip())
        asn = self._lookup_asn(ip_text) if needs_asn else None
        if needs_asn and asn is None:
            return False, "ASN policy is configured but no ASN database match is available"
        blocked_asns = {x.strip().upper().removeprefix("AS") for x in self.asn_blacklist.splitlines() if x.strip()}
        allowed_asns = {x.strip().upper().removeprefix("AS") for x in self.asn_whitelist.splitlines() if x.strip()}
        if asn is not None and str(asn) in blocked_asns:
            return False, f"ASN AS{asn} is blacklisted"
        if allowed_asns and (asn is None or str(asn) not in allowed_asns):
            return False, f"ASN AS{asn} is not in the whitelist"
        return True, ""

    # STREAMFORGE_NODE_PANEL_IP_WHITELIST_V98:
    # STREAMFORGE_NODE_PANEL_FULL_ACCESS_POLICY_V99:
    # STREAMFORGE_NODE_PANEL_CLIENT_IP_METHOD_FIX_V99R4:
    # Panel browser access is deliberately independent from playback/catalog
    # rules, but uses the same four-rule evaluation model. Empty lists are no-op.
    def panel_access_allowed(self, request: Request) -> tuple[bool, str]:
        ip_text = self._client_ip(request)
        if self._ip_in_rules(ip_text, self.panel_ip_blacklist):
            return False, "Panel client IP is blacklisted"
        if self.panel_ip_whitelist.strip() and not self._ip_in_rules(ip_text, self.panel_ip_whitelist):
            return False, "Panel client IP is not in the whitelist"
        needs_asn = bool(self.panel_asn_whitelist.strip() or self.panel_asn_blacklist.strip())
        asn = self._lookup_asn(ip_text) if needs_asn else None
        if needs_asn and asn is None:
            return False, "Panel ASN policy is configured but no ASN database match is available"
        blocked_asns = {x.strip().upper().removeprefix("AS") for x in self.panel_asn_blacklist.splitlines() if x.strip()}
        allowed_asns = {x.strip().upper().removeprefix("AS") for x in self.panel_asn_whitelist.splitlines() if x.strip()}
        if asn is not None and str(asn) in blocked_asns:
            return False, f"Panel ASN AS{asn} is blacklisted"
        if allowed_asns and (asn is None or str(asn) not in allowed_asns):
            return False, f"Panel ASN AS{asn} is not in the whitelist"
        return True, ""


    def load_logs(self) -> None:
        # STREAMFORGE_NODE_LOG_RETENTION_V88: stream the newest safety window
        # instead of reading/truncating the entire log file into memory.
        try:
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=NODE_EVENT_LOG_MAX)
        except OSError:
            return
        cutoff = time.time() - (_node_log_retention_days() * 86400)
        for line in lines:
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                raw = str(item.get("time") or "").strip()
                try:
                    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    stamp = time.time()
                if stamp >= cutoff and self._remember_event_log(item):
                    self.event_logs.appendleft(item)
            except ValueError:
                continue
        try:
            stat = LOG_FILE.stat()
            self._event_log_file_identity = (int(stat.st_dev), int(stat.st_ino))
            self._event_log_file_offset = int(stat.st_size)
        except OSError:
            self._event_log_file_identity = None
            self._event_log_file_offset = 0
        self.prune_event_logs(force=True)

    def stream_output_base(self) -> str:
        return (self.stream_urls[0] if self.stream_urls else "").strip().rstrip("/")

    def current_xtream_output_url(self, user: NodeUserConfig) -> str:
        """Return a saved Xtream query on the current Playlist/App base URL.

        Passwords are intentionally not stored separately in clear text. The
        existing output URL already contains the original encoded credentials,
        so only its scheme/host/port/access-path is replaced.
        """
        saved = str(user.xtream_output_url or "").strip()
        base = self.stream_output_base()
        if not saved or not base:
            return saved
        try:
            parsed = urllib.parse.urlsplit(saved)
        except ValueError:
            return saved
        if not parsed.query or not parsed.path.rstrip("/").endswith("/get.php"):
            return saved
        return f"{base}/get.php?{parsed.query}"

    def refresh_xtream_output_urls(self) -> int:
        """Persist current Playlist/App host, port and path for local users."""
        changed = 0
        for token, user in list(self.users.items()):
            if str(user.source or "").strip().lower() != "node_local":
                continue
            current = self.current_xtream_output_url(user)
            if current and current != str(user.xtream_output_url or "").strip():
                self.users[token] = user.model_copy(update={"xtream_output_url": current})
                changed += 1
        return changed

    def _save_panel_state_only(self) -> None:
        # Public workers may learn a newer Main URL from heartbeat, but must never
        # rewrite users.json from their process-local user snapshot.
        PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        panel_temp = PANEL_FILE.with_name(
            f".{PANEL_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        panel_temp.write_text(json.dumps({
            "panel_url": self.panel_url, "node_slug": self.panel_node_slug,
            "independent_mode": bool(self.independent_mode),
            "local_channel_limit": int(self.local_channel_limit),
        }, separators=(",", ":")), encoding="utf-8")
        panel_temp.replace(PANEL_FILE)

    def save_categories(self) -> None:
        # STREAMFORGE_NODE_CATEGORY_STATE_SEPARATE_WRITER_V127:
        # Mode/category reconciliation owns categories.json only. Keeping this
        # separate prevents Test & Sync from replaying a stale playlist-user
        # snapshot through save_users().
        CATEGORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        category_temp = CATEGORY_FILE.with_name(
            f".{CATEGORY_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            category_temp.write_text(json.dumps({
                "categories": [item.model_dump() for item in self.main_categories],
                "order_override": list(self.category_order_overrides),
            }, separators=(",", ":")), encoding="utf-8")
            category_temp.replace(CATEGORY_FILE)
        finally:
            category_temp.unlink(missing_ok=True)

    def save_users(self) -> None:
        # STREAMFORGE_NODE_PLAYLIST_USER_CROSS_PROCESS_SYNC_V123:
        # Control-plane CRUD is authoritative. Serialize file writers and publish
        # by atomic inode replacement so public workers can reload without restart.
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        USERS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with USERS_LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                temp = USERS_FILE.with_name(
                    f".{USERS_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
                )
                temp.write_text(json.dumps({
                    "users": [item.model_dump() for item in self.users.values()],
                    "playlists": [item.model_dump() for item in self.playlists.values()],
                }, separators=(",", ":")), encoding="utf-8")
                os.chmod(temp, 0o600)
                temp.replace(USERS_FILE)
                current_stat = USERS_FILE.stat()
                self.users_file_signature = self._users_signature(current_stat)
                self.users_file_mtime = current_stat.st_mtime
                self._save_panel_state_only()
                self.save_categories()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def save_panel_users(self) -> None:
        PANEL_USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = PANEL_USERS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "node_name": self.node_name,
            "node_logo_url": self.node_logo_url,
            "independent_mode": bool(self.independent_mode),
            "users": [item.model_dump() for item in self.panel_users.values()],
        }, separators=(",", ":")))
        temp.replace(PANEL_USERS_FILE)

    def apply_main_node_name(self, value: Any) -> bool:
        """Persist a Main-owned Node name received through any live sync path."""
        cleaned = str(value or "").strip()[:120]
        if not cleaned or cleaned == self.node_name:
            return False
        # STREAMFORGE_NODE_NAME_PATCH_NO_ACCESS_ALIAS_CLOBBER_V1150:
        # Heartbeat/live-auth callers may run in a long-lived process whose
        # panel_urls/stream_urls snapshot predates a Main-side alias edit. A Node
        # name update must therefore patch only node_name instead of serializing
        # the entire stale access object back over the authoritative access sync.
        with self.lock:
            self.node_name = cleaned
            self.patch_access_file({"node_name": cleaned})
            self.save_panel_users()
        self.log("Node name synchronized from Main", scope="settings", details=f"node_name={cleaned}")
        return True

    def sync_panel_users(self, payload: PanelUserSyncPayload) -> dict[str, Any]:
        with self.lock:
            self.panel_url = payload.panel_url.strip().rstrip("/")
            self.panel_node_slug = payload.node_slug.strip()
            synced_node_name = payload.node_name.strip()[:120]
            if synced_node_name:
                self.node_name = synced_node_name
            self.node_logo_url = payload.node_logo_url.strip()
            if payload.node_logo_filename and payload.node_logo_content_base64:
                filename = Path(payload.node_logo_filename).name
                extension = Path(filename).suffix.lower()
                if filename != payload.node_logo_filename or extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    raise ValueError("Invalid synchronized Node logo filename")
                try:
                    logo_data = base64.b64decode(payload.node_logo_content_base64, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValueError("Invalid synchronized Node logo data") from exc
                if not logo_data or len(logo_data) > 2 * 1024 * 1024:
                    raise ValueError("Synchronized Node logo must be between 1 byte and 2 MB")
                NODE_LOGO_ROOT.mkdir(parents=True, exist_ok=True)
                target = NODE_LOGO_ROOT / filename
                temporary = NODE_LOGO_ROOT / f".{filename}.{secrets.token_hex(4)}.tmp"
                temporary.write_bytes(logo_data)
                os.chmod(temporary, 0o644)
                temporary.replace(target)
                self.node_logo_url = f"/node-logos/{filename}"
            if payload.remove_node_favicon:
                settings_data = _node_operations_settings()
                old_name = str(settings_data.get("favicon_filename") or "").strip()
                settings_data["favicon_filename"] = ""
                OPERATIONS_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                OPERATIONS_SETTINGS_FILE.write_text(json.dumps(settings_data, separators=(",", ":")), encoding="utf-8")
                if old_name and old_name == Path(old_name).name:
                    (NODE_FAVICON_ROOT / old_name).unlink(missing_ok=True)
            elif payload.node_favicon_filename and payload.node_favicon_content_base64:
                favicon_name = Path(payload.node_favicon_filename).name
                favicon_ext = Path(favicon_name).suffix.lower()
                if favicon_name != payload.node_favicon_filename or favicon_ext not in {".ico", ".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    raise ValueError("Invalid synchronized Node favicon filename")
                try:
                    favicon_data = base64.b64decode(payload.node_favicon_content_base64, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValueError("Invalid synchronized Node favicon data") from exc
                if not favicon_data or len(favicon_data) > 2 * 1024 * 1024:
                    raise ValueError("Synchronized Node favicon must be between 1 byte and 2 MB")
                NODE_FAVICON_ROOT.mkdir(parents=True, exist_ok=True)
                favicon_target = NODE_FAVICON_ROOT / favicon_name
                favicon_temp = NODE_FAVICON_ROOT / f".{favicon_name}.{secrets.token_hex(4)}.tmp"
                favicon_temp.write_bytes(favicon_data)
                os.chmod(favicon_temp, 0o644)
                favicon_temp.replace(favicon_target)
                settings_data = _node_operations_settings()
                settings_data["favicon_filename"] = favicon_name
                OPERATIONS_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                OPERATIONS_SETTINGS_FILE.write_text(json.dumps(settings_data, separators=(",", ":")), encoding="utf-8")
            self.panel_users = {
                item.username: item.model_copy(update={"password_hash": "", "auth_version": ""})
                for item in payload.users
            }
            self.save_panel_users()
            # STREAMFORGE_NODE_PANEL_USER_SYNC_STREAM_USER_IMMUTABLE_V127:
            # Panel credential sync is not a playlist-user writer. Test & Sync
            # can hit a stale control worker here, so reload but never persist it.
            self.reload_users_if_changed()
            self.panel_last_check = 0.0
        return {"ok": True, "users": len(self.panel_users), "node": self.node_name, "independent_mode": self.independent_mode}

    def recover_node_logo_from_main(self) -> bool:
        logo = self.node_logo_url.strip()
        if not logo.startswith("/node-logos/") or not self.panel_url:
            return False
        filename = Path(logo[len("/node-logos/"):]).name
        if not filename or filename != logo[len("/node-logos/"):]:
            return False
        target = NODE_LOGO_ROOT / filename
        if target.is_file():
            return True
        try:
            request = urllib.request.Request(
                f"{self.panel_url.rstrip('/')}/node-logos/{urllib.parse.quote(filename, safe='')}",
                headers={"Accept": "image/*", "User-Agent": f"StreamForge-Node/{VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=10.0) as response:
                data = response.read(2 * 1024 * 1024 + 1)
            if not data or len(data) > 2 * 1024 * 1024:
                return False
            NODE_LOGO_ROOT.mkdir(parents=True, exist_ok=True)
            temporary = NODE_LOGO_ROOT / f".{filename}.{secrets.token_hex(4)}.tmp"
            temporary.write_bytes(data)
            os.chmod(temporary, 0o644)
            temporary.replace(target)
            return True
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return False

    def save(self) -> None:
        payload = self._state_payload()
        if self.independent_mode:
            payload = self._prepare_independent_active_payload(payload, self._read_state(SHARED_STATE_FILE))
            self._write_state(STATE_FILE, payload)
            self._write_state(INDEPENDENT_STATE_FILE, self._select_owner(payload, "local"))
            self._write_state(SHARED_STATE_FILE, self._select_owner(payload, "main"))
        else:
            payload = self._prepare_shared_payload(payload)
            self._write_state(STATE_FILE, payload)
            self._write_state(INDEPENDENT_STATE_FILE, self._select_owner(payload, "local"))
            self._write_state(SHARED_STATE_FILE, self._select_owner(payload, "main"))

    def _channel_supervisor_rpc(self, payload: dict[str, Any], *, timeout: float = 12.0) -> dict[str, Any]:
        if not NODE_DEDICATED_CHANNEL_SUPERVISOR:
            raise RuntimeError("Dedicated channel supervisor is disabled")
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(max(0.2, float(timeout)))
        try:
            sock.connect(str(NODE_CHANNEL_SUPERVISOR_SOCKET))
            sock.sendall(encoded)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise RuntimeError("Channel supervisor response is too large")
                if b"\n" in chunk:
                    break
            raw = b"".join(chunks).split(b"\n", 1)[0]
            response = json.loads(raw.decode("utf-8") or "{}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Channel supervisor unavailable: {exc}") from exc
        finally:
            sock.close()
        if not isinstance(response, dict):
            raise RuntimeError("Invalid channel supervisor response")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Channel supervisor command failed"))
        result = response.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def channel_supervisor_ping(self, *, timeout: float = 0.6) -> bool:
        if not NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return False
        try:
            result = self._channel_supervisor_rpc({"action": "ping"}, timeout=timeout)
            return bool(result.get("ready"))
        except RuntimeError:
            return False

    def _supervisor_pid_alive(self) -> bool:
        try:
            pid = int(NODE_CHANNEL_SUPERVISOR_PID_FILE.read_text(encoding="utf-8").strip())
            if pid <= 1:
                return False
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def ensure_channel_supervisor(self) -> bool:
        if NODE_MODE != "control" or not NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return False
        if self.channel_supervisor_ping():
            return True
        with self._channel_supervisor_spawn_lock:
            if self.channel_supervisor_ping():
                return True
            # An already-spawned daemon may still be importing the application.
            if self._supervisor_pid_alive():
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if self.channel_supervisor_ping(timeout=0.4):
                        return True
                    time.sleep(0.2)
            try:
                NODE_CHANNEL_SUPERVISOR_SOCKET.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                NODE_CHANNEL_SUPERVISOR_PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            NODE_CHANNEL_SUPERVISOR_LOG.parent.mkdir(parents=True, exist_ok=True)
            try:
                log_handle = NODE_CHANNEL_SUPERVISOR_LOG.open("ab", buffering=0)
                env = os.environ.copy()
                env["STREAMFORGE_NODE_MODE"] = "supervisor"
                env["PYTHONUNBUFFERED"] = "1"
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--channel-supervisor"],
                    cwd=str(Path(__file__).resolve().parent), env=env,
                    stdin=subprocess.DEVNULL, stdout=log_handle, stderr=log_handle,
                    close_fds=True, start_new_session=True,
                )
                self.channel_supervisor_process = process
                self.channel_supervisor_log_handle = log_handle
            except Exception as exc:
                try:
                    log_handle.close()  # type: ignore[name-defined]
                except Exception:
                    pass
                raise RuntimeError(f"Could not start channel supervisor: {exc}") from exc
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self.channel_supervisor_ping(timeout=0.5):
                    self.log(
                        "Dedicated channel supervisor ready",
                        scope="channel", details=f"pid={process.pid}; socket={NODE_CHANNEL_SUPERVISOR_SOCKET}",
                    )
                    return True
                time.sleep(0.2)
            rc = process.poll()
            raise RuntimeError(f"Channel supervisor did not become ready (rc={rc})")

    def watch_channel_supervisor(self) -> None:
        if NODE_MODE != "control" or not NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return
        while True:
            time.sleep(2.0)
            if _NODE_UPDATE_RESTART_ARMED.is_set():
                continue
            if self.channel_supervisor_ping(timeout=0.5):
                continue
            try:
                self.ensure_channel_supervisor()
                self.reload_channel_catalog_if_changed(force=True)
            except Exception as exc:
                self.log("Channel supervisor recovery failed", scope="channel", level="error", details=str(exc))
                time.sleep(3.0)

    def _supervisor_statuses(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        if NODE_CHANNEL_OWNER:
            with self.lock:
                keys = list(self.channels)
            return {key: self.status(key) for key in keys}
        now = time.monotonic()
        with self._channel_supervisor_status_lock:
            if not force and now - self._channel_supervisor_status_cache_at <= 0.5:
                return {key: dict(value) for key, value in self._channel_supervisor_status_cache.items()}
            result = self._channel_supervisor_rpc({"action": "statuses"}, timeout=3.0)
            channels = result.get("channels") if isinstance(result, dict) else {}
            parsed = {
                str(key): dict(value)
                for key, value in (channels or {}).items()
                if isinstance(value, dict)
            }
            self._channel_supervisor_status_cache = parsed
            self._channel_supervisor_status_cache_at = time.monotonic()
            return {key: dict(value) for key, value in parsed.items()}

    def _invalidate_supervisor_status_cache(self) -> None:
        with self._channel_supervisor_status_lock:
            self._channel_supervisor_status_cache_at = 0.0
            self._channel_supervisor_status_cache = {}

    def _delegate_channel_command(
        self, action: str, key: str = "", config: ChannelConfig | None = None,
        *, restart_running: bool = True, target_version: str = "", timeout: float = 15.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": action}
        if key:
            payload["key"] = key
        if config is not None:
            payload["config"] = config.model_dump()
        if action == "sync":
            payload["restart_running"] = bool(restart_running)
        if action == "prepare_update":
            payload["target_version"] = str(target_version or "")
        result = self._channel_supervisor_rpc(payload, timeout=timeout)
        self._invalidate_supervisor_status_cache()
        try:
            self.reload_channel_catalog_if_changed(force=True)
        except Exception:
            pass
        return result

    @staticmethod
    def encoders() -> set[str]:
        try:
            result = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
        except Exception:
            return set()
        found = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) >= 6:
                found.add(parts[1])
        return found

    @staticmethod
    def render_nodes() -> list[Path]:
        return sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []

    @staticmethod
    def has_nvidia() -> bool:
        return Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()

    @staticmethod
    def ffmpeg_for_codec(codec: str) -> str:
        # STREAMFORGE_NODE_NVENC_DEDICATED_FFMPEG_V1117: no concurrency cap is
        # applied. When present, the dedicated binary is used only by explicit
        # NVIDIA codecs; all existing Copy/CPU/AUTO paths keep the system FFmpeg.
        if str(codec or "").lower() == "h264_nvenc":
            candidate = str(NVENC_FFMPEG or "").strip()
            if candidate and (shutil.which(candidate) or Path(candidate).exists()):
                return candidate
        return FFMPEG

    def probe_encoder(self, codec: str, ffmpeg_bin: str | None = None) -> bool:
        binary = ffmpeg_bin or self.ffmpeg_for_codec(codec)
        cache_key = (binary, codec)
        if cache_key in self.encoder_probe_cache:
            return self.encoder_probe_cache[cache_key]
        cmd = [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if codec.endswith("_qsv"):
            nodes = self.render_nodes()
            if not nodes:
                return False
            cmd += ["-qsv_device", str(nodes[0])]
        elif codec.endswith("_vaapi"):
            node = Path(VAAPI_DEVICE)
            if not node.exists():
                nodes = self.render_nodes()
                if not nodes:
                    return False
                node = nodes[0]
            cmd += ["-vaapi_device", str(node)]
        cmd += ["-f", "lavfi", "-i", "color=c=black:s=128x72:r=25:d=0.08", "-frames:v", "1"]
        if codec.endswith("_vaapi"):
            cmd += ["-vf", "format=nv12,hwupload"]
        elif codec.endswith("_qsv"):
            cmd += ["-vf", "format=nv12"]
        cmd += ["-c:v", codec, "-f", "null", "-"]
        try:
            ok = subprocess.run(cmd, capture_output=True, timeout=8).returncode == 0
        except Exception:
            ok = False
        self.encoder_probe_cache[cache_key] = ok
        return ok

    def auto_encoder(self, family: str) -> str:
        family = "h265" if family in {"h265", "hevc"} else "h264"
        if family in self.encoder_cache:
            return self.encoder_cache[family]
        available = self.encoders()
        ordered = (
            ["hevc_nvenc", "hevc_qsv", "hevc_vaapi", "libx265", "hevc"]
            if family == "h265"
            else ["h264_nvenc", "h264_qsv", "h264_vaapi", "libx264", "h264"]
        )
        for codec in ordered:
            if codec not in available:
                continue
            if codec.endswith("_nvenc") and not self.has_nvidia():
                continue
            if codec.endswith(("_qsv", "_vaapi")) and not self.render_nodes():
                continue
            if self.probe_encoder(codec):
                self.encoder_cache[family] = codec
                return codec
        raise RuntimeError(f"No working {family} encoder")

    @staticmethod
    def input_args(
        url: str, *, youtube_live: bool = False, reconnect_http_errors: str = "4xx,5xx",
        live_start_index: int | None = None, reconnect_delay_max: int = 10,
        reconnect_at_eof: bool = False,
    ) -> tuple[list[str], str]:
        raw = url.strip()
        headers: list[str] = []
        if "|" in raw:
            raw, header_text = raw.split("|", 1)
            for pair in header_text.split("&"):
                if "=" not in pair:
                    continue
                key, value = pair.split("=", 1)
                from urllib.parse import unquote_plus
                key, value = unquote_plus(key).strip(), unquote_plus(value).strip()
                if key.lower() == "user-agent":
                    headers += ["-user_agent", value]
                elif key.lower() == "referer":
                    headers += ["-referer", value]
                elif key.lower() == "cookie":
                    headers += ["-cookies", value]
                elif key and value:
                    headers += ["-headers", f"{key}: {value}\r\n"]
        if raw.lower().startswith(("http://", "https://")):
            parts = urllib.parse.urlsplit(raw)
            lowered = (parts.path + "?" + parts.query).lower()
            hls_http_input = ".m3u8" in lowered or "format=hls" in lowered
            headers += [
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
            ]
            if str(reconnect_http_errors or "").strip():
                headers += ["-reconnect_on_http_error", str(reconnect_http_errors).strip()]
            # STREAMFORGE_NODE_HTTP_EOF_NON_HLS_ONLY_V1145:
            # EOF reconnect is valid for raw HTTP streams, but an HLS manifest
            # normally reaches EOF on every reload. Never loop the HTTP protocol
            # on that normal playlist EOF; let the HLS demuxer fetch segments.
            if reconnect_at_eof and not hls_http_input:
                headers += ["-reconnect_at_eof", "1"]
            headers += [
                "-reconnect_delay_max", str(max(1, min(600, int(reconnect_delay_max or 10)))),
                "-rw_timeout", "15000000",
            ]
            # STREAMFORGE_NODE_YOUTUBE_HLS_INPUT_PARITY_V1015:
            # Match the Main HLS input options for resolved YouTube manifests.
            if hls_http_input:
                # STREAMFORGE_NODE_YOUTUBE_LIVE_EDGE_V1017:
                # Keep ordinary HLS at -3 for compatibility, but resolved YouTube
                # Live starts one upstream segment behind the edge to cut ~10s.
                start_index = int(live_start_index) if live_start_index is not None else (-1 if youtube_live else -3)
                headers += ["-live_start_index", str(start_index), "-allowed_extensions", "ALL", "-http_persistent", "1"]
        return headers, raw

    @staticmethod
    def is_hls_http_input(url: str) -> bool:
        raw = str(url or "").strip().split("|", 1)[0].strip()
        parts = urllib.parse.urlsplit(raw)
        if parts.scheme.lower() not in {"http", "https"}:
            return False
        lowered = (parts.path + "?" + parts.query).lower()
        return ".m3u8" in lowered or "format=hls" in lowered

    def build_command(self, cfg: ChannelConfig) -> list[str]:
        inputs = self.config_inputs(cfg)
        if not inputs:
            raise RuntimeError("No input URL is configured")
        index = max(0, min(int(cfg.active_input_index or 0), len(inputs) - 1))
        source_target = inputs[index]
        youtube_source = is_youtube_url(source_target)
        resolved_target = _guarded_resolve_stream_source(source_target, force=youtube_source)
        if youtube_source:
            self.log("YouTube source resolved", scope="channel", key=cfg.key, details=source_target)
        # STREAMFORGE_NODE_LOCAL_RELAY_404_BACKOFF_V1037: historical relay
        # retry guard retained; v11.4 replaces the old long-exit backoff with
        # bounded in-process 404/5xx recovery.
        # STREAMFORGE_NODE_LOCAL_RELAY_RECOVERY_V114: Main relay is a trusted
        # live HLS source. A 404 can be a one-segment rotation race, so reconnect
        # 404/5xx in-process instead of exiting into a long retry timer. Start one
        # segment from the live edge so a 1s relay has maximum deletion headroom.
        relay_mode = str(cfg.input_mode or "").strip().lower() == "local_relay"
        inp_args, input_url = self.input_args(
            resolved_target, youtube_live=youtube_source,
            reconnect_http_errors="404,5xx" if relay_mode else "4xx,5xx",
            live_start_index=-1 if relay_mode else None,
            # STREAMFORGE_NODE_LOCAL_RELAY_STICKY_RECONNECT_V1141:
            # A running Main may transiently return 404/503 during HLS file
            # rotation or a very short recovery. Keep this FFmpeg process alive
            # instead of resetting Node channel uptime every ~10 seconds.
            reconnect_delay_max=RELAY_RECONNECT_DELAY_MAX_SECONDS if relay_mode else 10,
            # STREAMFORGE_NODE_LOCAL_RELAY_HLS_EOF_FIX_V1145:
            # An HLS manifest is a finite HTTP response: EOF after reading the
            # current index.m3u8 is normal and is how the HLS demuxer proceeds to
            # media segments. reconnect_at_eof on a Local-relay manifest makes
            # FFmpeg reopen the same playlist byte offset forever and prevents
            # the first .ts/.m4s segment from being loaded. Keep network/404/5xx
            # reconnects, but never treat normal HLS playlist EOF as a failure.
            reconnect_at_eof=False,
        )
        requested = (cfg.video_codec or "auto").strip().lower()
        youtube_low_latency_transcode = youtube_source and cfg.output_type == "hls"
        if youtube_low_latency_transcode and requested == "copy":
            requested = "auto_h264"
        # Explicit NVENC profiles get their own compatible binary. AUTO remains
        # on the system FFmpeg so this change cannot silently alter old profiles.
        ffmpeg_bin = self.ffmpeg_for_codec(requested)
        if not shutil.which(ffmpeg_bin) and not Path(ffmpeg_bin).exists():
            raise RuntimeError(f"FFmpeg not found: {ffmpeg_bin}")
        if requested.endswith("_nvenc"):
            if not self.has_nvidia():
                raise RuntimeError("NVIDIA NVENC profile selected but no NVIDIA device is available")
            if not self.probe_encoder(requested, ffmpeg_bin):
                raise RuntimeError(f"NVIDIA encoder is not usable with {ffmpeg_bin}: {requested}")
        cmd = [ffmpeg_bin, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y", "-fflags", "+genpts+discardcorrupt", "-thread_queue_size", "1024"]
        cmd += inp_args
        # STREAMFORGE_NODE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK: protect Node restreams
        # from re-amplifying upstream/Main HLS segment bursts. Keep ffprobe paths
        # unpaced; this applies only to the long-running FFmpeg channel process.
        # STREAMFORGE_NODE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016:
        # Historical note: v10.16 excluded YouTube from -re for catch-up.
        # STREAMFORGE_NODE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021:
        # Production traces show YouTube arrives in completed ~5s HLS chunks.
        # Without pacing, FFmpeg publishes several 1s local segments at once and
        # then leaves a dry gap. Pace all long-running HLS inputs at media time.
        # STREAMFORGE_NODE_LOCAL_RELAY_CATCHUP_V114: -re prevents a relay
        # consumer from ever catching up after a short pause; with 1s segments
        # that can push it past the retained window and create a false 404/exit.
        # Main relay is already live-paced, so let only Local-relay FFmpeg catch
        # up faster than realtime until it reaches the live edge.
        if self.is_hls_http_input(input_url) and not relay_mode:
            cmd += ["-re"]
        cmd += ["-i", input_url]
        program_ids = self.config_program_ids(cfg)
        active_program_id = program_ids[index] if index < len(program_ids) else None
        if active_program_id:
            cmd += ["-map", f"0:p:{active_program_id}:v:0?", "-map", f"0:p:{active_program_id}:a:0?"]
        else:
            cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]

        # STREAMFORGE_NODE_YOUTUBE_TRUE_1S_GOP_TRANSCODE_V1022:
        # YouTube copy-mode HLS commonly has ~5s source GOPs.  A true 1s local HLS
        # cadence needs a newly encoded keyframe every second, so only YouTube HLS
        # copy-mode channels are promoted to the existing auto H.264 encoder path.
        if youtube_low_latency_transcode and (cfg.video_codec or "copy").strip().lower() == "copy":
            self.log(
                "YouTube low-latency H.264 transcode enabled",
                scope="channel",
                key=cfg.key,
                details="copy input upgraded to hardware-aware H.264 with 1s forced keyframes",
            )
        if requested == "copy":
            cmd += ["-c:v", "copy"]
        else:
            manual_video_codecs = {
                "libx264", "libx265", "h264_nvenc", "hevc_nvenc",
                "h264_qsv", "hevc_qsv", "h264_vaapi", "hevc_vaapi",
            }
            if requested in {"auto", "auto_h264"}:
                codec = self.auto_encoder("h264")
            elif requested in {"auto_hevc", "auto_h265"}:
                codec = self.auto_encoder("h265")
            elif requested in manual_video_codecs:
                codec = requested
            else:
                raise RuntimeError(f"Unsupported video codec: {requested}")
            filters: list[str] = []
            if cfg.width and cfg.height:
                filters.append(f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease,pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2")
            if cfg.fps:
                filters.append(f"fps={cfg.fps}")
            if codec.endswith("_vaapi"):
                render_nodes = self.render_nodes()
                if Path(VAAPI_DEVICE).exists():
                    node = VAAPI_DEVICE
                elif render_nodes:
                    node = str(render_nodes[0])
                else:
                    raise RuntimeError("VAAPI encoder selected but no render device is available")
                filters += ["format=nv12", "hwupload"]
                cmd += ["-vaapi_device", node, "-vf", ",".join(filters), "-c:v", codec, "-b:v", cfg.video_bitrate, "-bf", "0"]
            elif codec.endswith("_qsv"):
                filters += ["format=nv12"]
                cmd += ["-vf", ",".join(filters), "-c:v", codec, "-b:v", cfg.video_bitrate, "-preset", "veryfast", "-look_ahead", "0", "-bf", "0"]
            elif codec.endswith("_nvenc"):
                nvenc_preset = "p4" if requested == "h264_nvenc" else "p1"
                cmd += ["-c:v", codec, "-b:v", cfg.video_bitrate, "-preset", nvenc_preset, "-tune", "ll", "-rc", "cbr", "-bf", "0", "-pix_fmt", "yuv420p"]
                if filters:
                    cmd += ["-filter_threads", "1", "-vf", ",".join(filters)]
            else:
                cmd += ["-c:v", codec, "-b:v", cfg.video_bitrate, "-preset", cfg.preset or "ultrafast", "-tune", "zerolatency", "-bf", "0", "-threads:v", str(CPU_THREADS), "-pix_fmt", "yuv420p"]
                if filters:
                    cmd += ["-filter_threads", "1", "-vf", ",".join(filters)]
            if codec in {"libx265", "hevc_nvenc", "hevc_qsv", "hevc_vaapi"}:
                cmd += ["-tag:v", "hvc1"]
            if cfg.output_type == "hls":
                fps = cfg.fps or 25
                seg = 1 if youtube_source else max(1, cfg.hls_segment_time)
                cmd += ["-g", str(max(24, fps * seg)), "-force_key_frames", f"expr:gte(t,n_forced*{seg})"]

        audio_map = {
            "auto": "aac", "aac": "aac", "ac3": "ac3", "eac3": "eac3", "mp2": "mp2",
            "libmp3lame": "libmp3lame", "libopus": "libopus", "flac": "flac",
            "libvorbis": "libvorbis", "pcm_s16le": "pcm_s16le",
        }
        if cfg.audio_codec == "copy":
            cmd += ["-c:a", "copy"]
        else:
            acodec = audio_map.get(cfg.audio_codec, "aac")
            cmd += ["-c:a", acodec, "-ac", "2", "-ar", "48000", "-threads:a", "1"]
            if acodec not in {"flac", "pcm_s16le"}:
                cmd += ["-b:a", cfg.audio_bitrate]
        cmd += ["-max_muxing_queue_size", "2048", "-progress", "pipe:1", "-stats_period", str(PROGRESS_INTERVAL), "-nostats"]

        # STREAMFORGE_NODE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020:
        # STREAMFORGE_NODE_YOUTUBE_TRUE_1S_GOP_OUTPUT_V1022:
        # v10.22 keeps independently decodable output while promoting YouTube HLS
        # video=copy to hardware-aware H.264 with a forced 1s keyframe cadence.
        # This avoids both unsafe mid-GOP slicing and the old ~5s publish cadence.
        local_hls_time = 1 if youtube_source else max(1, cfg.hls_segment_time)
        local_hls_flags = "delete_segments+independent_segments+program_date_time+temp_file+omit_endlist"
        local_hls_list_size = 8 if youtube_source else 6
        # STREAMFORGE_NODE_HLS_BROWSER_SEGMENT_GRACE_V1167:
        # Browser HLS may legally sit 12-25 seconds behind the live edge while
        # its buffer drains/rebuilds. Keeping only ~8 seconds of unreferenced
        # segments let a still-online channel delete a fragment before HLS.js
        # fetched it, producing 404/network-fatal recovery and a visible reconnect.
        # Retain ~30 seconds of segments that have fallen out of the playlist.
        # This changes disk retention only; the playlist size/live-edge latency
        # remains unchanged.
        local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)

        remote_outputs = _remote_output_urls(cfg.output_url)
        if cfg.output_type == "hls" and not remote_outputs:
            out = HLS_ROOT / cfg.key
            out.mkdir(parents=True, exist_ok=True)
            for old in out.iterdir():
                if old.is_file():
                    old.unlink(missing_ok=True)
            cmd += [
                "-f", "hls", "-hls_segment_type", "mpegts", "-hls_time", str(local_hls_time),
                "-hls_list_size", str(local_hls_list_size), "-hls_delete_threshold", str(local_hls_delete_threshold), "-hls_allow_cache", "0",
                "-hls_start_number_source", "epoch", "-hls_flags", local_hls_flags,
                "-hls_segment_filename", str(out / "segment_%06d.ts"), str(out / "index.m3u8"),
            ]
        elif cfg.output_type in {"hls", "udp", "srt", "http_post", "http_put"}:
            if cfg.output_type != "hls" and not remote_outputs:
                raise RuntimeError("At least one remote output URL is required")
            slaves: list[str] = []
            if cfg.output_type == "hls":
                out = HLS_ROOT / cfg.key
                out.mkdir(parents=True, exist_ok=True)
                for old in out.iterdir():
                    if old.is_file():
                        old.unlink(missing_ok=True)
                seg = _tee_escape_filename(str(out / "segment_%06d.ts"))
                playlist = _tee_escape_filename(str(out / "index.m3u8"))
                slaves.append(
                    f"[f=hls:hls_segment_type=mpegts:hls_time={local_hls_time}:"
                    f"hls_list_size={local_hls_list_size}:hls_delete_threshold={local_hls_delete_threshold}:hls_allow_cache=0:"
                    "hls_start_number_source=epoch:"
                    f"hls_flags={local_hls_flags}:"
                    f"hls_segment_filename={seg}]" + playlist
                )
            for index, url in enumerate(remote_outputs):
                slaves.append(_remote_tee_slave(
                    url,
                    http_put=cfg.output_type == "http_put",
                    onfail_ignore=(cfg.output_type == "hls" or index > 0),
                ))
            # STREAMFORGE_NODE_MULTI_REMOTE_OUTPUT_TEE_V54
            cmd += ["-f", "tee", "-use_fifo", "1", "|".join(slaves)]
        else:
            raise RuntimeError(f"Unsupported output type: {cfg.output_type}")
        return cmd

    @staticmethod
    def process_write_counter(pid: int) -> int | None:
        try:
            values: dict[str, int] = {}
            for line in Path(f"/proc/{pid}/io").read_text().splitlines():
                key, value = line.split(":", 1)
                values[key.strip()] = int(value.strip())
            return values.get("wchar") or values.get("write_bytes")
        except (OSError, ValueError):
            return None

    @staticmethod
    def relay_playlist_health(url: str) -> tuple[bool, str]:
        """Lightweight Main-relay availability check used only after a long stall."""
        # STREAMFORGE_NODE_RELAY_STALL_HEALTH_V111: this is intentionally a
        # small manifest GET, not ffprobe. During a Main outage hundreds of
        # channels can be reconnecting, so each channel probes at most once per
        # RELAY_HEALTH_PROBE_INTERVAL_SECONDS.
        target = str(url or "").strip().split("|", 1)[0].strip()
        try:
            parts = urllib.parse.urlsplit(target)
        except Exception:
            return False, "invalid relay URL"
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return False, "relay URL is not HTTP"
        request = urllib.request.Request(
            target,
            headers={
                "User-Agent": "StreamForge-Node-RelayHealth/11.1",
                "Cache-Control": "no-cache",
                "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*;q=0.1",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                status = int(getattr(response, "status", 200) or 200)
                sample = response.read(4096)
            if 200 <= status < 300 and b"#EXTM3U" in sample:
                # STREAMFORGE_NODE_RELAY_FRESHNESS_V114: HTTP 200 alone can be
                # a stale playlist. When PROGRAM-DATE-TIME is present, require
                # the newest advertised segment clock to be recent before the
                # watchdog declares Main healthy and kills a reconnecting FFmpeg.
                text_sample = sample.decode("utf-8", errors="replace")
                pdt_values = [
                    line.split(":", 1)[1].strip()
                    for line in text_sample.splitlines()
                    if line.startswith("#EXT-X-PROGRAM-DATE-TIME:") and ":" in line
                ]
                if pdt_values:
                    try:
                        latest = datetime.fromisoformat(pdt_values[-1].replace("Z", "+00:00"))
                        if latest.tzinfo is None:
                            latest = latest.replace(tzinfo=timezone.utc)
                        age = max(0.0, (datetime.now(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds())
                        if age > 20.0:
                            return False, f"HTTP {status} stale HLS ({int(age)}s)"
                    except (TypeError, ValueError):
                        pass
                return True, f"HTTP {status} fresh HLS"
            return False, f"HTTP {status} without HLS manifest"
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {int(exc.code or 0)}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return False, str(reason or exc)[:160]
        except Exception as exc:
            return False, str(exc)[:160]

    def probe_input(self, url: str) -> bool:
        ffprobe = shutil.which("ffprobe") or "/usr/bin/ffprobe"
        youtube_source = is_youtube_url(url)
        try:
            resolved = _guarded_resolve_stream_source(url, force=youtube_source)
        except SourceResolveError:
            return False
        args, target = self.input_args(resolved, youtube_live=youtube_source)
        # YouTube resolution can succeed quickly while the signed HLS manifest
        # still needs more time to expose streams than a normal direct input.
        cmd = [ffprobe, "-v", "error", "-analyzeduration", "2000000", "-probesize", "4000000"] + args + ["-i", target, "-show_entries", "stream=index", "-of", "csv=p=0"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20 if youtube_source else 5, check=False)
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def probe_runtime_metadata(
        self,
        key: str,
        process: subprocess.Popen[str],
        target: str,
        program_id: int | None,
    ) -> None:
        """Cache source resolution/FPS once per channel start."""
        result = probe_node_source(target, timeout=12)
        if not result.get("ok"):
            return
        streams = list(result.get("streams") or [])
        preferred = [
            item for item in streams
            if item.get("type") == "video"
            and program_id is not None
            and item.get("program_id") == program_id
        ]
        video = next(iter(preferred), None) or next(
            (item for item in streams if item.get("type") == "video"), None
        )
        if not video:
            return
        try:
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        try:
            fps = float(video.get("fps") or 0.0)
        except (TypeError, ValueError):
            fps = 0.0
        with self.lock:
            rt = self.channels.get(key)
            if not rt or rt.process is not process or process.poll() is not None:
                return
            if width > 0 and height > 0:
                rt.live_width = width
                rt.live_height = height
            if fps > 0:
                rt.live_fps = fps

    def watch_failback(self, key: str, process: subprocess.Popen[str]) -> None:
        while True:
            with self.lock:
                rt = self.channels.get(key)
                if not rt or rt.process is not process or process.poll() is not None:
                    return
                if not rt.config.failback_enabled or rt.active_input_index <= 0:
                    return
                interval = max(10, int(rt.config.failback_interval or 30))
                inputs = self.config_inputs(rt.config)
                primary = inputs[0] if inputs else ""
            time.sleep(interval)
            with self.lock:
                rt = self.channels.get(key)
                if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:
                    return
            if primary and self.probe_input(primary):
                with self.lock:
                    rt = self.channels.get(key)
                    if not rt:
                        return
                    rt.active_input_index = 0
                    rt.config.active_input_index = 0
                self.log("Primary input recovered; returning to source #1", scope="channel", key=key)
                self.restart(key, rt.config)
                return

    def start(self, key: str, config: ChannelConfig) -> dict[str, Any]:
        key = self.safe_key(key)
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return self._delegate_channel_command("start", key, config, timeout=15.0)
        if key != config.key:
            raise ValueError("Channel key mismatch")
        with self.lock:
            rt = self.channels.get(key) or Runtime(config)
            self.channels[key] = rt
            rt.config = config
            rt.active_input_index = max(0, int(config.active_input_index or rt.active_input_index or 0))
            rt.config.active_input_index = rt.active_input_index
            rt.intentional_stop = False
            rt.desired_running = True
            if rt.restart_timer:
                rt.restart_timer.cancel()
                rt.restart_timer = None
            if rt.process and rt.process.poll() is None:
                return self.status(key)
            try:
                cmd = self.build_command(config)
                # STREAMFORGE_NODE_SUPERVISOR_DURABLE_FFMPEG_IO_V115:
                # A supervisor crash/reload must never break FFmpeg by closing an
                # anonymous progress pipe. Progress stdout goes to /dev/null and
                # runtime activity is sampled from /proc + HLS freshness; stderr
                # uses a durable per-channel file for post-failure diagnostics.
                if NODE_MODE == "supervisor" and NODE_DEDICATED_CHANNEL_SUPERVISOR:
                    NODE_CHANNEL_SUPERVISOR_IO_ROOT.mkdir(parents=True, exist_ok=True)
                    error_path = NODE_CHANNEL_SUPERVISOR_IO_ROOT / f"{key}.stderr.log"
                    error_path.write_bytes(b"")
                    error_handle = error_path.open("ab", buffering=0)
                    try:
                        rt.process = subprocess.Popen(
                            cmd, stdout=subprocess.DEVNULL, stderr=error_handle,
                            stdin=subprocess.DEVNULL, close_fds=True, start_new_session=True,
                        )
                    except Exception:
                        error_handle.close()
                        raise
                    rt.error_log_path = error_path
                    rt.error_log_handle = error_handle
                else:
                    rt.process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, bufsize=1, start_new_session=True,
                    )
            except Exception as exc:
                rt.status = "error"
                rt.last_error = str(exc)
                self.save()
                raise
            rt.status = "starting"
            rt.last_error = None
            rt.bitrate_kbps = 0
            rt.speed_x = 0.0
            rt.live_width = 0
            rt.live_height = 0
            rt.live_fps = 0.0
            rt.started_at = time.monotonic()
            rt.updated_at = rt.started_at
            rt.relay_health_checked_at = 0.0
            rt.relay_health_ready = False
            rt.relay_health_detail = ""
            rt.relay_health_log_at = 0.0
            if rt.waiting_since <= 0:
                rt.waiting_since = rt.started_at
            process = rt.process
            inputs = self.config_inputs(config)
            active_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1)) if inputs else 0
            active_target = resolve_stream_source(inputs[active_index] if inputs else config.input_url)
            program_ids = self.config_program_ids(config)
            active_program_id = program_ids[active_index] if active_index < len(program_ids) else None
        if NODE_MODE == "supervisor" and NODE_DEDICATED_CHANNEL_SUPERVISOR and rt.error_log_path:
            threading.Thread(
                target=self.watch_adopted_progress, args=(key, process),
                name=f"streamforge-supervisor-progress-{key}", daemon=True,
            ).start()
            threading.Thread(
                target=self.read_errors_file, args=(key, process, rt.error_log_path), daemon=True
            ).start()
        else:
            threading.Thread(target=self.read_progress, args=(key, process), daemon=True).start()
            threading.Thread(target=self.read_errors, args=(key, process), daemon=True).start()
        threading.Thread(
            target=self.probe_runtime_metadata,
            args=(key, process, active_target, active_program_id),
            daemon=True,
        ).start()
        threading.Thread(target=self.wait_process, args=(key, process), daemon=True).start()
        if config.auto_restart:
            threading.Thread(target=self.watch_stall, args=(key, process), daemon=True).start()
        if config.failback_enabled and rt.active_input_index > 0:
            threading.Thread(target=self.watch_failback, args=(key, process), daemon=True).start()
        self.log(f"Channel started on input #{rt.active_input_index + 1}", scope="channel", key=key)
        self.save()
        if NODE_CHANNEL_OWNER:
            self.persist_channel_supervisor_runtime()
        return self.status(key)

    def watch_stall(self, key: str, process: subprocess.Popen[str]) -> None:
        while True:
            time.sleep(5)
            relay_probe_url = ""
            with self.lock:
                rt = self.channels.get(key)
                if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:
                    return
                now = time.monotonic()
                stall_seconds = STALL_SECONDS
                inputs = self.config_inputs(rt.config)
                active_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1)) if inputs else 0
                # STREAMFORGE_NODE_YOUTUBE_LIVE_STALL_TOLERANCE_V1016:
                # Allow a short YouTube CDN/proxy recovery window before restart.
                if inputs and is_youtube_url(inputs[active_index]):
                    stall_seconds = max(STALL_SECONDS, 60)
                if not rt.config.auto_restart or now - rt.updated_at <= stall_seconds:
                    continue
                # STREAMFORGE_NODE_STALL_GUARD_HLS_FRESHNESS_V1167:
                # Progress-pipe silence is not enough to restart FFmpeg when the
                # shared HLS playlist/segment are still fresh.  This also protects
                # adopted/supervisor-owned FFmpeg processes across worker reloads.
                output_type = str(rt.config.output_type or "hls").strip().lower()
                if output_type == "hls":
                    ready, fresh_hls = self._hls_output_state(key, int(rt.config.hls_segment_time or 1))
                    if ready and fresh_hls:
                        rt.updated_at = now
                        rt.status = "running"
                        if str(rt.last_error or "").startswith("No FFmpeg progress for"):
                            rt.last_error = None
                        continue
                relay_mode = str(rt.config.input_mode or "").strip().lower() == "local_relay"
                if relay_mode:
                    # STREAMFORGE_NODE_RELAY_STALL_HEALTH_V111: relay outages
                    # are upstream delivery failures, not a stuck local FFmpeg.
                    # Let FFmpeg's reconnect logic ride through the grace window.
                    elapsed = now - rt.updated_at
                    if elapsed < RELAY_STALL_GRACE_SECONDS:
                        continue
                    if not inputs:
                        continue
                    if now - rt.relay_health_checked_at < RELAY_HEALTH_PROBE_INTERVAL_SECONDS:
                        # The last probe still says Main relay is unavailable, so
                        # a local process restart would only add recovery backoff.
                        if not rt.relay_health_ready:
                            continue
                    else:
                        relay_probe_url = inputs[active_index]
                if not relay_mode:
                    rt.last_error = f"No FFmpeg progress for {stall_seconds} seconds"
                    self.log(rt.last_error, scope="channel", level="warning", key=key)
            if relay_probe_url:
                relay_ready, relay_detail = self.relay_playlist_health(relay_probe_url)
                with self.lock:
                    rt = self.channels.get(key)
                    if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:
                        return
                    now = time.monotonic()
                    # Progress may have resumed while the health request was in flight.
                    if now - rt.updated_at <= STALL_SECONDS:
                        rt.relay_health_checked_at = now
                        rt.relay_health_ready = relay_ready
                        rt.relay_health_detail = relay_detail
                        continue
                    rt.relay_health_checked_at = now
                    rt.relay_health_ready = relay_ready
                    rt.relay_health_detail = relay_detail
                    if not relay_ready:
                        if now - rt.relay_health_log_at >= 60.0:
                            rt.relay_health_log_at = now
                            self.log(
                                "Main relay unavailable; keeping FFmpeg reconnect alive",
                                scope="channel", level="warning", key=key,
                                details=relay_detail,
                            )
                        continue
                    rt.last_error = (
                        f"No FFmpeg progress for {int(now - rt.updated_at)} seconds "
                        f"while Main relay is healthy ({relay_detail})"
                    )
                    self.log(rt.last_error, scope="channel", level="warning", key=key)
            elif str(getattr(rt.config, "input_mode", "") or "").strip().lower() == "local_relay":
                # A recent healthy probe plus continued silence means the local
                # FFmpeg failed to recover even though its source is available.
                with self.lock:
                    rt = self.channels.get(key)
                    if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:
                        return
                    if not rt.relay_health_ready:
                        continue
                    rt.last_error = (
                        f"No FFmpeg progress for {int(time.monotonic() - rt.updated_at)} seconds "
                        f"while Main relay is healthy ({rt.relay_health_detail or 'ready'})"
                    )
                    self.log(rt.last_error, scope="channel", level="warning", key=key)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
            return

    def read_errors_file(self, key: str, process: subprocess.Popen[Any], path: Path) -> None:
        recent: deque[str] = deque(maxlen=25)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                while True:
                    raw = handle.readline()
                    if not raw:
                        if process.poll() is not None:
                            break
                        time.sleep(0.2)
                        continue
                    line = raw.strip()
                    if not line:
                        continue
                    recent.append(line)
                    if "Video:" in line:
                        dimension = VIDEO_DIMENSION_RE.search(line)
                        fps_match = VIDEO_FPS_RE.search(line)
                        if dimension or fps_match:
                            with self.lock:
                                rt = self.channels.get(key)
                                if not rt or rt.process is not process:
                                    return
                                if dimension:
                                    rt.live_width = int(dimension.group(1))
                                    rt.live_height = int(dimension.group(2))
                                if fps_match:
                                    try:
                                        rt.live_fps = max(rt.live_fps, float(fps_match.group(1)))
                                    except ValueError:
                                        pass
        except OSError as exc:
            self.log("FFmpeg stderr file reader failed", scope="channel", level="warning", key=key, details=str(exc))
        if recent:
            with self.lock:
                rt = self.channels.get(key)
                if rt and rt.process is process:
                    rt.last_error = "\n".join(recent)[-4000:]
                    self.log("FFmpeg error output", scope="channel", level="error", key=key, details=rt.last_error)

    def read_progress(self, key: str, process: subprocess.Popen[str]) -> None:
        if not process.stdout:
            return
        pending_bitrate: int | None = None
        pending_speed: float | None = None
        pending_fps: float | None = None
        size_samples: deque[tuple[float, int]] = deque(maxlen=8)
        for raw in process.stdout:
            line = raw.strip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name == "bitrate":
                m = BITRATE_RE.search(value)
                if m:
                    pending_bitrate = int(float(m.group(1)))
            elif name == "speed":
                m = SPEED_RE.search(value)
                if m:
                    pending_speed = float(m.group(1))
            elif name == "fps":
                try:
                    pending_fps = max(0.0, float(value))
                except ValueError:
                    pending_fps = None
            elif name == "progress":
                now = time.monotonic()
                counter = self.process_write_counter(process.pid)
                calculated_bitrate: int | None = None
                if counter is not None:
                    if size_samples and counter < size_samples[-1][1]:
                        size_samples.clear()
                    size_samples.append((now, counter))
                    while len(size_samples) > 2 and now - size_samples[0][0] > 12.0:
                        size_samples.popleft()
                    if len(size_samples) >= 2:
                        first_time, first_size = size_samples[0]
                        elapsed = now - first_time
                        delta = counter - first_size
                        if elapsed >= 0.5 and delta >= 0:
                            calculated_bitrate = int(round(delta * 8 / elapsed / 1000))
                with self.lock:
                    rt = self.channels.get(key)
                    if not rt or rt.process is not process:
                        return
                    sample = calculated_bitrate if calculated_bitrate and calculated_bitrate > 0 else pending_bitrate
                    if sample is not None:
                        rt.bitrate_kbps = sample
                    if pending_speed is not None:
                        rt.speed_x = pending_speed
                    if pending_fps is not None and pending_fps > 0:
                        rt.live_fps = pending_fps
                    rt.updated_at = now
                    if value == "continue":
                        rt.status = "running"
                        # STREAMFORGE_NODE_RESTART_BACKOFF_STABLE_RESET_V1037:
                        # keep escalating retries during a missing relay, but
                        # forgive old failures after a genuinely stable run.
                        stable_reset_seconds = 15.0 if str(rt.config.input_mode or "").strip().lower() == "local_relay" else 30.0
                        if rt.restart_attempt and now - rt.started_at >= stable_reset_seconds:
                            rt.restart_attempt = 0
                pending_bitrate = None
                pending_speed = None
                pending_fps = None

    def read_errors(self, key: str, process: subprocess.Popen[str]) -> None:
        if not process.stderr:
            return
        recent: deque[str] = deque(maxlen=25)
        for raw in process.stderr:
            line = raw.strip()
            if not line:
                continue
            recent.append(line)
            if "Video:" in line:
                dimension = VIDEO_DIMENSION_RE.search(line)
                fps_match = VIDEO_FPS_RE.search(line)
                if dimension or fps_match:
                    with self.lock:
                        rt = self.channels.get(key)
                        if not rt or rt.process is not process:
                            return
                        if dimension:
                            rt.live_width = int(dimension.group(1))
                            rt.live_height = int(dimension.group(2))
                        if fps_match:
                            try:
                                rt.live_fps = max(rt.live_fps, float(fps_match.group(1)))
                            except ValueError:
                                pass
        if recent:
            with self.lock:
                rt = self.channels.get(key)
                if rt and rt.process is process:
                    rt.last_error = "\n".join(recent)[-4000:]
                    self.log("FFmpeg error output", scope="channel", level="error", key=key, details=rt.last_error)

    @staticmethod
    def clear_hls_output(key: str) -> None:
        """Remove stale HLS files whenever the native channel process goes down.

        STREAMFORGE_NODE_HLS_CLEAN_ON_DOWN_V63R3: Public gateway workers can
        inspect shared HLS storage, so old media must not look like a live
        channel after the control process has stopped or exited.
        """
        try:
            safe_key = AgentManager.safe_key(key)
            out = HLS_ROOT / safe_key
            if out.exists():
                shutil.rmtree(out, ignore_errors=True)
        except Exception:
            return

    # STREAMFORGE_NODE_RELAY_FAST_RECOVERY_V114: Local-relay exits use a short
    # capped retry ladder; a recovered Main must never leave a Node dark for 5m.
    def wait_process(self, key: str, process: subprocess.Popen[str]) -> None:
        code = process.wait()
        restart = False
        with self.lock:
            completed_rt = self.channels.get(key)
            if completed_rt and completed_rt.process is process:
                for handle_name in ("error_log_handle",):
                    handle = getattr(completed_rt, handle_name, None)
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception:
                            pass
                        setattr(completed_rt, handle_name, None)
        with self.lock:
            rt = self.channels.get(key)
            if not rt or rt.process is not process:
                return
            rt.process = None
            rt.bitrate_kbps = 0
            rt.speed_x = 0.0
            restart = bool(rt.desired_running and not rt.intentional_stop and rt.config.enabled and rt.config.auto_restart)
            rt.status = "restarting" if restart else ("stopped" if rt.intentional_stop or code in {0, -15, -2} else "error")
            # STREAMFORGE_NODE_FFMPEG_EXIT_REASON_PRESERVE_V121:
            # Unexpected live-process exit code 0 is still a failure when
            # desired_running remains true. Surface it instead of leaving an
            # empty error while the auto-restart loop runs.
            if restart and not rt.last_error:
                rt.last_error = f"FFmpeg exited with code {code}"
            if restart:
                inputs = self.config_inputs(rt.config)
                if len(inputs) > 1:
                    rt.active_input_index = (rt.active_input_index + 1) % len(inputs)
                    rt.config.active_input_index = rt.active_input_index
                    self.log(f"Input failed; switching to source #{rt.active_input_index + 1}", scope="channel", level="warning", key=key)
                rt.restart_attempt += 1
                if str(rt.config.input_mode or "").strip().lower() == "local_relay":
                    # STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147:
                    # The next timer performs a manifest health check before any
                    # new FFmpeg/ffprobe process is created.
                    delay = RELAY_RECOVERY_PROBE_SECONDS
                else:
                    delay = [3, 5, 10, 20, 30, 60][min(rt.restart_attempt, 6) - 1]
                rt.restart_timer = threading.Timer(delay, self._recover, args=(key,))
                rt.restart_timer.daemon = True
                rt.restart_timer.start()
        self.clear_hls_output(key)
        self.save()

    def _recover(self, key: str) -> None:
        relay_probe_url = ""
        relay_mode = False
        with self.lock:
            rt = self.channels.get(key)
            if not rt or rt.intentional_stop:
                return
            # STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147:
            # The Timer invoking this recovery has fired. Clear its handle so a
            # successful start or a newly scheduled health wait owns the slot.
            rt.restart_timer = None
            config = rt.config
            relay_mode = str(config.input_mode or "").strip().lower() == "local_relay"
            if relay_mode:
                inputs = self.config_inputs(config)
                if inputs:
                    active_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1))
                    relay_probe_url = inputs[active_index]

        # STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147:
        # After an actual Local-relay FFmpeg exit, keep the channel Waiting and
        # probe only the small Main manifest. Do not respawn FFmpeg/ffprobe until
        # the relay is really back. This collapses the old process-spawn storm
        # into one lightweight request every ~15 seconds.
        if relay_mode and relay_probe_url:
            relay_ready, relay_detail = self.relay_playlist_health(relay_probe_url)
            now = time.monotonic()
            should_log = False
            with self.lock:
                rt = self.channels.get(key)
                if not rt or rt.intentional_stop or not rt.desired_running or not rt.config.enabled:
                    return
                rt.relay_health_checked_at = now
                rt.relay_health_ready = relay_ready
                rt.relay_health_detail = relay_detail
                if not relay_ready:
                    rt.status = "waiting"
                    if rt.waiting_since <= 0:
                        rt.waiting_since = now
                    if now - rt.relay_health_log_at >= 60.0:
                        rt.relay_health_log_at = now
                        should_log = True
                    timer = threading.Timer(RELAY_RECOVERY_PROBE_SECONDS, self._recover, args=(key,))
                    timer.daemon = True
                    rt.restart_timer = timer
                else:
                    timer = None
            if not relay_ready:
                if should_log:
                    self.log(
                        "Main relay unavailable; automatic FFmpeg respawn deferred",
                        scope="channel", level="warning", key=key, details=relay_detail,
                    )
                self.save()
                timer.start()
                return

        try:
            # STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052:
            # Synchronized retry timers share the same automatic-start budget.
            self._paced_automatic_start(key, config)
        except Exception:
            with self.lock:
                rt = self.channels.get(key)
                if rt and not rt.intentional_stop:
                    rt.status = "restarting"
                    rt.restart_attempt += 1
                    if str(rt.config.input_mode or "").strip().lower() == "local_relay":
                        delay = RELAY_RECOVERY_PROBE_SECONDS
                    else:
                        delay = [3, 5, 10, 20, 30, 60][min(rt.restart_attempt, 6) - 1]
                    rt.restart_timer = threading.Timer(delay, self._recover, args=(key,))
                    rt.restart_timer.daemon = True
                    rt.restart_timer.start()

    def watch_desired_channels(self) -> None:
        # STREAMFORGE_NODE_DESIRED_WATCHDOG_V33:
        # wait_process handles normal FFmpeg exits. This supervisor is a second
        # safety net for any desired-running channel whose process disappears or
        # whose recovery timer is lost. Manual Stop sets desired_running=False
        # and intentional_stop=True, so stopped channels are never resurrected.
        while True:
            time.sleep(5)
            candidates: list[tuple[str, ChannelConfig]] = []
            with self.lock:
                for key, rt in self.channels.items():
                    process_alive = bool(rt.process and rt.process.poll() is None)
                    if (
                        rt.desired_running
                        and not rt.intentional_stop
                        and rt.config.enabled
                        and rt.config.auto_restart
                        and key not in self._startup_pending
                        and not process_alive
                        and rt.restart_timer is None
                    ):
                        rt.status = "restarting"
                        candidates.append((key, rt.config))
            for candidate_index, (key, config) in enumerate(candidates):
                try:
                    if str(config.input_mode or "").strip().lower() == "local_relay":
                        # STREAMFORGE_NODE_RELAY_WATCHDOG_HEALTH_GATE_V1147:
                        # Missing Local-relay processes must pass the same relay
                        # health gate as normal exit recovery. Reserve a timer
                        # handle first so the 5s watchdog cannot queue duplicates.
                        with self.lock:
                            rt = self.channels.get(key)
                            if not rt or rt.intentional_stop or rt.restart_timer is not None:
                                continue
                            gate_delay = max(0.01, float(candidate_index) / float(self._automatic_starts_per_second))
                            timer = threading.Timer(gate_delay, self._recover, args=(key,))
                            timer.daemon = True
                            rt.restart_timer = timer
                        timer.start()
                        continue
                    # STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052:
                    # A watchdog sweep may find many missing channels together;
                    # repair them through the same bounded automatic-start path.
                    self._paced_automatic_start(key, config)
                    self.log("Desired-running watchdog restored channel", scope="channel", key=key)
                except Exception as exc:
                    with self.lock:
                        rt = self.channels.get(key)
                        if rt and rt.desired_running and not rt.intentional_stop:
                            rt.status = "restarting"
                            rt.last_error = str(exc)[-4000:]
                    self.log(
                        "Desired-running watchdog restart failed", scope="channel",
                        level="warning", key=key, details=str(exc),
                    )
            if candidates:
                self.save()

    # STREAMFORGE_NODE_BULK_CONFIG_APPLY_V1127:
    def sync_channels_bulk(self, configs: list[ChannelConfig]) -> dict[str, Any]:
        """Apply Main-shared channel configs without touching FFmpeg state.

        The control worker forwards one RPC to the dedicated channel supervisor.
        The supervisor updates every Runtime in memory and persists state once,
        avoiding one HTTP request + one state-file rewrite per channel.
        """
        if len(configs) > 500:
            raise ValueError("Bulk channel sync is limited to 500 channels")
        normalized: dict[str, ChannelConfig] = {}
        for raw in configs:
            key = self.safe_key(str(raw.key or ""))
            config = self.normalize_channel_config(raw.model_copy(update={"key": key, "catalog_owner": "main"}))
            normalized[key] = config
        if not normalized:
            return {"ok": True, "synced": 0, "restarted": 0}

        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            result = self._channel_supervisor_rpc(
                {
                    "action": "bulk_sync",
                    "channels": [config.model_dump() for config in normalized.values()],
                },
                timeout=max(20.0, min(60.0, 12.0 + (len(normalized) * 0.08))),
            )
            self.load_access()
            self.load_users()
            self.reload_channel_catalog_if_changed(force=True)
            self._invalidate_supervisor_status_cache()
            return result

        with self.lock:
            for key, config in normalized.items():
                rt = self.channels.get(key)
                if rt is None:
                    self.channels[key] = Runtime(config)
                    continue
                running = bool(rt.process and rt.process.poll() is None)
                rt.config = config
                inputs = self.config_inputs(config)
                if inputs:
                    rt.active_input_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1))
                    rt.config.active_input_index = rt.active_input_index
                else:
                    rt.active_input_index = 0
                    rt.config.active_input_index = 0
                if not running and not rt.desired_running:
                    rt.last_error = None
        self.save()
        self.log(
            "Main-shared bulk channel configurations synchronized without process restart",
            scope="channel", details=f"channels={len(normalized)}",
        )
        return {"ok": True, "synced": len(normalized), "restarted": 0, "process_control": "node_local"}

    def sync_channel(self, key: str, config: ChannelConfig, *, restart_running: bool = True) -> dict[str, Any]:
        key = self.safe_key(key)
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return self._delegate_channel_command(
                "sync", key, config, restart_running=restart_running, timeout=18.0
            )
        config.key = key
        config = self.normalize_channel_config(config)
        with self.lock:
            rt = self.channels.get(key)
            should_restart = bool(rt and (rt.desired_running or (rt.process and rt.process.poll() is None)))

        # STREAMFORGE_NODE_CONFIG_SYNC_NO_RESTART_V33:
        # Main-shared synchronization passes restart_running=False. In that
        # mode config metadata is updated without touching this Node's process
        # or desired state. Node-local edits keep the historical behavior and
        # may restart a running local channel so their settings apply at once.
        if not restart_running:
            with self.lock:
                rt = self.channels.get(key)
                if rt is None:
                    rt = Runtime(config)
                    self.channels[key] = rt
                    running = False
                else:
                    running = bool(rt.process and rt.process.poll() is None)
                    rt.config = config
                    inputs = self.config_inputs(config)
                    if inputs:
                        rt.active_input_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1))
                        rt.config.active_input_index = rt.active_input_index
                    else:
                        rt.active_input_index = 0
                        rt.config.active_input_index = 0
                    if not running and not rt.desired_running:
                        rt.last_error = None
            self.save()
            self.log(
                "Main-shared channel configuration synchronized without process restart",
                scope="channel", key=key,
            )
            result = self.status(key)
            result.update({
                "config_synced": True,
                "restarted": False,
                "restart_required": bool(running),
                "process_control": "node_local",
            })
            return result

        if rt and not config.enabled:
            if should_restart:
                self.stop(key)
            with self.lock:
                rt = self.channels.get(key)
                if rt:
                    rt.config = config
                    rt.active_input_index = max(0, int(config.active_input_index or 0))
                    rt.desired_running = False
                    rt.intentional_stop = True
                    rt.last_error = None
            self.save()
            result = self.status(key)
            result.update({"config_synced": True, "restarted": False, "disabled": True})
            self.log("Channel configuration synchronized and disabled", scope="channel", key=key)
            return result
        with self.lock:
            rt = self.channels.get(key)
            if rt and not should_restart:
                rt.config = config
                rt.active_input_index = max(0, int(config.active_input_index or 0))
                rt.last_error = None
            elif not rt:
                self.channels[key] = Runtime(config)
        if should_restart:
            result = self.restart(key, config)
            result["config_synced"] = True
            result["restarted"] = True
            self.log("Channel configuration synchronized and restarted", scope="channel", key=key)
            return result
        self.save()
        self.log("Channel configuration synchronized", scope="channel", key=key)
        result = self.status(key)
        result["config_synced"] = True
        result["restarted"] = False
        return result

    def stop(self, key: str) -> dict[str, Any]:
        key = self.safe_key(key)
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return self._delegate_channel_command("stop", key, timeout=15.0)
        with self.lock:
            rt = self.channels.get(key)
            if not rt:
                return {"status": "stopped", "alive": False, "bitrate_kbps": 0, "speed_x": 0.0}
            rt.intentional_stop = True
            rt.desired_running = False
            if rt.restart_timer:
                rt.restart_timer.cancel()
                rt.restart_timer = None
            process = rt.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
        with self.lock:
            rt = self.channels.get(key)
            if rt:
                rt.process = None
                rt.status = "stopped"
                rt.bitrate_kbps = 0
                rt.speed_x = 0.0
        self.clear_hls_output(key)
        self.log("Channel stopped", scope="channel", key=key)
        self.save()
        if NODE_CHANNEL_OWNER:
            self.persist_channel_supervisor_runtime()
        return self.status(key)

    def restart(self, key: str, config: ChannelConfig) -> dict[str, Any]:
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            return self._delegate_channel_command("restart", self.safe_key(key), config, timeout=22.0)
        self.stop(key)
        return self.start(key, config)

    def delete(self, key: str) -> None:
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            self._delegate_channel_command("delete", self.safe_key(key), timeout=15.0)
            return
        self.stop(key)
        with self.lock:
            self.channels.pop(key, None)
        out = HLS_ROOT / key
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        self.save()

    @staticmethod
    def _hls_output_state(key: str, segment_time: int = 1) -> tuple[bool, bool]:
        """Return ``(ready, fresh)`` for an HLS output on shared storage."""
        playlist = HLS_ROOT / key / "index.m3u8"
        if not playlist.is_file():
            return False, False
        try:
            lines = playlist.read_text(errors="replace").splitlines()
            segments = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
            if not segments:
                return False, False
            segment = playlist.parent / Path(segments[-1]).name
            if not segment.is_file():
                return False, False
            latest_mtime = max(playlist.stat().st_mtime, segment.stat().st_mtime)
            freshness_window = max(20, max(1, int(segment_time or 1)) * 8)
            # STREAMFORGE_NODE_CLOCK_SAFE_HLS_FRESHNESS_V121:
            # A wall-clock step backwards must not make an old future-mtime HLS
            # output look fresh for minutes. Active 1s HLS output immediately
            # writes a new current-time segment, so bounding both directions is
            # safe and self-correcting.
            age = time.time() - latest_mtime
            return True, (-freshness_window <= age <= freshness_window)
        except OSError:
            return False, False

    def status(self, key: str) -> dict[str, Any]:
        key = self.safe_key(key)
        if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:
            try:
                return dict(self._supervisor_statuses().get(key) or {
                    "managed": False, "alive": False, "status": "missing",
                    "bitrate_kbps": 0, "speed_x": 0.0, "uptime_seconds": 0, "hls_ready": False,
                })
            except RuntimeError:
                # Keep Panel/API readable while the control watcher respawns the
                # supervisor.  The filesystem fallback never starts/kills FFmpeg.
                pass
        if not NODE_CHANNEL_OWNER:
            self.reload_channel_catalog_if_changed()

        # STREAMFORGE_NODE_STATUS_LOCK_MINIMIZE_V1066:
        # Snapshot mutable runtime fields under the shared lock, then release it
        # before process.poll() and HLS playlist/segment filesystem reads.
        with self.lock:
            rt = self.channels.get(key)
            if rt is not None:
                process = rt.process
                config = rt.config
                runtime_status = rt.status
                desired_running = bool(rt.desired_running)
                updated_at = float(rt.updated_at)
                started_at = float(rt.started_at)
                waiting_since = float(rt.waiting_since)
                last_error = rt.last_error
                bitrate_kbps = rt.bitrate_kbps
                speed_x = rt.speed_x
                live_width = rt.live_width
                live_height = rt.live_height
                live_fps = rt.live_fps
                active_input_index = rt.active_input_index

        if rt is None:
            ready, fresh_hls = self._hls_output_state(key)
            external_alive = bool(not NODE_CHANNEL_OWNER and ready and fresh_hls)
            return {
                "managed": external_alive, "alive": external_alive,
                "status": "running" if external_alive else "stopped", "pid": None,
                "bitrate_kbps": 0, "speed_x": 0.0, "uptime_seconds": 0, "waiting_seconds": 0,
                "last_error": None, "hls_ready": external_alive,
                "metrics_fresh": False,
            }

        local_alive = bool(process and process.poll() is None)
        ready, fresh_hls = self._hls_output_state(key, int(config.hls_segment_time or 1))
        external_alive = bool(not NODE_CHANNEL_OWNER and ready and fresh_hls)
        alive = bool(local_alive or external_alive)
        now_mono = time.monotonic()
        fresh = local_alive and now_mono - updated_at <= max(10, PROGRESS_INTERVAL * 4)
        delivery_ready = bool(alive and (ready and fresh_hls if config.output_type == "hls" else True))
        delivery_waiting = bool(
            not delivery_ready and (
                desired_running or alive or str(runtime_status or "").lower() in {"running", "starting", "restarting", "degraded"}
            )
        )
        if delivery_waiting:
            if waiting_since <= 0:
                waiting_since = now_mono
                with self.lock:
                    current = self.channels.get(key)
                    if current is rt and current.waiting_since <= 0:
                        current.waiting_since = waiting_since
            waiting_seconds = int(max(0.0, now_mono - waiting_since))
        else:
            waiting_seconds = 0
            if waiting_since > 0:
                with self.lock:
                    current = self.channels.get(key)
                    if current is rt:
                        current.waiting_since = 0.0

        width = live_width or config.width
        height = live_height or config.height
        return {
            "managed": True, "alive": alive,
            "status": runtime_status if local_alive else ("running" if external_alive else runtime_status),
            "pid": process.pid if local_alive else None,
            "bitrate_kbps": bitrate_kbps if fresh else 0,
            "speed_x": round(speed_x, 2) if fresh else 0.0,
            "uptime_seconds": int(now_mono - started_at) if local_alive else 0,
            "waiting_seconds": waiting_seconds,
            "last_error": last_error, "hls_ready": bool(ready and fresh_hls and alive),
            "metrics_fresh": fresh,
            "width": width, "height": height,
            "fps": round(live_fps, 2) if live_fps > 0 else config.fps,
            "resolution": f"{width} Ã— {height}" if width and height else "Source",
            "active_input_index": active_input_index,
            "input_count": len(self.config_inputs(config)),
            "desired_running": desired_running,
        }

    @staticmethod
    def _cpu_counters() -> tuple[int, int]:
        # STREAMFORGE_NODE_REAL_CPU_PROC_STAT_V1148: aggregate all logical CPUs
        # exactly like Main system_metrics.  idle includes iowait, so heavy HLS
        # filesystem/network wait remains visible as Load instead of fake CPU busy.
        try:
            fields = [int(value) for value in Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines()[0].split()[1:]]
            if len(fields) < 4:
                return 0, 0
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            return sum(fields), idle
        except (OSError, ValueError, IndexError):
            return 0, 0

    @staticmethod
    def cpu_memory() -> dict[str, Any]:
        try:
            fields = Path("/proc/loadavg").read_text().split()
            load1 = float(fields[0])
            cores = os.cpu_count() or 1
        except Exception:
            load1, cores = 0.0, os.cpu_count() or 1
        mem_total = mem_available = 0
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, rest = line.split(":", 1)
                values[key] = int(rest.split()[0]) * 1024
            mem_total = values.get("MemTotal", 0)
            mem_available = values.get("MemAvailable", values.get("MemFree", 0))
        except Exception:
            pass
        used = max(0, mem_total - mem_available)
        # STREAMFORGE_NODE_DASHBOARD_DISK_USAGE_V96: report the filesystem
        # that actually hosts Node HLS output, not an unrelated mount.
        disk_probe = HLS_ROOT
        while not disk_probe.exists() and disk_probe != disk_probe.parent:
            disk_probe = disk_probe.parent
        try:
            disk_usage = shutil.disk_usage(disk_probe)
            disk_total = max(0, int(disk_usage.total))
            disk_used = max(0, int(disk_usage.used))
            disk_free = max(0, int(disk_usage.free))
        except OSError:
            disk_total = disk_used = disk_free = 0
        return {
            "cpu_cores": cores, "load_1": round(load1, 2),
            "memory_percent": round((used / mem_total * 100) if mem_total else 0, 1),
            "memory_used_bytes": used, "memory_total_bytes": mem_total,
            "disk_percent": round((disk_used / disk_total * 100) if disk_total else 0, 1),
            "disk_used_bytes": disk_used, "disk_free_bytes": disk_free, "disk_total_bytes": disk_total,
            "disk_path": str(disk_probe),
            "uptime_seconds": int(float(Path("/proc/uptime").read_text().split()[0])) if Path("/proc/uptime").exists() else 0,
        }

    @staticmethod
    def _network_counters() -> dict[str, tuple[int, int]]:
        counters: dict[str, tuple[int, int]] = {}
        try:
            for line in Path("/proc/net/dev").read_text(encoding="utf-8", errors="replace").splitlines()[2:]:
                if ":" not in line:
                    continue
                raw_name, values = line.split(":", 1)
                name = raw_name.strip()
                if not name or name == "lo":
                    continue
                parts = values.split()
                if len(parts) < 9:
                    continue
                counters[name] = (max(0, int(parts[0])), max(0, int(parts[8])))
        except (OSError, ValueError):
            pass
        return counters

    @staticmethod
    def _default_route_interfaces() -> set[str]:
        interfaces: set[str] = set()
        try:
            for line in Path("/proc/net/route").read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 0x1:
                    interfaces.add(fields[0])
        except (OSError, ValueError):
            pass
        try:
            for line in Path("/proc/net/ipv6_route").read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.split()
                if len(fields) >= 10 and fields[0] == "0" * 32 and fields[1] == "00":
                    interfaces.add(fields[-1])
        except OSError:
            pass
        return interfaces

    @staticmethod
    def _select_network_interfaces(
        deltas: dict[str, tuple[int, int]], default_routes: set[str], previous: list[str]
    ) -> list[str]:
        # Prefer the host's default-route NIC when it is carrying traffic. If
        # streaming uses a dedicated NIC/bridge instead, choose the interface
        # with the largest current byte delta rather than the largest lifetime
        # counter (which may belong to an old or dormant device).
        routed = [name for name in sorted(default_routes) if name in deltas]
        active_routed = [name for name in routed if sum(deltas[name]) > 0]
        best_name = ""
        best_total = 0
        if deltas:
            best_name, best_delta = max(deltas.items(), key=lambda item: sum(item[1]))
            best_total = sum(best_delta)
        routed_total = sum(sum(deltas[name]) for name in active_routed)
        if active_routed:
            # Tiny heartbeat/DNS traffic on the default route must not hide a
            # dedicated high-volume streaming NIC. Keep the routed NIC for
            # equal/duplicated bridge traffic, but switch when another device
            # is carrying substantially more current bytes.
            if best_name not in active_routed and best_total > max(65_536, routed_total * 1.5):
                return [best_name]
            return active_routed
        if best_name and best_total > 0:
            return [best_name]
        preserved = [name for name in previous if name in deltas]
        if preserved:
            return preserved
        return routed[:1] or (sorted(deltas)[:1] if deltas else [])

    def metrics(self) -> dict[str, Any]:
        data = self.cpu_memory()
        now = time.monotonic()
        cpu_total, cpu_idle = self._cpu_counters()
        counters = self._network_counters()
        with self.metrics_lock:
            # STREAMFORGE_NODE_REAL_CPU_PROC_STAT_V1148: real busy-time delta.
            # Reuse the cached value for closely spaced status/heartbeat calls so
            # no request sleeps and no second caller destroys the sampling window.
            cpu_elapsed = max(0.0, now - self.last_cpu_sample_at)
            previous_total, previous_idle = self.last_cpu
            if cpu_total > 0 and previous_total > 0 and cpu_elapsed >= 0.25:
                total_delta = cpu_total - previous_total
                idle_delta = cpu_idle - previous_idle
                if total_delta > 0:
                    busy_delta = max(0, total_delta - max(0, idle_delta))
                    self.cached_cpu_percent = max(0.0, min(100.0, busy_delta / total_delta * 100.0))
                self.last_cpu = (cpu_total, cpu_idle)
                self.last_cpu_sample_at = now
            elif cpu_total > 0 and previous_total <= 0:
                self.last_cpu = (cpu_total, cpu_idle)
                self.last_cpu_sample_at = now
            data["cpu_percent"] = round(self.cached_cpu_percent, 1)

            if self.last_net is None:
                self.last_net = (now, counters)
                selected = self._select_network_interfaces(
                    {name: (0, 0) for name in counters},
                    self._default_route_interfaces(),
                    [],
                )
                self.cached_network_metrics["network_interfaces"] = selected
                self.cached_network_metrics["network_interface"] = ", ".join(selected)
            else:
                previous_time, previous_counters = self.last_net
                elapsed = max(0.0, now - previous_time)
                # Do not move the baseline for closely spaced heartbeat/status
                # calls. The next caller receives the same valid cached rate.
                if elapsed >= 0.75:
                    deltas: dict[str, tuple[int, int]] = {}
                    for name, (rx, tx) in counters.items():
                        old_rx, old_tx = previous_counters.get(name, (rx, tx))
                        deltas[name] = (max(0, rx - old_rx), max(0, tx - old_tx))
                    previous_selected = list(self.cached_network_metrics.get("network_interfaces") or [])
                    selected = self._select_network_interfaces(
                        deltas,
                        self._default_route_interfaces(),
                        previous_selected,
                    )
                    rx_delta = sum(deltas.get(name, (0, 0))[0] for name in selected)
                    tx_delta = sum(deltas.get(name, (0, 0))[1] for name in selected)
                    self.cached_network_metrics = {
                        "network_interface": ", ".join(selected),
                        "network_interfaces": selected,
                        "network_download_mbps": round(rx_delta * 8.0 / elapsed / 1_000_000, 2),
                        "network_upload_mbps": round(tx_delta * 8.0 / elapsed / 1_000_000, 2),
                        "network_sample_seconds": round(elapsed, 3),
                    }
                    self.last_net = (now, counters)
            data.update(dict(self.cached_network_metrics))
        # STREAMFORGE_NODE_GPU_METRICS_CACHE_V1069: never probe GPU in HTTP routes.
        data["gpu"] = _node_gpu_metrics.snapshot()
        return data

    @staticmethod
    def _client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        # STREAMFORGE_NODE_MEDIA_AUTH_CLIENT_IP_V67: the Nginx auth_request
        # subrequest must keep its transport peer loopback so the internal
        # endpoint remains private. Carry the original viewer IP in a dedicated
        # header instead of X-Forwarded-For (which Uvicorn's proxy middleware
        # rewrites into request.client and makes the internal scope look public).
        if _internal_media_auth_scope(request.scope):
            # STREAMFORGE_NODE_MULTI_URL_MEDIA_CLIENT_IP_V68:
            # Alternate Playlist/App aliases may sit behind a reverse proxy/CDN
            # while another alias reaches this Nginx directly.  Keep media-auth
            # client identity consistent with the original public request by
            # preferring the incoming forwarded chain captured by Nginx.
            internal_forwarded = request.headers.get("x-streamforge-forwarded-client-ip", "").split(",", 1)[0].strip()
            internal_client = internal_forwarded or request.headers.get("x-streamforge-client-ip", "").strip()
            if internal_client:
                try:
                    return str(ipaddress.ip_address(internal_client))
                except ValueError:
                    return internal_client
        try:
            peer_address = ipaddress.ip_address(peer)
            trust_forwarded = peer_address.is_loopback
        except ValueError:
            trust_forwarded = False
        forwarded = request.headers.get("x-forwarded-for", "") if trust_forwarded else ""
        candidate = forwarded.split(",", 1)[0].strip() if forwarded else peer
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return candidate

    def sync_users(self, payload: UserSyncPayload) -> dict[str, Any]:
        # STREAMFORGE_NODE_TEST_SYNC_LOCAL_USER_AUTHORITATIVE_V125:
        # Since Node-local playlist accounts are authoritative on the Node, a
        # Main Test & Sync is metadata-only for this registry. v12.3 still rebuilt
        # users.json from whichever control worker received /users/sync; with two
        # Gunicorn control workers that snapshot could predate a just-created
        # account and silently delete it. Always reload the shared file and never
        # rewrite it from a Main registry sync.
        self.panel_url = payload.panel_url.strip().rstrip("/")
        self.panel_node_slug = payload.node_slug.strip()
        self.reload_users_if_changed()
        with self.lock:
            local_users = {
                token: item for token, item in self.users.items()
                if str(item.source or "").strip().lower() == "node_local"
            }
            local_names = {
                str(item.username or "").strip().lower()
                for item in local_users.values() if str(item.username or "").strip()
            }
            skipped_conflicts = sum(
                1 for item in payload.users
                if str(item.username or "").strip().lower() in local_names
            )
            self.users = local_users
            self._save_panel_state_only()
        self.panel_last_check = 0.0
        return {
            "ok": True, "users": len(local_users), "panel_url": self.panel_url,
            "node_slug": self.panel_node_slug, "independent_mode": self.independent_mode,
            "local_users_preserved": len(local_users), "username_conflicts_skipped": skipped_conflicts,
            "main_registry_ignored": True,
        }

    def _publish_panel_connectivity(self, ok: bool) -> None:
        """Publish the control worker's last Main heartbeat result atomically."""
        payload = {
            "ok": bool(ok),
            "checked_at": time.time(),
            "node_slug": str(self.panel_node_slug or ""),
            "panel_url": str(self.panel_url or ""),
            "writer_pid": os.getpid(),
            "version": VERSION,
        }
        try:
            PANEL_CONNECTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self._panel_connectivity_write_lock:
                temporary = PANEL_CONNECTIVITY_FILE.with_name(
                    f"{PANEL_CONNECTIVITY_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                os.replace(temporary, PANEL_CONNECTIVITY_FILE)
        except OSError:
            # Heartbeat truth still remains in memory for the control worker. A
            # failed local-state write must not turn a Main heartbeat into a
            # foreground playback exception.
            pass

    def _shared_panel_connected(self) -> bool:
        """Read control-worker connectivity without any Main/network request."""
        now_mono = time.monotonic()
        if now_mono - self._panel_shared_last_check < PANEL_CONNECTIVITY_READ_CACHE_SECONDS:
            return self._panel_shared_last_ok
        self._panel_shared_last_check = now_mono
        ok = False
        try:
            stat = PANEL_CONNECTIVITY_FILE.stat()
            data = json.loads(PANEL_CONNECTIVITY_FILE.read_text(encoding="utf-8"))
            checked_at = float(data.get("checked_at") or 0.0)
            # Prefer the newest trustworthy local timestamp. A small negative
            # age is tolerated for normal NTP corrections; a large future stamp
            # is rejected so a clock jump cannot keep an old 'connected' state
            # alive indefinitely.
            state_epoch = max(checked_at, float(stat.st_mtime))
            age = time.time() - state_epoch
            same_node = (
                not str(self.panel_node_slug or "").strip()
                or not str(data.get("node_slug") or "").strip()
                or str(data.get("node_slug") or "").strip() == str(self.panel_node_slug or "").strip()
            )
            ok = bool(data.get("ok")) and same_node and (-5.0 <= age <= float(PANEL_CONNECTIVITY_STALE_SECONDS))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            ok = False
        self._panel_shared_last_ok = ok
        return ok

    def panel_connected(self, *, force: bool = False) -> bool:
        # STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210:
        # 8821 may have many Gunicorn workers. Letting each one run the old
        # four-second Main heartbeat made channel startup randomly block for
        # seconds. Public mode is therefore strictly read-only for connectivity.
        if NODE_MODE == "public":
            return self._shared_panel_connected()

        now = time.monotonic()
        if not force and now - self.panel_last_check < PANEL_CACHE_SECONDS:
            return self.panel_last_ok
        self.panel_last_check = now
        if not self.panel_url or not self.panel_node_slug or not TOKEN:
            self.panel_last_ok = False
            self._publish_panel_connectivity(False)
            return False
        url = f"{self.panel_url}/api/v1/node-heartbeat/{urllib.parse.quote(self.panel_node_slug)}"
        heartbeat_metrics = self.metrics()
        # STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6:
        # Send only Main-owned delivery counts so Main's Nodes card can show
        # true per-Node Up/Down state without a second status request.  Node-local
        # catalogue channels are intentionally excluded from Main-assigned totals.
        heartbeat_delivery = _node_card_delivery_counts(owner="main")
        heartbeat_headers = {
            "X-Node-Token": TOKEN,
            "User-Agent": f"StreamForge-Node/{VERSION}",
            "X-Node-CPU-Percent": str(heartbeat_metrics.get("cpu_percent", 0)),
            "X-Node-Memory-Percent": str(heartbeat_metrics.get("memory_percent", 0)),
            "X-Node-Download-Mbps": str(heartbeat_metrics.get("network_download_mbps", 0)),
            "X-Node-Upload-Mbps": str(heartbeat_metrics.get("network_upload_mbps", 0)),
            "X-Node-Uptime-Seconds": str(heartbeat_metrics.get("uptime_seconds", 0)),
            "X-Node-Up-Channels": str(int(heartbeat_delivery.get("up", 0))),
            "X-Node-Waiting-Channels": str(int(heartbeat_delivery.get("waiting", 0))),
            "X-Node-Down-Channels": str(int(heartbeat_delivery.get("down", 0))),
            "X-Node-Active-Connections": str(int(self.total_active_connections())),
        }
        request = urllib.request.Request(url, headers=heartbeat_headers)
        try:
            with urllib.request.urlopen(request, timeout=4.0) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
                self.panel_last_ok = bool(response.status == 200 and data.get("ok"))
                if self.panel_last_ok:
                    self.apply_main_node_name(data.get("node_name"))
                if self.panel_last_ok and str(data.get("panel_url") or "").strip():
                    new_panel_url = str(data.get("panel_url")).strip().rstrip("/")
                    if new_panel_url != self.panel_url:
                        self.panel_url = new_panel_url
                        # STREAMFORGE_NODE_PUBLIC_NO_STALE_USER_WRITE_V123:
                        # Public workers own no user mutations; persist only panel metadata.
                        self._save_panel_state_only()
                self.proxied_viewer_sessions = [
                    dict(item) for item in (data.get("viewer_sessions") or []) if isinstance(item, dict)
                ] if self.panel_last_ok else []
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
            self.panel_last_ok = False
            self.proxied_viewer_sessions = []
        self._publish_panel_connectivity(self.panel_last_ok)
        return self.panel_last_ok

    @staticmethod
    def _panel_live_auth_key() -> bytes:
        return hashlib.sha256(b"streamforge-live-panel-auth-v1\0" + TOKEN.encode("utf-8")).digest()

    def _panel_live_auth_aad(self) -> bytes:
        return f"streamforge-live-panel-auth-v1|{self.panel_node_slug}".encode("utf-8")

    def _encrypt_panel_live_auth(self, payload: dict[str, Any]) -> str:
        nonce = secrets.token_bytes(12)
        body = dict(payload)
        body["issued_at"] = int(time.time())
        plaintext = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(self._panel_live_auth_key()).encrypt(
            nonce, plaintext, self._panel_live_auth_aad()
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def _decrypt_panel_live_auth(self, encoded: object) -> dict[str, Any]:
        raw = base64.urlsafe_b64decode(str(encoded or "").encode("ascii"))
        if len(raw) < 29:
            raise ValueError("short live-auth envelope")
        nonce, ciphertext = raw[:12], raw[12:]
        plaintext = AESGCM(self._panel_live_auth_key()).decrypt(
            nonce, ciphertext, self._panel_live_auth_aad()
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid live-auth response")
        issued_at = int(payload.get("issued_at") or 0)
        if abs(int(time.time()) - issued_at) > 45:
            raise ValueError("expired live-auth response")
        return payload

    def live_panel_authorize(
        self,
        username: str,
        *,
        action: str,
        password: str = "",
        auth_version: str = "",
        client_ip: str = "",
        timeout: float = 4.0,
    ) -> tuple[str, PanelAccessUser | None, str]:
        """Authorize a Node panel login/session against the live Main database.

        Returns (status, user, detail), where status is ok, denied or
        unavailable. The HTTP authorization remains fail-closed; ordinary session callers use only a short bounded success cache.
        """
        cleaned_username = str(username or "").strip()
        if not self.panel_url or not self.panel_node_slug or not TOKEN:
            self.panel_last_ok = False
            return "unavailable", None, "Main panel connection settings are incomplete"
        payload: dict[str, Any] = {"action": action, "username": cleaned_username}
        if client_ip:
            payload["client_ip"] = str(client_ip).strip()[:120]
        if action == "login":
            payload["password"] = password
        elif action == "change_password":
            payload["current_password"] = password
            payload["new_password"] = auth_version
        else:
            payload["auth_version"] = auth_version
        url = (
            f"{self.panel_url.rstrip('/')}/api/v1/node-panel-live-auth/"
            f"{urllib.parse.quote(self.panel_node_slug, safe='')}"
        )
        body = json.dumps(
            {"envelope": self._encrypt_panel_live_auth(payload)}, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "X-Node-Token": TOKEN,
                "X-Node-Slug": self.panel_node_slug,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"StreamForge-Node/{VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                outer = json.loads(response.read().decode("utf-8") or "{}")
                decoded = self._decrypt_panel_live_auth(outer.get("envelope"))
                if response.status != 200 or not decoded.get("ok"):
                    raise ValueError("Main panel rejected live authorization")
                self.apply_main_node_name(decoded.get("node_name"))
                returned_username = str(decoded.get("username") or "").strip()
                if not returned_username or returned_username != cleaned_username:
                    raise ValueError("Live authorization identity mismatch")
                user = PanelAccessUser(
                    username=returned_username,
                    password_hash="",
                    auth_version=str(decoded.get("auth_version") or ""),
                    enabled=True,
                    permissions=[str(item) for item in (decoded.get("permissions") or [])],
                )
                if not user.auth_version:
                    raise ValueError("Live authorization version missing")
                self.panel_last_check = time.monotonic()
                self.panel_last_ok = True
                with self.lock:
                    self.panel_users[user.username] = user
                return "ok", user, ""
        except urllib.error.HTTPError as exc:
            self.panel_last_check = time.monotonic()
            if exc.code in {400, 401, 403}:
                self.panel_last_ok = True
                try:
                    detail = str(json.loads(exc.read().decode("utf-8") or "{}").get("detail") or "Authorization denied")
                except Exception:
                    detail = "Authorization denied"
                return "denied", None, detail
            self.panel_last_ok = False
            return "unavailable", None, f"Main panel returned HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError):
            self.panel_last_check = time.monotonic()
            self.panel_last_ok = False
            return "unavailable", None, "Main panel is unreachable"
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            self.panel_last_check = time.monotonic()
            self.panel_last_ok = False
            return "unavailable", None, "Invalid live authorization response"
        except Exception:
            self.panel_last_check = time.monotonic()
            self.panel_last_ok = False
            return "unavailable", None, "Live authorization failed"

    def panel_control_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if not self.panel_connected(force=True):
            raise RuntimeError("Main panel is disconnected")
        if not self.panel_url or not self.panel_node_slug or not TOKEN:
            raise RuntimeError("Main panel control settings are incomplete")
        url = f"{self.panel_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "X-Node-Token": TOKEN,
            "X-Node-Slug": self.panel_node_slug,
            "Accept": "application/json",
            "User-Agent": f"StreamForge-Node/{VERSION}",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1600:]
            try:
                parsed = json.loads(detail)
                message = str(parsed.get("detail") or parsed.get("message") or detail)
            except (ValueError, TypeError):
                message = detail or str(exc.reason)
            raise RuntimeError(f"Main Panel HTTP {exc.code}: {message}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError(f"Main Panel request failed: {exc}") from exc

    def reload_users_if_changed(self) -> None:
        # STREAMFORGE_NODE_PLAYLIST_USER_SIGNATURE_RELOAD_V123:
        # Compare identity, not `mtime > old`: atomic replacement, equal mtimes,
        # and backward wall-clock corrections must all trigger a public reload.
        try:
            current = self._users_signature(USERS_FILE.stat())
        except OSError:
            current = None
        if current != self.users_file_signature:
            self.load_users()

    def _save_shared_viewers(self) -> None:
        try:
            VIEWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            items = []
            for (token, ip, channel_key, session_id), session in self.viewer_sessions.items():
                items.append({
                    "token": token, "ip": ip, "channel_key": channel_key, "session_id": session_id,
                    "first_seen_epoch": float(session.get("first_seen_epoch") or time.time()),
                    "last_seen_epoch": float(session.get("last_seen_epoch") or time.time()),
                    "user_agent": str(session.get("user_agent") or ""),
                })
            temp = VIEWERS_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps({"sessions": items}, separators=(",", ":")), encoding="utf-8")
            temp.replace(VIEWERS_FILE)
        except OSError:
            pass

    def _scheduled_shared_viewers_save(self) -> None:
        with self.lock:
            self._shared_viewers_save_timer=None
        self._save_shared_viewers()
        try:
            self._shared_viewers_mtime_ns=VIEWERS_FILE.stat().st_mtime_ns
        except OSError:
            pass

    def _schedule_shared_viewers_save(self) -> None:
        with self.lock:
            if self._shared_viewers_save_timer is not None: return
            timer=threading.Timer(0.5,self._scheduled_shared_viewers_save)
            timer.daemon=True
            self._shared_viewers_save_timer=timer
            timer.start()

    def _load_shared_viewers(self) -> None:
        try:
            mtime_ns=VIEWERS_FILE.stat().st_mtime_ns
        except OSError:
            mtime_ns=-1
        if self._shared_viewers_loaded and mtime_ns==self._shared_viewers_mtime_ns:
            return
        try:
            raw = json.loads(VIEWERS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self._shared_viewers_loaded=True
            self._shared_viewers_mtime_ns=mtime_ns
            return
        now_epoch = time.time()
        now_mono = time.monotonic()
        for item in raw.get("sessions", []):
            try:
                last_epoch = float(item.get("last_seen_epoch") or 0.0)
                if now_epoch - last_epoch > VIEWER_TTL:
                    continue
                first_epoch = float(item.get("first_seen_epoch") or last_epoch)
                session_id = str(item.get("session_id") or "") or ("legacy-" + hashlib.sha256((str(item["token"]) + "|" + str(item["ip"]) + "|" + str(item["channel_key"]) + "|" + str(item.get("user_agent") or "")).encode()).hexdigest()[:20])
                key = (str(item["token"]), str(item["ip"]), str(item["channel_key"]), session_id)
                self.viewer_sessions[key] = {
                    "first_seen": now_mono - max(0.0, now_epoch - first_epoch),
                    "last_seen": now_mono - max(0.0, now_epoch - last_epoch),
                    "first_seen_epoch": first_epoch,
                    "last_seen_epoch": last_epoch,
                    "user_agent": str(item.get("user_agent") or ""),
                }
                self.viewer_session_index[(str(item["token"]),session_id)]=key
            except (KeyError, TypeError, ValueError):
                continue
        self._shared_viewers_loaded=True
        self._shared_viewers_mtime_ns=mtime_ns

    def valid_user(self, token: str) -> NodeUserConfig | None:
        self.reload_users_if_changed()
        user = self.users.get(token)
        if not user or not user.enabled:
            return None
        if user.expires_at:
            try:
                from datetime import datetime, timezone
                expiry = datetime.fromisoformat(user.expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= datetime.now(timezone.utc):
                    return None
            except ValueError:
                return None
        return user

    def catalog_session_id(self, user: NodeUserConfig, request: Request) -> str:
        explicit = (request.query_params.get("device_id", "") or request.query_params.get("sid", "") or request.headers.get("x-device-id", "") or request.headers.get("x-streamforge-device", "") or request.cookies.get("sf_device", "")).strip()
        cleaned = re.sub(r"[^A-Za-z0-9._~-]+", "", explicit)[:96]
        seed = f"{user.token}|{cleaned}" if cleaned else "|".join([user.token, self._client_ip(request), request.headers.get("user-agent", ""), request.headers.get("accept-language", ""), request.headers.get("x-requested-with", "")])
        return "dev-" + hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:32]

    def total_active_connections(self) -> int:
        # STREAMFORGE_NODE_REDIS_CONNECTION_COUNT_V65
        if node_redis is not None and node_redis.available:
            try:
                return node_redis.total_active(VIEWER_TTL)
            except RuntimeError:
                pass
        now = time.monotonic()
        with self.lock:
            self._load_shared_viewers()
            active = {
                (key[0], key[3]) for key, session in self.viewer_sessions.items()
                if now - float(session.get("last_seen", 0.0)) <= VIEWER_TTL
            }
            active.update(
                (token, sid) for (token, sid), last_seen in self.connection_reservations.items()
                if now - float(last_seen) <= VIEWER_TTL
            )
            return len(active)

    def active_connections_for_user(self, user: NodeUserConfig) -> int:
        # STREAMFORGE_NODE_PLAYER_CONNECTION_COUNT_V2238:
        if node_redis is not None and node_redis.available:
            try:
                return node_redis.active_for_user(user.token, VIEWER_TTL)
            except RuntimeError:
                pass
        now = time.monotonic()
        with self.lock:
            self._load_shared_viewers()
            active = {
                key[3] for key, session in self.viewer_sessions.items()
                if key[0] == user.token and now - float(session.get("last_seen", 0.0)) <= VIEWER_TTL
            }
            active.update(
                sid for (token, sid), last_seen in self.connection_reservations.items()
                if token == user.token and now - float(last_seen) <= VIEWER_TTL
            )
            return len(active)

    def allow_connection(self, user: NodeUserConfig, request: Request, session_id: str = "", playback_start: bool = False) -> bool:
        """Reserve only at playback start; trailing HLS segments cannot reopen slots."""
        self.refresh_runtime_access_if_changed()
        now = time.monotonic()
        requested_sid = self.request_session_id(request, session_id)
        if node_redis is not None and node_redis.available:
            try:
                return node_redis.reserve_connection(
                    user.token, requested_sid,
                    user_limit=max(0, int(user.max_connections or 0)),
                    total_limit=max(0, int(self.total_max_connections or 0)),
                    ttl=VIEWER_TTL,
                    playback_start=bool(playback_start),
                )
            except RuntimeError:
                pass
        if NODE_MODE == "public" and NODE_PUBLIC_WORKER_COUNT > 1:
            # Never split connection-limit state across process-local memory.
            return False
        with self.lock:
            self._load_shared_viewers()
            for key in [key for key, last_seen in self.connection_reservations.items() if now - float(last_seen) > VIEWER_TTL]:
                self.connection_reservations.pop(key, None)
            active_pairs = {(key[0], key[3]) for key, session in self.viewer_sessions.items() if now - float(session.get("last_seen", 0.0)) <= VIEWER_TTL}
            active_pairs.update((token, sid) for (token, sid), last_seen in self.connection_reservations.items() if now - float(last_seen) <= VIEWER_TTL)
            active_sessions = {sid for token, sid in active_pairs if token == user.token}
            reservation_key = (user.token, requested_sid)
            if requested_sid in active_sessions:
                self.connection_reservations[reservation_key] = now
                return True
            if not playback_start:
                return False
            # STREAMFORGE_NODE_USER_ZERO_UNLIMITED_V2245:
            limit = max(0, int(user.max_connections or 0))
            if limit > 0 and len(active_sessions) >= limit:
                # STREAMFORGE_STRICT_MAX_CONNECTIONS: same-session channel
                # changes are allowed above; a distinct active session is denied.
                # A per-user value of 0 means unlimited.
                return False
            if self.total_max_connections > 0 and len(active_pairs) >= self.total_max_connections:
                return False
            self.connection_reservations[reservation_key] = now
            return True

    def client_session_reset_seconds(self) -> int:
        # STREAMFORGE_NODE_CLIENT_SESSION_RESET_OFFLINE_V1081
        return max(60, min(10080 * 60, int(self.client_session_reset_offline_minutes or 60) * 60))

    def client_session_history_ttl_seconds(self) -> int:
        # Refresh at most once a minute in the control observer. Keep retained
        # session history slightly longer than the configured Client log window.
        now = time.monotonic()
        if now >= float(self._client_session_history_ttl_refresh_at or 0.0):
            self._client_session_history_ttl_seconds = max(
                3600, min(3650 * 86400 + 3600, _node_log_retention_days() * 86400 + 3600)
            )
            self._client_session_history_ttl_refresh_at = now + 60.0
        return int(self._client_session_history_ttl_seconds)

    def client_session_history_observer_loop(self) -> None:
        # STREAMFORGE_NODE_SESSION_OBSERVER_V1049: one control-plane observer
        # samples the already-shared active viewer state and persists Client-log
        # Session age/reconnect generations. This replaces the v10.47 design of
        # one Redis history flush daemon per public worker, keeping all logging
        # and historical bookkeeping completely off the playback request path.
        if self._viewer_observer_thread_started:
            return
        self._viewer_observer_thread_started = True
        while True:
            time.sleep(2.0)
            if node_redis is None or not bool(getattr(node_redis, "enabled", False)):
                continue
            try:
                rows = node_redis.viewer_sessions(VIEWER_TTL)
            except RuntimeError:
                # Do not clear previous state on a transient Redis/control read
                # failure; doing so would create false reconnect events later.
                continue

            current: dict[tuple[str, str], tuple[float, float]] = {}
            history_rows: list[tuple[str, str, float]] = []
            reconnect_events: list[tuple[str, str, float, str, str, str]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                token = str(row.get("token") or "")
                sid = str(row.get("sid") or "")
                if not token or not sid:
                    continue
                try:
                    first = float(row.get("first_seen_epoch") or 0.0)
                    last = float(row.get("last_seen_epoch") or 0.0)
                except (TypeError, ValueError):
                    continue
                if first <= 0 or last < first:
                    continue
                key = (token, sid)
                current[key] = (first, last)
                history_rows.append((token, sid, last))
                previous = self._viewer_observer_previous.get(key)
                new_generation = bool(
                    self._viewer_observer_warmed
                    and (previous is None or abs(float(previous[0]) - first) > 0.5)
                )
                if new_generation:
                    user = self.users.get(token)
                    reconnect_events.append((
                        token, sid, first, str(row.get("channel_key") or ""),
                        str(row.get("ip") or ""),
                        str(row.get("user_agent") or "")[:300],
                    ))

            if history_rows:
                node_redis.touch_viewer_history_batch(
                    history_rows,
                    ttl=VIEWER_TTL,
                    history_ttl=self.client_session_history_ttl_seconds(),
                )
                node_redis.touch_client_log_sessions_batch(
                    [(str(token), str(sid)) for token, sid, _last in history_rows],
                    reset_ttl=self.client_session_reset_seconds(),
                )

            # STREAMFORGE_NODE_CLIENT_SESSION_RESET_BOUNDARY_V1081: a short
            # viewer-TTL reconnect creates a physical history generation but not
            # a new logical Client session. Only an offline gap greater than the
            # configured reset threshold may emit another playback-start row.
            reset_reconnect_events: list[tuple[str, str, float, str, str, str]] = []
            if reconnect_events:
                try:
                    retained = node_redis.viewer_session_history_by_sid(sorted({row[1] for row in reconnect_events}))
                except RuntimeError:
                    retained = []
                by_sid: dict[str, list[dict[str, Any]]] = {}
                for row in retained:
                    by_sid.setdefault(str(row.get('sid') or ''), []).append(row)
                reset_seconds = self.client_session_reset_seconds()
                for token, sid, _viewer_first, channel_key, ip, client in reconnect_events:
                    generations = sorted(by_sid.get(sid, []), key=lambda row: float(row.get('first_seen_epoch') or 0.0))
                    if not generations:
                        continue
                    current_gen = generations[-1]
                    current_first = float(current_gen.get('first_seen_epoch') or 0.0)
                    previous_gen = generations[-2] if len(generations) > 1 else None
                    if previous_gen is None or current_first - float(previous_gen.get('last_seen_epoch') or 0.0) > reset_seconds:
                        reset_reconnect_events.append((token, sid, current_first, channel_key, ip, client))

            # Publish a playback-start log once per logical Live-session cluster.
            retained_playback: dict[str, list[float]] = {}
            with self.lock:
                retained_events = list(self.event_logs)
            for event in retained_events:
                if str(event.get("scope") or "").strip().lower() != "client":
                    continue
                if str(event.get("message") or "").strip().lower() != "playback session started":
                    continue
                sid_value = _node_client_log_session_id(event)
                if not sid_value:
                    continue
                epoch = _node_client_log_generation_epoch(event) or _node_client_log_event_epoch(event)
                if epoch > 0:
                    retained_playback.setdefault(sid_value, []).append(epoch)

            reset_seconds = self.client_session_reset_seconds()
            for token, sid, first, channel_key, ip, client in reset_reconnect_events:
                # Skip an already-retained row from this same logical cluster,
                # including after a control-worker restart.
                prior_starts = retained_playback.get(sid, [])
                if any(abs(float(existing) - first) <= reset_seconds for existing in prior_starts):
                    continue
                user = self.users.get(token)
                username = user.username if user is not None else "Unknown user"
                # STREAMFORGE_NODE_CLIENT_LOG_LIVE_SESSION_ID_V1073:
                # Keep the exact Live-session/device SID visible; v10.81 changes
                # only the logical generation boundary, never the SID itself.
                self.log(
                    "Playback session started", scope="client", key=channel_key, user=username,
                    details=(f"user={username}; ip={ip}; client={client}; "
                             f"channel={channel_key}; session_id={sid}; "
                             f"session_started_epoch={first:.6f}"),
                )
                retained_playback.setdefault(sid, []).append(first)

            self._viewer_observer_previous = current
            self._viewer_observer_warmed = True

    @staticmethod
    def request_session_id(request: Request, explicit: str = "") -> str:
        value = (
            explicit or request.query_params.get("sid", "")
            or request.headers.get("x-streamforge-session", "")
            or request.headers.get("x-playback-session", "")
            or request.headers.get("x-device-id", "")
        ).strip()
        value = re.sub(r"[^A-Za-z0-9._~-]+", "", value)[:96]
        if value:
            return value
        fingerprint = "|".join([
            AgentManager._client_ip(request), request.headers.get("user-agent", ""),
            request.headers.get("accept", ""), request.headers.get("origin", ""),
        ])
        return "fp-" + hashlib.sha256(fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]

    def touch_viewer(self, token: str, channel_key: str, request: Request, session_id: str = "") -> None:
        self.refresh_runtime_access_if_changed()
        now=time.monotonic(); ip=self._client_ip(request); sid=self.request_session_id(request,session_id)
        if node_redis is not None and node_redis.available:
            try:
                node_redis.touch_viewer(
                    token, sid, channel_key, ip, request.headers.get("user-agent", ""),
                    ttl=VIEWER_TTL,
                )
                # STREAMFORGE_NODE_PLAYBACK_NO_LOG_SIDE_EFFECTS_V1049:
                # successful viewer heartbeat returns immediately. Persistent
                # Session age/reconnect logging is sampled by the control worker.
                return
            except RuntimeError:
                pass
        if NODE_MODE == "public" and NODE_PUBLIC_WORKER_COUNT > 1:
            return
        key=(token,ip,channel_key,sid)
        with self.lock:
            old_key=self.viewer_session_index.get((token,sid))
            previous=self.viewer_sessions.get(old_key) if old_key else None
            if old_key and old_key!=key: self.viewer_sessions.pop(old_key,None)
            now_epoch=time.time()
            history = self.viewer_session_history.setdefault(sid, [])
            force_new_history = sid in self._viewer_session_history_force_new
            self._viewer_session_history_force_new.discard(sid)
            if force_new_history or not history or now_epoch - float(history[-1].get("last_seen_epoch") or 0.0) > VIEWER_TTL:
                history.append({"first_seen_epoch": now_epoch, "last_seen_epoch": now_epoch})
            else:
                history[-1]["last_seen_epoch"] = now_epoch
            history_cutoff = now_epoch - self.client_session_history_ttl_seconds()
            if len(history) > 1:
                self.viewer_session_history[sid] = [
                    row for row in history if float(row.get("last_seen_epoch") or 0.0) >= history_cutoff
                ][-128:]
            self.viewer_sessions[key]={
                "first_seen":float(previous.get("first_seen",now)) if previous else now,
                "last_seen":now,
                "first_seen_epoch":float(previous.get("first_seen_epoch",now_epoch)) if previous else now_epoch,
                "last_seen_epoch":now_epoch,
                "user_agent":request.headers.get("user-agent","")[:300] or str((previous or {}).get("user_agent") or ""),
            }
            self.viewer_session_index[(token,sid)]=key
            self.connection_reservations[(token,sid)]=now
            if now>=self._viewer_next_prune:
                stale=[k for k,v in self.viewer_sessions.items() if now-float(v.get("last_seen",0.0))>VIEWER_TTL]
                for old in stale:
                    self.viewer_sessions.pop(old,None)
                    ik=(old[0],old[3])
                    if self.viewer_session_index.get(ik)==old: self.viewer_session_index.pop(ik,None)
                self._viewer_next_prune=now+2.0
        self._schedule_shared_viewers_save()


    def kill_viewer_session(self, session_id: str) -> int:
        cleaned = re.sub(r"[^A-Za-z0-9._~-]+", "", str(session_id or "").strip())[:96]
        if not cleaned:
            return 0
        if node_redis is not None and node_redis.available:
            try:
                return node_redis.kill_session(cleaned)
            except RuntimeError:
                pass
        with self.lock:
            keys = [key for key in self.viewer_sessions if key[3] == cleaned]
            tokens = {key[0] for key in keys}
            for key in keys:
                self.viewer_sessions.pop(key, None)
            for key in [key for key in self.connection_reservations if key[1] == cleaned or key[0] in tokens]:
                self.connection_reservations.pop(key, None)
            self._viewer_session_history_force_new.add(cleaned)
            self._save_shared_viewers()
            return len(keys)

    def viewer_stats(self, *, include_geo: bool = True) -> dict[str, Any]:
        self.refresh_runtime_access_if_changed()
        self.reload_users_if_changed()
        # STREAMFORGE_NODE_DIRECT_SESSION_ONLY_V90:
        # Node-local dashboards and Online Sessions show only sessions actually
        # delivered by this Node. Do not block viewer refreshes on a Main heartbeat.
        from datetime import datetime, timedelta, timezone
        now = time.monotonic()
        now_epoch = time.time()
        now_utc = datetime.now(timezone.utc)
        direct_sessions: list[dict[str, Any]] = []
        direct_channels: dict[str, set[str]] = {}

        redis_rows: list[dict[str, Any]] | None = None
        if node_redis is not None and node_redis.available:
            try:
                redis_rows = node_redis.viewer_sessions(VIEWER_TTL)
            except RuntimeError:
                redis_rows = None

        if redis_rows is not None:
            # STREAMFORGE_NODE_REDIS_VIEWER_DASHBOARD_V65: the control worker
            # reads the same shared heartbeat state written by every public worker.
            for session in redis_rows:
                token = str(session.get("token") or "")
                ip = str(session.get("ip") or "")
                channel_key = str(session.get("channel_key") or "")
                session_id = str(session.get("sid") or "")
                if not token or not channel_key or not session_id:
                    continue
                user = self.users.get(token)
                runtime = self.channels.get(channel_key)
                try:
                    first_epoch = float(session.get("first_seen_epoch") or now_epoch)
                    last_epoch = float(session.get("last_seen_epoch") or now_epoch)
                except (TypeError, ValueError):
                    first_epoch = last_epoch = now_epoch
                duration = max(0, int(now_epoch - first_epoch))
                idle = max(0, int(now_epoch - last_epoch))
                direct_channels.setdefault(channel_key, set()).add(session_id)
                user_ref = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
                geo = self._lookup_geo(ip) if include_geo else self._lookup_geo_cached_nonblocking(ip)
                direct_sessions.append({
                    "session_id": session_id,
                    "source": "Direct node playlist",
                    "source_key": "direct_node",
                    "user_key": user_ref,
                    "user_id": user.user_id if user else None,
                    "user_name": user.name if user else "Unknown user",
                    "client_ip": ip,
                    "asn": geo.get("asn"),
                    "business_name": geo.get("business_name") or "",
                    "country_name": geo.get("country_name") or "",
                    "country_code": geo.get("country_code") or "",
                    "channel_key": channel_key,
                    "channel_name": runtime.config.name if runtime else channel_key,
                    "started_at": (now_utc - timedelta(seconds=duration)).isoformat(),
                    "last_seen_at": (now_utc - timedelta(seconds=idle)).isoformat(),
                    "duration_seconds": duration,
                    "idle_seconds": idle,
                    "user_agent": str(session.get("user_agent") or ""),
                })
        else:
            self._load_shared_viewers()
            stale = [
                item_key for item_key, session in self.viewer_sessions.items()
                if now - float(session.get("last_seen", 0.0)) > VIEWER_TTL
            ]
            for item_key in stale:
                self.viewer_sessions.pop(item_key, None)
            for (token, ip, channel_key, session_id), session in self.viewer_sessions.items():
                user = self.users.get(token)
                runtime = self.channels.get(channel_key)
                first_seen = float(session.get("first_seen", now))
                last_seen = float(session.get("last_seen", now))
                duration = max(0, int(now - first_seen))
                idle = max(0, int(now - last_seen))
                direct_channels.setdefault(channel_key, set()).add(session_id)
                user_ref = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
                geo = self._lookup_geo(ip) if include_geo else self._lookup_geo_cached_nonblocking(ip)
                direct_sessions.append({
                    "session_id": session_id, "source": "Direct node playlist", "source_key": "direct_node",
                    "user_key": user_ref, "user_id": user.user_id if user else None,
                    "user_name": user.name if user else "Unknown user", "client_ip": ip,
                    "asn": geo.get("asn"), "business_name": geo.get("business_name") or "",
                    "country_name": geo.get("country_name") or "", "country_code": geo.get("country_code") or "",
                    "channel_key": channel_key, "channel_name": runtime.config.name if runtime else channel_key,
                    "started_at": (now_utc - timedelta(seconds=duration)).isoformat(),
                    "last_seen_at": (now_utc - timedelta(seconds=idle)).isoformat(),
                    "duration_seconds": duration, "idle_seconds": idle,
                    "user_agent": str(session.get("user_agent") or ""),
                })

        # Main-proxy rows are retained only as an internal diagnostic payload;
        # they are not Node-local online users and must never appear in this
        # Node's Live Sessions/dashboard counters.
        proxied_sessions = [dict(item) for item in self.proxied_viewer_sessions if isinstance(item, dict)]
        direct_sessions.sort(key=lambda item: (int(item.get("idle_seconds") or 0), str(item.get("user_name") or "")))
        direct_session_ids = {
            str(item.get("session_id") or "") for item in direct_sessions
            if str(item.get("session_id") or "")
        }
        account_ids: set[str] = set()
        for item in direct_sessions:
            user_id = item.get("user_id")
            account_ids.add(
                "user:" + str(int(user_id))
                if user_id not in (None, "", 0, "0")
                else "name:" + str(item.get("user_name") or "unknown")
            )
        proxied_session_ids = {
            str(item.get("session_id") or "") for item in proxied_sessions
            if str(item.get("session_id") or "")
        }
        return {
            "total_users": len(direct_session_ids),
            "unique_accounts": len(account_ids),
            "channels": {key: len(value) for key, value in direct_channels.items()},
            "sessions": direct_sessions,
            "direct_total_users": len(direct_session_ids),
            "direct_channels": {key: len(value) for key, value in direct_channels.items()},
            "direct_sessions": direct_sessions,
            "proxied_total_users": len(proxied_session_ids),
        }



def hash_scrypt_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p_value = 16384, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p_value, dklen=32)
    return "$".join([
        "scrypt", str(n), str(r), str(p_value),
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    ])


def verify_scrypt_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p_value, salt_b64, digest_b64 = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p_value), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# STREAMFORGE_NODE_BROWSER_COOKIE_FIX_V60R1: keep v6.0 authentication semantics
# but isolate every Node's browser session cookie and aggressively clear legacy
# parent-domain/duplicate cookies. This fixes Chrome/Firefox login loops caused
# by stale sf_node_panel or hidden-route cookies without adding newer v6.1+
# session-revocation behavior.
LEGACY_NODE_PANEL_COOKIE = "sf_node_panel"


def _panel_session_cookie_name() -> str:
    identity = str(manager.panel_node_slug or TOKEN or "node").strip().lower()
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"sf_node_panel_{suffix}"


def _public_request_is_https(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or (not forwarded and str(request.url.scheme).lower() == "https")


def _cookie_values(request: Request, name: str) -> list[str]:
    raw_header = str(request.headers.get("cookie") or "")
    values: list[str] = []
    for item in raw_header.split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == name:
            cleaned = value.strip().strip('"')
            if cleaned:
                values.append(cleaned)
    return values


def _legacy_cookie_domains(request: Request) -> list[str]:
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    if host.startswith("["):
        host = host.split("]", 1)[0].lstrip("[")
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    host = host.strip().lower().rstrip(".")
    parts = [part for part in host.split(".") if part]
    domains: list[str] = []
    if len(parts) >= 2:
        for index in range(0, max(1, len(parts) - 1)):
            suffix = ".".join(parts[index:])
            if suffix.count(".") >= 1:
                domains.extend([suffix, "." + suffix])
    return list(dict.fromkeys(domains))


def _clear_panel_session_cookies(response: Response, request: Request | None = None) -> None:
    names = [_panel_session_cookie_name(), LEGACY_NODE_PANEL_COOKIE]
    for name in names:
        response.delete_cookie(name, path="/")
    if request is not None:
        for domain in _legacy_cookie_domains(request):
            for name in names:
                response.delete_cookie(name, path="/", domain=domain)


def _set_panel_route_cookie(response: Response, request: Request, target: str, max_age: int = 43200) -> None:
    # Hidden-route state is also migrated back to a host-only cookie. A stale
    # Domain=.example.com /panel/login cookie must never shadow a fresh login.
    response.delete_cookie(STREAMFORGE_NODE_PANEL_ROUTE_COOKIE, path="/")
    for domain in _legacy_cookie_domains(request):
        response.delete_cookie(STREAMFORGE_NODE_PANEL_ROUTE_COOKIE, path="/", domain=domain)
    response.set_cookie(
        STREAMFORGE_NODE_PANEL_ROUTE_COOKIE,
        urllib.parse.quote(target, safe=""),
        max_age=max_age,
        path="/",
        samesite="lax",
        secure=_public_request_is_https(request),
    )


def _set_panel_session_cookie(response: Response, request: Request, value: str) -> None:
    # No Domain attribute: this intentionally creates a host-only cookie.
    response.set_cookie(
        _panel_session_cookie_name(),
        value,
        httponly=True,
        secure=_public_request_is_https(request),
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
    # Remove the old generic cookie from host and parent-domain scopes.
    response.delete_cookie(LEGACY_NODE_PANEL_COOKIE, path="/")
    for domain in _legacy_cookie_domains(request):
        response.delete_cookie(LEGACY_NODE_PANEL_COOKIE, path="/", domain=domain)


def _session_cookie(username: str, auth_version: str) -> str:
    expires = int(time.time()) + 12 * 60 * 60
    encoded_username = base64.urlsafe_b64encode(username.encode("utf-8")).decode("ascii").rstrip("=")
    payload = f"v2|{encoded_username}|{auth_version}|{expires}"
    signature = hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()


def _decode_session_claims(raw: str) -> tuple[str, str] | None:
    if not raw or not TOKEN:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        version, encoded_username, auth_version, expires_raw, signature = decoded.split("|", 4)
        payload = f"{version}|{encoded_username}|{auth_version}|{expires_raw}"
        expected = hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if version != "v2" or not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded_username) % 4)
        username = base64.urlsafe_b64decode((encoded_username + padding).encode("ascii")).decode("utf-8")
        if int(expires_raw) < int(time.time()) or not username or not auth_version:
            return None
        return username, auth_version
    except (ValueError, TypeError, UnicodeError):
        return None


def _session_claims(request: Request) -> tuple[str, str] | None:
    current_name = _panel_session_cookie_name()
    current_values = _cookie_values(request, current_name)
    for raw in current_values:
        claims = _decode_session_claims(raw)
        if claims:
            return claims
    # If a Node-scoped cookie exists but is invalid, do not resurrect an older
    # generic cookie. A successful login will replace/clean both scopes.
    if current_values:
        return None
    # Upgrade compatibility for the original v6.0 generic cookie. Parse every
    # duplicate candidate because browsers can send host and parent-domain
    # cookies with the same name in one Cookie header.
    for raw in _cookie_values(request, LEGACY_NODE_PANEL_COOKIE):
        claims = _decode_session_claims(raw)
        if claims:
            return claims
    return None


# STREAMFORGE_NODE_PANEL_SESSION_AUTH_CACHE_V1066:
# Keep login/password-change authorization fully live, but coalesce ordinary
# session revalidation for the configured short panel cache window. This avoids
# a synchronous Main HTTP round-trip for every asset/status request while keeping
# revocation/permission changes bounded by PANEL_CACHE_SECONDS.
_NODE_PANEL_SESSION_AUTH_CACHE_LOCK = threading.RLock()
_NODE_PANEL_SESSION_AUTH_CACHE: dict[tuple[str, str], tuple[float, PanelAccessUser]] = {}
_NODE_PANEL_SESSION_AUTH_KEY_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _cached_panel_session_authorize(
    username: str, auth_version: str
) -> tuple[str, PanelAccessUser | None, str]:
    cleaned_username = str(username or "").strip()
    cleaned_version = str(auth_version or "").strip()
    cache_key = (cleaned_username, cleaned_version)
    now = time.monotonic()

    with _NODE_PANEL_SESSION_AUTH_CACHE_LOCK:
        cached = _NODE_PANEL_SESSION_AUTH_CACHE.get(cache_key)
        if cached and cached[0] > now:
            user = cached[1]
            if user.enabled and str(user.auth_version or "") == cleaned_version:
                return "ok", user, ""
        key_lock = _NODE_PANEL_SESSION_AUTH_KEY_LOCKS.setdefault(cache_key, threading.Lock())

    # One in-flight Main authorization per session identity. Other concurrent
    # panel requests wait for it and then consume the same short-lived result.
    with key_lock:
        now = time.monotonic()
        with _NODE_PANEL_SESSION_AUTH_CACHE_LOCK:
            cached = _NODE_PANEL_SESSION_AUTH_CACHE.get(cache_key)
            if cached and cached[0] > now:
                user = cached[1]
                if user.enabled and str(user.auth_version or "") == cleaned_version:
                    return "ok", user, ""

        status, user, detail = manager.live_panel_authorize(
            cleaned_username, action="session", auth_version=cleaned_version
        )
        with _NODE_PANEL_SESSION_AUTH_CACHE_LOCK:
            if status == "ok" and user:
                _NODE_PANEL_SESSION_AUTH_CACHE[cache_key] = (
                    time.monotonic() + PANEL_CACHE_SECONDS, user
                )
            else:
                _NODE_PANEL_SESSION_AUTH_CACHE.pop(cache_key, None)
            if len(_NODE_PANEL_SESSION_AUTH_CACHE) > 256:
                expiry_order = sorted(
                    _NODE_PANEL_SESSION_AUTH_CACHE.items(),
                    key=lambda item: item[1][0],
                )[:64]
                for stale_key, _value in expiry_order:
                    _NODE_PANEL_SESSION_AUTH_CACHE.pop(stale_key, None)
                    _NODE_PANEL_SESSION_AUTH_KEY_LOCKS.pop(stale_key, None)
        return status, user, detail


def _session_user(request: Request) -> PanelAccessUser | None:
    claims = _session_claims(request)
    if not claims:
        return None
    status, user, _detail = _cached_panel_session_authorize(claims[0], claims[1])
    return user if status == "ok" else None


def _has_panel_permission(user: PanelAccessUser, permission: str) -> bool:
    return "*" in user.permissions or permission in user.permissions


def require_node_panel_user(request: Request, permission: str = "channels.view") -> PanelAccessUser:
    claims = _session_claims(request)
    if not claims:
        raise HTTPException(401, "Node panel login required")
    status, user, detail = _cached_panel_session_authorize(claims[0], claims[1])
    if status == "unavailable":
        raise HTTPException(503, MAIN_PANEL_DISCONNECTED_DETAIL)
    if status != "ok" or not user:
        raise HTTPException(401, detail or "Node panel session was revoked")
    if not _has_panel_permission(user, permission):
        raise HTTPException(403, "Permission denied")
    return user


def require_independent_node_user(request: Request, permission: str = "channels.view") -> PanelAccessUser:
    user = require_node_panel_user(request, permission)
    if not manager.independent_mode:
        raise HTTPException(409, "This page is available only while Independent Node mode is enabled")
    return user


def _local_channel_count() -> int:
    with manager.lock:
        return sum(1 for runtime in manager.channels.values() if runtime.config.catalog_owner == "local")


def _node_channel_display_ids() -> dict[str, int]:
    """Main rows keep Main 101+ IDs; Node-local rows have an independent 1+ sequence."""
    with manager.lock:
        rows = [(key, runtime.config) for key, runtime in manager.channels.items()]
    order = lambda item: (
        _category_sort_value(item[1].category),
        int(getattr(item[1], "channel_order", 100000) or 100000),
        item[1].name.lower(), item[0],
    )
    main_rows = sorted((item for item in rows if item[1].catalog_owner == "main"), key=order)
    local_rows = sorted((item for item in rows if item[1].catalog_owner == "local"), key=order)
    result = {key: index for index, (key, _config) in enumerate(local_rows, 1)}
    for index, (key, config) in enumerate(main_rows, 101):
        result[key] = int(getattr(config, "display_id", None) or index)
    return result


def _local_catalogue_available() -> bool:
    return bool(manager.independent_mode or manager.local_channel_limit > 0 or _local_channel_count() > 0)


def require_local_catalogue_user(request: Request, permission: str = "channels.view") -> PanelAccessUser:
    user = require_node_panel_user(request, permission)
    if not _local_catalogue_available():
        raise HTTPException(409, "Node-local channel creation is disabled by the Main Panel")
    return user


def enforce_local_channel_capacity() -> None:
    limit = max(0, int(manager.local_channel_limit or 0))
    # Independent mode keeps its historic unlimited behaviour when the Main
    # Panel limit is zero. Shared mode requires an explicit positive allowance.
    if not manager.independent_mode and limit <= 0:
        raise ValueError("Node-local channel creation is disabled by the Main Panel")
    if limit > 0 and _local_channel_count() >= limit:
        raise ValueError(f"Node-local channel limit reached ({limit})")


NODE_RUNTIME_ERROR_SCRIPT = r"""<script>
(()=>{
 const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
 let modal=null;
 let state={endpoint:'',clearEndpoint:'',channelName:'',page:1,pageSize:10,loading:false};
 const ensure=()=>{
  if(modal)return modal;
  modal=document.createElement('div');
  modal.className='node-channel-log-overlay';modal.hidden=true;
  modal.innerHTML='<section class="node-channel-log-dialog" role="dialog" aria-modal="true" aria-labelledby="node-channel-log-title">'
   +'<header class="node-channel-log-header"><div><h2 id="node-channel-log-title" data-node-channel-log-title>Channel stream logs</h2><p>Stream event and error history</p></div><button type="button" data-node-channel-log-close aria-label="Close">Ã—</button></header>'
   +'<div class="node-channel-log-toolbar"><button type="button" class="danger" data-node-channel-log-clear hidden>Clear Stream Logs</button><label>Show <select data-node-channel-log-size><option>10</option><option>25</option><option>50</option><option>100</option></select> entries</label><span data-node-channel-log-count></span></div>'
   +'<div class="node-channel-log-current" data-node-channel-log-current hidden></div>'
   +'<div class="node-channel-log-table-wrap"><table class="node-channel-log-table"><thead><tr><th>Server name</th><th data-node-channel-log-source-head>Source</th><th>Action</th><th>Date</th></tr></thead><tbody data-node-channel-log-body></tbody></table></div>'
   +'<footer class="node-channel-log-footer"><span data-node-channel-log-range></span><nav data-node-channel-log-pages></nav></footer></section>';
  document.body.appendChild(modal);
  modal.querySelector('[data-node-channel-log-close]')?.addEventListener('click',close);
  modal.addEventListener('click',e=>{if(e.target===modal)close();});
  modal.querySelector('[data-node-channel-log-size]')?.addEventListener('change',e=>{state.pageSize=Math.max(5,Number(e.target.value)||10);state.page=1;load();});
  modal.querySelector('[data-node-channel-log-clear]')?.addEventListener('click',async()=>{
   if(!state.clearEndpoint||!confirm('Clear stream logs for '+(state.channelName||'this channel')+'?'))return;
   const button=modal.querySelector('[data-node-channel-log-clear]');button.disabled=true;
   try{
    const response=await fetch(state.clearEndpoint,{method:'POST',cache:'no-store'});const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.detail||('HTTP '+response.status));
    state.page=1;
    document.querySelectorAll('[data-node-runtime-alert]').forEach(trigger=>{if(trigger.dataset.errorEndpoint===state.endpoint){trigger.dataset.hasError='0';trigger.className='node-runtime-alert-dot healthy';trigger.removeAttribute('data-tooltip');trigger.setAttribute('aria-label','Healthy');}});
    await load();
   }catch(error){alert(error.message||'Could not clear logs');}finally{button.disabled=false;}
  });
  return modal;
 };
 const close=()=>{if(!modal)return;modal.hidden=true;document.body.classList.remove('node-channel-log-open');};
 const pageItems=(page,pages)=>{if(pages<=7)return Array.from({length:pages},(_,i)=>i+1);const out=[1],start=Math.max(2,page-2),end=Math.min(pages-1,page+2);if(start>2)out.push('â€¦');for(let i=start;i<=end;i++)out.push(i);if(end<pages-1)out.push('â€¦');out.push(pages);return out;};
 const render=data=>{
  const root=ensure(),body=root.querySelector('[data-node-channel-log-body]'),entries=Array.isArray(data.entries)?data.entries:[];
  root.querySelector('[data-node-channel-log-title]').textContent=data.channel_name||state.channelName||'Channel stream logs';
  const current=root.querySelector('[data-node-channel-log-current]'),currentText=String(data.current_error||'').trim();current.hidden=!currentText;current.innerHTML=currentText?'<b>Current error</b><span>'+esc(currentText)+'</span>':'';
  state.clearEndpoint=String(data.clear_endpoint||state.clearEndpoint||'');root.querySelector('[data-node-channel-log-clear]').hidden=!data.can_clear;
  const hideSource=Boolean(data.hide_source);const sourceHead=root.querySelector('[data-node-channel-log-source-head]');if(sourceHead)sourceHead.hidden=hideSource;const columnCount=hideSource?3:4;
  body.innerHTML=entries.map(entry=>{
   let details=String(entry.details||'').trim();if(details){try{details=JSON.stringify(JSON.parse(details),null,2);}catch(_){}}
   const sourceCell=hideSource?'':'<td class="mono">'+esc(entry.source||'â€”')+'</td>';
   return '<tr class="node-channel-log-row" tabindex="0" data-node-channel-log-row><td>'+esc(entry.server_name||'Node')+'</td>'+sourceCell+'<td><span class="node-channel-log-action '+esc(entry.action_class||'info')+'">'+esc(entry.action||'INFO')+'</span></td><td>'+esc(entry.created_at||'')+'</td></tr>'
    +'<tr class="node-channel-log-details" hidden><td colspan="'+columnCount+'"><b>'+esc(entry.message||'Event details')+'</b>'+(details?'<pre>'+esc(details)+'</pre>':'')+'</td></tr>';
  }).join('')||'<tr><td colspan="'+columnCount+'" class="empty">No stream logs found.</td></tr>';
  body.querySelectorAll('[data-node-channel-log-row]').forEach(row=>{const toggle=()=>{const detail=row.nextElementSibling;if(detail)detail.hidden=!detail.hidden;};row.addEventListener('click',toggle);row.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}});});
  const total=Math.max(0,Number(data.total)||0),page=Math.max(1,Number(data.page)||1),pages=Math.max(1,Number(data.pages)||1),size=Math.max(1,Number(data.page_size)||state.pageSize);state.page=page;state.pageSize=size;
  root.querySelector('[data-node-channel-log-size]').value=String(size);root.querySelector('[data-node-channel-log-count]').textContent=total+' event'+(total===1?'':'s');
  const from=total?((page-1)*size)+1:0,to=Math.min(total,page*size);root.querySelector('[data-node-channel-log-range]').textContent=total?('Showing '+from+' to '+to+' of '+total):'Showing 0 entries';
  const nav=root.querySelector('[data-node-channel-log-pages]');const btn=(label,target,disabled,active)=>'<button type="button" data-page="'+target+'" '+(disabled?'disabled':'')+' class="'+(active?'active':'')+'">'+label+'</button>';
  nav.innerHTML=btn('Previous',page-1,page<=1,false)+pageItems(page,pages).map(item=>item==='â€¦'?'<span>â€¦</span>':btn(item,item,false,Number(item)===page)).join('')+btn('Next',page+1,page>=pages,false);
  nav.querySelectorAll('button[data-page]').forEach(button=>button.addEventListener('click',()=>{if(button.disabled)return;state.page=Number(button.dataset.page)||1;load();}));
 };
 const load=async()=>{
  if(!state.endpoint||state.loading)return;state.loading=true;const root=ensure();root.querySelector('[data-node-channel-log-body]').innerHTML='<tr><td colspan="4" class="node-channel-log-loading">Loading stream logsâ€¦</td></tr>';
  try{const url=new URL(state.endpoint,location.origin);url.searchParams.set('page',String(state.page));url.searchParams.set('page_size',String(state.pageSize));url.searchParams.set('_',String(Date.now()));const response=await fetch(url,{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.detail||('HTTP '+response.status));render(data);}catch(error){root.querySelector('[data-node-channel-log-body]').innerHTML='<tr><td colspan="4" class="empty">'+esc(error.message||'Could not load stream logs')+'</td></tr>';}finally{state.loading=false;}
 };
 const open=(trigger,requireError=true)=>{if(requireError&&trigger.dataset.hasError!=='1')return;state={endpoint:trigger.dataset.errorEndpoint||'',clearEndpoint:trigger.dataset.clearEndpoint||'',channelName:trigger.dataset.channelName||'',page:1,pageSize:10,loading:false};const root=ensure();root.hidden=false;document.body.classList.add('node-channel-log-open');load();requestAnimationFrame(()=>root.querySelector('[data-node-channel-log-close]')?.focus());};
 document.querySelectorAll('[data-node-runtime-alert-menu]').forEach(details=>{const trigger=details.querySelector('[data-node-runtime-alert]');trigger?.addEventListener('click',event=>{event.preventDefault();details.open=false;open(trigger,true);});});
 document.querySelectorAll('[data-node-channel-log-open]').forEach(trigger=>{trigger.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();const details=trigger.closest('details');if(details)details.open=false;open(trigger,false);});});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&modal&&!modal.hidden)close();});
})();
</script>"""

def _node_log_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except Exception:
        parsed = datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dedupe_node_channel_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[object, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in sorted(events, key=lambda row: _node_log_datetime(row.get("time")), reverse=True):
        message = str(item.get("message") or "").strip()
        if message.lower().startswith("panel user requested channel"):
            continue
        stamp = _node_log_datetime(item.get("time"))
        key = (
            re.sub(r"\s+", " ", message.lower()),
            re.sub(r"\s+", " ", str(item.get("details") or "").strip().lower()),
            str(item.get("level") or "info").lower(),
            int(stamp.timestamp()),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _node_channel_log_action(message: object, level: object = "info") -> tuple[str, str]:
    text = str(message or "").strip().lower()
    level_text = str(level or "info").strip().lower()
    if "start failed" in text or ("failed" in text and "start" in text):
        return "START FAILED", "start-failed"
    if "stream failed" in text or "ffmpeg exited" in text or "encoder failed" in text:
        return "STREAM FAILED", "stream-failed"
    if "restart" in text:
        return "RESTARTED", "restarted"
    if "stop" in text:
        return "STOPPED", "stopped"
    if "start" in text or "running" in text:
        return "STARTED", "started"
    if level_text == "error":
        return "ERROR", "error"
    if level_text == "warning":
        return "WARNING", "warning"
    return "INFO", "info"


def _rewrite_node_event_log_file() -> None:
    try:
        with manager.lock:
            rows = list(reversed(manager.event_logs))
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(
            "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in rows),
            encoding="utf-8",
        )
    except OSError:
        pass


def _node_recent_channel_errors() -> dict[str, list[dict[str, Any]]]:
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    result: dict[str, list[dict[str, Any]]] = {}
    with manager.lock:
        events = list(manager.event_logs)
    for item in events:
        key = str(item.get("channel") or "").strip()
        level = str(item.get("level") or "").strip().lower()
        if not key or level not in {"warning", "error"}:
            continue
        try:
            stamp = datetime.fromisoformat(str(item.get("time") or "").replace("Z", "+00:00")).timestamp()
            if stamp < cutoff:
                continue
        except Exception:
            pass
        bucket = result.setdefault(key, [])
        if len(bucket) < 20:
            bucket.append(item)
    return result


def _node_runtime_alert_markup(key: str, channel_name: str, status: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    status_name = str(status.get("status") or "unknown").strip().lower()
    runtime_error = str(status.get("last_error") or "").strip()
    current_error = runtime_error if status_name in {"error", "degraded", "starting", "restarting"} else ""
    restart_count = sum(
        1 for item in entries
        if "restart" in str(item.get("message") or "").lower()
        or "ffmpeg exited" in str(item.get("message") or "").lower()
    )
    count = max(len(entries), 1 if current_error else 0)
    has_error = count > 0
    if restart_count:
        tooltip = f"{restart_count} restart{'s' if restart_count != 1 else ''} Â· click for details"
    elif count:
        tooltip = f"{count} recent error{'s' if count != 1 else ''} Â· click for details"
    else:
        tooltip = ""
    safe_key = urllib.parse.quote(key, safe="")
    css_state = "has-error" if has_error else ("healthy" if status_name == "running" else "idle")
    tooltip_attr = f' data-tooltip="{html.escape(tooltip, quote=True)}"' if tooltip else ""
    aria_label = tooltip or ("Running" if status_name == "running" else "Stopped")
    return (
        '<details class="node-runtime-alert-menu" data-node-runtime-alert-menu>'
        f'<summary class="node-runtime-alert-dot {css_state}" data-node-runtime-alert '
        f'data-has-error="{1 if has_error else 0}"{tooltip_attr} '
        f'data-error-endpoint="/panel/channels/{safe_key}/errors.json" '
        f'data-clear-endpoint="/panel/channels/{safe_key}/errors/clear" '
        f'data-channel-name="{html.escape(channel_name, quote=True)}" '
        f'aria-label="{html.escape(aria_label, quote=True)}"><span aria-hidden="true">!</span></summary></details>'
    )


def _node_runtime_error_script() -> str:
    return NODE_RUNTIME_ERROR_SCRIPT


NODE_MORE_MENU_SCRIPT = r"""
<script>
(()=>{
 const menus=()=>Array.from(document.querySelectorAll('details.node-more-menu'));
 const closeMenu=details=>{if(details.open)details.open=false;};
 const restore=details=>{
   const popover=details._sfPopover;
   const marker=details._sfMarker;
   if(popover&&marker&&marker.parentNode){marker.parentNode.insertBefore(popover,marker);marker.remove();}
   if(popover){popover.style.position='';popover.style.left='';popover.style.right='';popover.style.top='';popover.style.maxHeight='';popover.style.overflowY='';}
   details._sfPopover=null;details._sfMarker=null;
 };
 const position=details=>{
   if(!details.open)return;
   const summary=details.querySelector('summary');
   let popover=details._sfPopover||details.querySelector('.node-more-popover');
   if(!summary||!popover)return;
   if(!details._sfPopover){
     const marker=document.createComment('node-more-popover');
     popover.parentNode.insertBefore(marker,popover);
     document.body.appendChild(popover);
     details._sfPopover=popover;details._sfMarker=marker;
   }
   popover.style.position='fixed';
   popover.style.right='auto';
   popover.style.left='0px';
   popover.style.top='0px';
   popover.style.maxHeight=Math.max(140,window.innerHeight-16)+'px';
   popover.style.overflowY='auto';
   const anchor=summary.getBoundingClientRect();
   const rect=popover.getBoundingClientRect();
   const margin=8;
   let left=anchor.right-rect.width;
   left=Math.max(margin,Math.min(left,window.innerWidth-rect.width-margin));
   let top=anchor.bottom+7;
   if(top+rect.height>window.innerHeight-margin)top=anchor.top-rect.height-7;
   top=Math.max(margin,Math.min(top,window.innerHeight-rect.height-margin));
   popover.style.left=Math.round(left)+'px';
   popover.style.top=Math.round(top)+'px';
 };
 menus().forEach(details=>{
   details.addEventListener('toggle',()=>{
     if(details.open){
       menus().forEach(other=>{if(other!==details)closeMenu(other);});
       requestAnimationFrame(()=>position(details));
     }else restore(details);
   });
 });
 const reposition=()=>menus().filter(details=>details.open).forEach(position);
 addEventListener('resize',reposition);
 addEventListener('scroll',reposition,true);
 document.addEventListener('keydown',event=>{if(event.key==='Escape')menus().forEach(closeMenu);});
 document.addEventListener('click',event=>menus().forEach(details=>{
   if(!details.open)return;
   const popover=details._sfPopover;
   if(!details.contains(event.target)&&!(popover&&popover.contains(event.target)))closeMenu(details);
 }));
})();
</script>
"""


def _node_more_menu_script() -> str:
    return NODE_MORE_MENU_SCRIPT


def _node_main_channel_direct_actions(key: str, name: str) -> str:
    escaped_key = urllib.parse.quote(key, safe="")
    escaped_name = html.escape(name, quote=True)
    return (
        '<div class="node-direct-more-actions">'
        f'<a class="compact-control node-info-control" href="/panel/channels/{escaped_key}/info" title="Info" aria-label="Open {escaped_name} information">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 10.5v6"></path><path d="M12 7.5h.01"></path></svg></a>'
        f'<button class="compact-control node-log-control" type="button" data-node-channel-log-open data-error-endpoint="/panel/channels/{escaped_key}/errors.json" '
        f'data-clear-endpoint="/panel/channels/{escaped_key}/errors/clear" data-channel-name="{escaped_name}" title="Stream logs" aria-label="Open {escaped_name} stream logs">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h9l3 3v14H6z"></path><path d="M15 3.5v4h4"></path><path d="M9 12h6M9 16h6"></path></svg></button>'
        '</div>'
    )


def _panel_page(user: PanelAccessUser, message: str = "") -> str:
    rows: list[str] = []
    active_channels = 0
    # STREAMFORGE_NODE_PANEL_INITIAL_NO_GEO_V1069: dashboard counters do not need remote GeoIP.
    viewer_stats = manager.viewer_stats(include_geo=False)
    channel_viewers = viewer_stats.get("channels", {})
    error_map = _node_recent_channel_errors()
    display_ids = _node_channel_display_ids()
    if _has_panel_permission(user, "channels.view"):
        with manager.lock:
            items = sorted(
                manager.channels.items(),
                key=lambda pair: (
                    _category_sort_value(pair[1].config.category),
                    int(getattr(pair[1].config, "channel_order", 100000) or 100000),
                    pair[1].config.name.lower(),
                ),
            )
        # STREAMFORGE_NODE_DASHBOARD_ACTIVE_NO_FLASH_V94:
        # Initial HTML and /panel/status.json must use the same playable-up
        # definition. Older initial HTML counted desired/starting/restarting
        # states, and even process-alive alone can precede HLS readiness, so
        # configured channels could flash as Active until the first AJAX pass.
        for key, runtime in items:
            status = manager.status(key)
            status_name = str(status.get("status") or "unknown").strip().lower()
            config = runtime.config
            is_live = _node_channel_delivery_status(config, status) == "up"
            if not is_live:
                continue
            active_channels += 1
            escaped_key = urllib.parse.quote(key, safe="")
            safe_key = html.escape(key, quote=True)
            is_main_readonly = bool(config.catalog_owner == "main")
            server_name = "Main Server" if is_main_readonly else manager.node_name
            owner_note = "Main Server Â· read-only" if is_main_readonly else "Node-local channel"
            display_id = str(display_ids.get(key, "â€”"))
            resolution = f"{config.width} Ã— {config.height}" if config.width and config.height else "Source"
            live_speed = float(status.get("speed_x") or 0)
            logo = (
                f'<img class="node-channel-logo" src="{html.escape(config.logo_url, quote=True)}" alt="" loading="lazy" onerror="this.remove()">'
                if config.logo_url else ""
            )
            readonly = '<span class="node-readonly-chip">Main Â· read-only</span>' if is_main_readonly else '<span class="node-readonly-chip">Node Â· local</span>'
            actions: list[str] = []
            if _has_panel_permission(user, "channels.start") and not status.get("alive"):
                actions.append(f'<form method="post" action="/panel/channels/{escaped_key}/start"><button class="compact-control play" title="Start" aria-label="Start channel"><span class="node-control-glyph">â–¶</span></button></form>')
            if _has_panel_permission(user, "channels.stop") and status.get("alive"):
                actions.append(f'<form method="post" action="/panel/channels/{escaped_key}/stop"><button class="compact-control stop" title="Stop" aria-label="Stop channel"><span class="node-control-glyph">â¹</span></button></form>')
            if _has_panel_permission(user, "channels.restart"):
                actions.append(f'<form method="post" action="/panel/channels/{escaped_key}/restart"><button class="compact-control restart" title="Restart" aria-label="Restart channel"><span class="node-control-glyph">â†»</span></button></form>')
            if status.get("hls_ready") and config.output_type == "hls":
                actions.append(f'<a class="btn compact-control http" target="_blank" rel="noopener" href="/panel/channels/{escaped_key}/play" title="HLS preview" aria-label="Play HLS preview"><span class="node-control-glyph">H</span></a>')
            more_actions: list[str] = [
                f'<button type="button" data-node-channel-log-open data-error-endpoint="/panel/channels/{escaped_key}/errors.json" '
                f'data-clear-endpoint="/panel/channels/{escaped_key}/errors/clear" data-channel-name="{html.escape(config.name, quote=True)}">Stream logs</button>'
            ]
            if not is_main_readonly and _has_panel_permission(user, "channels.edit"):
                more_actions.append(f'<a href="/panel/manage/channels/{escaped_key}/edit">Edit</a>')
            if is_main_readonly:
                more_actions.append('<span>Main Server channel Â· read-only</span>')
            more_menu = _node_main_channel_direct_actions(key, config.name) if is_main_readonly else (
                '<details class="node-more-menu"><summary class="compact-control more" title="More actions" aria-label="More actions"><span class="node-control-glyph node-more-glyph" aria-hidden="true"></span></summary>'
                '<div class="node-more-popover">' + ''.join(more_actions) + '<a href="/panel/manage/channels">Open Channels page</a></div></details>'
            )
            # STREAMFORGE_NODE_HIDE_MAIN_SYNC_SOURCE_LIST_V55: do not expose or
            # index Main-owned source URLs/hosts in the Node channel table.
            source_hint = "Main-synced source hidden" if is_main_readonly else _source_endpoint(config.input_url)
            search_value = " ".join((display_id, config.name, key, config.category or "", ("" if is_main_readonly else config.input_url), owner_note, server_name, source_hint)).lower()
            category_text = html.escape(", ".join(_config_categories(config)))
            rows.append(
                f'<tr class="node-control-row" data-channel-row="{safe_key}" data-runtime-live="1" data-channel-search="{html.escape(search_value, quote=True)}">'
                f'<td class="node-main-id">{html.escape(display_id)}</td>'
                f'<td class="node-channel-identity-cell"><div class="node-channel-identity">{logo}<span class="node-channel-copy"><b>{html.escape(config.name)}</b><small>{category_text} Â· {html.escape(config.output_type.upper())}</small>{readonly}</span></div></td>'
                f'<td class="node-server-cell"><span class="node-server-summary"><b>{html.escape(server_name)}</b><small>{html.escape("Main-synced" if is_main_readonly else _source_endpoint(config.input_url))}</small></span></td>'
                f'<td class="node-online-cell"><a class="node-online-badge" data-channel-users href="/panel/sessions?channel={escaped_key}">{int(channel_viewers.get(key, 0) or 0)}</a></td>'
                f'<td class="node-runtime-cell"><div class="node-runtime">{_node_runtime_alert_markup(key, config.name, status, error_map.get(key, []))}<span class="node-runtime-status {html.escape(str(status.get("status") or "unknown"))}" data-channel-status>{html.escape(str(status.get("status") or "unknown"))}</span><b data-channel-uptime>{html.escape(_human_duration((status.get("waiting_seconds") if str(status.get("status") or "").lower() == "waiting" else status.get("uptime_seconds")) or 0))}</b></div></td>'
                f'<td class="node-controls-cell"><div class="actions">{"".join(actions) or "â€”"}</div></td>'
                f'<td class="node-stream-summary"><b data-channel-resolution>{html.escape(str(status.get("resolution") or resolution).replace(" resolution", ""))}</b><small data-channel-bitrate>{int(status.get("bitrate_kbps") or 0)} kb/s</small></td>'
                f'<td class="node-codec"><span class="node-metric-icon video">V</span><b>{html.escape(config.video_codec)}</b><small>{html.escape(config.video_bitrate)}</small></td>'
                f'<td class="node-codec"><span class="node-metric-icon audio">A</span><b>{html.escape(config.audio_codec)}</b><small>{html.escape(config.audio_bitrate)}</small></td>'
                f'<td class="node-mono node-speed-cell"><span class="node-metric-icon speed">Ã—</span><b data-channel-speed>{f"{live_speed:.2f}x" if live_speed > 0 else "â€”"}</b></td>'
                f'<td class="node-mono node-fps-cell"><span class="node-metric-icon fps">F</span><b data-channel-fps>{html.escape(str(status.get("fps") or config.fps or "â€”"))}</b><small>FPS</small></td>'
                f'<td class="node-more-cell">{more_menu}</td>'
                '</tr>'
            )
    metrics = manager.metrics()
    gpu = metrics.get("gpu") or {}
    gpu_hidden = "" if gpu.get("available") else " hidden"
    gpu_usage = "â€”" if gpu.get("usage_percent") is None else f"{float(gpu.get('usage_percent') or 0):.1f}%"
    gpu_name = html.escape(str(gpu.get("name") or "GPU"))
    gpu_mem_total = int(gpu.get("memory_total_bytes") or 0)
    gpu_mem_used = int(gpu.get("memory_used_bytes") or 0)
    gpu_vram = (f"{gpu_mem_used / (1024 ** 3):.2f} / {gpu_mem_total / (1024 ** 3):.2f} GiB" if gpu_mem_total else "shared / unavailable")
    gpu_temp = "Temp â€”" if gpu.get("temperature_c") is None else f"{float(gpu.get('temperature_c') or 0):.0f}Â°C"
    gpu_encoder = html.escape(str(gpu.get("encoder") or "GPU encoder"))
    gpu_streams = max(0, int(gpu.get("active_streams") or 0))
    message_html = f'<div class="ok">{html.escape(message)}</div>' if message else ""
    rows_html = "".join(rows) if rows else '<tr><td colspan="12" class="empty">No live channels are running.</td></tr>'
    total_live_users = int(viewer_stats.get("total_users", 0) or 0)
    channel_colgroup = '''<colgroup>
<col class="node-col-id"><col class="node-col-channel"><col class="node-col-server"><col class="node-col-online"><col class="node-col-status"><col class="node-col-controls"><col class="node-col-stream"><col class="node-col-video"><col class="node-col-audio"><col class="node-col-speed"><col class="node-col-fps"><col class="node-col-more">
</colgroup>'''
    content = f'''{message_html}
<section class="cards">
  <a class="card" href="/panel/sessions"><span>Live users</span><b data-live-users>{total_live_users}</b><small>Click to view sessions</small></a>
  <div class="card"><span>Download</span><b data-network-download>{metrics.get('network_download_mbps', 0):.2f} Mb/s</b><small>{html.escape(str(metrics.get('network_interface') or ''))}</small></div>
  <div class="card"><span>Upload</span><b data-network-upload>{metrics.get('network_upload_mbps', 0):.2f} Mb/s</b><small>{html.escape(str(metrics.get('network_interface') or ''))}</small></div>
  <div class="card"><span>CPU</span><b data-cpu>{metrics.get('cpu_percent', 0)}%</b><small class="hardware-summary"><span><span data-cpu-cores>{int(metrics.get('cpu_cores') or 0)}</span> cores</span><span>Load <span data-cpu-load>{metrics.get('load_1', 0)}</span></span></small></div>
  <div class="card"><span>Memory</span><b data-memory>{metrics.get('memory_percent', 0)}%</b><small class="hardware-summary"><span><span data-memory-used>{float(metrics.get('memory_used_bytes') or 0) / (1024 ** 3):.2f} GiB</span> used</span><span><span data-memory-total>{float(metrics.get('memory_total_bytes') or 0) / (1024 ** 3):.2f} GiB</span> total</span></small></div>
  <div class="card"><span>Disk usage</span><b data-disk>{metrics.get('disk_percent', 0)}%</b><small class="hardware-summary"><span><span data-disk-used>{float(metrics.get('disk_used_bytes') or 0) / (1024 ** 3):.2f} GiB</span> used</span><span><span data-disk-free>{float(metrics.get('disk_free_bytes') or 0) / (1024 ** 3):.2f} GiB</span> free</span></small></div>
  <div class="card node-gpu-card" data-node-gpu-card{gpu_hidden}><span>GPU</span><b data-node-gpu>{gpu_usage}</b><small data-node-gpu-name>{gpu_name}</small><small class="node-gpu-summary"><span data-node-gpu-vram>{gpu_vram}</span><span data-node-gpu-temp>{gpu_temp}</span><span data-node-gpu-encoder>{gpu_encoder}</span><span><b data-node-gpu-streams>{gpu_streams}</b> streams</span></small></div>
  <div class="card"><span>Active channels</span><ßÝ½ã†òµë(š+myÒÖ—FV×3¦6VçFW"–×÷'FçC¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVâ–×÷'FçC¶v£'‚–×÷'FçC·FF–æs£'‚7‚–×÷'FçC¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC¶&6¶w&÷VæC¢3#C#’–×÷'FçC¶6öÆ÷#§f"‚Ò×FW‡B’–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö†VCæF—g¶Ö–â×v–GFƒ£–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö†VB'¶†V–v‡C¦WFò–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC·FF–æs£–×÷'FçC¶&6¶w&÷VæC§G&ç7&VçB–×÷'FçC¶6öÆ÷#¢6ccff2–×÷'FçC¶föçB×6—¦S£'‚–×÷'FçC¶föçB×vV–v‡C£sS–×÷'FçC¶Æ–æRÖ†V–v‡C£ã#R–×÷'FçC·v†—FR×76S¦æ÷w&–×÷'FçC¶÷fW&fÆ÷s¦†–FFVâ–×÷'FçC·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—2–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö†VB6ÖÆÇ¶†V–v‡C¦WFò–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC¶Ö&v–â×F÷£7‚–×÷'FçC·FF–æs£–×÷'FçC¶&6¶w&÷VæC§G&ç7&VçB–×÷'FçC¶6öÆ÷#§f"‚ÒÖ×WFVB’–×÷'FçC¶föçB×6—¦S£‚–×÷'FçC¶Æ–æRÖ†V–v‡C£ã"–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö6Æ÷6W·v–GFƒ£3‚–×÷'FçC¶†V–v‡C£3‚–×÷'FçC¶fÆWƒ£3‚–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC¶Ö&v–ã£–×÷'FçC·FF–æs£–×÷'FçC¶&÷&FW#£‚6öÆ–B36F#VR–×÷'FçC¶&÷&FW"×&F—W3£‡‚–×÷'FçC¶&6¶w&÷VæC¢3s#33–×÷'FçC¶6öÆ÷#¢6F6Svc"–×÷'FçC¶föçB×6—¦S£—‚–×÷'FçC¶Æ–æRÖ†V–v‡C£–×÷'FçC¶7W'6÷#§ö–çFW"–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö6Æ÷6S¦†÷fW'¶&6¶w&÷VæC¢3##3#Cr–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"Ö6öçFVçG¶Ö–âÖ†V–v‡C£–×÷'FçC¶÷fW&fÆ÷s¦WFò–×÷'FçC¶÷fW'67&öÆÂÖ&V†f–÷#¦6öçF–â–×÷'FçC·FF–æs£‚–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC¶v£‡‚–×÷'FçC¶&6¶w&÷VæC¢3CSb–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’ÂææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’æ7F—fW¶F—7Æ“¦&Æö6²–×÷'FçC¶†V–v‡C¦WFò–×÷'FçC·FF–æs£‚‚–×÷'FçC¶&÷&FW"×&F—W3£—‚–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖVçG'“æ"ÂææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’†VFW"'¶†V–v‡C¦WFò–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC·FF–æs£–×÷'FçC¶&6¶w&÷VæC§G&ç7&VçB–×÷'FçC¶6öÆ÷#¢6ff#3VB–×÷'FçC¶föçB×6—¦S£‚–×÷'FçC¶Æ–æRÖ†V–v‡C£ã"–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’¶†V–v‡C¦WFò–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC·FF–æs£–×÷'FçC¶&6¶w&÷VæC§G&ç7&VçB–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’&W¶Ö‚Ö†V–v‡C£#‚–×÷'FçC¶÷fW&fÆ÷s¦WFò–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖÆörÖÆ–æ·¶fÆWƒ£WFò–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖÖVçU¶÷Vå×·¢Ö–æFWƒ£–×÷'FçGÔÖVF–†Ö‚×v–GFƒ£Sc‚—²ææöFR×'VçF–ÖRÖW'&÷"×÷÷fW'·v–GFƒ¦6Æ2ƒgrÒ#‚’–×÷'FçC¶Ö‚Ö†V–v‡C¦6Æ2ƒf‚Ò#‚’–×÷'FçGÒææöFR×'VçF–ÖRÖW'&÷"ÖVçG'’&W¶Ö‚Ö†V–v‡C£c‚–×÷'FçG×Ð  ¢ò¢cãã3ræòÖÆövò&÷w2æB6VçFW&VBæöFR'VçF–ÖR–æF–6F÷"¢ð¢ææöFRÖ6†ææVÂÖ–FVçF—G“¦æ÷Bƒ¦†2‚ææöFRÖ6†ææVÂÖÆövò’—¶v£–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖÖVçW¶Æ–vâ×6VÆc§7G&WF6‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷G·÷6—F–öã§&VÆF—fR–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC·v–GFƒ£3G‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçC¶Ö–â×v–GFƒ£3G‚–×÷'FçC¶Ö&v–ã£–×÷'FçC·FF–æs£–×÷'FçC¶Æ–æRÖ†V–v‡C£–×÷'FçC¶÷fW&fÆ÷s¦†–FFVâ–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖF÷Cç7ç·÷6—F–öã¦'6öÇWFR–×÷'FçC¶–ç6WC£–×÷'FçC·v–GFƒ£3G‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçC¶Ö&v–ã£–×÷'FçC·FF–æs£–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC¶Æ–æRÖ†V–v‡C£–×÷'FçC·G&ç6f÷&Ó¦æöæR–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ç¶föçB×6—¦S£–×÷'FçGÒææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ã£¦&Vf÷&W¶6öçFVçC¢"#¶F—7Æ“¦&Æö6³·v–GFƒ£wƒ¶†V–v‡C£wƒ¶&÷&FW"×&F—W3£SS¶&6¶w&÷VæC¢6ffc¶&÷‚×6†F÷s£'‚&v&ƒ#SRÃ#SRÃ#SRÂã—ÒææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†2ÖW'&÷#ç7ç¶föçB×6—¦S£G‚–×÷'FçC¶föçB×vV–v‡C£“–×÷'FçGÐ¢ò¢cãã3r†VÇF‡’F÷BæBæWWG&ÂæöFRÖ÷&R7F–öâ¢ð¢ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡—¶&6¶w&÷VæC¢3s#33–×÷'FçC¶&÷‚×6†F÷s¦–ç6WB‚33#C#SR–×÷'FçC¶7W'6÷#¦FVfVÇB–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ã£¦&Vf÷&W·v–GFƒ£‡‚–×÷'FçC¶†V–v‡C£‡‚–×÷'FçC¶&6¶w&÷VæC¢3&&C#†b–×÷'FçC¶&÷‚×6†F÷s£7‚&v&ƒC2Ã#ÃC2Âã"’Ã‚&v&ƒC2Ã#ÃC2ÂãCR’–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'“¦fö7W2ÂææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'“¦fö7W2×f—6–&ÆW¶÷WFÆ–æS£–×÷'FçC¶&÷‚×6†F÷s¦æöæR–×÷'FçGÐ¢æ6ö×7BÖ6öçG&öÂæÖ÷&W¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ36#F#VBÂ3&#3sCR’–×÷'FçC¶&÷&FW#£‚6öÆ–B3CcS“fR–×÷'FçC¶6öÆ÷#¢6F6Svc"–×÷'FçC¶&÷‚×6†F÷s¦–ç6WB‚&v&ƒ#SRÃ#SRÃ#SRÂã3R’Ã7‚‚&v&ƒÃÃÂã"’–×÷'FçGÐ¢æ6ö×7BÖ6öçG&öÂæÖ÷&S¦†÷fW'¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ3CcS“fBÂ33CC#S"’–×÷'FçGÐ¢ò¢cããCf–Ww÷'B×6fRæöFR7F–öâÖVçRæB6†&VBÆöw2¢ð¢ææöFRÖÖ÷&R×÷÷fW'·÷6—F–öã¦f—†VB–×÷'FçC·&–v‡C¦WFò–×÷'FçC·F÷£¶ÆVgC£·¢Ö–æFWƒ£C–×÷'FçC¶Ö–â×v–GFƒ£#ƒ¶Ö‚×v–GFƒ¦Ö–âƒ3‚Æ6Æ2ƒgrÒg‚’“¶Ö‚Ö†V–v‡C¦6Æ2ƒf‚Òg‚“¶÷fW&fÆ÷r×“¦WFó·FF–æs£wƒ¶&÷&FW#£‚6öÆ–B33SSfC¶&÷&FW"×&F—W3£ƒ¶&6¶w&÷VæC¢3“#3¶&÷‚×6†F÷s£‡‚Cg‚&v&ƒÃÃÂãS‚—Ð¢ææöFRÖÖ÷&R×6÷W&6W¶F—7Æ“¦æöæR–×÷'FçGÐ¢ææöFRÖÖ÷&R×÷÷fW#ç7ç¶6öÆ÷#§f"‚ÒÖ×WFVB“¶7W'6÷#¦FVfVÇGÐ¢ææöFRÖÖ÷&R×÷÷fW"ÂææöFRÖÖ÷&R×÷÷fW"'WGFöâÂææöFRÖÖ÷&R×÷÷fW#ç7ç¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‡Ð ¢ò¢cãã3’—†VÂÖ6VçFW&VBæöFRÖ÷&R–æF–6F÷"¢ð¢æ6ö×7BÖ6öçG&öÂæÖ÷&W·÷6—F–öã§&VÆF—fR–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC¶÷fW&fÆ÷s¦†–FFVâ–×÷'FçGÐ¢ææöFRÖÖ÷&RÖvÇ—‡·÷6—F–öã¦'6öÇWFR–×÷'FçC¶–ç6WC£–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC·v–GFƒ£R–×÷'FçC¶†V–v‡C£R–×÷'FçC¶föçB×6—¦S£–×÷'FçC¶Æ–æRÖ†V–v‡C£–×÷'FçC·G&ç6f÷&Ó¦æöæR–×÷'FçC·ö–çFW"ÖWfVçG3¦æöæR–×÷'FçGÐ¢ææöFRÖÖ÷&RÖvÇ—ƒ£¦&Vf÷&W¶6öçFVçC¢"#·÷6—F–öã¦'6öÇWFS¶ÆVgC£SS·F÷£SS·v–GFƒ£7ƒ¶†V–v‡C£7ƒ¶&÷&FW"×&F—W3£SS¶&6¶w&÷VæC¢6S6VFcc·G&ç6f÷&Ó§G&ç6ÆFR‚ÓSRÂÓSR“¶&÷‚×6†F÷s£Óg‚6S6VFcbÃg‚6S6VFcgÐ ¢ò¢cããCrW'6—7FVçBæöFRv&æ–ær–6öâv—F†÷WBFööÇF—6WVFòÖVÆVÖVçB6öÆÆ—6–öâ¢ð¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷G°¢÷6—F–öã§&VÆF—fR–×÷'FçC°¢F—7Æ“¦&Æö6²–×÷'FçC°¢&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçC°¢v–GFƒ£3G‚–×÷'FçC°¢†V–v‡C£3G‚–×÷'FçC°¢Ö–â×v–GFƒ£3G‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢FF–æs£–×÷'FçC°¢&÷&FW#£–×÷'FçC°¢Æ–æRÖ†V–v‡C£–×÷'FçC°¢föçB×6—¦S£–×÷'FçC°¢÷fW&fÆ÷s§f—6–&ÆR–×÷'FçC°§Ð¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Cç7ç°¢÷6—F–öã¦'6öÇWFR–×÷'FçC°¢–ç6WC£–×÷'FçC°¢v–GFƒ£3G‚–×÷'FçC°¢†V–v‡C£3G‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢FF–æs£–×÷'FçC°¢F—7Æ“¦w&–B–×÷'FçC°¢Æ6RÖ—FV×3¦6VçFW"–×÷'FçC°¢6öÆ÷#¢6ffb–×÷'FçC°¢föçC£“G‚ó&–ÂÇ6ç2×6W&–b–×÷'FçC°¢ö–çFW"ÖWfVçG3¦æöæR–×÷'FçC°¢&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçC°§Ð¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ç¶föçB×6—¦S£–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ã£¦&Vf÷&W°¢6öçFVçC¢""–×÷'FçC°¢F—7Æ“¦&Æö6²–×÷'FçC°¢v–GFƒ£‡‚–×÷'FçC°¢†V–v‡C£‡‚–×÷'FçC°¢&÷&FW"×&F—W3£SR–×÷'FçC°¢&6¶w&÷VæC¢3&&C#†b–×÷'FçC°¢&÷‚×6†F÷s£7‚&v&ƒC2Ã#ÃC2Âã"’Ã‚&v&ƒC2Ã#ÃC2ÂãCR’–×÷'FçC°§Ð¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†2ÖW'&÷#ç7ç¶föçB×6—¦S£G‚–×÷'FçGÐ  ¢ò¢cããSÆörÖG&—fVâv&æ–ær7FFRæB7F÷VBw&’–æF–6F÷"¢ð¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡’À¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ–FÆW·÷6—F–öã§&VÆF—fR–×÷'FçC¶&6¶w&÷VæC¢3s#33–×÷'FçC¶6öÆ÷#§G&ç7&VçB–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7âÀ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ–FÆSç7ç·÷6—F–öã¦'6öÇWFR–×÷'FçC¶–ç6WC£–×÷'FçC·v–GFƒ£3G‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçC¶F—7Æ“¦&Æö6²–×÷'FçC¶föçB×6—¦S£–×÷'FçC¶Æ–æRÖ†V–v‡C£–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ã£¦&Vf÷&RÀ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ–FÆSç7ã£¦&Vf÷&W¶6öçFVçC¢""–×÷'FçC·÷6—F–öã¦'6öÇWFR–×÷'FçC¶ÆVgC£SR–×÷'FçC·F÷£SR–×÷'FçC·v–GFƒ£‡‚–×÷'FçC¶†V–v‡C£‡‚–×÷'FçC¶Ö&v–ã£–×÷'FçC¶&÷&FW"×&F—W3£SR–×÷'FçC·G&ç6f÷&Ó§G&ç6ÆFR‚ÓSRÂÓSR’–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†VÇF‡“ç7ã£¦&Vf÷&W¶&6¶w&÷VæC¢3&&C#†b–×÷'FçC¶&÷‚×6†F÷s£7‚&v&ƒC2Ã#ÃC2Âã"’Ã‚&v&ƒC2Ã#ÃC2ÂãCR’–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ–FÆSç7ã£¦&Vf÷&W¶&6¶w&÷VæC¢3v#ƒs“B–×÷'FçC¶&÷‚×6†F÷s£7‚&v&ƒ#2Ã3RÃC‚Âã’–×÷'FçGÐ¢ææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'’ææöFR×'VçF–ÖRÖÆW'BÖF÷Bæ†2ÖW'&÷#ç7ç·÷6—F–öã¦'6öÇWFR–×÷'FçC¶–ç6WC£–×÷'FçC·v–GFƒ£3G‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC¶föçC£“G‚ó&–ÂÇ6ç2×6W&–b–×÷'FçC¶6öÆ÷#¢6ffb–×÷'FçGÐ ¢ò¢cããC6†ææVÂ7G&VÒÆörÖöFÂ¢ð¦&öG’ææöFRÖ6†ææVÂÖÆörÖ÷Vç¶÷fW&fÆ÷s¦†–FFVçÒææöFRÖ6†ææVÂÖÆörÖ÷fW&Æ—·÷6—F–öã¦f—†VC¶–ç6WC£·¢Ö–æFWƒ£3¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#·FF–æs£‡ƒ¶&6¶w&÷VæC§&v&ƒ"ÃbÃÂãsB“¶&6¶G&÷Öf–ÇFW#¦&ÇW"ƒ7‚—ÒææöFRÖ6†ææVÂÖÆörÖ÷fW&Æ•¶†–FFVå×¶F—7Æ“¦æöæWÒææöFRÖ6†ææVÂÖÆörÖF–Æöw·v–GFƒ¦Ö–âƒ#‚Æ6Æ2ƒgrÒ#‡‚’“¶Ö‚Ö†V–v‡C¦Ö–âƒƒ#‚Æ6Æ2ƒf‚Ò#‡‚’“¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶&÷&FW#£‚6öÆ–B3F#S“c“¶&÷&FW"×&F—W3£Gƒ¶&6¶w&÷VæC¢33“C3Fc¶6öÆ÷#¢6VVcFf¶&÷‚×6†F÷s£3‚“‚&v&ƒÃÃÂãr“¶÷fW&fÆ÷s¦†–FFVçÒææöFRÖ6†ææVÂÖÆörÖ†VFW'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£gƒ·FF–æs£g‚‡ƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–B&v&ƒ#SRÃ#SRÃ#SRÂã‚“¶&6¶w&÷VæC¢36CCsSGÒææöFRÖ6†ææVÂÖÆörÖ†VFW"ƒ'¶Ö&v–ã£¶föçB×6—¦S£w‡ÒææöFRÖ6†ææVÂÖÆörÖ†VFW"¶Ö&v–ã£G‚¶6öÆ÷#¢6&F3†C#¶föçB×6—¦S£‡ÒææöFRÖ6†ææVÂÖÆörÖ†VFW#æ'WGFöç·v–GFƒ£3Gƒ¶†V–v‡C£3Gƒ¶Ö–âÖ†V–v‡C£3Gƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#·FF–æs£¶&÷&FW#£¶&6¶w&÷VæC§G&ç7&VçC¶6öÆ÷#¢66&CFFC¶föçB×6—¦S£#Gƒ¶Æ–æRÖ†V–v‡C£ÒææöFRÖ6†ææVÂÖÆörÖ†VFW#æ'WGFöã¦†÷fW'¶&6¶w&÷VæC§&v&ƒ#SRÃ#SRÃ#SRÂã‚—ÒææöFRÖ6†ææVÂÖÆör×FööÆ&'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£gƒ¶fÆW‚×w&§w&·FF–æs£G‚gƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–B&v&ƒ#SRÃ#SRÃ#SRÂã‚“¶&6¶w&÷VæC¢33“C3FgÒææöFRÖ6†ææVÂÖÆör×FööÆ&#æÆ&VÇ¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã§&÷s¶Æ–vâÖ—FV×3¦6VçFW#¶v£w‡ÒææöFRÖ6†ææVÂÖÆör×FööÆ&"6VÆV7G·v–GFƒ£c‡ƒ·FF–æs£wƒ¶&6¶w&÷VæC¢3CcSVWÒææöFRÖ6†ææVÂÖÆör×FööÆ&#å¶FFÖæöFRÖ6†ææVÂÖÆörÖ6÷VçE×¶Ö&v–âÖÆVgC¦WFó¶6öÆ÷#¢6&F3†C#¶föçB×6—¦S£'‡ÒææöFRÖ6†ææVÂÖÆörÖ7W'&VçG¶F—7Æ“¦fÆWƒ¶v£'ƒ¶Æ–vâÖ—FV×3¦fÆW‚×7F'C·FF–æs£‚gƒ¶&6¶w&÷VæC¢3Ss3c¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–B3†CSC&#¶6öÆ÷#¢6ffS&GÒææöFRÖ6†ææVÂÖÆörÖ7W'&VçE¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÒææöFRÖ6†ææVÂÖÆörÖ7W'&VçB'·v†—FR×76S¦æ÷w&ÒææöFRÖ6†ææVÂÖÆörÖ7W'&VçB7ç¶÷fW&fÆ÷r×w&¦ç—v†W&WÒææöFRÖ6†ææVÂÖÆör×F&ÆR×w&¶Ö–âÖ†V–v‡C£¶÷fW&fÆ÷s¦WFó·FF–æs£‡‚g‚¶&6¶w&÷VæC¢33“C3FgÒææöFRÖ6†ææVÂÖÆör×F&ÆW·v–GFƒ£S¶&÷&FW#£¶&÷&FW"Ö6öÆÆ6S§6W&FS¶&÷&FW"×76–æs£‡ƒ¶&÷&FW"×&F—W3£¶&6¶w&÷VæC§G&ç7&VçC¶÷fW&fÆ÷s§f—6–&ÆWÒææöFRÖ6†ææVÂÖÆör×F&ÆRF†VBF‡·FF–æs£‚7ƒ¶&÷&FW#£¶&6¶w&÷VæC§G&ç7&VçC¶6öÆ÷#¢6&F3†C#¶föçB×6—¦S£ƒ·FW‡B×G&ç6f÷&Ó§WW&66S¶ÆWGFW"×76–æs¢ãFV×ÒææöFRÖ6†ææVÂÖÆör×F&ÆRF&öG’FG·FF–æs£G‚7ƒ¶&÷&FW#£¶&6¶w&÷VæC¢3CcSVC¶6öÆ÷#¢6CvFfSs·fW'F–6ÂÖÆ–vã¦Ö–FFÆWÒææöFRÖ6†ææVÂÖÆör×&÷w¶7W'6÷#§ö–çFW'ÒææöFRÖ6†ææVÂÖÆör×&÷s¦†÷fW"FG¶&6¶w&÷VæC¢3FCS“cgÒææöFRÖ6†ææVÂÖÆörÖ7F–öç¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Ö–â×v–GFƒ£3'ƒ·FF–æs£w‚Gƒ¶&6¶w&÷VæC¢3&#“c6c¶6öÆ÷#¢6ffc¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£ƒ·FW‡B×G&ç6f÷&Ó§WW&66WÒææöFRÖ6†ææVÂÖÆörÖ7F–öâç7F÷VBÂææöFRÖ6†ææVÂÖÆörÖ7F–öâæ–æf÷¶&6¶w&÷VæC¢3cCsƒÒææöFRÖ6†ææVÂÖÆörÖ7F–öâçv&æ–æw¶&6¶w&÷VæC¢6CCss'ÒææöFRÖ6†ææVÂÖÆörÖ7F–öâæW'&÷"ÂææöFRÖ6†ææVÂÖÆörÖ7F–öâç7F'BÖf–ÆVBÂææöFRÖ6†ææVÂÖÆörÖ7F–öâç7G&VÒÖf–ÆVG¶&6¶w&÷VæC¢6CƒS'ÒææöFRÖ6†ææVÂÖÆörÖFWF–Ç2FG·FF–æs£7‚7‚–×÷'FçC¶&6¶w&÷VæC¢3CcSVB–×÷'FçGÒææöFRÖ6†ææVÂÖÆörÖFWF–Ç2'¶F—7Æ“¦&Æö6³¶Ö&v–âÖ&÷GFöÓ£w‡ÒææöFRÖ6†ææVÂÖÆörÖFWF–Ç2&W¶Ö&v–ã£·FF–æs£ƒ¶&÷&FW"×&F—W3£Wƒ¶&6¶w&÷VæC¢3##ƒ3#¶6öÆ÷#¢6CFFFSc·v†—FR×76S§&R×w&¶÷fW&fÆ÷r×w&¦ç—v†W&S¶Ö‚Ö†V–v‡C£#3ƒ¶÷fW&fÆ÷s¦WF÷ÒææöFRÖ6†ææVÂÖÆörÖÆöF–æw·FW‡BÖÆ–vã¦6VçFW"–×÷'FçC·FF–æs£3‡‚–×÷'FçC¶6öÆ÷#¢63–C6FB–×÷'FçGÒææöFRÖ6†ææVÂÖÆörÖfö÷FW'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£Gƒ·FF–æs£7‚g‚Wƒ¶&6¶w&÷VæC¢33“C3Fc¶6öÆ÷#¢63F6VCƒ¶föçB×6—¦S£'‡ÒææöFRÖ6†ææVÂÖÆörÖfö÷FW"æg¶F—7Æ“¦fÆW‡ÒææöFRÖ6†ææVÂÖÆörÖfö÷FW"æb'WGFöâÂææöFRÖ6†ææVÂÖÆörÖfö÷FW"æb7ç¶Ö–â×v–GFƒ£3gƒ¶†V–v‡C£3gƒ¶Ö–âÖ†V–v‡C£3gƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶Ö&v–âÖÆVgC¢Óƒ·FF–æs£ƒ¶&÷&FW#£‚6öÆ–B3S“ccsc¶&÷&FW"×&F—W3£¶&6¶w&÷VæC¢3CcSVS¶6öÆ÷#¢6ffgÒææöFRÖ6†ææVÂÖÆörÖfö÷FW"æb'WGFöâæ7F—fW¶&6¶w&÷VæC¢3V3†VSƒ¶&÷&FW"Ö6öÆ÷#¢3V3†VS‡ÒææöFRÖ6†ææVÂÖÆörÖfö÷FW"æb'WGFöã¦F—6&ÆVG¶÷6—G“¢ãSWÒææöFR×'VçF–ÖRÖÆW'BÖÖVçSç7VÖÖ'•¶FFÖæöFR×'VçF–ÖRÖÆW'E×¶7W'6÷#§ö–çFW'ÔÖVF–†Ö‚×v–GFƒ£sc‚—²ææöFRÖ6†ææVÂÖÆörÖ÷fW&Æ—·FF–æs£‡‡ÒææöFRÖ6†ææVÂÖÆörÖF–Æöw·v–GFƒ¦6Æ2ƒgrÒg‚“¶Ö‚Ö†V–v‡C¦6Æ2ƒf‚Òg‚—ÒææöFRÖ6†ææVÂÖÆör×F&ÆW¶Ö–â×v–GFƒ£s#‡ÒææöFRÖ6†ææVÂÖÆörÖfö÷FW'¶Æ–vâÖ—FV×3¦fÆW‚×7F'C¶fÆW‚ÖF—&V7F–öã¦6öÇVÖç×Ð   ¢ò¢cããS‚W†7BæöFR6†ææVÂ†VFW"öFFÆ–væÖVçB¢ð¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×6VÆV7BÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×6VÆV7BÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖÖ–âÖ–BÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖÖ–âÖ–BÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖöæÆ–æRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖöæÆ–æRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×'VçF–ÖRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×'VçF–ÖRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖ6öçG&öÇ2Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖ6öçG&öÇ2Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×7G&VÒ×7VÖÖ'’À¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×7G&VÒ×7VÖÖ'’À¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖ6öFV2À¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖ6öFV2À¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×7VVBÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×7VVBÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖg2Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖg2Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖÖ÷&RÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖÖ÷&RÖ6VÆÇ·FW‡BÖÆ–vã¦6VçFW"–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖ6†ææVÂÖ–FVçF—G’Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖ6†ææVÂÖ–FVçF—G’Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×6W'fW"Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×6W'fW"Ö6VÆÇ·FW‡BÖÆ–vã¦ÆVgB–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFR×'VçF–ÖRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×'VçF–ÖRÖ6VÆÇ·FF–ærÖÆVgC£‡‚–×÷'FçC·FF–ær×&–v‡C£‡‚–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFR×'VçF–ÖRÖ6VÆÃâææöFR×'VçF–ÖW¶F—7Æ“¦–æÆ–æRÖfÆW‚–×÷'FçC¶Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC¶§W7F–g’Ö6öçFVçC¦6VçFW"–×÷'FçC·v–GFƒ¦WFò–×÷'FçC¶Ö–â×v–GFƒ£–×÷'FçC¶Ö‚×v–GFƒ£R–×÷'FçC¶Ö&v–ã£WFò–×÷'FçC·fW'F–6ÂÖÆ–vã¦Ö–FFÆR–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖ6öçG&öÇ2Ö6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖ6öçG&öÇ2Ö6VÆÇ·FF–ærÖÆVgC£w‚–×÷'FçC·FF–ær×&–v‡C£w‚–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖ6öçG&öÇ2Ö6VÆÃâæ7F–öç7¶F—7Æ“¦–æÆ–æRÖfÆW‚–×÷'FçC¶Æ–vâÖ—FV×3¦6VçFW"–×÷'FçC¶§W7F–g’Ö6öçFVçC¦6VçFW"–×÷'FçC·v–GFƒ¦WFò–×÷'FçC¶Ö&v–ã£WFò–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF‚ææöFRÖöæÆ–æRÖ6VÆÂÀ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖöæÆ–æRÖ6VÆÇ·FF–ærÖÆVgC£‡‚–×÷'FçC·FF–ær×&–v‡C£‡‚–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRFBææöFRÖöæÆ–æRÖ6VÆÃâææöFRÖöæÆ–æRÖ&FvW¶Ö&v–âÖÆVgC¦WFò–×÷'FçC¶Ö&v–â×&–v‡C¦WFò–×÷'FçGÐ¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRF†VBF‡·fW'F–6ÂÖÆ–vã¦Ö–FFÆR–×÷'FçGÐ ¢ò¢cããS‚Væ–f–VBæöFR6†ææVÂVF—F÷"f–VÆB6—¦–æræBÆ–væÖVçB¢ð¢æ6†ææVÂÖVF—F÷"ÖÖ–ç·v–GFƒ¦Ö–âƒCS‚ÃR“¶Ö&v–ã£WF÷Òæ6†ææVÂÖVF—F÷"Öf÷&×¶F—7Æ“¦w&–C¶v£‡‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’“¶v£g‚‡ƒ·FF–æs£#ƒ¶Æ–vâÖ—FV×3§7F'GÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÂÂæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–Câæ6†ææVÂÖÆövòÖf–VÆG¶w&–BÖ6öÇVÖã§7âc¶Ö–â×v–GFƒ£Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–Câçv–FW¶w&–BÖ6öÇVÖã£òÓÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÃ¦æ÷B‚æ6†V6²’Âæ6†ææVÂÖVF—F÷"Öf÷&Òæ6†ææVÂÖÆövòÖf–VÆG¶F—7Æ“¦w&–C¶w&–B×FV×ÆFR×&÷w3¦WFòC'‚WFó¶v£wƒ¶Æ–vâÖ6öçFVçC§7F'GÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf–VÆBÖÆ&VÇ¶Ö–âÖ†V–v‡C£Wƒ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£'ƒ¶Æ–æRÖ†V–v‡C£W‡Òæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWC¦æ÷B…·G—SÖ6†V6¶&÷…Ò“¦æ÷B…·G—SÖf–ÆUÒ’Âæ6†ææVÂÖVF—F÷"Öf÷&Ò6VÆV7G¶†V–v‡C£C'ƒ¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£'‡Òæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE·G—SÖf–ÆU×¶†V–v‡C£C'ƒ¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£W‚w‡Òæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE·G—SÖf–ÆUÓ£¦f–ÆR×6VÆV7F÷"Ö'WGFöç¶†V–v‡C£3ƒ¶Ö&v–ã¢Ó‚‚Ó‚Ó'ƒ·FF–æs£'ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR×7G&öær“¶&÷&FW"×&F—W3£gƒ¶&6¶w&÷VæC¢3ƒ#C3#¶6öÆ÷#§f"‚Ò×FW‡B“¶föçC¦–æ†W&—GÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf–VÆBÖ†VÇ¶Ö–âÖ†V–v‡C£gƒ¶Ö&v–ã£Òæ6†ææVÂÖVF—F÷"Öf÷&Òæ6†V6·¶Ö–âÖ†V–v‡C£C‡ƒ·FF–æs£'‚Gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£—ƒ¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"“¶Æ–vâ×6VÆc§7F'GÒæ6†ææVÂÖVF—F÷"Öf÷&ÒæÆövò×&Wf–Ww¶†V–v‡C£C'ƒ¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£G‚—‡Òæ6†ææVÂÖVF—F÷"Öf÷&ÒæÆövò×&Wf–Wr–Öw·v–GFƒ£3'ƒ¶†V–v‡C£3'ƒ¶fÆWƒ£3'‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£c'‚Ö–æÖ‚ƒ3C‚Ãg"’Ö–æÖ‚ƒ#c‚Ã3c‚’“gƒ¶Ö–âÖ†V–v‡C£cgƒ¶v£'ƒ·FF–æs£‚'‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Â–çWG¶†V–v‡C£C'‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖ7F–öç7·÷6—F–öã§&VÆF—fS¶Ö–âÖ†V–v‡C£c'ƒ¶Æ–vâÖ—FV×3¦6VçFW#·FF–æs£‚'ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£ƒ¶&6¶w&÷VæC§&v&ƒRÃ#"Ã3Âã“R“¶&÷‚×6†F÷s£'‚3G‚&v&ƒÃÃÂã#‚“¶&6¶G&÷Öf–ÇFW#¦&ÇW"ƒ‡‚—Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖ7F–öç2æ'FâÂæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖ7F–öç2'WGFöç¶Ö–â×v–GFƒ£'ƒ¶Ö–âÖ†V–v‡C£C‡ÔÖVF–†Ö‚×v–GFƒ£“ƒ‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÂÂæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–Câæ6†ææVÂÖÆövòÖf–VÆG¶w&–BÖ6öÇVÖã£òÓ×ÔÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"ÖÖ–ç·v–GFƒ£WÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£g#·FF–æs£G‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æfòÂæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç2Âæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&–÷&—G—·FF–ærÖÆVgC£3‡‡×Ð ¢ò¢cããS‚6ö×7BæöFR6†ææVÂVF—F÷"6öçG&öÇ2æB6÷W&6R&÷w2¢ð¢æ6†ææVÂÖVF—F÷"ÖÖ–ç·v–GFƒ¦Ö–âƒ3#‚ÃR—Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–G¶v£G‚gƒ·FF–æs£‡‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÃ¦æ÷B‚çv–FR“¦æ÷B‚æ6†V6²—·v–GFƒ£S¶Ö‚×v–GFƒ£SCƒ¶§W7F–g’×6VÆc§7F'GÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖS×&öw&Õö–EÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖf–Æ&6µö–çFW'fÅÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖS×f–FVõö&—G&FUÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖVF–õö&—G&FUÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖg5ÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖS×v–GF…ÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖ†V–v‡EÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖ†Ç5÷6VvÖVçE÷F–ÖU×¶Ö‚×v–GFƒ£##‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Ò6VÆV7E¶æÖS×&W6WEÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖ6FVv÷'•×¶Ö‚×v–GFƒ£3c‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Ò6VÆV7E¶æÖS×f–FVõö6öFV5ÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò6VÆV7E¶æÖSÖVF–õö6öFV5ÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò6VÆV7E¶æÖSÖ÷WGWE÷G—UÒÂæ6†ææVÂÖVF—F÷"Öf÷&Ò7&W6öÇWF–öâ×&W6WG¶Ö‚×v–GFƒ£S#‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖÆövõ÷W&Å×¶Ö‚×v–GFƒ£“‡Òç&VÖ÷FRÖ÷WGWBÖVF—F÷'¶F—7Æ“¦w&–C¶v£—‡Òç&VÖ÷FRÖ÷WGWBÖ†VG¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦VæC¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£‡Òç&VÖ÷FRÖ÷WGWBÖÆ—7G¶F—7Æ“¦w&–C¶v£‡‡Òç&VÖ÷FRÖ÷WGWB×&÷w¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£3'‚Ö–æÖ‚ƒÃg"’C'ƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW'Òç&VÖ÷FRÖ÷WGWB×&÷r–çWG·v–GFƒ£S¶Ö‚×v–GFƒ¦æöæWÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Ò–çWE¶æÖSÖÆövõöf–ÆU×¶Ö‚×v–GFƒ£S‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷w¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ3‚ÃS#‚’##ƒ¶Æ–vâÖ—FV×3¦VæC¶§W7F–g’Ö6öçFVçC§7F'C¶v£gƒ¶Ö–â×v–GFƒ£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷sâæf–Æ&6²×FövvÆW·v–GFƒ£S¶†V–v‡C£C'ƒ¶Ö–âÖ†V–v‡C£C'ƒ¶Æ–vâ×6VÆc¦VæC·FF–æs£G‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷sâæf–Æ&6²Ö–çFW'fÂÖf–VÆG¶F—7Æ“¦w&–C¶w&–B×FV×ÆFR×&÷w3¦WFòC'ƒ¶v£wƒ·v–GFƒ£##ƒ¶Ö–â×v–GFƒ£¶föçB×6—¦S£'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷sâæf–Æ&6²Ö–çFW'fÂÖf–VÆB–çWG·v–GFƒ£S¶Ö‚×v–GFƒ¦æöæWÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£c'‚Ö–æÖ‚ƒ3C‚Ãs#‚’Ö–æÖ‚ƒ3‚Ã#‚’WFó¶§W7F–g’Ö6öçFVçC§7F'C¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶Ö–âÖ†V–v‡C£c'ƒ·FF–æs£—‚'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Ç¶Ö–â×v–GFƒ£·v–GFƒ£WÒæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Â–çWG·v–GFƒ£S¶Ö‚×v–GFƒ£s#‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æf÷¶§W7F–g’Ö6öçFVçC¦fÆW‚×7F'C¶Ö–â×v–GFƒ£3‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7¶§W7F–g’Ö6öçFVçC¦fÆW‚×7F'C·v†—FR×76S¦æ÷w&Ð¤ÖVF–†Ö‚×v–GFƒ£“ƒ‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ#ƒ‚Ãg"’#‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷sâæf–Æ&6²Ö–çFW'fÂÖf–VÆG·v–GFƒ£#‡×Ð¤ÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÃ¦æ÷B‚çv–FR“¦æ÷B‚æ6†V6²’Âæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÃæ–çWBÂæ6†ææVÂÖVF—F÷"Öf÷&Òæf÷&ÒÖw&–CæÆ&VÃç6VÆV7G¶Ö‚×v–GFƒ¦æöæWÒæ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'Òæ6†ææVÂÖVF—F÷"Öf÷&Òæf–Æ&6²Ö6öçG&öÂ×&÷sâæf–Æ&6²Ö–çFW'fÂÖf–VÆG·v–GFƒ£S¶Ö‚×v–GFƒ£##‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æfòÂæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç2Âæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&–÷&—G—·FF–ærÖÆVgC£3‡‡×Ð  ¢ò¢cããc26ö×7B6–ævÆRÖÆ–æRæöFR6†ææVÂVF—F÷"w&÷W2¢ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7B×æVÂÖ†VG·FF–æs£7‚‡‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ3‚Ãg"’S‚“‚Ö–æÖ‚ƒ##‚Ã#“‚“¶v£'ƒ¶Æ–vâÖ—FV×3¦VæC¶§W7F–g’Ö6öçFVçC§7F'C¶Ö–â×v–GFƒ£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sæÆ&VÇ¶Ö–â×v–GFƒ£¶Ö&v–ã£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæf–Æ&6²×FövvÆW¶†V–v‡C£3‡ƒ¶Ö–âÖ†V–v‡C£3‡ƒ·FF–æs£'ƒ¶Æ–vâ×6VÆc¦VæC·v†—FR×76S¦æ÷w&Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæ6ö×7BÖf–VÆG¶F—7Æ“¦w&–C¶w&–B×FV×ÆFR×&÷w3£G‚3‡ƒ¶v£Wƒ·v–GFƒ£S¶föçB×6—¦S£'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷r–çWBÂæ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷r6VÆV7G¶†V–v‡C£3‡‚–×÷'FçC¶Ö–âÖ†V–v‡C£3‡‚–×÷'FçC·FF–æs£‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç&öw&ÒÖf–VÆB6VÆV7G·v–GFƒ£S¶Ö–â×v–GFƒ£·FF–ær×&–v‡C£#‡‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£c'‚Ö–æÖ‚ƒ3C‚Ãs‚’Ö–æÖ‚ƒ3‚Ã#‚’WFó¶Ö–âÖ†V–v‡C£S'ƒ·FF–æs£w‚'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Ç¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£3‚Ö–æÖ‚ƒÃg"“¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW'Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Â–çWG¶w&–BÖ6öÇVÖã£#¶†V–v‡C£3‡‚–×÷'FçC¶Ö–âÖ†V–v‡C£3‡‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&æ·¶w&–B×&÷s£·v–GFƒ£#‡ƒ¶†V–v‡C£#‡‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–G¶F—7Æ“¦w&–C¶Æ–vâÖ—FV×3¦VæC¶§W7F–g’Ö6öçFVçC§7F'C¶v£'ƒ·FF–æs£g‚‡‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–CæÆ&VÇ¶w&–BÖ6öÇVÖã¦WFò–×÷'FçC·v–GFƒ£R–×÷'FçC¶Ö‚×v–GFƒ¦æöæR–×÷'FçC¶Ö–â×v–GFƒ£¶Ö–âÖ†V–v‡C£–×÷'FçC¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFR×&÷w3£G‚3‡‚–×÷'FçC¶v£W‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–B–çWBÂæ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–B6VÆV7G¶†V–v‡C£3‡‚–×÷'FçC¶Ö–âÖ†V–v‡C£3‡‚–×÷'FçC·FF–æs£‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òçf–FVòÖVæ6öF–ærÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£3‚3‚#C‚W‚‚‚SW‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£3‚3‚3‚#W‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–Câæ÷WGWB×W&ÂÖf–VÆG¶w&–BÖ6öÇVÖã£óB–×÷'FçC·v–GFƒ¦Ö–âƒsC‚ÃR’–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–B–çWG¶Ö‚×v–GFƒ¦æöæR–×÷'FçGÒæ6†ææVÂÖVF—F÷"Öf÷&Òæ6ö×7BÖVæ6öF–ærÖw&–B6VÆV7G¶Ö‚×v–GFƒ¦æöæR–×÷'FçGÐ¤ÖVF–†Ö‚×v–GFƒ£#ƒ‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ#ƒ‚Ãg"’C‚ƒ‚#3‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òçf–FVòÖVæ6öF–ærÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£#s‚#W‚##‚‚W‚W‚CWƒ¶v£‡×Ð¤ÖVF–†Ö‚×v–GFƒ£ƒ‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"S‚“‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæ6FVv÷'’Öf–VÆG¶w&–BÖ6öÇVÖã£ó7Òæ6†ææVÂÖVF—F÷"Öf÷&Òçf–FVòÖVæ6öF–ærÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒBÆÖ–æÖ‚ƒ3‚Ãg"’—Òæ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ2ÆÖ–æÖ‚ƒS‚Ãg"’—Òæ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–Câæ÷WGWB×W&ÂÖf–VÆG¶w&–BÖ6öÇVÖã£òÓ–×÷'FçG×Ð¤ÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷rÂæ6†ææVÂÖVF—F÷"Öf÷&Òçf–FVòÖVæ6öF–ærÖw&–BÂæ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'Òæ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæ6FVv÷'’Öf–VÆBÂæ6†ææVÂÖVF—F÷"Öf÷&ÒæVF–òÖ÷WGWBÖw&–Câæ÷WGWB×W&ÂÖf–VÆG¶w&–BÖ6öÇVÖã¦WFò–×÷'FçC·v–GFƒ£R–×÷'FçGÒæ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæf–Æ&6²×FövvÆW·v†—FR×76S¦æ÷&ÖÃ¶†V–v‡C¦WFó¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£—‚'‡×Ð  ¢ò¢cããc2Æ–væVBæöFR6÷W&6R&÷w2æB6öæ6—6R6öFV2Æ&VÇ2¢ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖÆ—7G·v–GFƒ£S¶F—7Æ“¦w&–GÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w·v–GFƒ£S¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£s'‚Ö–æÖ‚ƒ3c‚Ãs‚’Ö–æÖ‚ƒƒ‚Ã#ƒ‚’“Gƒ¶6öÇVÖâÖv£'ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§7F'C¶Ö–âÖ†V–v‡C£SGƒ·FF–æs£‡‚'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&–÷&—G—¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£#‡‚#‡ƒ¶v£gƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§7F'C·v–GFƒ£c'‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&–÷&—G’æ'Fç·v–GFƒ£#‡ƒ¶Ö–â×v–GFƒ£#‡ƒ¶†V–v‡C£3'ƒ¶Ö–âÖ†V–v‡C£3'ƒ·FF–æs£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Ç·v–GFƒ£S¶Ö–â×v–GFƒ£¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£3‚Ö–æÖ‚ƒÃg"“¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW'Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×W&Â–çWG¶w&–BÖ6öÇVÖã£#·v–GFƒ£S¶Ö‚×v–GFƒ¦æöæS¶Ö&v–ã£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&æ·¶w&–B×&÷s£¶Æ–vâ×6VÆc¦6VçFW#¶§W7F–g’×6VÆc¦6VçFW'Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æf÷¶Ö–â×v–GFƒ£·v–GFƒ£S¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦fÆW‚×7F'C¶v£ƒ¶fÆW‚×w&§w&·FF–æs£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7·v–GFƒ£“Gƒ¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£S‚3gƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§7F'C·FF–æs£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç2æ'Fã¦f—'7BÖ6†–ÆG¶Ö–â×v–GFƒ£·v–GFƒ£Sƒ·FF–æs£‡‡Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç2æFævW'¶Ö–â×v–GFƒ£·v–GFƒ£3gƒ·FF–æs£Ð¤ÖVF–†Ö‚×v–GFƒ£#‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£s'‚Ö–æÖ‚ƒ3‚Ãg"’Ö–æÖ‚ƒS‚Ã##‚’“G‡×Ð¤ÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g#·&÷rÖv£‡‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&–÷&—G’Âæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æfòÂæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7·FF–ærÖÆVgC£3‡‡Òæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7·v–GFƒ¦WFó¶w&–B×FV×ÆFRÖ6öÇVÖç3£S‚3g‡×Ð   ¢ò¢cããc2æöFR6÷W&6R7F–öç2æB×VÇF’Ö6FVv÷'’7W÷'B¢ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£s'‚Ö–æÖ‚ƒ3c‚Ãg"’Ö–æÖ‚ƒ##‚Ã3#‚’“G‚–×÷'FçC·v–GFƒ£S¶§W7F–g’Ö6öçFVçC§7G&WF6‚–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7¶§W7F–g’×6VÆc¦VæB–×÷'FçGÐ¤ÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçGÒæ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ7F–öç7¶§W7F–g’×6VÆc§7F'B–×÷'FçG×Ð¢ò¢cããcrVæ–f–VBæöFRG&÷F÷vç2ÂWôF÷vâf–ÇFW&–ærÂæBG–æÖ–2…EE7F–öâ¢òçFööÆ&"¶FFÖæöFR×7FGW2Öf–ÇFW%×·v–GFƒ£3ƒ¶Ö–â×v–GFƒ£3‡Òæ6ö×7BÖ6öçG&öÂæ‡GG¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÐ¢ò¢5E$TÔdõ$tUô4„ääTÅõ5E$”5Eô„Å5õUõt•D”äuõc3SR¢òææöFR×'VçF–ÖR×7FGW2çW¶&6¶w&÷VæC¢3#C32–×÷'FçC¶6öÆ÷#¢6Ffc–Vb–×÷'FçGÒææöFR×'VçF–ÖR×7FGW2çv—F–æw¶&6¶w&÷VæC¢3fSFb–×÷'FçC¶6öÆ÷#¢6ffSB–×÷'FçGÒææöFR×'VçF–ÖR×7FGW2æF÷vç¶&6¶w&÷VæC¢3CSS#c–×÷'FçC¶6öÆ÷#¢6VFcFf"–×÷'FçGÒææöFR×'VçF–ÖR×7FGW2çv—F–ær¶'¶&6¶w&÷VæC¢3–cS‚–×÷'FçGÒææöFR×'VçF–ÖR×7FGW2æF÷vâ¶'¶&6¶w&÷VæC¢3FCVc‚–×÷'FçGÐ¢ò¢cããcræöFR6÷W&6R×7V6–f–2ÕE2&öw&Ò6VÆV7F÷'2¢ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ3‚Ãg"’S‚Ö–æÖ‚ƒ##‚Ã#“‚—Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6RÖ–æf÷¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"“¶v£gƒ¶Æ–vâÖ6öçFVçC¦6VçFW'Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç7G&VÒ×6÷W&6R×66â×7VÖÖ'—¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶fÆW‚×w&§w&¶Ö–â×v–GFƒ£Ð¢æ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&öw&ÒÖf–VÆG¶F—7Æ“¦&Æö6³¶Ö&v–ã£¶Ö–â×v–GFƒ£Òæ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&öw&ÒÖf–VÆE¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÐ¢æ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&öw&ÒÖf–VÆB6VÆV7G·v–GFƒ£S¶Ö‚×v–GFƒ£3#ƒ¶†V–v‡C£3G‚–×÷'FçC¶Ö–âÖ†V–v‡C£3G‚–×÷'FçC·FF–æs£3‚—‚–×÷'FçC¶föçB×6—¦S£‡Ð¤ÖVF–†Ö‚×v–GFƒ£ƒ‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"S‚Ö–æÖ‚ƒ##‚Ãg"—Òæ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷sâæ6FVv÷'’Öf–VÆG¶w&–BÖ6öÇVÖã¦WF÷×Ð¤ÖVF–†Ö‚×v–GFƒ£sc‚—²æ6†ææVÂÖVF—F÷"Öf÷&Òæ–FVçF—G’Ö6ö×7B×&÷w¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'Òæ6†ææVÂÖVF—F÷"Öf÷&Òç6÷W&6R×&öw&ÒÖf–VÆB6VÆV7G¶Ö‚×v–GFƒ¦æöæW×Ð¢ò¢c"ããR&VF&ÆRæöFRÆöw2¢ð¢ææöFRÖÆör×æVÇ¶÷fW&fÆ÷s¦†–FFVã¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£ƒ¶&6¶w&÷VæC§f"‚Ò×7W&f6R—ÒææöFRÖÆör×F'7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£wƒ·FF–æs£'‚Gƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR“¶÷fW&fÆ÷r×ƒ¦WFó¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"—ÒææöFRÖÆör×F'¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Ö–âÖ†V–v‡C£3Wƒ·FF–æs£w‚'ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR×7G&öær“¶&÷&FW"×&F—W3£‡ƒ¶&6¶w&÷VæC§f"‚ÒÖf–VÆB“¶6öÆ÷#§f"‚Ò×FW‡B“¶föçB×vV–v‡C£cS·FW‡BÖFV6÷&F–öã¦æöæS·v†—FR×76S¦æ÷w&ÒææöFRÖÆör×F#¦†÷fW'¶&÷&FW"Ö6öÆ÷#¢3Sss“–¶&6¶w&÷VæC¢3#&36GÒææöFRÖÆör×F"æ7F—fW¶&÷&FW"Ö6öÆ÷#¢3&f&c–C¶&6¶w&÷VæC¢3#s–CƒC¶6öÆ÷#¢6ffgÒææöFRÖÆör×FööÆ&'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£Gƒ·FF–æs£W‚gƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR—ÒææöFRÖÆör×FööÆ&"ƒ"ÂææöFRÖÆör×FööÆ&"¶Ö&v–ã£ÒææöFRÖÆör×FööÆ&"¶Ö&v–â×F÷£7ƒ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£'‡ÒææöFRÖÆör×FööÆ&"f÷&×¶fÆWƒ£WF÷ÒææöFRÖÆör×F&ÆR×w&¶&÷&FW#£¶&÷&FW"×&F—W3£¶&6¶w&÷VæC§f"‚Ò×7W&f6R“¶÷fW&fÆ÷s¦WF÷ÒææöFRÖÆör×F&ÆW¶Ö–â×v–GFƒ£#ƒƒ¶&÷&FW#£¶&÷&FW"×&F—W3£·F&ÆRÖÆ–÷WC¦f—†VGÒææöFRÖÆörÖ6öÂ×F–ÖW·v–GFƒ£“‡ÒææöFRÖÆörÖ6öÂÖÆWfVÇ·v–GFƒ£“‡ÒææöFRÖÆörÖ6öÂ×66÷W·v–GFƒ£#‡ÒææöFRÖÆörÖ6öÂÖ—·v–GFƒ£CW‡ÒææöFRÖÆörÖ6öÂ×W6W'·v–GFƒ£S‡ÒææöFRÖÆörÖ6öÂ×6W76–öç·v–GFƒ£#‡ÒææöFRÖÆörÖ6öÂÖGW&F–öç·v–GFƒ£CW‡ÒææöFRÖÆörÖ6öÂÖÖW76vRÖ6Æ–VçG·v–GFƒ£3S‡ÒææöFRÖÆörÖ6öÂÖÖW76vW·v–GFƒ£#ƒ‡ÒææöFRÖÆörÖ6öÂÖFWF–Ç7·v–GFƒ£3c‡ÒææöFRÖÆör×F&ÆRF†VBF‡¶&6¶w&÷VæC¢3cs#¶6öÆ÷#¢6v&&6c¶föçB×6—¦S£ƒ·FW‡B×G&ç6f÷&Ó§WW&66S¶ÆWGFW"×76–æs¢ãFV×ÒææöFRÖÆör×F&ÆRF‚ÂææöFRÖÆör×F&ÆRFG·fW'F–6ÂÖÆ–vã¦Ö–FFÆWÒææöFRÖÆör×F–ÖW·v†—FR×76S¦æ÷w&¶föçB×f&–çBÖçVÖW&–3§F'VÆ"ÖçV×3¶6öÆ÷#¢63†CfSWÒææöFRÖÆörÖ6†—ÂææöFRÖÆör×66÷W¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#·v–GFƒ¦Ö‚Ö6öçFVçC·FF–æs£7‚wƒ¶&÷&FW#£‚6öÆ–B33SSfC¶&÷&FW"×&F—W3£““—ƒ¶6öÆ÷#¢6&6CS3¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£sÒææöFRÖÆörÖÆWfVÂÖ–æf÷¶&÷&FW"Ö6öÆ÷#¢3#ssVS¶6öÆ÷#¢3sFF&c¶&6¶w&÷VæC¢3&##WÒææöFRÖÆörÖÆWfVÂÖW'&÷"ÂææöFRÖÆörÖÆWfVÂÖ7&—F–6Ç¶&÷&FW"Ö6öÆ÷#¢3ƒs6Cc¶6öÆ÷#¢6ff&#C¶&6¶w&÷VæC¢33ƒ#ÒææöFRÖÆörÖÆWfVÂ×v&æ–ærÂææöFRÖÆörÖÆWfVÂ×v&ç¶&÷&FW"Ö6öÆ÷#¢3ƒc&C¶6öÆ÷#¢6ffCƒ#¶&6¶w&÷VæC¢3&3#CwÒææöFRÖÆör×66÷W·FW‡B×G&ç6f÷&Ó¦6—FÆ—¦WÒææöFRÖÆörÖÖW76vW·v†—FR×76S¦æ÷&ÖÃ¶÷fW&fÆ÷r×w&¦ç—v†W&S¶Æ–æRÖ†V–v‡C£ãGÒææöFRÖÆörÖ—·v†—FR×76S¦æ÷w&¶6öÆ÷#¢6&6CS3¶föçBÖfÖ–Ç“§V’ÖÖöæ÷76RÅ4dÖöæòÕ&VwVÆ"ÄÖVæÆòÄ6öç6öÆ2ÆÖöæ÷76WÒææöFRÖÆör×W6W'·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—3¶föçB×vV–v‡C£s¶6öÆ÷#¢6F6S†cGÒææöFRÖÆör×6W76–öç·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—3¶föçBÖfÖ–Ç“§V’ÖÖöæ÷76RÅ4dÖöæòÕ&VwVÆ"ÄÖVæÆòÄ6öç6öÆ2ÆÖöæ÷76S¶föçB×6—¦S£ƒ¶6öÆ÷#¢6–&fC7ÒææöFRÖÆörÖGW&F–öç·v†—FR×76S¦æ÷w&¶föçB×f&–çBÖçVÖW&–3§F'VÆ"ÖçV×3¶6öÆ÷#¢3†VS†C¶föçB×vV–v‡C£sÒææöFRÖÆörÖFWF–Ç2ÖF—&V7G¶Ö‚Ö†V–v‡C£“'ƒ¶÷fW&fÆ÷s¦WFó·v†—FR×76S§&R×w&¶÷fW&fÆ÷r×w&¦ç—v†W&S¶6öÆ÷#¢6V&fC¶föçBÖfÖ–Ç“§V’ÖÖöæ÷76RÅ4dÖöæòÕ&VwVÆ"ÄÖVæÆòÄ6öç6öÆ2ÆÖöæ÷76S¶föçB×6—¦S£ƒ¶Æ–æRÖ†V–v‡C£ãGÔÖVF–†Ö‚×v–GFƒ£s‚—²ææöFRÖÆör×FööÆ&'¶Æ–vâÖ—FV×3¦fÆW‚×7F'GÒææöFRÖÆör×FööÆ&"¶Ö‚×v–GFƒ£#3‡ÒææöFRÖÆör×F'7·FF–æs£‡ÒææöFRÖÆör×F&ÆW¶Ö–â×v–GFƒ£cƒ‡×Ð¢ò¢5E$TÔdõ$tUôäôDUôÄôuôÄ”õUEõ$U•%õcƒb¢ð¢ò¢5E$TÔdõ$tUôäôDUõäTÅôÄôuõ4T$4…õT•õc3r¢òò¢5E$TÔdõ$tUôäôDUõäTÅôÄôuôÄ•dUô¤…õT•õc3‚¢òææöFRÖÆör×æVÂææöFRÖÆörÖÆ—fRÖÆöF–ærææöFRÖÆör×F&ÆR×w&¶÷6—G“¢ãs'ÒææöFRÖÆör×6÷'BÖ'WGFöç¶V&æ6S¦æöæS¶&÷&FW#£¶&6¶w&÷VæC§G&ç7&VçC¶6öÆ÷#¦–æ†W&—C¶föçC¦–æ†W&—C¶föçB×vV–v‡C¦–æ†W&—C·FW‡B×G&ç6f÷&Ó¦–æ†W&—C¶ÆWGFW"×76–æs¦–æ†W&—C·FF–æs£¶7W'6÷#§ö–çFW'ÒææöFRÖÆör×6÷'BÖ'WGFöã¦†÷fW"ÂææöFRÖÆör×6÷'BÖ'WGFöâæ7F—fW¶6öÆ÷#¢6F6V6fgÒææöFRÖÆör×v–æF–öç¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶v£'ƒ·FF–æs£G‚gƒ¶&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR—ÒææöFRÖÆör×v–æF–öãç7ç¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£'‡ÒææöFRÖÆör×vRÖçVÖ&W'7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£W‡ÒææöFRÖÆör×vRÖçVÖ&W'2æ7F—fW¶&÷&FW"Ö6öÆ÷#¢3&f&c–C¶6öÆ÷#¢3†VS†CÒææöFRÖÆör×vRÖF—6&ÆVG¶÷6—G“¢ãCS·ö–çFW"ÖWfVçG3¦æöæWÒææöFRÖÆörÖf–ÇFW&&'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦fÆW‚ÖVæC¶v£'ƒ·FF–æs£'‚gƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"—ÒææöFRÖÆörÖf–ÇFW&&"Æ&VÇ¶F—7Æ“¦w&–C¶v£gƒ¶Ö&v–ã£¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£cSÒææöFRÖÆörÖf–ÇFW&&"ææöFRÖÆör×6V&6‚Öf–VÆG¶fÆWƒ£3cƒ¶Ö–â×v–GFƒ£#ƒ‡ÒææöFRÖÆörÖf–ÇFW&&"ææöFRÖÆör×6V&6‚Öf–VÆB–çWE·G—S×6V&6…×·v–GFƒ£S¶Ö–â×v–GFƒ£¶†V–v‡C£C‡ÒææöFRÖÆörÖf–ÇFW&&"6VÆV7G¶Ö–â×v–GFƒ£'ƒ¶Ö&v–ã£ÒææöFRÖ6†ææVÂÖÆ–Ö—BÖf–ÇFW'¶F—7Æ“¦w&–C¶v£Wƒ¶Ö–â×v–GFƒ£Gƒ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£cSÒææöFRÖ6†ææVÂÖÆ–Ö—BÖf–ÇFW"6VÆV7G·v–GFƒ£Gƒ¶Ö–â×v–GFƒ£G‡ÔÖVF–†Ö‚×v–GFƒ£s‚—²ææöFRÖÆörÖf–ÇFW&&'·FF–æs£‚'ƒ¶fÆW‚×w&§w&ÒææöFRÖÆörÖf–ÇFW&&"ææöFRÖÆör×6V&6‚Öf–VÆG¶fÆW‚Ö&6—3£S¶Ö–â×v–GFƒ£·v–GFƒ£WÒææöFRÖ6†ææVÂÖÆ–Ö—BÖf–ÇFW'¶Ö–â×v–GFƒ£“'‡ÒææöFRÖ6†ææVÂÖÆ–Ö—BÖf–ÇFW"6VÆV7G·v–GFƒ£“'ƒ¶Ö–â×v–GFƒ£“'‡×Ð¢ò¢c"ãã#"7F&ÆRæöFRÆ—fR×6W76–öâÆ–÷WB¢ð¢ææöFR×6W76–öâÖf–ÇFW'7¶F—7Æ“¦fÆW‚–×÷'FçC¶Æ–vâÖ—FV×3¦fÆW‚ÖVæC¶v£ƒ¶fÆW‚×w&§w&¶Ö&v–ã£G‚ÒææöFR×6W76–öâÖf–ÇFW'2Æ&VÇ·v–GFƒ¦Ö–âƒ3#‚ÃR“¶F—7Æ“¦w&–C¶v£g‡ÒææöFR×6W76–öâÖf–ÇFW'26VÆV7G¶Ö–â×v–GFƒ£#c‡ÒææöFR×6W76–öâ×F&ÆR×w&¶÷fW&fÆ÷r×ƒ¦WF÷ÒææöFR×6W76–öâ×F&ÆW¶Ö–â×v–GFƒ£#ƒ·F&ÆRÖÆ–÷WC¦f—†VC¶&÷&FW"×&F—W3£‡ÒææöFR×6W76–öâÖ6öÂ×W6W'·v–GFƒ£##‡ÒææöFR×6W76–öâÖ6öÂÖ—·v–GFƒ£#W‡ÒææöFR×6W76–öâÖ6öÂÖæWGv÷&··v–GFƒ£ƒ‡ÒææöFR×6W76–öâÖ6öÂÖ6÷VçG'—·v–GFƒ£‡ÒææöFR×6W76–öâÖ6öÂÖ6†ææVÇ·v–GFƒ£‡ÒææöFR×6W76–öâÖ6öÂÖvW·v–GFƒ£“W‡ÒææöFR×6W76–öâÖ6öÂÖ7F—f—G—·v–GFƒ£#‡ÒææöFR×6W76–öâÖ6öÂÖ6Æ–VçG·v–GFƒ£##‡ÒææöFR×6W76–öâÖ6öÂÖ7F–öç·v–GFƒ£s‡ÒææöFR×6W76–öâ×F&ÆRF‚ÂææöFR×6W76–öâ×F&ÆRFG·fW'F–6ÂÖÆ–vã¦Ö–FFÆWÒææöFR×6W76–öâ×F&ÆRFC¦f—'7BÖ6†–ÆB6ÖÆÇ·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7ÔÖVF–†Ö‚×v–GFƒ£s‚—²ææöFR×6W76–öâÖf–ÇFW'7¶Æ–vâÖ—FV×3§7G&WF6ƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖçÒææöFR×6W76–öâÖf–ÇFW'2Æ&VÂÂææöFR×6W76–öâÖf–ÇFW'26VÆV7BÂææöFR×6W76–öâÖf–ÇFW'2'WGFöâÂææöFR×6W76–öâÖf–ÇFW'2æ'Fç·v–GFƒ£S¶Ö–â×v–GFƒ£×Ð¢ææöFRÖ'VÆ²×6VvÖVçG¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFRÖ6öÇVÖç3¦WFòsgƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£w‚–×÷'FçC¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£‡ÒææöFRÖ'VÆ²×6VvÖVçB7ç·v†—FR×76S¦æ÷w&ÒææöFRÖ'VÆ²×6VvÖVçB6VÆV7G·v–GFƒ£sgƒ¶†V–v‡C£3gƒ¶Ö–âÖ†V–v‡C£3gƒ·FF–ær×F÷£·FF–ærÖ&÷GFöÓ£ÔÖVF–†Ö‚×v–GFƒ£s‚—²ææöFRÖ'VÆ²×6VvÖVçG¶w&–B×FV×ÆFRÖ6öÇVÖç3£g#·v–GFƒ£WÒææöFRÖ'VÆ²×6VvÖVçB6VÆV7G·v–GFƒ£W×Ð¢ò¢c"ãã3B&ö¦V7B×v–FRVæ–f÷&Òf÷&Ò6öçG&öÂ†V–v‡B¢ð¦–çWC¦æ÷B…·G—SÒ&6†V6¶&÷‚%Ò“¦æ÷B…·G—SÒ'&F–ò%Ò“¦æ÷B…·G—SÒ&f–ÆR%Ò“¦æ÷B…·G—SÒ&†–FFVâ%Ò’Ç6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B…¶FF×Væ–f÷&ÒÖ†V–v‡BÖW†V×EÒ“¦æ÷B…¶FFÖæF—fRÖ†V–v‡EÒ—¶†V–v‡C£C‚–×÷'FçC¶Ö–âÖ†V–v‡C£C‚–×÷'FçC¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçGÐ¢ò¢c"ãã3b&ö¦V7B×v–FRVæ–f÷&ÒÆ&VÂ×FòÖ6öçG&öÂ76–ær¢ð¦Æ&VÃ¦†2ƒæ–çWC¦æ÷B…·G—SÒ&6†V6¶&÷‚%Ò“¦æ÷B…·G—SÒ'&F–ò%Ò“¦æ÷B…·G—SÒ&†–FFVâ%Ò’’ÆÆ&VÃ¦†2ƒç6VÆV7B’ÆÆ&VÃ¦†2ƒçFW‡F&V—¶v£w‚–×÷'FçC·&÷rÖv£w‚–×÷'FçGÐ¢ò¢c"ãã3‚&W6W'fRF†R6ÖR†VÇW"&÷r6òF¦6VçBf–VÆG2æWfW"6†–gB¢ð¢æf÷&ÒÖw&–CæÆ&VÃ¦æ÷B‚æ6†V6²’ÂçæVÂÖf÷&Óâæw&–CæÆ&VÃ¦æ÷B‚æ6†V6²’Âæw&–BçæVÂÖf÷&ÓæÆ&VÃ¦æ÷B‚æ6†V6²—¶F—7Æ“¦w&–C¶w&–B×FV×ÆFR×&÷w3¦WFòWFòÖ–æÖ‚ƒg‚ÆWFò“¶Æ–vâÖ6öçFVçC§7F'GÐ¢ò¢F—&V7B&VBÖöæÇ’Ö–â6†ææVÂ7F–öç2&WÆ6RF†RVÆÆ—6—2ÖVçRâ¢ð¢ææöFRÖF—&V7BÖÖ÷&RÖ7F–öç7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶v£w‡ÒææöFRÖF—&V7BÖÖ÷&RÖ7F–öç2æ6ö×7BÖ6öçG&öÇ¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC·FF–æs£–×÷'FçC¶&÷&FW#£–×÷'FçC¶7W'6÷#§ö–çFW'ÒææöFRÖF—&V7BÖÖ÷&RÖ7F–öç27fw·v–GFƒ£‡ƒ¶†V–v‡C£‡ƒ¶f–ÆÃ¦æöæS·7G&ö¶S¦7W'&VçD6öÆ÷#·7G&ö¶R×v–GFƒ£ã“·7G&ö¶RÖÆ–æV6§&÷VæC·7G&ö¶RÖÆ–æV¦ö–ã§&÷VæGÒææöFRÖ–æfòÖ6öçG&öÇ¶6öÆ÷#¢6FfcFfb–×÷'FçC¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ33“sf‚Â3#ƒSssr’–×÷'FçGÒææöFRÖÆörÖ6öçG&öÇ¶6öÆ÷#¢6S–S&fb–×÷'FçC¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ3cSS3–Â3C“63sR’–×÷'FçGÐ ¢ò¢c"ããs"tÄô$ÂæöFR6–FV&"æf–vF–öâ¢ð¤ÖVF–†Ö–â×v–GFƒ£“‚—°¢&öG—·FF–ærÖÆVgC£##‚–×÷'FçGÐ ¢çæVÂÖ†VFW'°¢÷6—F–öã¦f—†VB–×÷'FçC°¢F÷£–×÷'FçC°¢ÆVgC£–×÷'FçC°¢&÷GFöÓ£–×÷'FçC°¢v–GFƒ£##‚–×÷'FçC°¢†V–v‡C£f‚–×÷'FçC°¢F—7Æ“¦fÆW‚–×÷'FçC°¢fÆW‚ÖF—&V7F–öã¦6öÇVÖâ–×÷'FçC°¢Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢v£G‚–×÷'FçC°¢FF–æs£‡‚G‚–×÷'FçC°¢&÷&FW"×&–v‡C£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC°¢&÷&FW"Ö&÷GFöÓ£–×÷'FçC°¢÷fW&fÆ÷r×“¦WFò–×÷'FçC°¢÷fW&fÆ÷r×ƒ¦†–FFVâ–×÷'FçC°¢Ð ¢çæVÂÖ'&æB×&÷w¶F—7Æ“¦&Æö6²–×÷'FçC·v–GFƒ£R–×÷'FçGÐ¢çæVÂÖ'&æG·v–GFƒ£R–×÷'FçC¶Ö–â×v–GFƒ£–×÷'FçC¶v£‚–×÷'FçC·FF–æs£G‚'‚'‚–×÷'FçGÐ¢çæVÂÖ'&æCæF—g¶Ö–â×v–GFƒ£–×÷'FçGÐ¢çæVÂÖ'&æB'¶÷fW&fÆ÷s¦†–FFVâ–×÷'FçC·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—2–×÷'FçC·v†—FR×76S¦æ÷w&–×÷'FçGÐ¢çæVÂÖ'&æB6ÖÆÇ·v†—FR×76S¦æ÷&ÖÂ–×÷'FçC¶föçB×6—¦S£‚–×÷'FçC¶Æ–æRÖ†V–v‡C£ã#R–×÷'FçGÐ¢çæVÂÖæb×FövvÆW¶F—7Æ“¦æöæR–×÷'FçGÐ ¢çæVÂÖæg°¢F—7Æ“¦fÆW‚–×÷'FçC°¢fÆWƒ£WFò–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Ö–â×v–GFƒ£–×÷'FçC°¢fÆW‚ÖF—&V7F–öã¦6öÇVÖâ–×÷'FçC°¢Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC°¢§W7F–g’Ö6öçFVçC§76RÖ&WGvVVâ–×÷'FçC°¢v£g‚–×÷'FçC°¢FF–æs£–×÷'FçC°¢&÷&FW#£–×÷'FçC°¢÷fW&fÆ÷s§f—6–&ÆR–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·7°¢F—7Æ“¦fÆW‚–×÷'FçC°¢fÆW‚ÖF—&V7F–öã¦6öÇVÖâ–×÷'FçC°¢Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC°¢v£g‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææbÖ'WGFöç°¢F—7Æ“¦fÆW‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Ö–âÖ†V–v‡C£C‚–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢FF–æs£—‚‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢&÷&FW"Ö6öÆ÷#§G&ç7&VçB–×÷'FçC°¢&6¶w&÷VæC§G&ç7&VçB–×÷'FçC°¢FW‡BÖÆ–vã¦ÆVgB–×÷'FçC°¢v†—FR×76S¦æ÷w&–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææbÖ'WGFöã¦†÷fW'°¢&÷&FW"Ö6öÆ÷#§f"‚ÒÖÆ–æR’–×÷'FçC°¢&6¶w&÷VæC¢3s#33–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææbÖ'WGFöâæ7F—fW°¢&6¶w&÷VæC¢3ƒ#ƒ3‚–×÷'FçC°¢&÷&FW"Ö6öÆ÷#¢33SSfB–×÷'FçC°¢&÷‚×6†F÷s¦–ç6WB7‚f"‚ÒÖ66VçB’–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW'°¢F—7Æ“¦w&–B–×÷'FçC°¢v£w‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢FF–ær×F÷£'‚–×÷'FçC°¢&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW"çæVÂ×W6W'°¢F—7Æ“¦fÆW‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Ö–âÖ†V–v‡C£3‡‚–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢FF–æs£‡‚‚–×÷'FçC°¢&÷&FW"Ö6öÆ÷#§G&ç7&VçB–×÷'FçC°¢&6¶w&÷VæC§G&ç7&VçB–×÷'FçC°¢föçB×6—¦S£'‚–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW"çæVÂ×W6W#£¦&Vf÷&W°¢6öçFVçC¢%W6W#¢"–×÷'FçC°¢6öÆ÷#§f"‚ÒÖ×WFVB’–×÷'FçC°¢Ö&v–â×&–v‡C£G‚–×÷'FçC°¢föçB×vV–v‡C£S–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW"æ66÷VçBÀ¢çæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WG°¢F—7Æ“¦fÆW‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Ö–âÖ†V–v‡C£C‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢&÷&FW"Ö6öÆ÷#§G&ç7&VçB–×÷'FçC°¢&6¶w&÷VæC§G&ç7&VçB–×÷'FçC°¢Ð ¢ò¢5E$TÔdõ$tUôäôDUõ55tõ$EôdôõDU%ô„õdU%ôd•…õc##ƒ2¢ð¢çæVÂ×6–FV&"Öfö÷FW"æ66÷VçC¦†÷fW"À¢çæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WC¦†÷fW'°¢&÷&FW"Ö6öÆ÷#§f"‚ÒÖÆ–æR’–×÷'FçC°¢&6¶w&÷VæC¢3s#33–×÷'FçC°¢Ð ¢Ö–ç°¢v–GFƒ¦WFò–×÷'FçC°¢Ö–â×v–GFƒ£–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢FF–æs£#G‚–×÷'FçC°¢Ð§Ð ¤ÖVF–†Ö‚×v–GFƒ£“‚—°¢&öG—·FF–ærÖÆVgC£–×÷'FçGÐ ¢çæVÂÖ†VFW'°¢÷6—F–öã§7F–6·’–×÷'FçC°¢F÷£–×÷'FçC°¢ÆVgC¦WFò–×÷'FçC°¢&÷GFöÓ¦WFò–×÷'FçC°¢v–GFƒ¦WFò–×÷'FçC°¢†V–v‡C¦WFò–×÷'FçC°¢Ö‚Ö†V–v‡C£f‚–×÷'FçC°¢F—7Æ“¦fÆW‚–×÷'FçC°¢fÆW‚ÖF—&V7F–öã¦6öÇVÖâ–×÷'FçC°¢Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC°¢FF–æs£‚'‚–×÷'FçC°¢&÷&FW"×&–v‡C£–×÷'FçC°¢&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC°¢÷fW&fÆ÷s§f—6–&ÆR–×÷'FçC°¢Ð ¢çæVÂÖ'&æB×&÷w°¢F—7Æ“¦fÆW‚–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢Æ–vâÖ—FV×3¦6VçFW"–×÷'FçC°¢§W7F–g’Ö6öçFVçC§76RÖ&WGvVVâ–×÷'FçC°¢v£'‚–×÷'FçC°¢Ð ¢çæVÂÖæb×FövvÆW¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçGÐ ¢çæVÂÖæg°¢F—7Æ“¦æöæR–×÷'FçC°¢v–GFƒ£R–×÷'FçC°¢FF–æs£‚'‚–×÷'FçC°¢&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC°¢÷fW&fÆ÷r×“¦WFò–×÷'FçC°¢Ð ¢çæVÂÖ†VFW"ææbÖ÷VâçæVÂÖæg¶F—7Æ“¦&Æö6²–×÷'FçGÐ ¢çæVÂÖæbÖÆ–æ·7°¢F—7Æ“¦w&–B–×÷'FçC°¢w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’’–×÷'FçC°¢v£g‚–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææbÖ'WGFöç°¢v–GFƒ£R–×÷'FçC°¢Ö–âÖ†V–v‡C£3‡‚–×÷'FçC°¢FW‡BÖÆ–vã¦6VçFW"–×÷'FçC°¢v†—FR×76S¦æ÷&ÖÂ–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW'°¢F—7Æ“¦w&–B–×÷'FçC°¢v£g‚–×÷'FçC°¢Ö&v–â×F÷£‡‚–×÷'FçC°¢FF–ær×F÷£‡‚–×÷'FçC°¢&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR’–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW"çæVÂ×W6W"À¢çæVÂ×6–FV&"Öfö÷FW"æ66÷VçBÀ¢çæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WG·v–GFƒ£R–×÷'FçGÐ ¢Ö–ç·FF–æs£'‚–×÷'FçGÐ§Ð ¤ÖVF–†Ö‚×v–GFƒ£C#‚—°¢çæVÂÖæbÖÆ–æ·7¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçGÐ§Ð  ¢ò¢c"ããs2æöFRFW6·F÷6–FV&"FövvÆR¢ð¢ææöFRÖæbÖ–6öç·v–GFƒ£‡ƒ¶†V–v‡C£‡ƒ¶Ö–â×v–GFƒ£‡ƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶6öÆ÷#¢3–f#3'Ð¢ææöFRÖæbÖ–6öâ7frÂææöFR×6–væ÷WBÖ–6öâ7fw·v–GFƒ£‡ƒ¶†V–v‡C£‡ƒ¶F—7Æ“¦&Æö6³¶f–ÆÃ¦æöæS·7G&ö¶S¦7W'&VçD6öÆ÷#·7G&ö¶R×v–GFƒ£ãs·7G&ö¶RÖÆ–æV6§&÷VæC·7G&ö¶RÖÆ–æV¦ö–ã§&÷VæGÐ¢ææöFR×6–væ÷WBÖ–6öç·v–GFƒ£‡ƒ¶†V–v‡C£‡ƒ¶Ö–â×v–GFƒ£‡ƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶6öÆ÷#¢3–f#3'Ð ¢ò¢5E$TÔdõ$tUôäôDUôtÄô$Åôäeô”4ôåõDU…Eõ54”äuõc##“r¢ð¤ÖVF–†Ö–â×v–GFƒ£“‚—°¢çæVÂÖæbÖÆ–æ·2ææbÖ'WGFöç°¢F—7Æ“¦fÆW‚–×÷'FçC°¢Æ–vâÖ—FV×3¦6VçFW"–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢v£'‚–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææöFRÖæbÖ–6öâÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFRÖ66÷VçBÖ–6öâÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFR×6–væ÷WBÖ–6öç°¢F—7Æ“¦w&–B–×÷'FçC°¢Æ6RÖ—FV×3¦6VçFW"–×÷'FçC°¢v–GFƒ£‡‚–×÷'FçC°¢†V–v‡C£‡‚–×÷'FçC°¢Ö–â×v–GFƒ£‡‚–×÷'FçC°¢fÆWƒ£‡‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢FF–æs£–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææöFRÖæbÖ–6öâ7frÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFRÖ66÷VçBÖ–6öâ7frÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFR×6–væ÷WBÖ–6öâ7fw°¢F—7Æ“¦&Æö6²–×÷'FçC°¢v–GFƒ£‡‚–×÷'FçC°¢†V–v‡C£‡‚–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢Ð ¢çæVÂÖæbÖÆ–æ·2ææöFRÖæbÖÆ&VÂÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFRÖ66÷VçBÖÆ&VÂÀ¢çæVÂ×6–FV&"Öfö÷FW"ææöFR×6–væ÷WBÖÆ&VÇ°¢F—7Æ“¦&Æö6²–×÷'FçC°¢Ö–â×v–GFƒ£–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢FF–æs£–×÷'FçC°¢Æ–æRÖ†V–v‡C£ã"–×÷'FçC°¢Ð ¢çæVÂ×6–FV&"Öfö÷FW"æ66÷VçBÀ¢çæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WG°¢F—7Æ“¦fÆW‚–×÷'FçC°¢Æ–vâÖ—FV×3¦6VçFW"–×÷'FçC°¢§W7F–g’Ö6öçFVçC¦fÆW‚×7F'B–×÷'FçC°¢v£'‚–×÷'FçC°¢Ð§Ð ¤ÖVF–†Ö–â×v–GFƒ£“‚—°¢çæVÂÖ'&æB×&÷w¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"’3g‚–×÷'FçC¶Æ–vâÖ—FV×3¦6VçFW"–×÷'FçC¶v£‡‚–×÷'FçGÐ¢çæVÂÖæb×FövvÆW¶F—7Æ“¦w&–B–×÷'FçC·Æ6RÖ—FV×3¦6VçFW"–×÷'FçC·v–GFƒ£3g‚–×÷'FçC¶†V–v‡C£3g‚–×÷'FçC¶Ö–â×v–GFƒ£3g‚–×÷'FçC¶Ö–âÖ†V–v‡C£3g‚–×÷'FçC·FF–æs£–×÷'FçC¶&÷&FW"×&F—W3£‡‚–×÷'FçGÐ¢çæVÂÖæb×FövvÆRçFövvÆRÖÆ–æW¶F—7Æ“¦æöæR–×÷'FçGÐ¢çæVÂÖæb×FövvÆRçFövvÆRÖ6†Wg&öç¶F—7Æ“¦&Æö6²–×÷'FçC·v–GFƒ£ƒ¶†V–v‡C£ƒ¶&÷&FW"ÖÆVgC£'‚6öÆ–B7W'&VçD6öÆ÷#¶&÷&FW"Ö&÷GFöÓ£'‚6öÆ–B7W'&VçD6öÆ÷#·G&ç6f÷&Ó§&÷FFRƒCVFVr“·G&ç6—F–öã§G&ç6f÷&Òã‡2V6WÐ ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VG·FF–ærÖÆVgC£sg‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖ†VFW'·v–GFƒ£sg‚–×÷'FçC·FF–æs£‡‚‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖ'&æB×&÷w¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçC¶v£‚–×÷'FçC¶§W7F–g’Ö—FV×3¦6VçFW"–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖ'&æG¶§W7F–g’Ö6öçFVçC¦6VçFW"–×÷'FçC·FF–æs£–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖ'&æCæF—g¶F—7Æ“¦æöæR–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖÆövòÆ&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖÖ&··v–GFƒ£C‚–×÷'FçC¶†V–v‡C£C‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖæb×FövvÆW·v–GFƒ£C‚–×÷'FçC¶†V–v‡C£C‚–×÷'FçC¶Ö–â×v–GFƒ£C‚–×÷'FçC¶Ö–âÖ†V–v‡C£C‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖæb×FövvÆRçFövvÆRÖ6†Wg&öç·G&ç6f÷&Ó§&÷FFRƒ##VFVr—Ð¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖæbÖÆ–æ·7¶Æ–vâÖ—FV×3¦6VçFW"–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂÖæbÖÆ–æ·2ææbÖ'WGFöç·v–GFƒ£CG‚–×÷'FçC¶Ö–â×v–GFƒ£CG‚–×÷'FçC¶†V–v‡C£C'‚–×÷'FçC¶Ö–âÖ†V–v‡C£C'‚–×÷'FçC·FF–æs£–×÷'FçC¶§W7F–g’Ö6öçFVçC¦6VçFW"–×÷'FçC¶÷fW&fÆ÷s¦†–FFVâ–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFRÖæbÖÆ&VÂÆ&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFR×6–væ÷WBÖÆ&VÇ¶F—7Æ“¦æöæR–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFRÖæbÖ–6öâÆ&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFR×6–væ÷WBÖ–6öç·v–GFƒ£—‚–×÷'FçC¶†V–v‡C£—‚–×÷'FçC¶Ö–â×v–GFƒ£—‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFRÖæbÖ–6öâ7frÆ&öG’ææöFR×6–FV&"Ö6öÆÆ6VBææöFR×6–væ÷WBÖ–6öâ7fw·v–GFƒ£—‚–×÷'FçC¶†V–v‡C£—‚–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂ×6–FV&"Öfö÷FW'¶§W7F–g’Ö—FV×3¦6VçFW"–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂ×W6W'¶F—7Æ“¦æöæR–×÷'FçGÐ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂ×6–FV&"Öfö÷FW"æ66÷VçBÀ¢&öG’ææöFR×6–FV&"Ö6öÆÆ6VBçæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WG·v–GFƒ£CG‚–×÷'FçC¶Ö–â×v–GFƒ£CG‚–×÷'FçC¶†V–v‡C£C'‚–×÷'FçC¶Ö–âÖ†V–v‡C£C'‚–×÷'FçC·FF–æs£–×÷'FçC¶§W7F–g’Ö6öçFVçC¦6VçFW"–×÷'FçGÐ§Ð ¤ÖVF–†Ö‚×v–GFƒ£“‚—°¢çæVÂÖæb×FövvÆRçFövvÆRÖ6†Wg&öç¶F—7Æ“¦æöæR–×÷'FçGÐ¢çæVÂÖæb×FövvÆRçFövvÆRÖÆ–æW¶F—7Æ“¦&Æö6²–×÷'FçC·v–GFƒ£‡‚–×÷'FçC¶†V–v‡C£'‚–×÷'FçC¶Ö&v–ã£'‚WFò–×÷'FçC¶&÷&FW"×&F—W3£'‚–×÷'FçC¶&6¶w&÷VæC¦7W'&VçD6öÆ÷"–×÷'FçGÐ¢ææöFRÖæbÖ–6öç¶F—7Æ“¦æöæR–×÷'FçGÐ§Ð  ¢ò¢c"ããsBæöFRFövvÆR²6†ææVÇ2FW6·F÷Æ–÷WBf—‚¢ð ¢ò¢F†RöÆBvVæW&–2çæVÂÖæb×FövvÆR7â'VÆRÇ6ò7G–ÆVBF†R6†Wg&öâ—G6VÆbÀ¢v†–6‚ÖFRF†RFW6·F÷FövvÆRÆöö²Æ–¶Rf–ÆÆVBF–ÖöæBâ¢ð¢çæVÂÖæb×FövvÆRçFövvÆRÖ6†Wg&öç°¢&6¶w&÷VæC§G&ç7&VçB–×÷'FçC°¢Ö&v–ã£–×÷'FçC°¢&÷&FW"×&F—W3£–×÷'FçC°§Ð¤ÖVF–†Ö–â×v–GFƒ£“‚—°¢çæVÂÖæb×FövvÆRçFövvÆRÖ6†Wg&öç°¢v–GFƒ£—‚–×÷'FçC°¢†V–v‡C£—‚–×÷'FçC°¢Ö–â×v–GFƒ£—‚–×÷'FçC°¢Ö–âÖ†V–v‡C£—‚–×÷'FçC°¢&÷&FW"ÖÆVgC£'‚6öÆ–B7W'&VçD6öÆ÷"–×÷'FçC°¢&÷&FW"Ö&÷GFöÓ£'‚6öÆ–B7W'&VçD6öÆ÷"–×÷'FçC°¢&6¶w&÷VæC§G&ç7&VçB–×÷'FçC°¢Ð ¢ò¢¶VWF†R6†ææVÇ2vR–ç6–FRF†R6öçFVçB&V7&VFVB'’F†R6–FV&"â¢ð¢ææöFRÖ6†ææVÂ×F&ÆR×w&°¢v–GFƒ£R–×÷'FçC°¢Ö‚×v–GFƒ£R–×÷'FçC°¢÷fW&fÆ÷r×ƒ¦WFò–×÷'FçC°¢÷fW&fÆ÷r×“¦†–FFVâ–×÷'FçC°¢67&öÆÆ&"ÖwWGFW#§7F&ÆS°¢Ð ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆW°¢v–GFƒ£R–×÷'FçC°¢Ð§Ð ¢ò¢v—F‚æ÷&ÖÂFW6·F÷öÆF÷f–Ww÷'BÂ¶VWF†R÷W&F–öæÂ6öÇVÖç2f—6–&ÆP¢æB†–FR6V6öæF'’6öFV2÷F‡&÷Vv‡WBFWF–Ç2–ç7FVBöbW6†–ærF†RvRv–FRâ¢ð¤ÖVF–†Ö–â×v–GFƒ£“‚’æB†Ö‚×v–GFƒ£S‚—°¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆW°¢Ö–â×v–GFƒ£“‚–×÷'FçC°¢F&ÆRÖÆ–÷WC¦f—†VB–×÷'FçC°¢Ð ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFR×7G&VÒ×7VÖÖ'’À¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖ6öFV2À¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFR×7VVBÖ6VÆÂÀ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖg2Ö6VÆÇ°¢F—7Æ“¦æöæR–×÷'FçC°¢Ð ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFR×6VÆV7BÖ6VÆÇ·v–GFƒ£C'‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖÖ–âÖ–G·v–GFƒ£SW‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖ6†ææVÂÖ–FVçF—G’Ö6VÆÇ·v–GFƒ£##‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFR×6W'fW"Ö6VÆÇ·v–GFƒ£ƒW‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖöæÆ–æRÖ6VÆÇ·v–GFƒ£“‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFR×'VçF–ÖRÖ6VÆÇ·v–GFƒ£#‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖ6öçG&öÇ2Ö6VÆÇ·v–GFƒ£S‚–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆRææöFRÖÖ÷&RÖ6VÆÇ·v–GFƒ£C‡‚–×÷'FçGÐ§Ð ¢ò¢6V&6‚öf–ÇFW"6öçG&öÇ26†÷VÆBw&6ÆVæÇ’&F†W"F†âv–FVæ–ærF†RvRâ¢ð¤ÖVF–†Ö–â×v–GFƒ£“‚’æB†Ö‚×v–GFƒ£#S‚—°¢ææöFRÖ6†ææVÂ×FööÆ&'°¢Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçC°¢fÆW‚ÖF—&V7F–öã¦6öÇVÖâ–×÷'FçC°¢Ð¢ææöFRÖ6†ææVÂ×FööÆ&#âæ7F–öç7°¢v–GFƒ£R–×÷'FçC°¢w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"’C‚WFòWFò–×÷'FçC°¢Ð¢ææöFRÖ6†ææVÂ×FööÆ&#âæ7F–öç2–çWE·G—S×6V&6…×°¢Ö–â×v–GFƒ£–×÷'FçC°¢Ð§Ð ¢ò¢Öö&–ÆR÷F&ÆWC¢fö–Bf÷&6–ærF†RgVÆÂC#‡‚FW6·F÷F&ÆRv–GF‚â¢ð¤ÖVF–†Ö‚×v–GFƒ£“‚—°¢ææöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆW°¢Ö–â×v–GFƒ£“#‚–×÷'FçC°¢Ð¢ææöFRÖ6†ææVÂ×F&ÆR×w&°¢÷fW&fÆ÷r×ƒ¦WFò–×÷'FçC°¢×vV&¶—BÖ÷fW&fÆ÷r×67&öÆÆ–æs§F÷V6ƒ°¢Ð§Ð  ¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅõd•4”$ÄUõt”ED…õcS¢6V6öæF'’FW6·F÷6öÇVÖç2&P¢†–FFVâöâÆF÷v–GF‡3²6öÆÆ6RF†V—"6öÆw&÷WG&6·2Föò6òF†W’Fòæ÷@¢ÆVfRÆ&vRV×G’7G&—öâF†R&–v‡Bâ¢ð¤ÖVF–†Ö–â×v–GFƒ£“‚’æB†Ö‚×v–GFƒ£S‚—°¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆW¶Ö–â×v–GFƒ£–×÷'FçC·v–GFƒ£R–×÷'FçC·F&ÆRÖÆ–÷WC¦f—†VB–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×7G&VÒÀ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×f–FVòÀ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖVF–òÀ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×7VVBÀ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖg7·f—6–&–Æ—G“¦6öÆÆ6R–×÷'FçC·v–GFƒ£–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×6VÆV7G·v–GFƒ£BR–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖ–G·v–GFƒ£RR–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖ6†ææVÇ·v–GFƒ£#2R–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×6W'fW'·v–GFƒ£#R–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖöæÆ–æW·v–GFƒ£’R–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂ×7FGW7·v–GFƒ£#R–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖ6öçG&öÇ7·v–GFƒ£BR–×÷'FçGÐ¢ææöFRÖÖævRÖ6†ææVÂ×F&ÆR6öÂææöFRÖ6öÂÖÖ÷&W·v–GFƒ£RR–×÷'FçGÐ¢ææöFRÖ6†ææVÂ×F&ÆR×w&¶÷fW&fÆ÷r×ƒ¦†–FFVâ–×÷'FçGÐ§Ð  ¢ò¢5E$TÔdõ$tUôäôDUôE$õDõtåôeTÄÅôTD•B¢ð¦‡FÖÂ&öG’6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"—°¢×vV&¶—BÖV&æ6S¦æöæR–×÷'FçC¶V&æ6S¦æöæR–×÷'FçC¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçC°¢†V–v‡C£C‚–×÷'FçC¶Ö–âÖ†V–v‡C£C‚–×÷'FçC·FF–æs£3G‚‚–×÷'FçC°¢&÷&FW#£‚6öÆ–B33CSb–×÷'FçC¶&÷&FW"×&F—W3£‡‚–×÷'FçC¶&6¶w&÷VæBÖ6öÆ÷#¢3#‚–×÷'FçC°¢6öÆ÷#§f"‚Ò×FW‡B’–×÷'FçC¶föçC¦–æ†W&—B–×÷'FçC¶föçB×6—¦S¦–æ†W&—B–×÷'FçC¶Æ–æRÖ†V–v‡C¦æ÷&ÖÂ–×÷'FçC°¢÷WFÆ–æS¦æöæR–×÷'FçC¶&÷‚×6†F÷s¦æöæR–×÷'FçC¶6öÆ÷"×66†VÖS¦F&²–×÷'FçC°¢&6¶w&÷VæBÖ–ÖvS¦Æ–æV"Öw&F–VçBƒCVFVrÇG&ç7&VçBSRÂ3†ff&SR’ÆÆ–æV"Öw&F–VçBƒ3VFVrÂ3†ff&SRÇG&ç7&VçBSR’–×÷'FçC°¢&6¶w&÷VæB×÷6—F–öã¦6Æ2ƒRÒW‚’6Æ2ƒSRÒ‚’Æ6Æ2ƒRÒ‚’6Æ2ƒSRÒ‚’–×÷'FçC°¢&6¶w&÷VæB×6—¦S£W‚W‚ÃW‚W‚–×÷'FçC¶&6¶w&÷VæB×&WVC¦æò×&WVB–×÷'FçC°§Ð¦‡FÖÂ&öG’6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"“¦†÷fW#¦æ÷Bƒ¦F—6&ÆVB—¶&÷&FW"Ö6öÆ÷#¢36#Sc‚–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"“¦fö7W7¶&÷&FW"Ö6öÆ÷#§f"‚ÒÖ66VçB’–×÷'FçC¶&÷‚×6†F÷s£'‚&v&ƒSRÃ“’Ãc"Âã"’–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"“¦F—6&ÆVG¶÷6—G“£–×÷'FçC¶6öÆ÷#§f"‚ÒÖ×WFVB’–×÷'FçC²×vV&¶—B×FW‡BÖf–ÆÂÖ6öÆ÷#§f"‚ÒÖ×WFVB’–×÷'FçC¶&6¶w&÷VæBÖ6öÆ÷#¢3“#2–×÷'FçC¶&÷&FW"Ö6öÆ÷#¢3#s3sCr–×÷'FçC¶7W'6÷#¦æ÷BÖÆÆ÷vVB–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7C¦æ÷B…¶×VÇF—ÆUÒ“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"’÷F–öç¶&6¶w&÷VæC¢3CS#–×÷'FçC¶6öÆ÷#§f"‚Ò×FW‡B’–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7E¶×VÇF—ÆUÓ¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"—¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçC·v–GFƒ£S¶&÷&FW#£‚6öÆ–B33CSb–×÷'FçC¶&÷&FW"×&F—W3£‡‚–×÷'FçC¶&6¶w&÷VæBÖ6öÆ÷#¢3#‚–×÷'FçC¶6öÆ÷#§f"‚Ò×FW‡B’–×÷'FçC¶föçC¦–æ†W&—B–×÷'FçC¶÷WFÆ–æS¦æöæR–×÷'FçC¶&÷‚×6†F÷s¦æöæR–×÷'FçC¶6öÆ÷"×66†VÖS¦F&²–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7E¶×VÇF—ÆUÕ·6—¦SÒ#%Ó¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"—²×vV&¶—BÖV&æ6S¦æöæR–×÷'FçC¶V&æ6S¦æöæR–×÷'FçC¶†V–v‡C£C‚–×÷'FçC¶Ö–âÖ†V–v‡C£C‚–×÷'FçC·FF–æs£3G‚‚–×÷'FçC¶&6¶w&÷VæBÖ–ÖvS¦Æ–æV"Öw&F–VçBƒCVFVrÇG&ç7&VçBSRÂ3†ff&SR’ÆÆ–æV"Öw&F–VçBƒ3VFVrÂ3†ff&SRÇG&ç7&VçBSR’–×÷'FçC¶&6¶w&÷VæB×÷6—F–öã¦6Æ2ƒRÒW‚’6Æ2ƒSRÒ‚’Æ6Æ2ƒRÒ‚’6Æ2ƒSRÒ‚’–×÷'FçC¶&6¶w&÷VæB×6—¦S£W‚W‚ÃW‚W‚–×÷'FçC¶&6¶w&÷VæB×&WVC¦æò×&WVB–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7E¶×VÇF—ÆUÓ¦æ÷B…·6—¦SÒ#%Ò“¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"—²×vV&¶—BÖV&æ6S¦WFò–×÷'FçC¶V&æ6S¦WFò–×÷'FçC¶Ö–âÖ†V–v‡C£“g‚–×÷'FçC·FF–æs£W‚‡‚–×÷'FçC¶&6¶w&÷VæBÖ–ÖvS¦æöæR–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7E¶×VÇF—ÆUÓ¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"“¦fö7W7¶&÷&FW"Ö6öÆ÷#§f"‚ÒÖ66VçB’–×÷'FçC¶&÷‚×6†F÷s£'‚&v&ƒSRÃ“’Ãc"Âã"’–×÷'FçGÐ¦‡FÖÂ&öG’6VÆV7E¶×VÇF—ÆUÓ¦æ÷B‚77G&VÖf÷&vRÖG&÷F÷vâÖæWfW"’÷F–öç¶&6¶w&÷VæC¢3CS#–×÷'FçC¶6öÆ÷#§f"‚Ò×FW‡B’–×÷'FçC·FF–æs£g‚—‚–×÷'FçGÐ  ¢ò¢5E$TÔdõ$tUõ$ô¤T5EôÔô$”ÄUôTD•EôäôDUõc##“‚¢ð¤ÖVF–†Ö‚×v–GFƒ£“‚—°¢‡FÖÂÆ&öG—·v–GFƒ£R–×÷'FçC¶Ö‚×v–GFƒ£R–×÷'FçC¶÷fW&fÆ÷r×ƒ¦†–FFVâ–×÷'FçGÐ¢&öG—¶Ö–â×v–GFƒ£–×÷'FçGÐ¢–ÖrÇf–FVòÆ6çf2Ç7fw¶Ö‚×v–GFƒ£WÐ¢Ö–âÂææ'&÷rÂçæVÂÖf÷&ÒÂæ6&BÂçF&ÆR×w&·v–GFƒ£R–×÷'FçC¶Ö‚×v–GFƒ£R–×÷'FçC¶Ö–â×v–GFƒ£–×÷'FçGÐ¢Ö–ç¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚–×÷'FçGÐ¢æw&–BÂæ6&G2Âæf÷&ÒÖw&–BÂæ6†V6·7¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçGÐ¢çv–FW¶w&–BÖ6öÇVÖã¦WFò–×÷'FçGÐ¢çFööÆ&"ÂçæVÂÖ†VBÂæ7F–öç2Âæf÷&ÒÖ7F–öç7°¢Ö‚×v–GFƒ£R–×÷'FçC°¢fÆW‚×w&§w&–×÷'FçC°¢Ð¢çFööÆ&"ÂçæVÂÖ†VG¶Æ–vâÖ—FV×3§7G&WF6‚–×÷'FçGÐ¢çFööÆ&#â§¶Ö–â×v–GFƒ£–×÷'FçGÐ¢–çWC¦æ÷B…·G—SÖ6†V6¶&÷…Ò“¦æ÷B…·G—S×&F–õÒ’Ç6VÆV7BÇFW‡F&V°¢v–GFƒ£R–×÷'FçC°¢Ö‚×v–GFƒ£R–×÷'FçC°¢Ö–â×v–GFƒ£–×÷'FçC°¢Ð¢æ6÷—·v–GFƒ£R–×÷'FçC¶Ö–â×v–GFƒ£–×÷'FçGÐ¢æ6÷’–çWG¶Ö–â×v–GFƒ£–×÷'FçGÐ¢çF&ÆR×w&ÂææöFR×6W76–öâ×F&ÆR×w&ÂææöFRÖ6†ææVÂ×F&ÆR×w&°¢v–GFƒ£R–×÷'FçC°¢Ö‚×v–GFƒ£R–×÷'FçC°¢÷fW&fÆ÷r×ƒ¦WFò–×÷'FçC°¢×vV&¶—BÖ÷fW&fÆ÷r×67&öÆÆ–æs§F÷V6ƒ°¢Ð¢æÖöæòÂæf–VÆBÖ†VÇÇ6ÖÆÂÇÇFG°¢÷fW&fÆ÷r×w&¦ç—v†W&R–×÷'FçC°¢v÷&BÖ'&V³¦'&V²×v÷&C°¢Ð¢&W¶Ö‚×v–GFƒ£R–×÷'FçC·v†—FR×76S§&R×w&–×÷'FçC¶÷fW&fÆ÷r×w&¦ç—v†W&R–×÷'FçGÐ¢çæVÂ×6–FV&"Öfö÷FW"æ66÷VçBÂçæVÂ×6–FV&"Öfö÷FW"ç6–væ÷WG°¢Ö–â×v–GFƒ£–×÷'FçC°¢Ð§Ð¤ÖVF–†Ö‚×v–GFƒ£S#‚—°¢Ö–ç·FF–æs£‚–×÷'FçGÐ¢çæVÂÖ†VFW'·FF–ærÖÆVgC£‚–×÷'FçC·FF–ær×&–v‡C£‚–×÷'FçGÐ¢çæVÂÖæbÖÆ–æ·7¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçGÐ¢æf÷&ÒÖ7F–öç7¶F—7Æ“¦w&–B–×÷'FçC¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçC·v–GFƒ£R–×÷'FçGÐ¢æf÷&ÒÖ7F–öç2'WGFöâÂæf÷&ÒÖ7F–öç2æ'Fç·v–GFƒ£R–×÷'FçGÐ¢çFööÆ&#âæ7F–öç7·v–GFƒ£R–×÷'FçGÐ§Ð £Â÷7G–ÆSà¢rrp  ¦FVböæöFU÷æVÅöFö7VÖVçB€¢W6W#¢æVÄ66W75W6W"À¢F—FÆS¢7G"À¢6öçFVçC¢7G"À¢7F—fS¢7G"Ò""À¢W‡G&ö†VC¢7G"Ò""À¢&öG•÷67&—C¢7G"Ò""À¢Ö–åö6Æ73¢7G"Ò""À¢’Óâ7G# ¢Ö–åöGG"Òbr6Æ73Ò'¶‡FÖÂæW66R†Ö–åö6Æ72ÂV÷FSÕG'VR—Ò"r–bÖ–åö6Æ72VÇ6R" ¢ÖF6†VE÷æVÅ÷&Vf—‚Òô5U%$TåEõäTÅõ$Td•‚ævWB‚¢66W75÷&Vf—‚ÒÖF6†VE÷æVÅ÷&Vf—‚–bÖF6†VE÷æVÅ÷&Vf—‚—2æ÷BæöæRVÇ6R†b"÷¶ÖævW"æ66W75÷6ÇVwÒ"–bÖævW"æ66W75÷6ÇVrVÇ6R""¢6æöæ–6Å÷F‚Ò66W75÷&Vf—‚÷""ò ¢&V†–FU÷67&—BÒ€¢sÇ7G–ÆSæ‡FÖÂç7G&VÖf÷&vRÖæöFRÖæF—fRÖÆöF–æw¶7W'6÷#§&öw&W72–×÷'FçGÖ‡FÖÂç7G&VÖf÷&vRÖæöFRÖæF—fRÖÆöF–ær&öG—¶÷6—G“¢ãƒƒ·G&ç6—F–öã¦÷6—G’ã‡2V6WÓÂ÷7G–ÆSãÇ67&—Câp¢rò¢5E$TÔdõ$tUôäôDUô„”DDTåôäD•dUõ$õUDUô„TEõcS¢òp¢r†gVæ7F–öâ‚—¶6öç7BÒr²§6öâæGV×2†6æöæ–6Å÷F‚’²s¶6öç7B&Vf—ƒÒr²§6öâæGV×2†66W75÷&Vf—‚’²s²p¢&6öç7B&6SÒròr²wæVÂrÆ³Òw7G&VÖf÷&vUöæöFU÷æVÅ÷&÷WFRs¶6öç7BÆÆ÷vVC×ƒÓçƒÓÓÖ&6WÇÇƒÓÓÖ&6R²röÆöv–âwÇÇƒÓÓÖ&6R²röÆöv÷WBwÇÇ‚ç7F'G5v—F‚†&6R²rö66÷VçBòr—ÇÇ‚ç7F'G5v—F‚†&6R²röÖævRòr—ÇÇ‚ç7F'G5v—F‚†&6R²rö6†ææVÇ2òr—ÇÇ‚ç7F'G5v—F‚†&6R²r÷Æ–Æ—7G2r—ÇÇ‚ç7F'G5v—F‚†&6R²r÷W6W'2r—ÇÇ‚ç7F'G5v—F‚†&6R²r÷6W76–öç2r“² ¢'G'—¶–b†Æö6F–öâçF†æÖRÓ×ÇÆÆö6F–öâç6V&6‡ÇÆÆö6F–öâæ†6‚—¶ÆWBFƒÖÆö6F–öâçF†æÖS¶–b‡&Vf—‚bb‡FƒÓÓ×&Vf—‡ÇÇF‚ç7F'G5v—F‚‡&Vf—‚²ròr’’—Fƒ×F‚ç6Æ–6R‡&Vf—‚æÆVæwF‚—ÇÂròs¶ÆWBF&vWC×FƒÓÓÖ&6WÇÇF‚ç7F'G5v—F‚†&6R²ròr“÷Fƒ¢‡FƒÓÓÒròsö&6S¦&6R·F‚“¶–b†ÆÆ÷vVB‡F&vWB’—¶6öç7Bc×F&vWB¶Æö6F–öâç6V&6ƒ¶Fö7VÖVçBæ6öö¶–SÖ²²sÒr¶Væ6öFUU$”6ö×öæVçB‡b’²s²FƒÒó²Ö‚ÔvSÓC3#²6ÖU6—FSÔÆ‚r²†Æö6F–öâç&÷Fö6öÃÓÓÒv‡GG3¢sòs²6V7W&Rs¢rr—Ö†—7F÷'’ç&WÆ6U7FFR††—7F÷'’ç7FFRÂrrÇ—×Ö6F6‚…ò—·Ó·Ò’‚“³Â÷67&—Câ ¢¢f—†VEöFG&W75÷67&—BÒ€¢sÇ67&—Câò¢5E$TÔdõ$tUôäôDUô„”DDTåôäD•dUõ$õUDUõcS¢òp¢r†gVæ7F–öâ‚—¶6öç7BÒr²§6öâæGV×2†6æöæ–6Å÷F‚’²s¶6öç7B&Vf—ƒÒr²§6öâæGV×2†66W75÷&Vf—‚’²s²p¢&6öç7B&6SÒròr²wæVÂrÆ³Òw7G&VÖf÷&vUöæöFU÷æVÅ÷&÷WFRs¶6öç7BÆÆ÷vVC×ƒÓçƒÓÓÖ&6WÇÇƒÓÓÖ&6R²röÆöv–âwÇÇƒÓÓÖ&6R²röÆöv÷WBwÇÇ‚ç7F'G5v—F‚†&6R²rö66÷VçBòr—ÇÇ‚ç7F'G5v—F‚†&6R²röÖævRòr—ÇÇ‚ç7F'G5v—F‚†&6R²rö6†ææVÇ2òr—ÇÇ‚ç7F'G5v—F‚†&6R²r÷Æ–Æ—7G2r—ÇÇ‚ç7F'G5v—F‚†&6R²r÷W6W'2r—ÇÇ‚ç7F'G5v—F‚†&6R²r÷6W76–öç2r“² ¢&6öç7B–çFW&æÃ×ƒÓç¶ÆWBS·G'—·SÖæWrU$Â…7G&–ær‡‡ÇÂrr’ÆÆö6F–öâæ÷&–v–â—Ö6F6‚…ò—·&WGW&ârwÖ–b‡Ræ÷&–v–âÓÖÆö6F–öâæ÷&–v–â—&WGW&ârs¶ÆWBFƒ×RçF†æÖS¶–b‡&Vf—‚bb‡FƒÓÓ×&Vf—‡ÇÇF‚ç7F'G5v—F‚‡&Vf—‚²ròr’’—Fƒ×F‚ç6Æ–6R‡&Vf—‚æÆVæwF‚—ÇÂròs¶–b‡F‚ÓÖ&6RbbF‚ç7F'G5v—F‚†&6R²ròr’—Fƒ×FƒÓÓÒròsö&6S¦&6R·Fƒ·&WGW&âÆÆ÷vVB‡F‚“÷F‚²‡Rç6V&6‡ÇÂrr“¢rwÓ² ¢&6öç7B&VE&÷WFT6öö¶–SÒ‚“Óç·G'—¶f÷"†6öç7B—FVÒöbFö7VÖVçBæ6öö¶–Rç7Æ—B‚s²r’—¶6öç7B¶æÖRÂââç&W7EÓÖ—FVÒçG&–Ò‚’ç7Æ—B‚sÒr“¶–b†æÖSÓÓÖ²—&WGW&âFV6öFUU$”6ö×öæVçB‡&W7Bæ¦ö–â‚sÒr—ÇÂrr—×Ö6F6‚…ò—·×&WGW&ârwÓ² ¢&ÆWB7W'&VçD–çFW&æÃÖ–çFW&æÂ‡&VE&÷WFT6öö¶–R‚’—ÇÆ&6S¶6öç7B6fS×cÓç¶6öç7BƒÖ–çFW&æÂ‡b—ÇÆ&6S·G'—¶Fö7VÖVçBæ6öö¶–SÖ²²sÒr¶Væ6öFUU$”6ö×öæVçB‡‚’²s²FƒÒó²Ö‚ÔvSÓC3#²6ÖU6—FSÔÆ‚r²†Æö6F–öâç&÷Fö6öÃÓÓÒv‡GG3¢sòs²6V7W&Rs¢rr—Ö6F6‚…ò—·Ó¶7W'&VçD–çFW&æÃ×ƒ·v–æF÷rå5E$TÔdõ$tUô”åDU$äÅõäTÅõU$Ã×ƒ·&WGW&â‡Ó² ¢'v–æF÷rå5E$TÔdõ$tUôd•„TEõäTÅôDE$U55ô$#×G'VS·v–æF÷rå5E$TÔdõ$tUô”åDU$äÅõäTÅõU$ÃÖ7W'&VçD–çFW&æÃ·v–æF÷rå5E$TÔdõ$tUôäôDUõäTÅõ$ôõC×ÇÂròs² ¢"ò¢5E$TÔdõ$tUôäôDUôäD•dUô%$õu4U%ô„•5Dõ%•õcsr¢ö6öç7B†³Òw7G&VÖf÷&vTæöFUæVÅcsrs¶6öç7B‡3Ò‡7FFSÖ†—7F÷'’ç7FFR“Óç·G'—¶6öç7B“×7FFRbgG—Vöb7FFSÓÓÒvö&¦V7Bs÷7FFU¶†µÓ¦çVÆÃ¶–b‚—ÇÇG—Vöb’ÓÒvö&¦V7Br—&WGW&âçVÆÃ¶6öç7B#Ö–çFW&æÂ…7G&–ær†’ç&÷WFWÇÂrr’“¶–b‚"—&WGW&âçVÆÃ·&WGW&ç·&÷WFS§"Ç“¤ÖF‚æÖ‚ƒÄçVÖ&W"†’ç’—ÇÃ’ÆFWFƒ¤ÖF‚æÖ‚ƒÄçVÖ&W"†’æFWF‚—ÇÃ—×Ö6F6‚…ò—·&WGW&âçVÆÇ×Ó¶6öç7BÖ³Ò‡"Ç’ÆB“Óç¶6öç7B7FFSÖ†—7F÷'’ç7FFRbgG—Vöb†—7F÷'’ç7FFSÓÓÒvö&¦V7Bs÷²ââæ†—7F÷'’ç7FFWÓ§·Ó·7FFU¶†µÓ×·&÷WFS¦–çFW&æÂ‡"—ÇÆ&6RÇ“¤ÖF‚æÖ‚ƒÄÖF‚ç&÷VæB„çVÖ&W"‡’—ÇÃ’’ÆFWFƒ¤ÖF‚æÖ‚ƒÄÖF‚ç&÷VæB„çVÖ&W"†B—ÇÃ’—Ó·&WGW&â7FFWÓ¶6öç7B&WÒ‡#Ö7W'&VçD–çFW&æÂÇ“×67&öÆÅ’ÆCÖçVÆÂ“Óç¶6öç7BöÆCÖ‡2‚’ÆFWÖCÓÓÖçVÆÃò†öÆCööÆBæFWFƒ£“¦C·G'—¶†—7F÷'’ç&WÆ6U7FFR†Ö²‡"Ç’ÆFW’ÂrrÇ—Ö6F6‚…ò—·×Ó¶6öç7BW6ƒ×#Óç¶6öç7BöÆCÖ‡2‚“·&W†7W'&VçD–çFW&æÂÇ67&öÆÅ’ÆöÆCööÆBæFWFƒ£“¶6öç7Bƒ×6fR‡"“·G'—¶†—7F÷'’çW6…7FFR†Ö²‡‚ÃÂ†öÆCööÆBæFWFƒ£’³’ÂrrÇ—Ö6F6‚…ò—·&W‡‚ÃÂ†öÆCööÆBæFWFƒ£’³—×&WGW&â‡Ó¶6öç7BFWFƒÒ‚“Óç¶6öç7B“Ö‡2‚“·&WGW&â“ö’æFWFƒ£Ó·G'—¶†—7F÷'’ç67&öÆÅ&W7F÷&F–öãÒvÖçVÂwÖ6F6‚…ò—·Ó¶6öç7B–æ—CÖ‡2‚“¶–b‚–æ—GÇÆ–æ—Bç&÷WFRÓÖ7W'&VçD–çFW&æÂ—&W†7W'&VçD–çFW&æÂÆ–æ—Cö–æ—Bç“£Æ–æ—Cö–æ—BæFWFƒ£“¶6öç7B&W7F÷&SÒ‚“Óç¶6öç7B“Ö‡2‚“¶–b‚—ÇÆ’ç&÷WFRÓÖ7W'&VçD–çFW&æÂ—&WGW&ã¶6öç7BvóÒ‚“Óç67&öÆÅFò‡·F÷¦’ç’ÆÆVgC£Æ&V†f–÷#¢vWFòwÒ“·&WVW7Dæ–ÖF–öäg&ÖR‚‚“Óç&WVW7Dæ–ÖF–öäg&ÖR†vò’“·6WEF–ÖV÷WB†vòÃ#“·6WEF–ÖV÷WB†vòÃC#—Ó¶Fö7VÖVçBç&VG•7FFSÓÓÒvÆöF–ærsöFö7VÖVçBæFDWfVçDÆ—7FVæW"‚tDôÔ6öçFVçDÆöFVBrÇ&W7F÷&RÇ¶öæ6S§G'VWÒ“§&W7F÷&R‚“¶ÆWB67&öÆÅF–ÖW#Ó¶FDWfVçDÆ—7FVæW"‚w67&öÆÂrÂ‚“Óç¶6ÆV%F–ÖV÷WB‡67&öÆÅF–ÖW"“·67&öÆÅF–ÖW#×6WEF–ÖV÷WB‚‚“Óç¶6öç7B“Ö‡2‚“¶–b†’bf’ç&÷WFSÓÓÖ7W'&VçD–çFW&æÂ—&W†7W'&VçD–çFW&æÂÇ67&öÆÅ’Æ’æFWF‚—ÒÃƒ—ÒÇ·76—fS§G'VWÒ“¶FDWfVçDÆ—7FVæW"‚w÷7FFRrÆSÓç¶6öç7B“Ö‡2†Rç7FFR“¶–b‚’—&WGW&ã¶6öç7B#Ö–çFW&æÂ†’ç&÷WFR“¶–b‚"—&WGW&ã·6fR‡"“¶Fö7VÖVçBæFö7VÖVçDVÆVÖVçBæ6Æ74Æ—7BæFB‚w7G&VÖf÷&vRÖæöFRÖæF—fRÖÆöF–ærr“·&WVW7Dæ–ÖF–öäg&ÖR‚‚“ÓæÆö6F–öâç&VÆöB‚’—Ò“¶6öç7B—4&6³ÖÓç¶–b‚†–ç7Fæ6Vöb…DÔÄæ6†÷$VÆVÖVçB’—&WGW&âfÇ6S¶–b†æFF6WBç7G&VÖf÷&vT&6³ÓÓÒsr—&WGW&âG'VS¶6öç7BCÕ7G&–ær†çFW‡D6öçFVçGÇÂrr’çG&–Ò‚’ç&WÆ6R‚õÅÇ2²örÂrr“·&WGW&âõâƒó¦&6²ƒó¥ÅÇ2·FõÅÆ"“÷Ævò&6µÅÆ"’ö’çFW7B‡B—Ó² ¢'G'—¶–b†Æö6F–öâçF†æÖRÓ×ÇÆÆö6F–öâç6V&6‡ÇÆÆö6F–öâæ†6‚–†—7F÷'’ç&WÆ6U7FFR††—7F÷'’ç7FFRÂrrÇ—Ö6F6‚…ò—·Ó² ¢&6öç7BÆöF–æs×CÓç¶Fö7VÖVçBæFö7VÖVçDVÆVÖVçBæ6Æ74Æ—7BæFB‚w7G&VÖf÷&vRÖæöFRÖæF—fRÖÆöF–ærr“·G'—·BbgBæ6Æ74Æ—7BbgBæ6Æ74Æ—7BæFB‚w7G&VÖf÷&vRÖæbÖÆöF–ærr—Ö6F6‚…ò—·×Ó² ¢&6öç7B&VÆöCÒ‚“Óç&WVW7Dæ–ÖF–öäg&ÖR‚‚“ÓæÆö6F–öâç&VÆöB‚’“² ¢&6öç7BF6ƒÖÓç¶–b‚†–ç7Fæ6Vöb…DÔÄæ6†÷$VÆVÖVçB—ÇÆæ†4GG&–'WFR‚vF÷væÆöBr’—&WGW&ã¶6öç7B&sÖæFF6WBç7G&VÖf÷&vTæeW&ÇÇÆævWDGG&–'WFR‚v‡&Vbr—ÇÂrs¶6öç7BƒÖ–çFW&æÂ‡&r“¶–b‚‚—&WGW&ã¶æFF6WBç7G&VÖf÷&vTæeW&Ã×ƒ¶ç6WDGG&–'WFR‚v‡&VbrÇ—Ó² ¢&Fö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚v¶‡&VeÒÆ¶FF×7G&VÖf÷&vRÖæb×W&ÅÒr’æf÷$V6‚‡F6‚“¶æWr×WFF–öäö'6W'fW"‡'3Óç'2æf÷$V6‚‡#Óç"æFFVDæöFW2æf÷$V6‚†ãÓç¶–b†âææöFUG—RÓÓ—&WGW&ã¶–b†â–ç7Fæ6Vöb…DÔÄæ6†÷$VÆVÖVçB—F6‚†â“¶âçVW'•6VÆV7F÷$ÆÂbfâçVW'•6VÆV7F÷$ÆÂ‚v¶‡&VeÒÆ¶FF×7G&VÖf÷&vRÖæb×W&ÅÒr’æf÷$V6‚‡F6‚—Ò’’’æö'6W'fR†Fö7VÖVçBæFö7VÖVçDVÆVÖVçBÇ¶6†–ÆDÆ—7C§G'VRÇ7V'G&VS§G'VWÒ“² ¢&Fö7VÖVçBæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆSÓç¶–b†RæFVfVÇE&WfVçFVGÇÆRæ'WGFöâÓÓ—&WGW&ã¶6öç7BÖRçF&vWBbfRçF&vWBæ6Æ÷6W7CöRçF&vWBæ6Æ÷6W7B‚vr“¦çVÆÃ¶–b‚ÇÆæ†4GG&–'WFR‚vF÷væÆöBr’—&WGW&ã¶6öç7BƒÖæFF6WBç7G&VÖf÷&vTæeW&ÇÇÂrs¶–b‚‚—&WGW&ã¶6öç7BvçG4æWsÔ&ööÆVâ†RæÖWF¶W—ÇÆRæ7G&Ä¶W—ÇÆRç6†–gD¶W—ÇÆRæÇD¶W—ÇÂ†çF&vWBbfçF&vWBçFôÆ÷vW$66R‚’ÓÒu÷6VÆbr’“¶–b‚vçG4æWrbf—4&6²†’bfFWF‚‚“ã—¶ÆöF–ær†“¶Rç&WfVçDFVfVÇB‚“¶Rç7F÷–ÖÖVF–FU&÷vF–öâ‚“¶†—7F÷'’æ&6²‚“·&WGW&çÖ–b‡vçG4æWr—·6fR‡‚“¶ÆöF–ær†“·&WGW&ç×W6‚‡‚“¶ÆöF–ær†“¶Rç&WfVçDFVfVÇB‚“¶Rç7F÷–ÖÖVF–FU&÷vF–öâ‚“·&VÆöB‚—ÒÇG'VR“² ¢&Fö7VÖVçBæFDWfVçDÆ—7FVæW"‚w7V&Ö—BrÆSÓç¶6öç7BcÖRçF&vWC¶–b‚†b–ç7Fæ6Vöb…DÔÄf÷&ÔVÆVÖVçB’—&WGW&ã¶6öç7BÖWF†öCÒ†bævWDGG&–'WFR‚vÖWF†öBr—ÇÂvvWBr’çFôÆ÷vW$66R‚“¶–b†ÖWF†öBÓÒvvWBwÇÆbçF&vWB—¶ÆöF–ær†Rç7V&Ö—GFW'ÇÆb“·&WGW&çÖ6öç7B#ÖRç7V&Ö—GFW"Ç&sÒ†"bf"ævWDGG&–'WFR‚vf÷&Ö7F–öâr’—ÇÆbævWDGG&–'WFR‚v7F–öâr—ÇÆ&6S¶6öç7BF&vWCÖ–çFW&æÂ‡&r“¶–b‚F&vWB—¶ÆöF–ær†Rç7V&Ö—GFW'ÇÆb“·&WGW&çÖ6öç7BSÖæWrU$Â‡F&vWBÆÆö6F–öâæ÷&–v–â’Ç£ÖæWrU$Å6V&6…&×2‚“¶f÷"†6öç7B¶âÇeÒöbæWrf÷&ÔFF†bÆ'ÇÇVæFVf–æVB’æVçG&–W2‚’––b‡G—VöbcÓÓÒw7G&–ærrbgbÓÒrr—¢æVæB†âÇb“·Rç6V&6ƒ×¢çFõ7G&–ær‚“·W6‚‡RçF†æÖR·Rç6V&6‚“¶ÆöF–ær†Rç7V&Ö—GFW'ÇÆb“¶Rç&WfVçDFVfVÇB‚“¶Rç7F÷–ÖÖVF–FU&÷vF–öâ‚“·&VÆöB‚—ÒÇG'VR“² ¢'Ò’‚“³Â÷67&—Câ ¢¢Fö7VÖVçBÒbrrsÂFö7G—R‡FÖÃãÆ‡FÖÃãÆ†VCãÆÖWF6†'6WCÒ'WFbÓ‚#ãÆÖWFæÖSÒ'f–Ww÷'B"6öçFVçCÒ'v–GFƒÖFWf–6R×v–GF‚Æ–æ—F–Â×66ÆSÓ#ç·&V†–FU÷67&—G×µöæöFUöff–6öåöÆ–æ²†66W75÷&Vf—‚—×¶W‡G&ö†VGÓÇF—FÆSç¶‡FÖÂæW66R‡F—FÆR—ÒÒ¶‡FÖÂæW66R†ÖævW"ææöFUöæÖR—ÓÂ÷F—FÆSçµöæöFU÷æVÅö772‚—ÓÂö†VCãÆ&öG“çµöæöFU÷æVÅö†VFW"‡W6W"Â7F—fR—ÓÆÖ–ç¶Ö–åöGG'Óç¶6öçFVçGÓÂöÖ–ãç¶&öG•÷67&—GÓÇ67&—Câ‚‚“Óç·¶6öç7BƒÖFö7VÖVçBçVW'•6VÆV7F÷"‚rçæVÂÖ†VFW"r“¶6öç7B#ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖæb×FövvÆUÒr“¶–b‚‡ÇÂ"—&WGW&ã¶6öç7BFW6·F÷Ò‚“Óçv–æF÷ræ–ææW%v–GFƒã“¶6öç7BÇ”FW6·F÷7FFSÒ‚“Óç·¶–b‚FW6·F÷‚’—·¶Fö7VÖVçBæ&öG’æ6Æ74Æ—7Bç&VÖ÷fR‚væöFR×6–FV&"Ö6öÆÆ6VBr“·&WGW&ã·×Ö6öç7B6fVCÖÆö6Å7F÷&vRævWD—FVÒ‚væöFU6–FV&$6öÆÆ6VBr“ÓÓÒss¶Fö7VÖVçBæ&öG’æ6Æ74Æ—7BçFövvÆR‚væöFR×6–FV&"Ö6öÆÆ6VBrÇ6fVB“¶"ç6WDGG&–'WFR‚v&–ÖW‡æFVBrÇ6fVCòvfÇ6Rs¢wG'VRr“¶"ç6WDGG&–'WFR‚v&–ÖÆ&VÂrÇ6fVCòtW‡æB6–FV&"s¢t6öÆÆ6R6–FV&"r“·×Ó¶"æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç·¶–b†FW6·F÷‚’—·¶6öç7B6öÆÆ6VCÖFö7VÖVçBæ&öG’æ6Æ74Æ—7BçFövvÆR‚væöFR×6–FV&"Ö6öÆÆ6VBr“¶Æö6Å7F÷&vRç6WD—FVÒ‚væöFU6–FV&$6öÆÆ6VBrÆ6öÆÆ6VCòss¢sr“¶"ç6WDGG&–'WFR‚v&–ÖW‡æFVBrÆ6öÆÆ6VCòvfÇ6Rs¢wG'VRr“¶"ç6WDGG&–'WFR‚v&–ÖÆ&VÂrÆ6öÆÆ6VCòtW‡æB6–FV&"s¢t6öÆÆ6R6–FV&"r“·&WGW&ã·×Ö6öç7B÷VãÖ‚æ6Æ74Æ—7BçFövvÆR‚væbÖ÷Vâr“¶"ç6WDGG&–'WFR‚v&–ÖW‡æFVBrÆ÷VãòwG'VRs¢vfÇ6Rr“¶"ç6WDGG&–'WFR‚v&–ÖÆ&VÂrÆ÷Vãòt6Æ÷6Ræf–vF–öâs¢t÷Vâæf–vF–öâr“·×Ò“¶Fö7VÖVçBæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆSÓç·¶–b†FW6·F÷‚—ÇÂ‚æ6Æ74Æ—7Bæ6öçF–ç2‚væbÖ÷Vâr’—&WGW&ã¶–b‚‚æ6öçF–ç2†RçF&vWB’—·¶‚æ6Æ74Æ—7Bç&VÖ÷fR‚væbÖ÷Vâr“¶"ç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂvfÇ6Rr“¶"ç6WDGG&–'WFR‚v&–ÖÆ&VÂrÂt÷Vâæf–vF–öâr“·×××Ò“·v–æF÷ræFDWfVçDÆ—7FVæW"‚w&W6—¦RrÂ‚“Óç·¶–b†FW6·F÷‚’—·¶‚æ6Æ74Æ—7Bç&VÖ÷fR‚væbÖ÷Vâr“¶Ç”FW6·F÷7FFR‚“·×ÖVÇ6W·¶Fö7VÖVçBæ&öG’æ6Æ74Æ—7Bç&VÖ÷fR‚væöFR×6–FV&"Ö6öÆÆ6VBr“¶"ç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂvfÇ6Rr“¶"ç6WDGG&–'WFR‚v&–ÖÆ&VÂrÂt÷Vâæf–vF–öâr“·×××Ò“¶Ç”FW6·F÷7FFR‚“·×Ò’‚“³Â÷67&—CçµöæöFU÷'VçF–ÖUöW'&÷%÷67&—B‚—×µöæöFUöÖ÷&UöÖVçU÷67&—B‚—×¶f—†VEöFG&W75÷67&—GÓÂö&öG“ãÂö‡FÖÃârrp¢–bÖF6†VE÷æVÅ÷&Vf—‚—2æ÷BæöæS ¢2¶VWF†R6öæf–wW&VBæVÂô’U$Â6æöæ–6Ã¢öFÖ–â—2F6†&ö&BÀ¢2öFÖ–âöÆöv–â—2Æöv–âæBöFÖ–âöÖævRòâââ—2æVÂæf–vF–öâà¢2ÆVv7’öFÖ–â÷æVÂòâââ&WVW7G2&R7F–ÆÂ66WFVB'’Ö–FFÆWv&Rà¢f÷"V÷FR–â‚r"rÂ"r"“ ¢V&Æ–5÷&ö÷BÒ66W75÷&Vf—‚÷""ò ¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b'·V÷FWÒ÷æVÃò"Âb'·V÷FW×·V&Æ–5÷&ö÷GÓò"¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b'·V÷FWÒ÷æVÂò"Âb'·V÷FW×¶66W75÷&Vf—‡Òò"¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b'·V÷FWÒ÷æVÇ·V÷FWÒ"Âb'·V÷FW×·V&Æ–5÷&ö÷G×·V÷FWÒ"¢–b66W75÷&Vf—ƒ ¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b'·V÷FWÒö’÷c"Âb'·V÷FW×¶66W75÷&Vf—‡Òö’÷c"¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b'·V÷FWÒ÷æVÂ×Æ’"Âb'·V÷FW×¶66W75÷&Vf—‡Ò÷æVÂ×Æ’"¢–b66W75÷&Vf—ƒ ¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R‚v‡&VcÒ"ò"rÂbv‡&VcÒ'¶66W75÷&Vf—‡Ò"r¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R‚&‡&VcÒròr"Âb&‡&VcÒw¶66W75÷&Vf—‡Òr"¢–bÖævW"æ†–FU÷æVÅö†÷fW%÷W&Ç3 ¢25E$TÔdõ$tUôäôDUô„”DDTåôäD•dUô„õdU%õcS¢F†RæF—fRgVÆÂ×&VÆö@¢2æf–vF÷"Ç&VG’W‡÷6W2öæÇ’F†R6æöæ–6ÂæVÂô’&ö÷B–â‡&Vbà¢2Fòæ÷B–æ¦V7BF†RöÆB‡&Vb×&VÖ÷fÂöÆö6F–öâæ76–vâ†æFÆW"&V6W6R—@¢2v÷VÆB'—72†–FFVâ×&÷WFRgVÆÂ&VÆöG2à¢70 ¢ÖF6†VE÷7G&VÕ÷&Vf—‚Òô5U%$TåEõ5E$TÕõ$Td•‚ævWB‚¢7G&VÕ÷&Vf—‚ÒÖF6†VE÷7G&VÕ÷&Vf—‚–bÖF6†VE÷7G&VÕ÷&Vf—‚—2æ÷BæöæRVÇ6R†b"÷¶ÖævW"ç7G&VÕ÷6ÇVwÒ"–bÖævW"ç7G&VÕ÷6ÇVrVÇ6R""¢–b7G&VÕ÷&Vf—ƒ ¢f÷"&÷WFR–â‚'Æ–Æ—7B"Â&æöFR×Æ’"Â&Æ—fR"Â&vWBç‡"Â'Æ–W%ö’ç‡"Â&6†ææVÂÖÆöv÷2"“ ¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†br"÷·&÷WFWÒrÂbr'·7G&VÕ÷&Vf—‡Ò÷·&÷WFWÒr¢Fö7VÖVçBÒFö7VÖVçBç&WÆ6R†b"r÷·&÷WFWÒ"Âb"w·7G&VÕ÷&Vf—‡Ò÷·&÷WFWÒ"¢&WGW&âFö7VÖVç@   ¦FVbö–æEöæb‡W6W#¢æVÄ66W75W6W"Â7F—fS¢7G"Ò""’Óâ7G# ¢2¶WBf÷"6ö×F–&–Æ—G’v—F‚öÆFW"W‡FVç6–öâ6öFRà¢&WGW&âöæöFU÷æVÅö†VFW"‡W6W"Â7F—fR  ¦FVbö–æE÷vR‡W6W#¢æVÄ66W75W6W"ÂF—FÆS¢7G"Â6öçFVçC¢7G"’Óâ7G# ¢7F—fUöÖÒ°¢$6†ææVÇ2#¢&6†ææVÇ2"À¢$6†ææVÂVF—F÷"#¢&6†ææVÇ2"À¢$6FVv÷&–W2#¢&6FVv÷&–W2"À¢$Æ—fR6W76–öç2#¢'6W76–öç2"À¢%Æ–Æ—7G2#¢'Æ–Æ—7G2"À¢%Æ–Æ—7BVF—F÷"#¢'Æ–Æ—7G2"À¢%Æ–Æ—7B÷&FW"#¢'Æ–Æ—7G2"À¢%Æ–Æ—7BW6W'2#¢'W6W'2"À¢$FBÆ–Æ—7BW6W"#¢'W6W'2"À¢$Æöw2#¢&Æöw2"À¢%6WGF–æw2#¢'6WGF–æw2"À¢Ð¢&WGW&âöæöFU÷æVÅöFö7VÖVçB‡W6W"ÂF—FÆRÂ6öçFVçBÂ7F—fSÖ7F—fUöÖævWB‡F—FÆRÂ""’ ¦FVbö÷F–öåöÆ—7B†7W'&VçC¢7G"ÂfÇVW3¢Æ—7E·GWÆU·7G"Â7G%ÕÒ’Óâ7G# ¢&WGW&ârræ¦ö–â€¢bsÆ÷F–öâfÇVSÒ'¶‡FÖÂæW66R‡fÇVRÂV÷FSÕG'VR—Ò"²'6VÆV7FVB"–bfÇVRÓÒ7W'&VçBVÇ6R"'Óç¶‡FÖÂæW66R†Æ&VÂ—ÓÂö÷F–öãâp¢f÷"fÇVRÂÆ&VÂ–âfÇVW0¢  ¦FVböæöFUö6FVv÷'•ö6FÆöuöæÖW2‚’ÓâÆ—7E·7G%Ó ¢æÖW3¢Æ—7E·7G%ÒÒµÐ¢6VVã¢6WE·7G%ÒÒ6WB‚¢f÷"—FVÒ–â6÷'FVB†ÖævW"æÖ–åö6FVv÷&–W2Â¶W“ÖÆÖ&F&÷s¢†–çB‡&÷rç6÷'Eö÷&FW"÷"’Â&÷rææÖRæÆ÷vW"‚’’“ ¢æÖRÒ7G"†—FVÒææÖR÷"""’ç7G&—‚¢Ö&¶W"ÒæÖRæÆ÷vW"‚¢–bæÖRæBÖ&¶W"æ÷B–â6VVâæBÖ&¶W"Ò'Væ6FVv÷&—¦VB# ¢æÖW2æVæB†æÖR“²6VVâæFB†Ö&¶W"¢v—F‚ÖævW"æÆö6³ ¢Æö6ÅöæÖW2Ò°¢æÖRf÷"'VçF–ÖR–âÖævW"æ6†ææVÇ2çfÇVW2‚¢f÷"æÖR–âö6öæf–uö6FVv÷&–W2‡'VçF–ÖRæ6öæf–r¢–bæÖRç7G&—‚’æBæÖRç7G&—‚’æÆ÷vW"‚’æ÷B–â6VVâæBæÖRç7G&—‚’æÆ÷vW"‚’Ò'Væ6FVv÷&—¦VB ¢Ð¢f÷"æÖR–â6÷'FVB†Æö6ÅöæÖW2Â¶W“×7G"æÆ÷vW"“ ¢Ö&¶W"ÒæÖRæÆ÷vW"‚¢–bÖ&¶W"æ÷B–â6VVã ¢æÖW2æVæB†æÖR“²6VVâæFB†Ö&¶W"¢æÖW2æVæB‚%Væ6FVv÷&—¦VB"¢&WGW&âæÖW0  ¦FVböVffV7F—fUö6FVv÷'•ö÷&FW%öæÖW2‚’ÓâÆ—7E·7G%Ó ¢6FÆörÒöæöFUö6FVv÷'•ö6FÆöuöæÖW2‚¢F—7Æ’Ò¶æÖRæÆ÷vW"‚“¢æÖRf÷"æÖR–â6FÆöwÐ¢÷&FW&VC¢Æ—7E·7G%ÒÒµÐ¢f÷"Ö&¶W"–âÖævW"æ6FVv÷'•ö÷&FW%ö÷fW'&–FW3 ¢¶W’Ò7G"†Ö&¶W"÷"""’ç7G&—‚’æÆ÷vW"‚¢–b¶W’–âF—7Æ’æB¶W’Ò'Væ6FVv÷&—¦VB"æBF—7Æ•¶¶W•Òæ÷B–â÷&FW&VC ¢÷&FW&VBæVæB†F—7Æ•¶¶W•Ò¢÷&FW&VBæW‡FVæB†æÖRf÷"æÖR–â6FÆör–bæÖRæ÷B–â÷&FW&VBæBæÖRæÆ÷vW"‚’Ò'Væ6FVv÷&—¦VB"¢÷&FW&VBæVæB‚%Væ6FVv÷&—¦VB"¢&WGW&â÷&FW&V@  ¦FVböÖ–åö6FVv÷'•ö÷&FW%öÖ‚’ÓâF–7E·7G"Â–çEÓ ¢&WGW&â¶æÖRæÆ÷vW"‚“¢†–æFW‚²’¢f÷"–æFW‚ÂæÖR–âVçVÖW&FR…öVffV7F—fUö6FVv÷'•ö÷&FW%öæÖW2‚’—Ð  ¦FVbö6FVv÷'•÷6÷'E÷fÇVR†æÖS¢7G"’ÓâGWÆU¶–çBÂ–çBÂ7G%Ó ¢6ÆVâÒ†æÖR÷"%Væ6FVv÷&—¦VB"’ç7G&—‚’÷"%Væ6FVv÷&—¦VB ¢–b6ÆVâæÆ÷vW"‚’ÓÒ'Væ6FVv÷&—¦VB# ¢&WGW&âƒ"Â¢£’Â6ÆVâæÆ÷vW"‚’¢Ö–åö÷&FW"ÒöÖ–åö6FVv÷'•ö÷&FW%öÖ‚¢–b6ÆVâæÆ÷vW"‚’–âÖ–åö÷&FW# ¢&WGW&âƒÂÖ–åö÷&FW%¶6ÆVâæÆ÷vW"‚•ÒÂ6ÆVâæÆ÷vW"‚’¢&WGW&âƒÂÂ6ÆVâæÆ÷vW"‚’  ¦FVbö6öæf–uö6FVv÷&–W2†6öæf–s¢ç’’ÓâÆ—7E·7G%Ó ¢fÇVW3¢Æ—7E·7G%ÒÒµÐ¢&–Ö'’Ò7G"†vWFGG"†6öæf–rÂ&6FVv÷'’"Â""’÷"""’ç7G&—‚¢–b&–Ö'“ ¢fÇVW2æVæB‡&–Ö'’¢f÷"&r–âÆ—7B†vWFGG"†6öæf–rÂ&6FVv÷&–W2"ÂµÒ’÷"µÒ“ ¢6ÆVæVBÒ7G"‡&r÷"""’ç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–âfÇVW3 ¢fÇVW2æVæB†6ÆVæVB¢&WGW&âfÇVW2÷"²%Væ6FVv÷&—¦VB%Ð  ¦FVböÆö6Åö6FVv÷&–W2‚’ÓâÆ—7E·7G%Ó ¢v—F‚ÖævW"æÆö6³ ¢æÖW2Ò°¢æÖP¢f÷"'B–âÖævW"æ6†ææVÇ2çfÇVW2‚¢–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒ&Æö6Â ¢f÷"æÖR–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r¢Ð¢æÖW2çWFFR†—FVÒææÖRf÷"—FVÒ–âÖævW"æÖ–åö6FVv÷&–W2–b—FVÒææÖRç7G&—‚’¢æÖW2æFB‚%Væ6FVv÷&—¦VB"¢&WGW&â6÷'FVB†æÖW2Â¶W“Õö6FVv÷'•÷6÷'E÷fÇVR  ¦FVböÆö6Å÷6ÇVr‡fÇVS¢7G"’Óâ7G# ¢FW‡BÒ&Rç7V"‡"%µäÕ¦×£Ó•òÕÒ²"Â"Ò"Â7G"‡fÇVR÷"""’ç7G&—‚’’ç7G&—‚"Õò"’æÆ÷vW"‚¢FW‡BÒ&Rç7V"‡""Ò²"Â"Ò"ÂFW‡B•³£“eÐ¢–bæ÷BFW‡C ¢FW‡BÒ&6†ææVÂÒ"²6V7&WG2çFö¶Våö†W‚ƒ2¢ÖævW"ç6fUö¶W’‡FW‡B¢&WGW&âFW‡@  ¦FVböÆö6Åö6†ææVÅ÷'VçF–ÖR†¶W“¢7G"’Óâ'VçF–ÖS ¢ÖævW"ç6fUö¶W’†¶W’¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB†¶W’¢–bæ÷B'VçF–ÖR÷"'VçF–ÖRæ6öæf–ræ6FÆöuö÷væW"Ò&Æö6Â# ¢&—6R…EEW†6WF–öâƒCBÂ$–æFWVæFVçBæöFR6†ææVÂæ÷Bf÷VæB"¢&WGW&â'VçF–ÖP  ¦FVböæöFU÷V&Æ–5ö&6R‡&WVW7C¢&WVW7B’Óâ7G# ¢†÷7BÂ&WVW7E÷÷'BÒ÷&WVW7EöWF†÷&—G’‡&WVW7B¢ÖF6†VE÷&Vf—‚Òô5U%$TåEõ5E$TÕõ$Td•‚ævWB‚¢f÷"fÇVR–âÖævW"ç7G&VÕ÷W&Ç3 ¢6ÆVæVBÒ7G"‡fÇVR÷"""’ç7G&—‚’ç'7G&—‚"ò"¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B†6ÆVæVB¢W‡V7FVEö†÷7BÒ‡'6VBæ†÷7FæÖR÷"""’æÆ÷vW"‚’ç'7G&—‚"â"¢FVfVÇE÷÷'BÒCC2–b'6VBç66†VÖRÓÒ&‡GG2"VÇ6Rƒ ¢W‡V7FVE÷÷'BÒ–çB‡'6VBç÷'B÷"FVfVÇE÷÷'B¢&Vf—‚Ò'6VBçF‚ç'7G&—‚"ò"¢–b†÷7BÓÒW‡V7FVEö†÷7BæB–çB‡&WVW7E÷÷'B÷"FVfVÇE÷÷'B’ÓÒW‡V7FVE÷÷'C ¢–bÖF6†VE÷&Vf—‚—2æöæR÷"&Vf—‚ÓÒÖF6†VE÷&Vf—ƒ ¢&WGW&â6ÆVæV@¢–bÖævW"ç7G&VÕ÷W&Ç3 ¢&WGW&âÖævW"ç7G&VÕ÷W&Ç5³Òç7G&—‚’ç'7G&—‚"ò"¢&WGW&â7G"‡&WVW7Bæ&6U÷W&Â’ç'7G&—‚"ò"  ¦FVböæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â‡fÇVS¢7G"Â&WVW7C¢&WVW7B’Óâ7G# ¢ÆövòÒ7G"‡fÇVR÷"""’ç7G&—‚¢–bæ÷BÆövó ¢&WGW&â" ¢–bÆövòç7F'G7v—F‚‚"ö6†ææVÂÖÆöv÷2ò"“ ¢&WGW&âb'µöæöFU÷V&Æ–5ö&6R‡&WVW7B—×¶Æöv÷Ò ¢&WGW&âÆövð  ¦FVböÆö6ÅöÆövõ÷F‚‡fÇVS¢7G"’ÓâF‚ÂæöæS ¢G'“ ¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B‡7G"‡fÇVR÷"""’¢F‚Ò'6VBçF‚÷"7G"‡fÇVR÷"""¢&Vf—‚Ò"ö6†ææVÂÖÆöv÷2ò ¢–bæ÷BF‚ç7F'G7v—F‚‡&Vf—‚“ ¢&WGW&âæöæP¢æÖRÒF‚‡F…¶ÆVâ‡&Vf—‚“¥Ò’ææÖP¢–bæ÷BæÖR÷"æÖRÒF…¶ÆVâ‡&Vf—‚“¥Ó ¢&WGW&âæöæP¢6æF–FFRÒ„4„ääTÅôÄôtõõ$ôõBòæÖR’ç&W6öÇfR‚¢&ö÷BÒ4„ääTÅôÄôtõõ$ôõBç&W6öÇfR‚¢–b6æF–FFRç&VçBÒ&ö÷C ¢&WGW&âæöæP¢&WGW&â6æF–FFP¢W†6WBW†6WF–öã ¢&WGW&âæöæP  ¦7–æ2FVb÷&W6öÇfUöÆö6Åö6†ææVÅöÆövò‡&WVW7C¢&WVW7BÂf÷&Ó¢ç’Â6†ææVÅ÷6ÇVs¢7G"Â&Wf–÷W3¢7G"Ò""’Óâ7G# ¢&VÖ÷fUöÆövòÒf÷&ÒævWB‚'&VÖ÷fUöÆövò"’—2æ÷BæöæP¢7WÆ–VE÷W&ÂÒ7G"†f÷&ÒævWB‚&Æövõ÷W&Â"’÷"""’ç7G&—‚¢WÆöBÒf÷&ÒævWB‚&Æövõöf–ÆR"¢f–ÆVæÖRÒ7G"†vWFGG"‡WÆöBÂ&f–ÆVæÖR"Â""’÷"""’ç7G&—‚¢öÆE÷F‚ÒöÆö6ÅöÆövõ÷F‚‡&Wf–÷W2 ¢–bf–ÆVæÖS ¢FFÒv—BWÆöBç&VB‚¢–bæ÷BFF ¢&—6RfÇVTW'&÷"‚%WÆöFVBÆövò—2V×G’"¢–bÆVâ†FF’â"¢#B¢#C ¢&—6RfÇVTW'&÷"‚$6†ææVÂÆövò×W7B&R"Ô"÷"6ÖÆÆW""¢W‡BÒ" ¢–bFFç7F'G7v—F‚†"%Çƒƒ•äuÇ%ÆåÇƒÆâ"“ ¢W‡BÒ"çær ¢VÆ–bFFç7F'G7v—F‚†"%Ç†feÇ†C…Ç†fb"“ ¢W‡BÒ"æ§r ¢VÆ–bÆVâ†FF’ãÒ"æBFF³£EÒÓÒ"%$”db"æBFF³ƒ£%ÒÓÒ"%tT%# ¢W‡BÒ"çvV' ¢VÆ–bFFç7F'G7v—F‚‚†"$t”cƒv"Â"$t”cƒ–"’“ ¢W‡BÒ"æv–b ¢–bæ÷BW‡C ¢&—6RfÇVTW'&÷"‚$6†ææVÂÆövò×W7B&RärÂ¥TrÂvV%÷"t”b"¢4„ääTÅôÄôtõõ$ôõBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢æWuöæÖRÒöÆö6Å÷6ÇVr†6†ææVÅ÷6ÇVr’²W‡@¢F&vWBÒ4„ääTÅôÄôtõõ$ôõBòæWuöæÖP¢FV×ÒF&vWBçv—F…÷7Vff—‚‡F&vWBç7Vff—‚²"çF×"¢FV×çw&—FUö'—FW2†FF¢÷2æ6†ÖöB‡FV×ÂócCB¢FV×ç&WÆ6R‡F&vWB¢–böÆE÷F‚æBöÆE÷F‚ÒF&vWC ¢öÆE÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢&WGW&âb"ö6†ææVÂÖÆöv÷2÷¶æWuöæÖWÒ  ¢–b7WÆ–VE÷W&Ã ¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B‡7WÆ–VE÷W&Â¢–b'6VBç66†VÖRæ÷B–â²&‡GG"Â&‡GG2'Ò÷"æ÷B'6VBææWFÆö3 ¢&—6RfÇVTW'&÷"‚$ÆövòU$Â×W7B7F'Bv—F‚‡GG¢òò÷"‡GG3¢òò"¢–böÆE÷F‚æB7WÆ–VE÷W&ÂÒ&Wf–÷W3 ¢öÆE÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&WGW&â7WÆ–VE÷W&Å³£#Ð ¢–b&VÖ÷fUöÆövó ¢–böÆE÷Fƒ ¢öÆE÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&WGW&â" ¢&WGW&â&Wf–÷W0  ¦FVb÷&ö&UöçVÖ&W"‡fÇVS¢ç’’ÓâfÆöBÂæöæS ¢G'“ ¢&WGW&âfÆöB‡fÇVR’–bfÇVRæ÷B–â„æöæRÂ""Â$âô"’VÇ6RæöæP¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&WGW&âæöæP  ¦FVb÷&ö&U÷&FR‡fÇVS¢ç’’ÓâfÆöBÂæöæS ¢FW‡BÒ7G"‡fÇVR÷"""¢–bæ÷BFW‡B÷"FW‡B–â²#ó"Â$âô'Ó ¢&WGW&âæöæP¢G'“ ¢–b"ò"–âFW‡C ¢ÆVgBÂ&–v‡BÒFW‡Bç7Æ—B‚"ò"Â¢&WGW&â&÷VæB†fÆöB†ÆVgB’òfÆöB‡&–v‡B’Â2’–bfÆöB‡&–v‡B’VÇ6RæöæP¢&WGW&â&÷VæB†fÆöB‡FW‡B’Â2¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Â¦W&ôF—f—6–öäW'&÷"“ ¢&WGW&âæöæP  ¦FVb&ö&UöæöFU÷6÷W&6R‡F&vWC¢7G"ÂF–ÖV÷WC¢–çBÒ’ÓâF–7E·7G"Âç•Ó ¢fg&ö&RÒ6‡WF–Âçv†–6‚‚&fg&ö&R"’÷""÷W7"ö&–âöfg&ö&R ¢–bæ÷B6‡WF–Âçv†–6‚†fg&ö&R’æBæ÷BF‚†fg&ö&R’æW†—7G2‚“ ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢b&fg&ö&Ræ÷Bf÷VæC¢¶fg&ö&WÒ'Ð¢6GW&VEöBÒFFWF–ÖRææ÷r‡F–ÖW¦öæRçWF2’æ—6öf÷&ÖB‚¢–÷WGV&U÷6÷W&6RÒ—5÷–÷WGV&U÷W&Â‡F&vWB¢&W6öÇfU÷7F'FVBÒF–ÖRæÖöæ÷Föæ–2‚¢G'“ ¢25E$TÔdõ$tUôäôDUõ”õUET$Uõ4õU$4Uõ44åôuT$DTEõccs¢&W6W'fRF†Rcã#R6÷W&6R×66â&V†f–÷"F‡&÷Vv‚F†Rcãcb6–ævÆRÖfÆ–v‡Bö&6¶öfb&W6öÇfW"à¢&W6öÇfVE÷F&vWBÒöwV&FVE÷&W6öÇfU÷7G&VÕ÷6÷W&6R‡F&vWBÂf÷&6S×–÷WGV&U÷6÷W&6RÂ'—75ö&6¶öfc×–÷WGV&U÷6÷W&6R¢W†6WB6÷W&6U&W6öÇfTW'&÷"2W†3 ¢&Vf—‚Ò%–÷UGV&R&W6öÇfRf–ÆVC¢"–b–÷WGV&U÷6÷W&6RVÇ6R" ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢&Vf—‚²7G"†W†2’Â'7FvR#¢'&W6öÇfR"Â&6GW&VEöB#¢6GW&VEöGÐ¢&W6öÇfU÷6V6öæG2Ò&÷VæB‡F–ÖRæÖöæ÷Föæ–2‚’Ò&W6öÇfU÷7F'FVBÂ2¢25E$TÔdõ$tUôäôDUõ”õUET$Uõ44åôÔUDDDôd5ED…õcS ¢2—BÖFÇÇ&VG’'6W2F†RÆ—fR„Å2Öæ–fW7BGW&–ær&W6öÇWF–öââfö–B¢26V6öæBgVÆÂfg&ö&R72†W&RÂv†–6‚6â7FÆÂöâ6öÖRvöövÆWf–FVòVFvW2à¢–b–÷WGV&U÷6÷W&6S ¢ÖWFFFÒ–÷WGV&U÷&ö&U÷–ÆöB‡F&vWB¢–bÖWFFF ¢–ÆöBÒF–7B†ÖWFFF¢–ÆöBçWFFR‡²&6GW&VEöB#¢6GW&VEöBÂ'7FvR#¢&6ö×ÆWFR"Â'&W6öÇfU÷6V6öæG2#¢&W6öÇfU÷6V6öæG7Ò¢&WGW&â–Æö@¢&w2Â6ÆVå÷F&vWBÒÖævW"æ–çWEö&w2‡&W6öÇfVE÷F&vWB¢6ÖBÒ¶fg&ö&RÂ"×b"Â&W'&÷""Â"Ö†–FUö&ææW""Â"ÖæÇ—¦VGW&F–öâ"Â#ƒ"Â"×&ö&W6—¦R"Â##"Â"×6†÷uöW'&÷""Â"×6†÷uöf÷&ÖB"Â"×6†÷u÷&öw&×2"Â"×6†÷u÷7G&V×2"Â"Ööb"Â&§6öâ%Ò²&w2²²"Ö’"Â6ÆVå÷F&vWEÐ¢&ö&U÷F–ÖV÷WBÒÖ‚ƒ3Â–çB‡F–ÖV÷WB’’–b–÷WGV&U÷6÷W&6RVÇ6RÖ‚ƒ2Â–çB‡F–ÖV÷WB’¢G'“ ¢&W7VÇBÒ7V'&ö6W72ç'Vâ†6ÖBÂ6GW&Uö÷WGWCÕG'VRÂFW‡CÕG'VRÂF–ÖV÷WC×&ö&U÷F–ÖV÷WBÂ6†V6³ÔfÇ6R¢W†6WB7V'&ö6W72åF–ÖV÷WDW‡—&VC ¢–b–÷WGV&U÷6÷W&6S ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢b%–÷UGV&R&W6öÇfVB7V66W76gVÆÇ’–â·&W6öÇfU÷6V6öæG3¢ãg×2Â'WB„Å2&ö&RF–ÖVB÷WBgFW"·&ö&U÷F–ÖV÷WGÒ6V6öæG2"Â'7FvR#¢&fg&ö&R"Â'&W6öÇfU÷6V6öæG2#¢&W6öÇfU÷6V6öæG2Â&6GW&VEöB#¢6GW&VEöGÐ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢b%&ö&RF–ÖVB÷WBgFW"·&ö&U÷F–ÖV÷WGÒ6V6öæG2"Â'7FvR#¢&fg&ö&R"Â&6GW&VEöB#¢6GW&VEöGÐ¢W†6WBõ4W'&÷"2W†3 ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢7G"†W†2’Â&6GW&VEöB#¢6GW&VEöGÐ¢G'“ ¢FFÒ§6öâæÆöG2‡&W7VÇBç7FF÷WB÷"'·Ò"¢W†6WB§6öâä¥4ôäFV6öFTW'&÷# ¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢‡&W7VÇBç7FFW'"÷"&fg&ö&R&WGW&æVB–çfÆ–B¥4ôâ"•²Ó#¥ÒÂ&6GW&VEöB#¢6GW&VEöGÐ¢–b&W7VÇBç&WGW&æ6öFRÒ÷"FFævWB‚&W'&÷""“ ¢FWF–ÂÒ7G"‚†FFævWB‚&W'&÷""’÷"·Ò’ævWB‚'7G&–ær"’÷"&W7VÇBç7FFW'"÷"%Væ&ÆRFò&VBF†R7G&VÒ"’ç7G&—‚¢&WGW&â²&ö²#¢fÇ6RÂ&W'&÷"#¢FWF–Å²Ó#¥ÒÂ'7FvR#¢&fg&ö&R"Â'&W6öÇfU÷6V6öæG2#¢&W6öÇfU÷6V6öæG2–b–÷WGV&U÷6÷W&6RVÇ6RæöæRÂ&6GW&VEöB#¢6GW&VEöGÐ¢7G&VÕ÷&öw&Ó¢F–7E¶–çBÂ–çEÒÒ·Ð¢&öw&×2ÒµÐ¢f÷"&u÷&öw&Ò–âFFævWB‚'&öw&×2"’÷"µÓ ¢–BÒ&u÷&öw&ÒævWB‚'&öw&Õö–B"Â&u÷&öw&ÒævWB‚'&öw&ÕöçVÒ"’¢G'“¢–BÒ–çB‡–B¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“¢–BÒæöæP¢Fw2Ò&u÷&öw&ÒævWB‚'Fw2"’÷"·Ð¢&öw&Õ÷7G&V×2ÒµÐ¢f÷"&u÷7G&VÒ–â&u÷&öw&ÒævWB‚'7G&V×2"’÷"µÓ ¢G'“¢–æFW‚Ò–çB‡&u÷7G&VÒævWB‚&–æFW‚"’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“¢–æFW‚ÒæöæP¢–b–æFW‚—2æ÷BæöæRæB–B—2æ÷BæöæS¢7G&VÕ÷&öw&Õ¶–æFW…ÒÒ–@¢&öw&Õ÷7G&V×2æVæB‡°¢&–æFW‚#¢–æFW‚Â'G—R#¢&u÷7G&VÒævWB‚&6öFV5÷G—R"’÷"'Væ¶æ÷vâ"À¢&6öFV2#¢&u÷7G&VÒævWB‚&6öFV5öæÖR"’÷"'Væ¶æ÷vâ"Â'v–GF‚#¢&u÷7G&VÒævWB‚'v–GF‚"’À¢&†V–v‡B#¢&u÷7G&VÒævWB‚&†V–v‡B"’Â&g2#¢÷&ö&U÷&FR‡&u÷7G&VÒævWB‚&fuög&ÖU÷&FR"’÷"&u÷7G&VÒævWB‚'%ög&ÖU÷&FR"’’À¢Ò¢&öw&×2æVæB‡²'&öw&Õö–B#¢–BÂ'&öw&ÕöçVÒ#¢&u÷&öw&ÒævWB‚'&öw&ÕöçVÒ"’Â'6W'f–6UöæÖR#¢Fw2ævWB‚'6W'f–6UöæÖR"’Â'6W'f–6U÷&÷f–FW"#¢Fw2ævWB‚'6W'f–6U÷&÷f–FW""’Â'7G&V×2#¢&öw&Õ÷7G&V×7Ò¢7G&V×2ÒµÐ¢6÷VçG3¢F–7E·7G"Â–çEÒÒ·Ð¢f÷"&u÷7G&VÒ–âFFævWB‚'7G&V×2"’÷"µÓ ¢G'“¢–æFW‚Ò–çB‡&u÷7G&VÒævWB‚&–æFW‚"’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“¢–æFW‚ÒæöæP¢¶–æBÒ&u÷7G&VÒævWB‚&6öFV5÷G—R"’÷"'Væ¶æ÷vâ ¢6÷VçG5¶¶–æEÒÒ6÷VçG2ævWB†¶–æBÂ’²¢Fw2Ò&u÷7G&VÒævWB‚'Fw2"’÷"·Ð¢7G&V×2æVæB‡°¢&–æFW‚#¢–æFW‚Â'G—R#¢¶–æBÂ&6öFV2#¢&u÷7G&VÒævWB‚&6öFV5öæÖR"’÷"'Væ¶æ÷vâ"À¢&6öFV5öÆöær#¢&u÷7G&VÒævWB‚&6öFV5öÆöæuöæÖR"’Â'&öw&Õö–B#¢7G&VÕ÷&öw&ÒævWB†–æFW‚’–b–æFW‚—2æ÷BæöæRVÇ6RæöæRÀ¢'v–GF‚#¢&u÷7G&VÒævWB‚'v–GF‚"’Â&†V–v‡B#¢&u÷7G&VÒævWB‚&†V–v‡B"’À¢&g2#¢÷&ö&U÷&FR‡&u÷7G&VÒævWB‚&fuög&ÖU÷&FR"’÷"&u÷7G&VÒævWB‚'%ög&ÖU÷&FR"’’À¢'6×ÆU÷&FR#¢&u÷7G&VÒævWB‚'6×ÆU÷&FR"’Â&6†ææVÇ2#¢&u÷7G&VÒævWB‚&6†ææVÇ2"’À¢&ÆæwVvR#¢Fw2ævWB‚&ÆæwVvR"’Â'F—FÆR#¢Fw2ævWB‚'F—FÆR"’À¢Ò¢f×BÒFFævWB‚&f÷&ÖB"’÷"·Ó²Fw2Òf×BævWB‚'Fw2"’÷"·Ð¢&WGW&â²&ö²#¢G'VRÂ&6GW&VEöB#¢6GW&VEöBÂ'7FvR#¢&6ö×ÆWFR"Â'&W6öÇfU÷6V6öæG2#¢&W6öÇfU÷6V6öæG2–b–÷WGV&U÷6÷W&6RVÇ6RæöæRÂ&f÷&ÖB#¢²&æÖR#¢f×BævWB‚&f÷&ÖEöæÖR"’Â&ÆöæuöæÖR#¢f×BævWB‚&f÷&ÖEöÆöæuöæÖR"’Â&GW&F–öâ#¢÷&ö&UöçVÖ&W"†f×BævWB‚&GW&F–öâ"’’Â&&—E÷&FR#¢–çB†fÆöB†f×BævWB‚&&—E÷&FR"’’’–b7G"†f×BævWB‚&&—E÷&FR"’÷"""’ç&WÆ6R‚rârÂrrÂ’æ—6F–v—B‚’VÇ6RæöæRÂ'6W'f–6UöæÖR#¢Fw2ævWB‚'6W'f–6UöæÖR"’Â'6W'f–6U÷&÷f–FW"#¢Fw2ævWB‚'6W'f–6U÷&÷f–FW""—ÒÂ'&öw&×2#¢&öw&×2Â'7G&V×2#¢7G&V×2Â&6÷VçG2#¢6÷VçG2Â'7FFW'"#¢&W7VÇBç7FFW'"ç7G&—‚•²Ó#¥Ò–b&W7VÇBç7FFW'"ç7G&—‚’VÇ6RæöæWÐ  ¦FVböÆö6Åö6†ææVÅöf÷&Ò‡W6W#¢æVÄ66W75W6W"Â6fs¢6†ææVÄ6öæf–rÂæöæRÒæöæRÂW'&÷#¢7G"Ò""’Óâ7G# ¢2Ò6fp ¢FVb&r†æÖS¢7G"ÂFVfVÇC¢ç’Ò""’Óâç“ ¢fÇVRÒvWFGG"†2ÂæÖRÂFVfVÇB’–b2VÇ6RFVfVÇ@¢&WGW&âFVfVÇB–bfÇVR—2æöæRVÇ6RfÇVP ¢FVbb†æÖS¢7G"ÂFVfVÇC¢ç’Ò""’Óâ7G# ¢&WGW&â‡FÖÂæW66R‡7G"‡&r†æÖRÂFVfVÇB’’ÂV÷FSÕG'VR ¢FVb6†V6¶VB†æÖS¢7G"ÂFVfVÇC¢&ööÂÒG'VR’Óâ7G# ¢&WGW&â&6†V6¶VB"–b&ööÂ‡&r†æÖRÂFVfVÇB’’VÇ6R"  ¢7W'&VçE÷f–FVòÒ7G"‡&r‚'f–FVõö6öFV2"Â&6÷’"’’æÆ÷vW"‚¢7W'&VçE÷f–FVòÒ²&WFõöƒ#cB#¢&WFò"Â&ƒ#cEöWFò#¢&WFò"Â&WFõöƒ#cR#¢&WFõö†Wf2"Â&ƒ#cUöWFò#¢&WFõö†Wf2'ÒævWB†7W'&VçE÷f–FVòÂ7W'&VçE÷f–FVò¢f–FVõ÷fÇVW2Ò²‚&6÷’"Â$6÷’ò77F‡&÷Vv‚"’Â‚&WFò"Â$‚ã#cBUDò(	BuRf—'7Bò6‡&öÖR6fR"’Â‚&ƒ#cEöçfVæ2"Â$åd”D”‚ã#cBådTä2(	BFVF–6FVBuR"’Â‚&WFõö†Wf2"Â$‚ã#cRUDò(	BuRf—'7Bò„Ud2"•Ð¢–b7W'&VçE÷f–FVòæ÷B–â·fÇVRf÷"fÇVRÂò–âf–FVõ÷fÇVW7Ó ¢f–FVõ÷fÇVW2æVæB‚†7W'&VçE÷f–FVòÂb$7W'&VçBÆVv7’6öFV2(	B¶7W'&VçE÷f–FV÷Ò"’¢7W'&VçEöVF–òÒ²&×2#¢&Æ–&×6ÆÖR"Â&÷W2#¢&Æ–&÷W2'ÒævWB‡7G"‡&r‚&VF–õö6öFV2"Â&6÷’"’’æÆ÷vW"‚’Â7G"‡&r‚&VF–õö6öFV2"Â&6÷’"’’æÆ÷vW"‚’¢7W'&VçEö÷WGWBÒ²&‡GG#¢&‡GG÷÷7B"Â''G#¢'VG'ÒævWB‡7G"‡&r‚&÷WGWE÷G—R"Â&†Ç2"’’æÆ÷vW"‚’Â7G"‡&r‚&÷WGWE÷G—R"Â&†Ç2"’’æÆ÷vW"‚’¢&W6öÇWF–öâÒb'·&r‚wv–GF‚r—×‡·&r‚v†V–v‡Br—Ò"–b&r‚'v–GF‚"’æB&r‚&†V–v‡B"’VÇ6R'6÷W&6R ¢¶æ÷vå÷&W6öÇWF–öç2Ò²#3ƒCƒ#c"Â##ScƒCC"Â#“#ƒƒ"Â#cƒ“"Â##ƒƒs#"Â##GƒSsb"Â#ƒSGƒCƒ"Â#s#ƒSsb"Â#s#ƒCƒ"Â#cCƒ3c"Â#C#gƒ#C'Ð¢&W6öÇWF–öå÷6VÆV7FVBÒ&W6öÇWF–öâ–b&W6öÇWF–öâ–â¶æ÷vå÷&W6öÇWF–öç2VÇ6R‚'6÷W&6R"–b&W6öÇWF–öâÓÒ'6÷W&6R"VÇ6R&7W7FöÒ"¢6FVv÷&–W2Ò""æ¦ö–â†bsÆ÷F–öâfÇVSÒ'¶‡FÖÂæW66R†æÖRÂV÷FSÕG'VR—Ò#ãÂö÷F–öãârf÷"æÖR–âöÆö6Åö6FVv÷&–W2‚’¢Æövõ÷&Wf–WrÒ" ¢–b&r‚&Æövõ÷W&Â"“ ¢Æövõ÷&Wf–WrÒbsÆF—b6Æ73Ò&6†ææVÂÖÆövòÖf–VÆB#ãÇ7â6Æ73Ò&f–VÆBÖÆ&VÂ#ä7W'&VçBÆövóÂ÷7ããÆF—b6Æ73Ò&Æövò×&Wf–Wr#ãÆ–Ör7&3Ò'·b‚&Æövõ÷W&Â"—Ò"ÇCÒ$7W'&VçB6†ææVÂÆövò#ãÇ7ãä7W'&VçBÆövóÂ÷7ããÂöF—cãÂöF—câp¢7F–öâÒbr÷æVÂöÖævRö6†ææVÇ2÷·W&ÆÆ–"ç'6RçV÷FR†2æ¶W’Â6fSÒ""—ÒöVF—Br–b2VÇ6R"÷æVÂöÖævRö6†ææVÇ2öæWr ¢¶W•ö†–FFVâÒbsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&¶W’"fÇVSÒ'·b‚&¶W’"—Ò#âr–b2VÇ6R" ¢W'&÷%ö‡FÖÂÒbsÆF—b6Æ73Ò&W'"v–FR#ç¶‡FÖÂæW66R†W'&÷"—ÓÂöF—câr–bW'&÷"VÇ6R" ¢÷WGWE÷&WV—&VEöæ÷FRÒ$ÆVfR&Ææ²f÷"Æö6Â…EE„Å2âTEÂ5%BæB…EEW6‚&WV—&RBÆV7BöæRFW7F–æF–öâU$Ââ ¢&VÖ÷FUö÷WGWE÷fÇVW2Ò÷&VÖ÷FUö÷WGWE÷W&Ç2‡7G"‡&r‚&÷WGWE÷W&Â"’÷"""’’÷"²"%Ð¢&VÖ÷FUö÷WGWE÷&÷w2Ò""æ¦ö–â€¢bsÆF—b6Æ73Ò'&VÖ÷FRÖ÷WGWB×&÷r"FF×&VÖ÷FRÖ÷WGWB×&÷sãÇ7â6Æ73×6÷W&6R×&æ²FF×&VÖ÷FRÖ÷WGWB×&æ³ç¶–æFW‡ÓÂ÷7ããÆ–çWBæÖSÖ÷WGWE÷W&Ç2fÇVSÒ'¶‡FÖÂæW66R‡fÇVRÂV÷FSÕG'VR—Ò"Æ6V†öÆFW#Ò&‡GG¢ò÷&V6V—fW#£ƒƒö–ævW7Bö6†ææVÂçG2ÂVG¢òó#3’ã#ã#ã£S÷"7'C¢òö†÷7C§÷'B#ãÆ'WGFöâ6Æ73Ò&'FâFævW""G—SÖ'WGFöâFF×&VÖ÷FRÖ÷WGWB×&VÖ÷fSì9sÂö'WGFöããÂöF—câp¢f÷"–æFW‚ÂfÇVR–âVçVÖW&FR‡&VÖ÷FUö÷WGWE÷fÇVW2Â¢¢6÷W&6U÷fÇVW2Ò·7G"‡&r‚&–çWE÷W&Â"’÷"""’ç7G&—‚•Ò²·7G"†—FVÒ÷"""’ç7G&—‚’f÷"—FVÒ–âÆ—7B‡&r‚&–çWE÷W&Ç2"ÂµÒ’•Ð¢6÷W&6U÷fÇVW2Ò¶—FVÒf÷"—FVÒ–â6÷W&6U÷fÇVW2–b—FVÕÐ¢–bæ÷B6÷W&6U÷fÇVW3 ¢6÷W&6U÷fÇVW2Ò²"%Ð¢6fVE÷6÷W&6U÷&öw&×2ÒÆ—7B‡&r‚&–çWE÷&öw&Õö–G2"ÂµÒ’¢v†–ÆRÆVâ‡6fVE÷6÷W&6U÷&öw&×2’ÂÆVâ‡6÷W&6U÷fÇVW2“ ¢6fVE÷6÷W&6U÷&öw&×2æVæB„æöæR¢–b6fVE÷6÷W&6U÷&öw&×2æBæ÷B6fVE÷6÷W&6U÷&öw&×5³ÒæB&r‚'&öw&Õö–B"“ ¢6fVE÷6÷W&6U÷&öw&×5³ÒÒ&r‚'&öw&Õö–B"¢6÷W&6U÷&÷w5ö‡FÖÂÒ""æ¦ö–â€¢bsÆ'F–6ÆR6Æ73Ò'7G&VÒ×6÷W&6R×&÷r"FF×6÷W&6R×&÷sãÆF—b6Æ73Ò'7G&VÒ×6÷W&6R×&–÷&—G’#ãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6R×Wî(iÂö'WGFöããÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6RÖF÷vãî(i3Âö'WGFöããÂöF—câp¢bsÆF—b6Æ73Ò'7G&VÒ×6÷W&6R×W&Â#ãÇ7â6Æ73×6÷W&6R×&æ²FF×6÷W&6R×&æ³ç¶–æFW‡ÓÂ÷7ããÆ–çWBæÖS×6÷W&6U÷W&Ç2fÇVSÒ'¶‡FÖÂæW66R‡fÇVRÂV÷FSÕG'VR—Ò"Æ6V†öÆFW#Ò&‡GG¢ò÷6÷W&6RöÆ—fRö–æFW‚æÓ7S‚÷"–÷UGV&RÆ—fRU$Â"²'&WV—&VB"–b–æFW‚ÓÒVÇ6R"'ÓãÂöF—câp¢bsÆF—b6Æ73×7G&VÒ×6÷W&6RÖ–æfóãÆF—b6Æ73×7G&VÒ×6÷W&6R×66â×7VÖÖ'’FF×6÷W&6RÖ6ö×7BÖ–æfóãÂöF—cãÆÆ&VÂ6Æ73×6÷W&6R×&öw&ÒÖf–VÆBFF×6÷W&6R×&öw&ÒÖf–VÆB²""–b6fVE÷6÷W&6U÷&öw&×5¶–æFW‚ÓÒVÇ6R&†–FFVâ'ÓãÇ6VÆV7BæÖS×6÷W&6U÷&öw&Õö–G2FF×6÷W&6R×&öw&Ò×6VÆV7BFF×&öw&Ò×6÷W&6SÒ'¶‡FÖÂæW66R‡fÇVRÂV÷FSÕG'VR—Ò#ãÆ÷F–öâfÇVSÒ"#äWFòòæò&öw&ÓÂö÷F–öãç¶b#Æ÷F–öâfÇVS×¶–çB‡6fVE÷6÷W&6U÷&öw&×5¶–æFW‚ÓÒ—Ò6VÆV7FVCå&öw&Ò¶–çB‡6fVE÷6÷W&6U÷&öw&×5¶–æFW‚ÓÒ—Ò‡6fVB“Âö÷F–öãâ"–b6fVE÷6÷W&6U÷&öw&×5¶–æFW‚ÓÒVÇ6R"'ÓÂ÷6VÆV7CãÂöÆ&VÃãÂöF—câp¢bsÆF—b6Æ73×7G&VÒ×6÷W&6RÖ7F–öç3ãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6R×66ãå66ãÂö'WGFöããÆ'WGFöâ6Æ73Ò&'FâFævW""G—SÖ'WGFöâFF×6÷W&6R×&VÖ÷fSì9sÂö'WGFöããÂöF—cãÂö'F–6ÆSâp¢f÷"–æFW‚ÂfÇVR–âVçVÖW&FR‡6÷W&6U÷fÇVW2Â¢ ¢6öçFVçBÒbrrp£ÆF—b6Æ73Ò'FööÆ&"#ãÆF—cãÆƒ#ç²tVF—B6†ææVÂr–b2VÇ6RtFB6†ææVÂwÓÂöƒ#ãÇ6ÖÆÃäæöFRÖ÷væVB6†ææVÂâÖ–â6W'fW"7–æ6‡&öæ—¦F–öâ6ææ÷BVF—B÷"FVÆWFR—BãÂ÷6ÖÆÃãÂöF—cãÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRö6†ææVÇ2#ä&6³ÂöãÂöF—cà£Æf÷&Ò6Æ73Ò&f÷&ÒÖÆ–÷WB6†ææVÂÖVF—F÷"Öf÷&Ò"ÖWF†öCÒ'÷7B"Væ7G—SÒ&×VÇF—'Böf÷&ÒÖFF"7F–öãÒ'¶7F–öçÒ#ç¶¶W•ö†–FFVç×¶W'&÷%ö‡FÖÇÐ£Ç6V7F–öâ6Æ73Ò'æVÂÖf÷&Òf÷&Ò×6V7F–öâ#ãÆF—b6Æ73Ò'æVÂÖ†VB#ãÆF—cãÆƒ#ä–FVçF—G’b–çWCÂöƒ#ãÇädf×VrÖ6ö×F–&ÆRTEÂ%EÂ…EEô„Å2÷"5%B–çWBâ…EE6÷W&6W2W6RWFöÖF–2&V6öææV7BãÂ÷ãÂöF—cãÇ7â6Æ73Ò&ÖöFRÖ6†—#äæöFRÖÆö6Â6†ææVÃÂ÷7ããÂöF—cà£ÆF—b6Æ73Ò&f÷&ÒÖw&–B#à£ÆÆ&VÃäæÖSÆ–çWBæÖSÒ&æÖR"fÇVSÒ'·b‚væÖRr—Ò"&WV—&VBÖ†ÆVæwFƒÒ#c#ãÂöÆ&VÃà£ÆÆ&VÃå6ÇVsÆ–çWBæÖSÒ'6ÇVr"fÇVSÒ'·b‚w6ÇVrr—Ò"Æ6V†öÆFW#Ò&WFòÖg&öÒÖæÖR"GFW&ãÒ%´Õ¦×£Ó•òÕÒ²"Ö†ÆVæwFƒÒ###ãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#åF†R–çFW&æÂ7G&VÒ¶W’&VÖ–ç27F&ÆRgFW"7&VF–öâãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆF—b6Æ73Ò'v–FR7G&VÒ×6÷W&6RÖVF—F÷""FF×7G&VÒ×6÷W&6RÖVF—F÷#à£ÆF—b6Æ73×7G&VÒ×6÷W&6RÖ†VCãÆF—cãÆ#å7G&VÒÆ–æ·2b6÷W&6R–æfóÂö#ãÇ6ÖÆÃä–çWB—2&–Ö'’âFB&6·WÆ–æ·2Â&V÷&FW"F†VÒæB66âV6‚6÷W&6RãÂ÷6ÖÆÃãÂöF—cãÆF—b6Æ73Ö7F–öç3ãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6RÖFCâ²FBÆ–æ³Âö'WGFöããÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6R×66âÖÆÃå66âÆÃÂö'WGFöããÂöF—cãÂöF—cà£ÆF—b6Æ73×7G&VÒ×6÷W&6RÖÆ—7BFF×6÷W&6RÖÆ—7Cç·6÷W&6U÷&÷w5ö‡FÖÇÓÂöF—cà£Æ–çWBG—SÖ†–FFVâæÖSÖ–çWE÷W&ÂfÇVSÒ'·b‚v–çWE÷W&Âr—Ò"FF×&–Ö'’×6÷W&6R×fÇVSãÇFW‡F&VæÖSÖ–çWE÷W&Ç2†–FFVâFFÖ&6·W×6÷W&6R×fÇVSç¶‡FÖÂæW66R†6‡"ƒ’æ¦ö–â‡&r‚v–çWE÷W&Ç2rÂµÒ’’—ÓÂ÷FW‡F&Và£Ç6ÖÆÂ6Æ73Öf–VÆBÖ†VÇä†VFW"7–çFƒ¢Ç7â6Æ73ÖÖöæóåU$ÇÅW6W"ÔvVçCÒâââe&VfW&W#Òâââd6öö¶–SÒââãÂ÷7ãââf–Æ÷fW"föÆÆ÷w2F†RF—7Æ–VB&–÷&—G’ãÂ÷6ÖÆÃà£ÂöF—cà£ÆF—b6Æ73Ò'v–FR–FVçF—G’Ö6ö×7B×&÷r#ãÆÆ&VÂ6Æ73Ò&6†V6²f–Æ&6²×FövvÆR#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ&f–Æ&6µöVæ&ÆVB"¶6†V6¶VB‚vf–Æ&6µöVæ&ÆVBr—ÓãÇ7ãå&WGW&âWFöÖF–6ÆÇ’Fò–çWBv†Vâ—B&V6öÖW2f–Æ&ÆSÂ÷7ããÂöÆ&VÃãÆÆ&VÂ6Æ73Ò&6ö×7BÖf–VÆB–çFW'fÂÖf–VÆB#å&–Ö'’Ö–çWB6†V6²–çFW'fÃÆ–çWBG—SÒ&çVÖ&W""æÖSÒ&f–Æ&6µö–çFW'fÂ"Ö–ãÒ#"ÖƒÒ#3c"fÇVSÒ'·b‚vf–Æ&6µö–çFW'fÂrÂ3—Ò#ãÂöÆ&VÃãÆÆ&VÂ6Æ73Ò&6ö×7BÖf–VÆB6FVv÷'’Öf–VÆB#ä6FVv÷&–W3Æ–çWBæÖSÒ&6FVv÷&–W2"Æ—7CÒ&Æö6ÂÖ6FVv÷'’ÖÆ—7B"fÇVSÒ'¶‡FÖÂæW66R‚rÂræ¦ö–â†Æ—7B‡&r‚v6FVv÷&–W2rÂµÒ’’÷"·7G"‡&r‚v6FVv÷'’rÂuVæ6FVv÷&—¦VBr’•Ò’ÂV÷FSÕG'VR—Ò"Æ6V†öÆFW#Ò%7÷'G2ÂæWw2"Ö†ÆVæwFƒÒ#S#ãÆFFÆ—7B–CÒ&Æö6ÂÖ6FVv÷'’ÖÆ—7B#ç¶6FVv÷&–W7ÓÂöFFÆ—7CãÂöÆ&VÃãÂöF—cà£ÆÆ&VÂ6Æ73Ò'v–FR#äÆövòU$ÃÆ–çWBG—SÒ'W&Â"æÖSÒ&Æövõ÷W&Â"fÇVSÒ'·b‚vÆövõ÷W&Âr’–b&r‚vÆövõ÷W&Âr’æBæ÷B7G"‡&r‚vÆövõ÷W&Âr’’ç7F'G7v—F‚‚rö6†ææVÂÖÆöv÷2òr’VÇ6RrwÒ"Æ6V†öÆFW#Ò&‡GG3¢òöW†×ÆRæ6öÒö6†ææVÂÖÆövòçær#ãÂöÆ&VÃà£ÆÆ&VÃåWÆöBÆövóÆ–çWBG—SÒ&f–ÆR"æÖSÒ&Æövõöf–ÆR"66WCÒ&–ÖvR÷ærÆ–ÖvRö§VrÆ–ÖvR÷vV'#ãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#åärÂ¥Tr÷"vV%²Ö†–×VÒ"Ô"ãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&6†V6²#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ'&VÖ÷fUöÆövò#ãÇ7ãå&VÖ÷fR7W'&VçBÆövóÂ÷7ããÂöÆ&VÃç¶Æövõ÷&Wf–WwÐ£ÂöF—cãÂ÷6V7F–öãà £Ç6V7F–öâ6Æ73Ò'æVÂÖf÷&Òf÷&Ò×6V7F–öâ#ãÆF—b6Æ73Ò'æVÂÖ†VB#ãÆF—cãÆƒ#ä÷W&F–öãÂöƒ#ãÇä6öçG&öÂv†WF†W"F†—2Æö6Â6†ææVÂ—2f–Æ&ÆRæBWFöÖF–6ÆÇ’&V6÷fW&VBãÂ÷ãÂöF—cãÂöF—cà£ÆF—b6Æ73Ò&f÷&ÒÖw&–B#à£ÆÆ&VÂ6Æ73Ò&6†V6²#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ&Væ&ÆVB"¶6†V6¶VB‚vVæ&ÆVBr—ÓãÇ7ãäVæ&ÆVCÂ÷7ããÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&6†V6²#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ&WFõ÷&W7F'B"¶6†V6¶VB‚vWFõ÷&W7F'Br—ÓãÇ7ãäWFò×&W7F'B–bdf×VrW†—G3Â÷7ããÂöÆ&VÃà£ÆF—b6Æ73Ò'v–FR÷væW'6†—Öæ÷FR#ãÆ#ä÷væW'6†—&÷FV7F–öãÂö#ãÇ6ÖÆÃåF†—26†ææVÂ&VÆöæw2öæÇ’FòF†—2æöFRâÖ–â6W'fW"6†ææVÂö6FVv÷'’÷Æ–Æ—7B7–æ26ææ÷BVF—BÂ7F'BÂ7F÷Â&W7F'B÷"FVÆWFR—BãÂ÷6ÖÆÃãÂöF—cà£ÂöF—cãÂ÷6V7F–öãà £Ç6V7F–öâ6Æ73Ò'æVÂÖf÷&Òf÷&Ò×6V7F–öâ#ãÆF—b6Æ73Ò'æVÂÖ†VB6ö×7B×æVÂÖ†VB#ãÆF—cãÆƒ#åf–FVòVæ6öF–æsÂöƒ#ãÂöF—cãÂöF—cà£ÆF—b6Æ73Ò&f÷&ÒÖw&–B6ö×7BÖVæ6öF–ærÖw&–Bf–FVòÖVæ6öF–ærÖw&–B#à£ÆÆ&VÃåf–FVò6öFV3Ç6VÆV7BæÖSÒ'f–FVõö6öFV2#çµö÷F–öåöÆ—7B†7W'&VçE÷f–FVòÂf–FVõ÷fÇVW2—ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÃåf–FVò&—G&FSÆ–çWBæÖSÒ'f–FVõö&—G&FR"fÇVSÒ'·b‚wf–FVõö&—G&FRrÂs#S²r—Ò#ãÂöÆ&VÃà£ÆÆ&VÃå&W6öÇWF–öâ&W6WCÇ6VÆV7B–CÒ'&W6öÇWF–öâ×&W6WB#à£Æ÷F–öâfÇVSÒ'6÷W&6R"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒw6÷W&6RrVÇ6RrwÓå6÷W&6Ròæò66Æ–æsÂö÷F–öãà£Æ÷F–öâfÇVSÒ#3ƒCƒ#c"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒs3ƒCƒ#crVÇ6RrwÓãD²T„B(	B3ƒC9s#cÂö÷F–öãà£Æ÷F–öâfÇVSÒ##ScƒCC"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒs#ScƒCCrVÇ6RrwÓå„B(	B#Sc9sCCÂö÷F–öãà£Æ÷F–öâfÇVSÒ#“#ƒƒ"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒs“#ƒƒrVÇ6RrwÓägVÆÂ„B(	B“#9sƒÂö÷F–öãà£Æ÷F–öâfÇVSÒ#cƒ“"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒscƒ“rVÇ6RrwÓä„B²(	Bc9s“Âö÷F–öãà£Æ÷F–öâfÇVSÒ##ƒƒs#"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒs#ƒƒs#rVÇ6RrwÓä„B(	B#ƒ9ss#Âö÷F–öãà£Æ÷F–öâfÇVSÒ##GƒSsb"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒs#GƒSsbrVÇ6RrwÓãSsg(	B#L9sSscÂö÷F–öãà£Æ÷F–öâfÇVSÒ#ƒSGƒCƒ"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒsƒSGƒCƒrVÇ6RrwÓãCƒ(	BƒSL9sCƒÂö÷F–öãà£Æ÷F–öâfÇVSÒ#s#ƒSsb"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒss#ƒSsbrVÇ6RrwÓåÂ(	Bs#9sSscÂö÷F–öãà£Æ÷F–öâfÇVSÒ#s#ƒCƒ"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒss#ƒCƒrVÇ6RrwÓäåE42(	Bs#9sCƒÂö÷F–öãà£Æ÷F–öâfÇVSÒ#cCƒ3c"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒscCƒ3crVÇ6RrwÓã3c(	BcC9s3cÂö÷F–öãà£Æ÷F–öâfÇVSÒ#C#gƒ#C"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒsC#gƒ#CrVÇ6RrwÓã#C(	BC#l9s#CÂö÷F–öãà£Æ÷F–öâfÇVSÒ&7W7FöÒ"²w6VÆV7FVBr–b&W6öÇWF–öå÷6VÆV7FVBÓÒv7W7FöÒrVÇ6RrwÓä7W7FöÓÂö÷F–öããÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÃäe3Æ–çWBG—SÒ&çVÖ&W""Ö–ãÒ#"ÖƒÒ##"æÖSÒ&g2"fÇVSÒ'·b‚vg2r—Ò"Æ6V†öÆFW#Ò$&Ææ²¶VW26÷W&6Re2#ãÂöÆ&VÃà£ÆÆ&VÃåv–GFƒÆ–çWB–CÒ'f–FVò×v–GF‚"G—SÒ&çVÖ&W""Ö–ãÒ#""ÖƒÒ#ƒ“""7FWÒ#""æÖSÒ'v–GF‚"fÇVSÒ'·b‚wv–GF‚r—Ò"Æ6V†öÆFW#Ò%6÷W&6Rv–GF‚#ãÂöÆ&VÃà£ÆÆ&VÃä†V–v‡CÆ–çWB–CÒ'f–FVòÖ†V–v‡B"G—SÒ&çVÖ&W""Ö–ãÒ#""ÖƒÒ#ƒ“""7FWÒ#""æÖSÒ&†V–v‡B"fÇVSÒ'·b‚v†V–v‡Br—Ò"Æ6V†öÆFW#Ò%6÷W&6R†V–v‡B#ãÂöÆ&VÃà£ÆÆ&VÃä5R&W6WCÇ6VÆV7BæÖSÒ'&W6WB#çµö÷F–öåöÆ—7B‡7G"‡&r‚w&W6WBrÂwVÇG&f7Br’’Â²‡‚Â‚’f÷"‚–â‚wVÇG&f7BrÂw7WW&f7BrÂwfW'–f7BrÂvf7FW"rÂvf7BrÂvÖVF—VÒr•Ò—ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÂöF—cãÂ÷6V7F–öãà £Ç6V7F–öâ6Æ73Ò'æVÂÖf÷&Òf÷&Ò×6V7F–öâ#ãÆF—b6Æ73Ò'æVÂÖ†VB6ö×7B×æVÂÖ†VB#ãÆF—cãÆƒ#äVF–òb÷WGWCÂöƒ#ãÂöF—cãÂöF—cà£ÆF—b6Æ73Ò&f÷&ÒÖw&–B6ö×7BÖVæ6öF–ærÖw&–BVF–òÖ÷WGWBÖw&–B#à£ÆÆ&VÃäVF–ò6öFV3Ç6VÆV7BæÖSÒ&VF–õö6öFV2#çµö÷F–öåöÆ—7B†7W'&VçEöVF–òÂ²‚v6÷’rÂt6÷’ò77F‡&÷Vv‚r’Â‚v2rÂt2ÔÄ2r’Â‚v32rÂt2Ó2òFöÆ'’F–v—FÂr’Â‚vV32rÂtRÔ2Ó2òFöÆ'’F–v—FÂÇW2r’Â‚v×"rÂtÕTrÆ–W"”’òÕ"r’Â‚vÆ–&×6ÆÖRrÂtÕ2r’Â‚vÆ–&÷W2rÂt÷W2r’Â‚vfÆ2rÂtdÄ2r’Â‚vÆ–'f÷&&—2rÂuf÷&&—2r’Â‚w6Õ÷3fÆRrÂu4ÒbÖ&—Br•Ò—ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÃäVF–ò&—G&FSÆ–çWBæÖSÒ&VF–õö&—G&FR"fÇVSÒ'·b‚vVF–õö&—G&FRrÂs#†²r—Ò#ãÂöÆ&VÃà£ÆÆ&VÃä÷WGWBG—SÇ6VÆV7BæÖSÒ&÷WGWE÷G—R"–CÒ&÷WGWB×G—R#çµö÷F–öåöÆ—7B†7W'&VçEö÷WGWBÂ²‚v†Ç2rÂt…EE„Å2(	BæVÂU$ÂòæöFRÆ–Æ—7Br’Â‚v‡GG÷÷7BrÂt…EEÕTrÕE2W6‚(	Bõ5Br’Â‚v‡GG÷WBrÂt…EEÕTrÕE2W6‚(	BUBr’Â‚wVGrÂuTEÕTrÕE2r’Â‚w7'BrÂu5%BÕTrÕE2r•Ò—ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÃä„Å26VvÖVçB6V6öæG3Æ–çWBG—SÒ&çVÖ&W""Ö–ãÒ#"ÖƒÒ##"æÖSÒ&†Ç5÷6VvÖVçE÷F–ÖR"fÇVSÒ'·b‚v†Ç5÷6VvÖVçE÷F–ÖRrÂ—Ò#ãÂöÆ&VÃà£ÆF—b6Æ73Ò'v–FR&VÖ÷FRÖ÷WGWBÖVF—F÷""FF×&VÖ÷FRÖ÷WGWBÖVF—F÷#ãÆF—b6Æ73×&VÖ÷FRÖ÷WGWBÖ†VCãÆF—cãÇ7â6Æ73Öf–VÆBÖÆ&VÃå&VÖ÷FR…EEõTEõ5%B÷WGWBU$Ç3Â÷7ããÇ6ÖÆÂ6Æ73Öf–VÆBÖ†VÇä÷F–öæÂv—F‚„Å2âFB×VÇF—ÆRFW7F–æF–öç2ãÂ÷6ÖÆÃãÂöF—cãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×&VÖ÷FRÖ÷WGWBÖFCâ²FB÷WGWCÂö'WGFöããÂöF—cãÆF—b6Æ73×&VÖ÷FRÖ÷WGWBÖÆ—7BFF×&VÖ÷FRÖ÷WGWBÖÆ—7Cç·&VÖ÷FUö÷WGWE÷&÷w7ÓÂöF—cãÂöF—cà£ÂöF—cãÂ÷6V7F–öãà£ÆF—b6Æ73Ò&f÷&ÒÖ7F–öç2v–FR#ãÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRö6†ææVÇ2#ä6æ6VÃÂöãÆ'WGFöãå6fR6†ææVÃÂö'WGFöããÂöF—cà£Âöf÷&Óârrp ¢67&—BÒrrsÇ67&—Cà¢†gVæ7F–öâ‚—°¢6öç7B&W6WCÖFö7VÖVçBævWDVÆVÖVçD'”–B‚w&W6öÇWF–öâ×&W6WBr“°¢6öç7Bv–GFƒÖFö7VÖVçBævWDVÆVÖVçD'”–B‚wf–FVò×v–GF‚r“°¢6öç7B†V–v‡CÖFö7VÖVçBævWDVÆVÖVçD'”–B‚wf–FVòÖ†V–v‡Br“°¢–b‡&W6WBbgv–GF‚bf†V–v‡B—·&W6WBæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚“Óç¶–b‡&W6WBçfÇVSÓÓÒw6÷W&6Rr—·v–GF‚çfÇVSÒrs¶†V–v‡BçfÇVSÒrs·ÖVÇ6R–b‡&W6WBçfÇVRÓÒv7W7FöÒr—¶6öç7B'G3×&W6WBçfÇVRç7Æ—B‚w‚r“·v–GF‚çfÇVS×'G5³Ó¶†V–v‡BçfÇVS×'G5³Ó·×Ò“·Ð¢6öç7B÷WGWCÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v÷WGWB×G—Rr“°¢6öç7B&VÖ÷FTVF—F÷#ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWBÖVF—F÷%Òr“°¢6öç7B&VÖ÷FTÆ—7C×&VÖ÷FTVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWBÖÆ—7EÒr“°¢6öç7B&VÖ÷FTFC×&VÖ÷FTVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWBÖFEÒr“°¢6öç7B&VÖ÷FU&÷w3Ò‚“Óå²âââ‡&VÖ÷FTÆ—7CòçVW'•6VÆV7F÷$ÆÂ‚u¶FF×&VÖ÷FRÖ÷WGWB×&÷uÒr—ÇÅµÒ•Ó°¢gVæ7F–öâ7–æ4÷WGWB‚—·&VÖ÷FU&÷w2‚’æf÷$V6‚‚‡&÷rÆ–æFW‚“Óç¶6öç7B&æ³×&÷rçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWB×&æµÒr“¶–b‡&æ²—&æ²çFW‡D6öçFVçCÕ7G&–ær†–æFW‚³“¶6öç7B–çWC×&÷rçVW'•6VÆV7F÷"‚v–çWE¶æÖSÒ&÷WGWE÷W&Ç2%Òr“¶–b†–çWB––çWBç&WV—&VCÔ&ööÆVâ†÷WGWBbf÷WGWBçfÇVRÓÒv†Ç2rbf–æFWƒÓÓÓ“¶6öç7B&VÖ÷fS×&÷rçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWB×&VÖ÷fUÒr“¶–b‡&VÖ÷fR—&VÖ÷fRæF—6&ÆVC×&VÖ÷FU&÷w2‚’æÆVæwFƒÃÓ·Ò“·Ð¢gVæ7F–öâ&–æE&VÖ÷FR‡&÷r—·&÷rçVW'•6VÆV7F÷"‚u¶FF×&VÖ÷FRÖ÷WGWB×&VÖ÷fUÒr“òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶–b‡&VÖ÷FU&÷w2‚’æÆVæwFƒÃÓ—¶6öç7B–çWC×&÷rçVW'•6VÆV7F÷"‚v–çWBr“¶–b†–çWB––çWBçfÇVSÒrs·&WGW&ã·×&÷rç&VÖ÷fR‚“·7–æ4÷WGWB‚“·Ò“·Ð¢gVæ7F–öâFE&VÖ÷FR‚—¶6öç7B&÷sÖFö7VÖVçBæ7&VFTVÆVÖVçB‚vF—br“·&÷ræ6Æ74æÖSÒw&VÖ÷FRÖ÷WGWB×&÷rs·&÷ræFF6WBç&VÖ÷FT÷WGWE&÷sÒrs·&÷ræ–ææW$…DÔÃÒsÇ7â6Æ73×6÷W&6R×&æ²FF×&VÖ÷FRÖ÷WGWB×&æ³ãÂ÷7ããÆ–çWBæÖSÖ÷WGWE÷W&Ç2Æ6V†öÆFW#Ò&‡GG¢ò÷&V6V—fW#£ƒƒö–ævW7Bö6†ææVÂçG2ÂVG¢òó#3’ã#ã#ã£S÷"7'C¢òö†÷7C§÷'B#ãÆ'WGFöâ6Æ73Ò&'FâFævW""G—SÖ'WGFöâFF×&VÖ÷FRÖ÷WGWB×&VÖ÷fSì9sÂö'WGFöãâs·&VÖ÷FTÆ—7CòæVæD6†–ÆB‡&÷r“¶&–æE&VÖ÷FR‡&÷r“·7–æ4÷WGWB‚“·&÷rçVW'•6VÆV7F÷"‚v–çWBr“òæfö7W2‚“·Ð¢&VÖ÷FU&÷w2‚’æf÷$V6‚†&–æE&VÖ÷FR“·&VÖ÷FTFCòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆFE&VÖ÷FR“°¢–b†÷WGWB—¶÷WGWBæFDWfVçDÆ—7FVæW"‚v6†ævRrÇ7–æ4÷WGWB“·7–æ4÷WGWB‚“·Ð¢6öç7BVF—F÷#ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FF×7G&VÒ×6÷W&6RÖVF—F÷%Òr“°¢6öç7BÆ—7CÖVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖÆ—7EÒr“°¢6öç7BFD'WGFöãÖVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖFEÒr“°¢6öç7B66äÆÃÖVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×66âÖÆÅÒr“°¢6öç7B&–Ö'”†–FFVãÖVF—F÷#òçVW'•6VÆV7F÷"‚u¶FF×&–Ö'’×6÷W&6R×fÇVUÒr“°¢6öç7B&6·W†–FFVãÖVF—F÷#òçVW'•6VÆV7F÷"‚u¶FFÖ&6·W×6÷W&6R×fÇVUÒr“°¢6öç7Bg3ÖFö7VÖVçBçVW'•6VÆV7F÷"‚v–çWE¶æÖSÒ&g2%Òr“°¢6öç7BW63Ò‡fÇVR“Óå7G&–ær‡fÇVSóòrr’ç&WÆ6R‚õ²cÃâ"uÒörÂ†6‚“Óâ‡²rbs¢rf×²rÂsÂs¢rfÇC²rÂsâs¢rfwC²rÂr"s¢rgV÷C²rÂ"r#¢rb33“²wÕ¶6…Ò’“°¢6öç7B&÷w3Ò‚“Óå²âââ†Æ—7CòçVW'•6VÆV7F÷$ÆÂ‚u¶FF×6÷W&6R×&÷uÒr—ÇÅµÒ•Ó°¢6öç7B–çWDf÷#Ò‡&÷r“Óç&÷rçVW'•6VÆV7F÷"‚v–çWE¶æÖSÒ'6÷W&6U÷W&Ç2%Òr“°¢gVæ7F–öâ&öw&ÔÆ&VÂ†—FVÒ—¶6öç7B–CÖ—FVÓòç&öw&Õö–Cóö—FVÓòç&öw&ÕöçVÓ¶6öç7B6W'f–6SÕ7G&–ær†—FVÓòç6W'f–6UöæÖWÇÂrr’çG&–Ò‚“¶6öç7B&÷f–FW#Õ7G&–ær†—FVÓòç6W'f–6U÷&÷f–FW'ÇÂrr’çG&–Ò‚“·&WGW&â‡6W'f–6WÇÂ‚u&öw&Òr¶–B’’²r(	B”Br¶–B²‡&÷f–FW#ò‚r+rr·&÷f–FW"“¢rr“·Ð¢gVæ7F–öâ&öw&Õ6VÆV7Df÷"‡&÷r—·&WGW&â&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×&öw&Ò×6VÆV7EÒr“·Ð¢gVæ7F–öâ&öw&Ôf–VÆDf÷"‡&÷r—·&WGW&â&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×&öw&ÒÖf–VÆEÒr“·Ð¢gVæ7F–öâ&W6WE&öw&Ò‡&÷rÆ†–FS×G'VR—¶6öç7B&öw&Ó×&öw&Õ6VÆV7Df÷"‡&÷r“¶6öç7Bf–VÆC×&öw&Ôf–VÆDf÷"‡&÷r“¶–b‚&öw&Ò—&WGW&ã·&öw&Òæ–ææW$…DÔÃÒsÆ÷F–öâfÇVSÒ"#äWFòòæò&öw&ÓÂö÷F–öãâs·&öw&ÒçfÇVSÒrs·&öw&ÒæFF6WBç&öw&Õ6÷W&6SÒrs¶–b†f–VÆB–f–VÆBæ†–FFVãÖ†–FS·Ð¢gVæ7F–öâ÷VÆFU&öw&×2‡&÷rÆFFÇ6÷W&6R—¶6öç7B&öw&Ó×&öw&Õ6VÆV7Df÷"‡&÷r“¶6öç7Bf–VÆC×&öw&Ôf–VÆDf÷"‡&÷r“¶–b‚&öw&×ÇÂf–VÆB—&WGW&ã¶6öç7Bf÷&ÖCÕ7G&–ær†FFòæf÷&ÖCòææÖWÇÆFFòæf÷&ÖCòæÆöæuöæÖWÇÂrr’çFôÆ÷vW$66R‚“¶6öç7B&öw&×3Ò†f÷&ÖBæ–æ6ÇVFW2‚v†Ç2r—ÇÆf÷&ÖBæ–æ6ÇVFW2‚vÆV‡GGr’“õµÓ¢†FFòç&öw&×7ÇÅµÒ’æf–ÇFW"†—FVÓÓæ—FVÓòç&öw&Õö–BÖçVÆÇÇÆ—FVÓòç&öw&ÕöçVÒÖçVÆÂ“¶6öç7B&Wf–÷W3Õ7G&–ær‡&öw&ÒçfÇVWÇÂrr“·&öw&Òæ–ææW$…DÔÃÒrs¶6öç7B&Ææ³ÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v÷F–öâr“¶&Ææ²çfÇVSÒrs¶&Ææ²çFW‡D6öçFVçC×&öw&×2æÆVæwFƒò‡&öw&×2æÆVæwFƒÓÓÓòtWFòòFWFV7FVB&öw&Òs¢u6VÆV7B&öw&Òò6W'f–6Rr“¢tWFòòæò&öw&Òs·&öw&ÒæVæD6†–ÆB†&Ææ²“¶6öç7B–G3ÕµÓ·&öw&×2æf÷$V6‚†—FVÓÓç¶6öç7B–CÕ7G&–ær†—FVÒç&öw&Õö–Cóö—FVÒç&öw&ÕöçVÒ“¶–G2çW6‚†–B“¶6öç7B÷F–öãÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v÷F–öâr“¶÷F–öâçfÇVSÖ–C¶÷F–öâçFW‡D6öçFVçC×&öw&ÔÆ&VÂ†—FVÒ“·&öw&ÒæVæD6†–ÆB†÷F–öâ“·Ò“¶–b‡&öw&×2æÆVæwFƒÓÓÓ—&öw&ÒçfÇVSÖ–G5³Ó¶VÇ6R–b†–G2æ–æ6ÇVFW2‡&Wf–÷W2’—&öw&ÒçfÇVS×&Wf–÷W3¶VÇ6R&öw&ÒçfÇVSÒrs·&öw&ÒæFF6WBç&öw&Õ6÷W&6S×6÷W&6S¶f–VÆBæ†–FFVã×&öw&×2æÆVæwFƒÓÓÓ·Ð¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅôTD•Dõ%ô¥5ôU44UõcS2¢ògVæ7F–öâ7–æ5&÷w2‚—¶6öç7B—FV×3×&÷w2‚“¶—FV×2æf÷$V6‚‚‡&÷rÆ–æFW‚“Óç¶6öç7B–çWCÖ–çWDf÷"‡&÷r“¶6öç7B&æ³×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×&æµÒr“¶6öç7BW×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×WÒr“¶6öç7BF÷vã×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖF÷våÒr“¶6öç7B&VÖ÷fS×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×&VÖ÷fUÒr“¶–b‡&æ²—&æ²çFW‡D6öçFVçCÕ7G&–ær†–æFW‚³“¶–b†–çWB––çWBç&WV—&VCÖ–æFWƒÓÓÓ¶–b‡W—WæF—6&ÆVCÖ–æFWƒÓÓÓ¶–b†F÷vâ–F÷vâæF—6&ÆVCÖ–æFWƒÓÓÖ—FV×2æÆVæwF‚Ó¶–b‡&VÖ÷fR—&VÖ÷fRæF—6&ÆVCÖ—FV×2æÆVæwFƒÓÓÓ¶6öç7B&öw&Ó×&öw&Õ6VÆV7Df÷"‡&÷r“¶–b‡&öw&Òbg&öw&ÒæFF6WBç&öw&Õ6÷W&6Rbg&öw&ÒæFF6WBç&öw&Õ6÷W&6RÓÒ†–çWCòçfÇVRçG&–Ò‚—ÇÂrr’—&W6WE&öw&Ò‡&÷rÇG'VR“·Ò“¶6öç7BfÇVW3Ö—FV×2æÖ‡&÷sÓæ–çWDf÷"‡&÷r“òçfÇVRçG&–Ò‚—ÇÂrr’æf–ÇFW"„&ööÆVâ“¶–b‡&–Ö'”†–FFVâ—&–Ö'”†–FFVâçfÇVS×fÇVW5³×ÇÂrs¶–b†&6·W†–FFVâ–&6·W†–FFVâçfÇVS×fÇVW2ç6Æ–6Rƒ’æ¦ö–â‚uÅÆâr“·Ð¢gVæ7F–öâ6ö×7B†FFÇ&÷r—¶6öç7BF&vWC×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖ6ö×7BÖ–æfõÒr“¶–b‚F&vWB—&WGW&ã¶–b‚FFòæö²—·F&vWBæ–ææW$…DÔÃÒsÇ7â6Æ73×6÷W&6RÖ–æfòÖW'&÷#ì9rr¶W62†FFòæW'&÷'ÇÆFFòæFWF–ÇÇÂu7G&VÒ&ö&Rf–ÆVBr’²sÂ÷7ãâs·&WGW&ã·Ö6öç7B7G&V×3ÖFFç7G&V×7ÇÅµÓ¶6öç7Bf–FVó×7G&V×2æf–æB‡ƒÓç‚çG—SÓÓÒwf–FVòr“¶6öç7BVF–ó×7G&V×2æf–æB‡ƒÓç‚çG—SÓÓÒvVF–òr“¶6öç7BFw3ÕµÓ¶–b‡f–FVóòçv–GF‚bgf–FVóòæ†V–v‡B—Fw2çW6‚‚sÇ7ãî)j2r¶W62‡f–FVòçv–GF‚’²r9rr¶W62‡f–FVòæ†V–v‡B’²sÂ÷7ãâr“¶–b‡f–FVóòæ6öFV2—Fw2çW6‚‚sÇ7ãî)k‚r¶W62‡f–FVòæ6öFV2’²sÂ÷7ãâr“¶–b†VF–óòæ6öFV2—Fw2çW6‚‚sÇ7ãî)š¢r¶W62†VF–òæ6öFV2’²sÂ÷7ãâr“¶–b‡f–FVóòæg2—Fw2çW6‚‚sÇ7ãî)xbr¶W62„ÖF‚ç&÷VæB„çVÖ&W"‡f–FVòæg2’£’ó’²re3Â÷7ãâr“¶6öç7Bf×CÖFFæf÷&ÖCòææÖWÇÆFFæf÷&ÖCòæÆöæuöæÖS¶–b†f×B—Fw2çW6‚‚sÇ7ãî)xrr¶W62…7G&–ær†f×B’ç7Æ—B‚rÂr•³Ò’²sÂ÷7ãâr“·F&vWBæ–ææW$…DÔÃ×Fw2æ¦ö–â‚rr—ÇÂsÇ7ãå7G&VÒFWFV7FVCÂ÷7ãâs·Ð¢7–æ2gVæ7F–öâ66å&÷r‡&÷r—¶6öç7B–çWCÖ–çWDf÷"‡&÷r“¶6öç7BF&vWC×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖ6ö×7BÖ–æfõÒr“¶6öç7B'WGFöã×&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×66åÒr“¶6öç7BfÇVSÖ–çWCòçfÇVRçG&–Ò‚—ÇÂrs¶–b‚fÇVR—¶–b‡F&vWB—F&vWBæ–ææW$…DÔÃÒsÇ7â6Æ73×6÷W&6RÖ–æfòÖW'&÷#äVçFW"7G&VÒU$ÃÂ÷7ãâs¶–çWCòæfö7W2‚“·&WGW&ã·Ö–b†'WGFöâ–'WGFöâæF—6&ÆVC×G'VS¶–b‡F&vWB—F&vWBæ–ææW$…DÔÃÒsÇ7ãå66ææ–æ~(
cÂ÷7ãâs·G'—²ò¢5E$TÔdõ$tUôäôDUõ$ôõEõ4õU$4Uõ44åô5D”ôåõcS3¢õ5BFòF†RW†7Bf—6–&ÆRæVÂô’&ö÷BæBÆWBF†R6W'fW"F—7F6‚F†RÆÆ÷rÖÆ—7FVB66â7F–öâ–çFW&æÆÇ’â¢ö6öç7BVæGö–çCÕ7G&–ær‡v–æF÷rå5E$TÔdõ$tUôäôDUõäTÅõ$ôõGÇÂròr“¶6öç7B&W7öç6SÖv—BfWF6‚†VæGö–çBÇ¶ÖWF†öC¢uõ5BrÆ†VFW'3§²t6öçFVçBÕG—Rs¢vÆ–6F–öâö§6öârÂt66WBs¢vÆ–6F–öâö§6öârÂu‚Õ7G&VÔf÷&vRÕæVÂÔ7F–öâs¢w6÷W&6R×66âwÒÆ&öG“¤¥4ôâç7G&–æv–g’‡¶–çWE÷W&Ã§fÇVWÒ’Æ66†S¢væò×7F÷&RrÆ7&VFVçF–Ç3¢w6ÖRÖ÷&–v–ârÇ&VF—&V7C¢vW'&÷"wÒ“¶ÆWBFF×·Ó·G'—¶FFÖv—B&W7öç6Ræ§6öâ‚“·Ö6F6‚…öR—·F‡&÷ræWrW'&÷"‚u66âVæGö–çB&WGW&æVBæöâÔ¥4ôâ&W7öç6R„…EEr·&W7öç6Rç7FGW2²r’r“·Ö–b‚&W7öç6Ræö²—F‡&÷ræWrW'&÷"†FFæFWF–ÇÇÂ‚t…EEr·&W7öç6Rç7FGW2’“¶6ö×7B†FFÇ&÷r“·÷VÆFU&öw&×2‡&÷rÆFFÇfÇVR“·Ö6F6‚†W'&÷"—¶6ö×7B‡¶ö³¦fÇ6RÆW'&÷#¦W'&÷"æÖW76vWÒÇ&÷r“·Öf–æÆÇ—¶–b†'WGFöâ–'WGFöâæF—6&ÆVCÖfÇ6S·×Ð¢gVæ7F–öâ&–æB‡&÷r—·&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×WÒr“òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶6öç7B&Wf–÷W3×&÷rç&Wf–÷W4VÆVÖVçE6–&Æ–æs¶–b‡&Wf–÷W2–Æ—7Bæ–ç6W'D&Vf÷&R‡&÷rÇ&Wf–÷W2“·7–æ5&÷w2‚“·Ò“·&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6RÖF÷våÒr“òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶6öç7BæW‡C×&÷rææW‡DVÆVÖVçE6–&Æ–æs¶–b†æW‡B–Æ—7Bæ–ç6W'D&Vf÷&R†æW‡BÇ&÷r“·7–æ5&÷w2‚“·Ò“·&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×&VÖ÷fUÒr“òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶–b‡&÷w2‚’æÆVæwFƒÃÓ—&WGW&ã·&÷rç&VÖ÷fR‚“·7–æ5&÷w2‚“·Ò“·&÷rçVW'•6VÆV7F÷"‚u¶FF×6÷W&6R×66åÒr“òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç66å&÷r‡&÷r’“¶–çWDf÷"‡&÷r“òæFDWfVçDÆ—7FVæW"‚v–çWBrÂ‚“Óç·&W6WE&öw&Ò‡&÷rÇG'VR“·7–æ5&÷w2‚“·Ò“·Ð¢gVæ7F–öâFE&÷r‚—¶6öç7B&÷sÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v'F–6ÆRr“·&÷ræ6Æ74æÖSÒw7G&VÒ×6÷W&6R×&÷rs·&÷ræFF6WBç6÷W&6U&÷sÒrs·&÷ræ–ææW$…DÔÃÒsÆF—b6Æ73×7G&VÒ×6÷W&6R×&–÷&—G“ãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6R×Wî(iÂö'WGFöããÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6RÖF÷vãî(i3Âö'WGFöããÂöF—cãÆF—b6Æ73×7G&VÒ×6÷W&6R×W&ÃãÇ7â6Æ73×6÷W&6R×&æ²FF×6÷W&6R×&æ³ãÂ÷7ããÆ–çWBæÖS×6÷W&6U÷W&Ç2Æ6V†öÆFW#Ò&‡GG¢ò÷6÷W&6RöÆ—fRö–æFW‚æÓ7S‚÷"–÷UGV&RÆ—fRU$Â#ãÂöF—cãÆF—b6Æ73×7G&VÒ×6÷W&6RÖ–æfóãÆF—b6Æ73×7G&VÒ×6÷W&6R×66â×7VÖÖ'’FF×6÷W&6RÖ6ö×7BÖ–æfóãÂöF—cãÆÆ&VÂ6Æ73×6÷W&6R×&öw&ÒÖf–VÆBFF×6÷W&6R×&öw&ÒÖf–VÆB†–FFVããÇ6VÆV7BæÖS×6÷W&6U÷&öw&Õö–G2FF×6÷W&6R×&öw&Ò×6VÆV7CãÆ÷F–öâfÇVSÒ"#äWFòòæò&öw&ÓÂö÷F–öããÂ÷6VÆV7CãÂöÆ&VÃãÂöF—cãÆF—b6Æ73×7G&VÒ×6÷W&6RÖ7F–öç3ãÆ'WGFöâ6Æ73Ö'FâG—SÖ'WGFöâFF×6÷W&6R×66ãå66ãÂö'WGFöããÆ'WGFöâ6Æ73Ò&'FâFævW""G—SÖ'WGFöâFF×6÷W&6R×&VÖ÷fSì9sÂö'WGFöããÂöF—câs¶Æ—7BæVæD6†–ÆB‡&÷r“¶&–æB‡&÷r“·7–æ5&÷w2‚“¶–çWDf÷"‡&÷r“òæfö7W2‚“·Ð¢&÷w2‚’æf÷$V6‚†&–æB“¶FD'WGFöãòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆFE&÷r“·66äÆÃòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆ7–æ2‚“Óç·66äÆÂæF—6&ÆVC×G'VS¶f÷"†6öç7B&÷röb&÷w2‚’–v—B66å&÷r‡&÷r“·66äÆÂæF—6&ÆVCÖfÇ6S·Ò“¶VF—F÷#òæ6Æ÷6W7B‚vf÷&Òr“òæFDWfVçDÆ—7FVæW"‚w7V&Ö—BrÂ†WfVçB“Óç·7–æ5&÷w2‚“¶–b‚&÷w2‚’ç6öÖR‡&÷sÓæ–çWDf÷"‡&÷r“òçfÇVRçG&–Ò‚’’—¶WfVçBç&WfVçDFVfVÇB‚“¶–çWDf÷"‡&÷w2‚•³Ò“òæfö7W2‚“·×Ò“·7–æ5&÷w2‚“°§Ò’‚“°£Â÷67&—Cârrp¢&WGW&âöæöFU÷æVÅöFö7VÖVçB‡W6W"Â$6†ææVÂVF—F÷""Â6öçFVçBÂ7F—fSÒ&6†ææVÇ2"Â&öG•÷67&—C×67&—BÂÖ–åö6Æ73Ò&6†ææVÂÖVF—F÷"ÖÖ–â"  ¦FVbö÷F–öæÅö–çB†f÷&Ó¢ç’ÂæÖS¢7G"ÂÖ–æ–×VÓ¢–çBÂÖ†–×VÓ¢–çB’Óâ–çBÂæöæS ¢fÇVRÒ7G"†f÷&ÒævWB†æÖR’÷"""’ç7G&—‚¢–bæ÷BfÇVS ¢&WGW&âæöæP¢'6VBÒ–çB‡fÇVR¢–b'6VBÂÖ–æ–×VÒ÷"'6VBâÖ†–×VÓ ¢&—6RfÇVTW'&÷"†bw¶æÖRç&WÆ6R‚%ò"Â""’çF—FÆR‚—Ò×W7B&R&WGvVVâ¶Ö–æ–×V×ÒæB¶Ö†–×V×Òr¢&WGW&â'6V@  ¦FVbö6†ææVÅög&öÕöf÷&Ò†f÷&Ó¢ç’Â&Wf–÷W3¢6†ææVÄ6öæf–rÂæöæRÒæöæR’Óâ6†ææVÄ6öæf–s ¢æÖRÒ7G"†f÷&ÒævWB‚&æÖR"’÷"$6†ææVÂ"’ç7G&—‚•³£cÒ÷"$6†ææVÂ ¢&WVW7FVE÷6ÇVrÒ7G"†f÷&ÒævWB‚'6ÇVr"’÷"""’ç7G&—‚¢6ÇVrÒöÆö6Å÷6ÇVr‡&WVW7FVE÷6ÇVr÷"æÖR¢¶W’Ò&Wf–÷W2æ¶W’–b&Wf–÷W2VÇ6RöÆö6Å÷6ÇVr‡7G"†f÷&ÒævWB‚&¶W’"’÷"6ÇVr’¢ÖævW"ç6fUö¶W’†¶W’¢f–FVõö6öFV2Ò7G"†f÷&ÒævWB‚'f–FVõö6öFV2"’÷"&6÷’"’ç7G&—‚’æÆ÷vW"‚¢f–FVõö6öFV2Ò²&WFõöƒ#cB#¢&WFò"Â&WFõö†Wf2#¢&WFõöƒ#cR"Â&ƒ#cEöWFò#¢&WFò"Â&ƒ#cUöWFò#¢&WFõöƒ#cR'ÒævWB‡f–FVõö6öFV2Âf–FVõö6öFV2¢VF–õö6öFV2Ò7G"†f÷&ÒævWB‚&VF–õö6öFV2"’÷"&6÷’"’ç7G&—‚’æÆ÷vW"‚¢VF–õö6öFV2Ò²&×2#¢&Æ–&×6ÆÖR"Â&÷W2#¢&Æ–&÷W2'ÒævWB†VF–õö6öFV2ÂVF–õö6öFV2¢÷WGWE÷G—RÒ7G"†f÷&ÒævWB‚&÷WGWE÷G—R"’÷"&†Ç2"’ç7G&—‚’æÆ÷vW"‚¢÷WGWE÷G—RÒ²&‡GG#¢&‡GG÷÷7B"Â''G#¢'VG'ÒævWB†÷WGWE÷G—RÂ÷WGWE÷G—R¢ÆÆ÷vVE÷f–FVòÒ²&6÷’"Â&WFò"Â&WFõöƒ#cR"Â&Æ–'ƒ#cB"Â&Æ–'ƒ#cR"Â&ƒ#cEöçfVæ2"Â&†Wf5öçfVæ2"Â&ƒ#cE÷7b"Â&†Wf5÷7b"Â&ƒ#cE÷f’"Â&†Wf5÷f’'Ð¢ÆÆ÷vVEöVF–òÒ²&6÷’"Â&WFò"Â&2"Â&32"Â&V32"Â&×""Â&Æ–&×6ÆÖR"Â&Æ–&÷W2"Â&fÆ2"Â&Æ–'f÷&&—2"Â'6Õ÷3fÆR'Ð¢ÆÆ÷vVEö÷WGWBÒ²&†Ç2"Â'VG"Â'7'B"Â&‡GG÷÷7B"Â&‡GG÷WB'Ð¢–bf–FVõö6öFV2æ÷B–âÆÆ÷vVE÷f–FVó ¢&—6RfÇVTW'&÷"‚%Vç7W÷'FVBf–FVò6öFV2"¢–bVF–õö6öFV2æ÷B–âÆÆ÷vVEöVF–ó ¢&—6RfÇVTW'&÷"‚%Vç7W÷'FVBVF–ò6öFV2"¢–b÷WGWE÷G—Ræ÷B–âÆÆ÷vVEö÷WGWC ¢&—6RfÇVTW'&÷"‚%Vç7W÷'FVB÷WGWBG—R"¢&uö÷WGWE÷fÇVW2ÒÆ—7B†vWFÆ—7B‚&÷WGWE÷W&Ç2"’–b6ÆÆ&ÆR†vWFÆ—7B£ÒvWFGG"†f÷&ÒÂ&vWFÆ—7B"ÂæöæR’’VÇ6RµÒ¢–bæ÷B&uö÷WGWE÷fÇVW3 ¢&uö÷WGWE÷fÇVW2Ò·7G"†f÷&ÒævWB‚&÷WGWE÷W&Â"’÷"""•Ð¢÷WGWE÷fÇVW3¢Æ—7E·7G%ÒÒµÐ¢f÷"&uö÷WGWB–â&uö÷WGWE÷fÇVW3 ¢f÷"Æ–æR–â7G"‡&uö÷WGWB÷"""’ç7Æ—FÆ–æW2‚“ ¢fÇVRÒÆ–æRç7G&—‚¢–bæ÷BfÇVR÷"fÇVR–â÷WGWE÷fÇVW3 ¢6öçF–çVP¢–bæ÷BfÇVRç7Æ—B‚'Â"Â•³Òç7G&—‚’æÆ÷vW"‚’ç7F'G7v—F‚‚‚&‡GG¢òò"Â&‡GG3¢òò"Â'VG¢òò"Â'7'C¢òò"’“ ¢&—6RfÇVTW'&÷"†b%Vç7W÷'FVB&VÖ÷FR÷WGWBU$Ã¢·fÇVWÒ"¢÷WGWE÷fÇVW2æVæB‡fÇVR¢÷WGWE÷W&ÂÒ%Æâ"æ¦ö–â†÷WGWE÷fÇVW2’÷"æöæP¢–b÷WGWE÷G—RÒ&†Ç2"æBæ÷B÷WGWE÷W&Ã ¢&—6RfÇVTW'&÷"‚$FBBÆV7BöæR&VÖ÷FR…EEõTEõ5%B÷WGWBU$Â"¢6÷W&6U÷fÇVW2ÒµÐ¢vWFÆ—7BÒvWFGG"†f÷&ÒÂ&vWFÆ—7B"ÂæöæR¢f÷"—FVÒ–â†vWFÆ—7B‚'6÷W&6U÷W&Ç2"’–b6ÆÆ&ÆR†vWFÆ—7B’VÇ6RµÒ“ ¢6ÆVæVBÒ7G"†—FVÒ÷"""’ç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–â6÷W&6U÷fÇVW3 ¢6÷W&6U÷fÇVW2æVæB†6ÆVæVB¢–bæ÷B6÷W&6U÷fÇVW3 ¢&–Ö'’Ò7G"†f÷&ÒævWB‚&–çWE÷W&Â"’÷"""’ç7G&—‚¢–b&–Ö'“ ¢6÷W&6U÷fÇVW2æVæB‡&–Ö'’¢f÷"—FVÒ–â7G"†f÷&ÒævWB‚&–çWE÷W&Ç2"’÷"""’ç7Æ—FÆ–æW2‚“ ¢6ÆVæVBÒ—FVÒç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–â6÷W&6U÷fÇVW3 ¢6÷W&6U÷fÇVW2æVæB†6ÆVæVB¢–bæ÷B6÷W&6U÷fÇVW3 ¢&—6RfÇVTW'&÷"‚%&–Ö'’–çWBU$Â—2&WV—&VB"¢&u÷6÷W&6U÷&öw&×2ÒÆ—7B†vWFÆ—7B‚'6÷W&6U÷&öw&Õö–G2"’–b6ÆÆ&ÆR†vWFÆ—7B’VÇ6RµÒ¢6÷W&6U÷&öw&Õö–G3¢Æ—7E¶–çBÂæöæUÒÒµÐ¢f÷"–æFW‚–â&ævR†ÆVâ‡6÷W&6U÷fÇVW2’“ ¢&u÷&öw&ÒÒ&u÷6÷W&6U÷&öw&×5¶–æFW…Ò–b–æFW‚ÂÆVâ‡&u÷6÷W&6U÷&öw&×2’VÇ6R" ¢G'“ ¢'6VE÷&öw&ÒÒ–çB‡&u÷&öw&Ò’–b7G"‡&u÷&öw&Ò÷"""’ç7G&—‚’VÇ6RæöæP¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢'6VE÷&öw&ÒÒæöæP¢6÷W&6U÷&öw&Õö–G2æVæB‡'6VE÷&öw&Ò–b'6VE÷&öw&ÒæBÃÒ'6VE÷&öw&ÒÃÒcSS3RVÇ6RæöæR¢–b6÷W&6U÷&öw&Õö–G2æB6÷W&6U÷&öw&Õö–G5³Ò—2æöæRæB&Wf–÷W2æB&Wf–÷W2ç&öw&Õö–C ¢6÷W&6U÷&öw&Õö–G5³ÒÒ–çB‡&Wf–÷W2ç&öw&Õö–B¢&6RÒ&Wf–÷W2æÖöFVÅöGV×‚’–b&Wf–÷W2VÇ6R·Ð¢&6RçWFFR‡°¢&¶W’#¢¶W’À¢&æÖR#¢æÖRÀ¢'6ÇVr#¢6ÇVrÀ¢&6FVv÷'’#¢†ÆÖ&FfÇVW3¢fÇVW5³Ò–bfÇVW2VÇ6R%Væ6FVv÷&—¦VB"’…¶—FVÒç7G&—‚•³£#Òf÷"—FVÒ–â7G"†f÷&ÒævWB‚&6FVv÷&–W2"’÷"f÷&ÒævWB‚&6FVv÷'’"’÷"""’ç7Æ—B‚"Â"’–b—FVÒç7G&—‚•Ò’À¢&6FVv÷&–W2#¢Æ—7B†F–7Bæg&öÖ¶W—2…¶—FVÒç7G&—‚•³£#Òf÷"—FVÒ–â7G"†f÷&ÒævWB‚&6FVv÷&–W2"’÷"f÷&ÒævWB‚&6FVv÷'’"’÷"""’ç7Æ—B‚"Â"’–b—FVÒç7G&—‚•Ò’’À¢&6FVv÷'•ö÷&FW"#¢öÖ–åö6FVv÷'•ö÷&FW%öÖ‚’ævWB‚‚†ÆÖ&FfÇVW3¢fÇVW5³Ò–bfÇVW2VÇ6R%Væ6FVv÷&—¦VB"’…¶—FVÒç7G&—‚•³£#Òf÷"—FVÒ–â7G"†f÷&ÒævWB‚&6FVv÷&–W2"’÷"f÷&ÒævWB‚&6FVv÷'’"’÷"""’ç7Æ—B‚"Â"’–b—FVÒç7G&—‚•Ò’’æÆ÷vW"‚’Â–çB†vWFGG"‡&Wf–÷W2Â&6FVv÷'•ö÷&FW""Â’–b&Wf–÷W2VÇ6R’’À¢&–çWE÷W&Â#¢6÷W&6U÷fÇVW5³ÒÀ¢&–çWE÷W&Ç2#¢6÷W&6U÷fÇVW5³¥ÒÀ¢&–çWE÷&öw&Õö–G2#¢6÷W&6U÷&öw&Õö–G2À¢'&öw&Õö–B#¢6÷W&6U÷&öw&Õö–G5³Ò–b6÷W&6U÷&öw&Õö–G2VÇ6RæöæRÀ¢&f–Æ&6µöVæ&ÆVB#¢f÷&ÒævWB‚&f–Æ&6µöVæ&ÆVB"’—2æ÷BæöæRÀ¢&f–Æ&6µö–çFW'fÂ#¢ö÷F–öæÅö–çB†f÷&ÒÂ&f–Æ&6µö–çFW'fÂ"ÂÂ3c’÷"3À¢&Væ&ÆVB#¢f÷&ÒævWB‚&Væ&ÆVB"’—2æ÷BæöæRÀ¢&WFõ÷&W7F'B#¢f÷&ÒævWB‚&WFõ÷&W7F'B"’—2æ÷BæöæRÀ¢'f–FVõö6öFV2#¢f–FVõö6öFV2À¢'f–FVõö&—G&FR#¢7G"†f÷&ÒævWB‚'f–FVõö&—G&FR"’÷"##S²"’ç7G&—‚’À¢'v–GF‚#¢ö÷F–öæÅö–çB†f÷&ÒÂ'v–GF‚"Â"Âƒ“"’À¢&†V–v‡B#¢ö÷F–öæÅö–çB†f÷&ÒÂ&†V–v‡B"Â"Âƒ“"’À¢&g2#¢ö÷F–öæÅö–çB†f÷&ÒÂ&g2"ÂÂ#’À¢'&W6WB#¢7G"†f÷&ÒævWB‚'&W6WB"’÷"'VÇG&f7B"’ç7G&—‚’À¢&VF–õö6öFV2#¢VF–õö6öFV2À¢&VF–õö&—G&FR#¢7G"†f÷&ÒævWB‚&VF–õö&—G&FR"’÷"##†²"’ç7G&—‚’À¢&÷WGWE÷G—R#¢÷WGWE÷G—RÀ¢&÷WGWE÷W&Â#¢÷WGWE÷W&ÂÀ¢&†Ç5÷6VvÖVçE÷F–ÖR#¢ö÷F–öæÅö–çB†f÷&ÒÂ&†Ç5÷6VvÖVçE÷F–ÖR"ÂÂ#’÷"À¢&6FÆöuö÷væW"#¢&Æö6Â"À¢Ò¢–bæ÷B&6U²&–çWE÷W&Â%Ó ¢&—6RfÇVTW'&÷"‚%&–Ö'’–çWBU$Â—2&WV—&VB"¢–b&6RævWB‚'v–GF‚"’—2æöæRæB&6RævWB‚&†V–v‡B"’—2æ÷BæöæR÷"&6RævWB‚'v–GF‚"’—2æ÷BæöæRæB&6RævWB‚&†V–v‡B"’—2æöæS ¢&—6RfÇVTW'&÷"‚%v–GF‚æB†V–v‡B×W7B&÷F‚&R6WBÂ÷"&÷F‚ÆVgB&Ææ²"¢&WGW&âÖævW"ææ÷&ÖÆ—¦Uö6†ææVÅö6öæf–r„6†ææVÄ6öæf–ræÖöFVÅ÷fÆ–FFR†&6R’  ¤ævWB‚r÷æVÂö6†ææVÇ2÷¶¶W—Òö–æfòrÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVbæöFUö6†ææVÅö–æfò†¶W“¢7G"Â&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6†ææVÇ2çf–Wrr¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB†¶W’¢–bæ÷B'VçF–ÖS ¢&—6R…EEW†6WF–öâƒCBÂt6†ææVÂæ÷Bf÷VæBr¢6öæf–rÒ'VçF–ÖRæ6öæf–p¢7FGW2ÒÖævW"ç7FGW2†¶W’¢F—7Æ•ö–BÒöæöFUö6†ææVÅöF—7Æ•ö–G2‚’ævWB†¶W’Â~(	Br¢25E$TÔdõ$tUôäôDUô„”DUôÔ”åõ5”ä5õ4õU$4UõcSS¢Ö–âÖ÷væVB6†ææVÂ6÷W&6P¢2U$Ç2&R6öæf–wW&F–öâ6V7&WG2âF†RæöFR–æfòvRÖ’6†÷rF†VÒöæÇ’f÷ ¢2æöFRÖÆö6Â6†ææVÇ2F†BF†RæöFR÷W&F÷"÷vç2à¢fÇVW2Ò°¢‚t”BrÂF—7Æ•ö–B’Â‚tæÖRrÂ6öæf–rææÖR’Â‚t÷væW"rÂtÖ–â6W'fW"r–b6öæf–ræ6FÆöuö÷væW"ÓÒvÖ–ârVÇ6RtæöFRÆö6Âr’À¢‚u7FGW2rÂ7FGW2ævWB‚w7FGW2r’÷"wVæ¶æ÷vâr’Â‚uWF–ÖRrÂö‡VÖåöGW&F–öâ‡7FGW2ævWB‚wWF–ÖU÷6V6öæG2r’÷"’’À¢‚t6FVv÷&–W2rÂrÂræ¦ö–â…ö6öæf–uö6FVv÷&–W2†6öæf–r’’’À¢Ð¢–b6öæf–ræ6FÆöuö÷væW"ÒvÖ–âs ¢fÇVW2æVæB‚‚u6÷W&6RrÂ6öæf–ræ–çWE÷W&Â’¢fÇVW2æW‡FVæB…°¢‚uf–FVòrÂbw¶6öæf–rçf–FVõö6öFV7Ò+r¶6öæf–rçf–FVõö&—G&FWÒr’Â‚tVF–òrÂbw¶6öæf–ræVF–õö6öFV7Ò+r¶6öæf–ræVF–õö&—G&FWÒr’À¢‚u&W6öÇWF–öârÂ7FGW2ævWB‚w&W6öÇWF–öâr’÷"†bw¶6öæf–rçv–GF‡Ò9r¶6öæf–ræ†V–v‡GÒr–b6öæf–rçv–GF‚æB6öæf–ræ†V–v‡BVÇ6Ru6÷W&6Rr’’À¢‚t÷WGWBrÂ6öæf–ræ÷WGWE÷G—RçWW"‚’’Â‚t„Å26VvÖVçBrÂbw¶6öæf–ræ†Ç5÷6VvÖVçE÷F–ÖW×2r–b6öæf–ræ÷WGWE÷G—RÓÒv†Ç2rVÇ6R~(	Br’À¢Ò¢6VÆÇ2Òrræ¦ö–â€¢bsÆF—cãÇ7ãç¶‡FÖÂæW66R‡7G"†Æ&VÂ’—ÓÂ÷7ããÆ#ç¶‡FÖÂæW66R‡7G"‡fÇVR÷".(	B"’—ÓÂö#ãÂöF—câp¢f÷"Æ&VÂÂfÇVR–âfÇVW0¢¢W66VEö¶W’ÒW&ÆÆ–"ç'6RçV÷FR†¶W’Â6fSÒrr¢6öçFVçBÒ€¢sÆF—b6Æ73Ò'FööÆ&"#ãÆF—cãÆƒ#ä6†ææVÂ–æf÷&ÖF–öãÂöƒ#ãÇ6ÖÆÃå&VBÖöæÇ’'VçF–ÖRæB7G&VÒ6öæf–wW&F–öãÂ÷6ÖÆÃãÂöF—câp¢sÆF—b6Æ73Ò&7F–öç2#ãÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRö6†ææVÇ2#ä&6²Fò6†ææVÇ3Âöâp¢bsÆ'WGFöâ6Æ73Ò&'Fâ"G—SÒ&'WGFöâ"FFÖæöFRÖ6†ææVÂÖÆörÖ÷VâFFÖW'&÷"ÖVæGö–çCÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—ÒöW'&÷'2æ§6öâ"p¢bvFFÖ6ÆV"ÖVæGö–çCÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—ÒöW'&÷'2ö6ÆV""FFÖ6†ææVÂÖæÖSÒ'¶‡FÖÂæW66R†6öæf–rææÖRÂV÷FSÕG'VR—Ò#å7G&VÒÆöw3Âö'WGFöããÂöF—cãÂöF—câp¢bsÇ6V7F–öâ6Æ73Ò'æVÂæöFRÖ6†ææVÂÖ–æfò×æVÂ#ãÆF—b6Æ73Ò&æöFRÖ6†ææVÂÖ–æfòÖw&–B#ç¶6VÆÇ7ÓÂöF—cãÂ÷6V7F–öãâp¢sÇ7G–ÆSâææöFRÖ6†ææVÂÖ–æfòÖw&–G¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ2ÆÖ–æÖ‚ƒÃg"’—ÒææöFRÖ6†ææVÂÖ–æfòÖw&–CæF—g·FF–æs£g‚‡ƒ¶&÷&FW"×&–v‡C£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR“¶Ö–â×v–GFƒ£ÒææöFRÖ6†ææVÂÖ–æfòÖw&–CæF—c¦çF‚Ö6†–ÆBƒ6â—¶&÷&FW"×&–v‡C£ÒææöFRÖ6†ææVÂÖ–æfòÖw&–B7ç¶F—7Æ“¦&Æö6³¶Ö&v–âÖ&÷GFöÓ£wƒ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£ƒ·FW‡B×G&ç6f÷&Ó§WW&66S¶ÆWGFW"×76–æs¢ãVV×ÒææöFRÖ6†ææVÂÖ–æfòÖw&–B'¶F—7Æ“¦&Æö6³¶÷fW&fÆ÷r×w&¦ç—v†W&WÔÖVF–†Ö‚×v–GFƒ£ƒ‚—²ææöFRÖ6†ææVÂÖ–æfòÖw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'ÒææöFRÖ6†ææVÂÖ–æfòÖw&–CæF—g¶&÷&FW"×&–v‡C£×ÓÂ÷7G–ÆSâp¢¢&WGW&âöæöFU÷æVÅöFö7VÖVçB‡W6W"Âbw¶6öæf–rææÖWÒ+r–æfòrÂ6öçFVçBÂ7F—fSÒv6†ææVÇ2r   ¤ævWB‚r÷æVÂöÖævRö6†ææVÇ2rÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçEö6†ææVÇ2‡&WVW7C¢&WVW7BÂÖW76vS¢7G"ÒrrÂW'&÷#¢7G"Òrr“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6†ææVÇ2çf–Wrr¢&÷w3ÕµÐ¢25E$TÔdõ$tUôäôDUô4„ääTÅ5ôäõôtTõõcs¢6†ææVÂ6÷VçFW'2æWfW"æVVB&VÖ÷FRvVô•à¢f–WvW%÷7FG2ÒÖævW"çf–WvW%÷7FG2†–æ6ÇVFUövVóÔfÇ6R¢6†ææVÅ÷f–WvW'2Òf–WvW%÷7FG2ævWB‚v6†ææVÇ2rÂ·Ò¢W'&÷%öÖÒöæöFU÷&V6VçEö6†ææVÅöW'&÷'2‚¢F—7Æ•ö–G2ÒöæöFUö6†ææVÅöF—7Æ•ö–G2‚¢v—F‚ÖævW"æÆö6³ ¢—FV×3×6÷'FVB€¢ÖævW"æ6†ææVÇ2æ—FV×2‚’À¢¶W“ÖÆÖ&Fƒ¢…ö6FVv÷'•÷6÷'E÷fÇVR‡…³Òæ6öæf–ræ6FVv÷'’’Â–çB†vWFGG"‡…³Òæ6öæf–rÂv6†ææVÅö÷&FW"rÂ’÷"’Â–b…³Òæ6öæf–ræ6FÆöuö÷væW"ÓÒ&Ö–â"VÇ6RÂ…³Òæ6öæf–rææÖRæÆ÷vW"‚’’À¢¢f÷"¶W’Ç'B–â—FV×3 ¢3×'Bæ6öæf–s²7CÖÖævW"ç7FGW2†¶W’“²W66VEö¶W“×W&ÆÆ–"ç'6RçV÷FR†¶W’Â6fSÒrr¢FVÆ—fW'•÷7FGW2ÒöæöFUö6†ææVÅöFVÆ—fW'•÷7FGW2†2Â7B¢6fUö¶W“Ö‡FÖÂæW66R†¶W’ÂV÷FSÕG'VR¢—5öÖ–âÒ2æ6FÆöuö÷væW"ÓÒvÖ–âp¢6öçG&öÇ3ÕµÐ¢Ö÷&Uö7F–öç3ÕµÐ¢25E$TÔdõ$tUôäôDUô4„ääTÅô5D”ôåô%UEDôåõ5DDUõc##S ¢'VçF–ÖU÷WÒ&ööÂ‡7BævWB‚vÆ—fRr’’÷"7G"‡7BævWB‚w7FGW2r’÷"rr’æÆ÷vW"‚’–â²w'Vææ–ærrÂw7F'F–ærrÂw&W7F'F–ærrÂvFVw&FVBwÐ¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç7F'Br“ ¢†–FFVâÒr†–FFVâ&–Ö†–FFVãÒ'G'VR"7G–ÆSÒ&F—7Æ“¦æöæR"r–b'VçF–ÖU÷WVÇ6Rr&–Ö†–FFVãÒ&fÇ6R"p¢6öçG&öÇ2æVæB†bsÆf÷&Ò6Æ73Ö–æÆ–æRÖWF†öC×÷7B7F–öãÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—Ò÷7F'B"FFÖæöFR×'VçF–ÖRÖ7F–öãÒ'7F'B'¶†–FFVçÓãÆ'WGFöâ6Æ73Ò&6ö×7BÖ6öçG&öÂÆ’"F—FÆSÒ%7F'B"&–ÖÆ&VÃÒ%7F'B6†ææVÂ#ãÇ7â6Æ73Ò&æöFRÖ6öçG&öÂÖvÇ—‚#î)kcÂ÷7ããÂö'WGFöããÂöf÷&Óâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç7F÷r“ ¢†–FFVâÒr&–Ö†–FFVãÒ&fÇ6R"r–b'VçF–ÖU÷WVÇ6Rr†–FFVâ&–Ö†–FFVãÒ'G'VR"7G–ÆSÒ&F—7Æ“¦æöæR"p¢6öçG&öÇ2æVæB†bsÆf÷&Ò6Æ73Ö–æÆ–æRÖWF†öC×÷7B7F–öãÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—Ò÷7F÷"FFÖæöFR×'VçF–ÖRÖ7F–öãÒ'7F÷'¶†–FFVçÓãÆ'WGFöâ6Æ73Ò&6ö×7BÖ6öçG&öÂ7F÷"F—FÆSÒ%7F÷"&–ÖÆ&VÃÒ%7F÷6†ææVÂ#ãÇ7â6Æ73Ò&æöFRÖ6öçG&öÂÖvÇ—‚#î(û“Â÷7ããÂö'WGFöããÂöf÷&Óâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç&W7F'Br“ ¢†–FFVâÒr&–Ö†–FFVãÒ&fÇ6R"r–b'VçF–ÖU÷WVÇ6Rr†–FFVâ&–Ö†–FFVãÒ'G'VR"7G–ÆSÒ&F—7Æ“¦æöæR"p¢6öçG&öÇ2æVæB†bsÆf÷&Ò6Æ73Ö–æÆ–æRÖWF†öC×÷7B7F–öãÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—Ò÷&W7F'B"FFÖæöFR×'VçF–ÖRÖ7F–öãÒ'&W7F'B'¶†–FFVçÓãÆ'WGFöâ6Æ73Ò&6ö×7BÖ6öçG&öÂ&W7F'B"F—FÆSÒ%&W7F'B"&–ÖÆ&VÃÒ%&W7F'B6†ææVÂ#ãÇ7â6Æ73Ò&æöFRÖ6öçG&öÂÖvÇ—‚#î(k³Â÷7ããÂö'WGFöããÂöf÷&Óâr¢–b2æ÷WGWE÷G—RÓÒv†Ç2s ¢&VG’Ò&ööÂ‡7BævWB‚vÆ—fRr’’æB&ööÂ‡7BævWB‚v†Ç5÷&VG’r’¢†–FFVâÒrr–b&VG’VÇ6Rr†–FFVâ&–Ö†–FFVãÒ'G'VR"p¢6öçG&öÇ2æVæB†bsÆ6Æ73Ò&'Fâ6ö×7BÖ6öçG&öÂ‡GG"FFÖæöFRÖ‡GGÖ7F–öâF&vWCÕö&Ææ²&VÃÖæö÷VæW"‡&VcÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—Ò÷Æ’"F—FÆSÒ$„Å2&Wf–Wr"&–ÖÆ&VÃÒ%Æ’„Å2&Wf–Wr'¶†–FFVçÓãÇ7â6Æ73Ò&æöFRÖ6öçG&öÂÖvÇ—‚#äƒÂ÷7ããÂöâr¢Ö÷&Uö7F–öç2æVæB€¢bsÆ'WGFöâG—SÒ&'WGFöâ"FFÖæöFRÖ6†ææVÂÖÆörÖ÷VâFFÖW'&÷"ÖVæGö–çCÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—ÒöW'&÷'2æ§6öâ"p¢bvFFÖ6ÆV"ÖVæGö–çCÒ"÷æVÂö6†ææVÇ2÷¶W66VEö¶W—ÒöW'&÷'2ö6ÆV""FFÖ6†ææVÂÖæÖSÒ'¶‡FÖÂæW66R†2ææÖRÂV÷FSÕG'VR—Ò#å7G&VÒÆöw3Âö'WGFöãâp¢¢–bæ÷B—5öÖ–ã ¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2æVF—Br“ ¢Ö÷&Uö7F–öç2æVæB†bsÆ‡&VcÒ"÷æVÂöÖævRö6†ææVÇ2÷¶W66VEö¶W—ÒöVF—B#äVF—CÂöâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2æFVÆWFRr“ ¢Ö÷&Uö7F–öç2æVæB†bsÆf÷&Ò6Æ73Ö–æÆ–æRÖWF†öC×÷7B7F–öãÒ"÷æVÂöÖævRö6†ææVÇ2÷¶W66VEö¶W—ÒöFVÆWFR"öç7V&Ö—CÒ'&WGW&â6öæf—&Ò…ÂtFVÆWFR6†ææVÃõÂr’#ãÆ'WGFöâ6Æ73ÖFævW#äFVÆWFSÂö'WGFöããÂöf÷&Óâr¢VÇ6S ¢Ö÷&Uö7F–öç2æVæB‚sÇ7ãäÖ–â6W'fW"6†ææVÂ+r&VBÖöæÇ“Â÷7ãâr¢6÷W&6RÒtÖ–â6W'fW"+r&VBÖöæÇ’r–b—5öÖ–âVÇ6RtæöFR+rÆö6Âp¢&W6öÇWF–öâÒb'¶2çv–GF‡Ò9r¶2æ†V–v‡GÒ"–b2çv–GF‚æB2æ†V–v‡BVÇ6R%6÷W&6R ¢6W'fW%öæÖRÒ$Ö–â6W'fW""–b—5öÖ–âVÇ6RÖævW"ææöFUöæÖP¢F—7Æ•ö–BÒ7G"†F—7Æ•ö–G2ævWB†¶W’Â~(	Br’¢ÆövòÒ€¢bsÆ–Ör6Æ73Ò&æöFRÖ6†ææVÂÖÆövò"7&3Ò'¶‡FÖÂæW66R†2æÆövõ÷W&ÂÂV÷FSÕG'VR—Ò"ÇCÒ""ÆöF–æsÒ&Æ§’"öæW'&÷#Ò'F†—2ç&VÖ÷fR‚’#âp¢–b2æÆövõ÷W&ÂVÇ6R" ¢¢&VFöæÇ’ÒsÇ7â6Æ73Ò&æöFR×&VFöæÇ’Ö6†—#äÖ–â+r&VBÖöæÇ“Â÷7ãâr–b—5öÖ–âVÇ6RsÇ7â6Æ73Ò&æöFR×&VFöæÇ’Ö6†—#äæöFR+rÆö6ÃÂ÷7ãâp¢Ö÷&UöÖVçRÒöæöFUöÖ–åö6†ææVÅöF—&V7Eö7F–öç2†¶W’Â2ææÖR’–b—5öÖ–âVÇ6R€¢sÆFWF–Ç26Æ73Ò&æöFRÖÖ÷&RÖÖVçR#ãÇ7VÖÖ'’6Æ73Ò&6ö×7BÖ6öçG&öÂÖ÷&R"F—FÆSÒ$Ö÷&R7F–öç2"&–ÖÆ&VÃÒ$Ö÷&R7F–öç2#ãÇ7â6Æ73Ò&æöFRÖ6öçG&öÂÖvÇ—‚æöFRÖÖ÷&RÖvÇ—‚"&–Ö†–FFVãÒ'G'VR#ãÂ÷7ããÂ÷7VÖÖ'“âp¢sÆF—b6Æ73Ò&æöFRÖÖ÷&R×÷÷fW"#âr²rræ¦ö–â†Ö÷&Uö7F–öç2’²sÂöF—cãÂöFWF–Ç3âp¢¢&ö6W75÷7FGW2Ò‡FÖÂæW66R‡7G"‡7BævWB‚'7FGW2"’÷"'Væ¶æ÷vâ"’æÆ÷vW"‚’ÂV÷FSÕG'VR¢6V&6…÷FW‡BÒ†F—7Æ•ö–B²""²2ææÖR²""²¶W’²""²2æ6FVv÷'’²""²‚""–b—5öÖ–âVÇ6R2æ–çWE÷W&Â’²""²6÷W&6R²""²6W'fW%öæÖR’æÆ÷vW"‚¢6V&6…öGG"Ò‡FÖÂæW66R‡6V&6…÷FW‡BÂV÷FSÕG'VR¢6FVv÷'•÷FW‡BÒ‡FÖÂæW66R‚"Â"æ¦ö–â…ö6öæf–uö6FVv÷&–W2†2’’¢7VVE÷fÇVRÒfÆöB‡7BævWB‚'7VVE÷‚"’÷"¢7VVE÷FW‡BÒb'·7VVE÷fÇVS¢ã&g×‚"–b7VVE÷fÇVRâVÇ6R.(	B ¢&÷w2æVæB€¢bsÇG"6Æ73Ò&æöFRÖ6öçG&öÂ×&÷r"FFÖæöFRÖ6†ææVÂ×&÷rFF×'VçF–ÖR×7FGW3Ò'¶‡FÖÂæW66R†FVÆ—fW'•÷7FGW2ÂV÷FSÕG'VR—Ò"FF×&ö6W72×7FGW3Ò'·&ö6W75÷7FGW7Ò"FFÖ6†ææVÂ×&÷sÒ'·6fUö¶W—Ò"FFÖ6†ææVÂ×6V&6ƒÒ'·6V&6…öGG'Ò#âp¢bsÇFCãÆ–çWBG—SÖ6†V6¶&÷‚6Æ73ÖæöFRÖ6†ææVÂ×6VÆV7BæÖSÖ6†ææVÅö¶W—2fÇVSÒ'·6fUö¶W—Ò"f÷&ÓÖæöFRÖ6†ææVÂÖ'VÆ²Öf÷&Ò&–ÖÆ&VÃÒ%6VÆV7B¶‡FÖÂæW66R†2ææÖRÂV÷FSÕG'VR—Ò#ãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÖ–âÖ–B#ç¶‡FÖÂæW66R†F—7Æ•ö–B—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6†ææVÂÖ–FVçF—G’Ö6VÆÂ#ãÆF—b6Æ73Ò&æöFRÖ6†ææVÂÖ–FVçF—G’#ç¶Æöv÷ÓÇ7â6Æ73Ò&æöFRÖ6†ææVÂÖ6÷’#ãÆ#ç¶‡FÖÂæW66R†2ææÖR—ÓÂö#ãÇ6ÖÆÃç¶6FVv÷'•÷FW‡GÒ+r¶‡FÖÂæW66R†2æ÷WGWE÷G—RçWW"‚’—ÓÂ÷6ÖÆÃç·&VFöæÇ—ÓÂ÷7ããÂöF—cãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFR×6W'fW"Ö6VÆÂ#ãÇ7â6Æ73Ò&æöFR×6W'fW"×7VÖÖ'’#ãÆ#ç¶‡FÖÂæW66R‡6W'fW%öæÖR—ÓÂö#ãÇ6ÖÆÃç¶‡FÖÂæW66R‚$Ö–â×7–æ6VB"–b—5öÖ–âVÇ6R÷6÷W&6UöVæGö–çB†2æ–çWE÷W&Â’—ÓÂ÷6ÖÆÃãÂ÷7ããÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖöæÆ–æRÖ6VÆÂ#ãÆ6Æ73Ò&æöFRÖöæÆ–æRÖ&FvR"‡&VcÒ"÷æVÂ÷6W76–öç3ö6†ææVÃ×¶W66VEö¶W—Ò#ç¶–çB†6†ææVÅ÷f–WvW'2ævWB†¶W’Â’÷"—ÓÂöãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFR×'VçF–ÖRÖ6VÆÂ#ãÆF—b6Æ73Ò&æöFR×'VçF–ÖR#çµöæöFU÷'VçF–ÖUöÆW'EöÖ&·W†¶W’Â2ææÖRÂ7BÂW'&÷%öÖævWB†¶W’ÂµÒ’—ÓÇ7â6Æ73Ò&æöFR×'VçF–ÖR×7FGW2¶‡FÖÂæW66R†FVÆ—fW'•÷7FGW2—Ò"FFÖ6†ææVÂ×7FGW3ç¶‡FÖÂæW66R†FVÆ—fW'•÷7FGW2—ÓÂ÷7ããÆ"FFÖ6†ææVÂ×WF–ÖSç¶‡FÖÂæW66R…ö‡VÖåöGW&F–öâ‚‡7BævWB‚'v—F–æu÷6V6öæG2"’–bFVÆ—fW'•÷7FGW2ÓÒ'v—F–ær"VÇ6R7BævWB‚'WF–ÖU÷6V6öæG2"’’÷"’—ÓÂö#ãÂöF—cãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6öçG&öÇ2Ö6VÆÂ#ãÆF—b6Æ73Ö7F–öç3ç²""æ¦ö–â†6öçG&öÇ2—ÓÂöF—cãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFR×7G&VÒ×7VÖÖ'’#ãÆ"FFÖ6†ææVÂ×&W6öÇWF–öãç¶‡FÖÂæW66R‡7G"‡7BævWB‚'&W6öÇWF–öâ"’÷"&W6öÇWF–öâ’ç&WÆ6R‚"&W6öÇWF–öâ"Â""’—ÓÂö#ãÇ6ÖÆÂFFÖ6†ææVÂÖ&—G&FSç¶–çB‡7BævWB‚&&—G&FUö¶'2"’÷"—Ò¶"÷3Â÷6ÖÆÃãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6öFV2#ãÇ7â6Æ73Ò&æöFRÖÖWG&–2Ö–6öâf–FVò#åcÂ÷7ããÆ#ç¶‡FÖÂæW66R†2çf–FVõö6öFV2—ÓÂö#ãÇ6ÖÆÃç¶‡FÖÂæW66R†2çf–FVõö&—G&FR—ÓÂ÷6ÖÆÃãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6öFV2#ãÇ7â6Æ73Ò&æöFRÖÖWG&–2Ö–6öâVF–ò#äÂ÷7ããÆ#ç¶‡FÖÂæW66R†2æVF–õö6öFV2—ÓÂö#ãÇ6ÖÆÃç¶‡FÖÂæW66R†2æVF–õö&—G&FR—ÓÂ÷6ÖÆÃãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÖöæòæöFR×7VVBÖ6VÆÂ#ãÇ7â6Æ73Ò&æöFRÖÖWG&–2Ö–6öâ7VVB#ì9sÂ÷7ããÆ"FFÖ6†ææVÂ×7VVCç·7VVE÷FW‡GÓÂö#ãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÖöæòæöFRÖg2Ö6VÆÂ#ãÇ7â6Æ73Ò&æöFRÖÖWG&–2Ö–6öâg2#äcÂ÷7ããÆ"FFÖ6†ææVÂÖg3ç¶‡FÖÂæW66R‡7G"‡7BævWB‚&g2"’÷"2æg2÷".(	B"’—ÓÂö#ãÇ6ÖÆÃäe3Â÷6ÖÆÃãÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÖ÷&RÖ6VÆÂ#ç¶Ö÷&UöÖVçWÓÂ÷FCãÂ÷G#âp¢¢ÆW'G3Ò†bsÆF—b6Æ73Öö³ç¶‡FÖÂæW66R†ÖW76vR—ÓÂöF—câr–bÖW76vRVÇ6Rrr’²†bsÆF—b6Æ73ÖW'#ç¶‡FÖÂæW66R†W'&÷"—ÓÂöF—câr–bW'&÷"VÇ6Rrr¢Æö6Åö6÷VçBÒöÆö6Åö6†ææVÅö6÷VçB‚¢Æ–Ö—BÒÖ‚ƒÂ–çB†ÖævW"æÆö6Åö6†ææVÅöÆ–Ö—B÷"’¢6åöFBÒö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2æ7&VFRr’æB†ÖævW"æ–æFWVæFVçEöÖöFR÷"Æ–Ö—Bâ’æB†Æ–Ö—BÃÒ÷"Æö6Åö6÷VçBÂÆ–Ö—B¢FEöÆ–æ²ÒsÆ6Æ73Ö'Fâ‡&VcÒ"÷æVÂöÖævRö6†ææVÇ2öæWr#äFBÆö6Â6†ææVÃÂöâr–b6åöFBVÇ6Rrp¢Æ–Ö—E÷FW‡BÒwVæÆ–Ö—FVB–â–æFWVæFVçBÖöFRr–bÖævW"æ–æFWVæFVçEöÖöFRæBÆ–Ö—BÃÒVÇ6Rbw¶Æö6Åö6÷VçGÒò¶Æ–Ö—GÒr–bÆ–Ö—BâVÇ6Rv7&VF–öâF—6&ÆVB'’Ö–âæVÂp¢vÆö&Åö'WGFöç3ÕµÓ²6VÆV7FVEö'WGFöç3ÕµÐ¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç7F'Br“ ¢vÆö&Åö'WGFöç2æVæB‚sÆ'WGFöâæÖSÖ7F–öâfÇVS×7F'EöÆÃå7F'BÆÃÂö'WGFöãâr¢6VÆV7FVEö'WGFöç2æVæB‚sÆ'WGFöâæÖSÖ7F–öâfÇVS×7F'E÷6VÆV7FVBFFÖæöFR×6VÆV7FVBÖ7F–öâF—6&ÆVCå7F'B6VÆV7FVCÂö'WGFöãâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç&W7F'Br“ ¢vÆö&Åö'WGFöç2æVæB‚sÆ'WGFöâæÖSÖ7F–öâfÇVS×&W7F'EöÆÂöæ6Æ–6³Ò'&WGW&â6öæf—&Ò…Âu&W7F'BÆÂ6†ææVÇ2öâF†—2æöFSõÂr’#å&W7F'BÆÃÂö'WGFöãâr¢6VÆV7FVEö'WGFöç2æVæB‚sÆ'WGFöâæÖSÖ7F–öâfÇVS×&W7F'E÷6VÆV7FVBFFÖæöFR×6VÆV7FVBÖ7F–öâF—6&ÆVCå&W7F'B6VÆV7FVCÂö'WGFöãâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2ç7F÷r“ ¢vÆö&Åö'WGFöç2æVæB‚sÆ'WGFöâ6Æ73ÖFævW"æÖSÖ7F–öâfÇVS×7F÷öÆÂöæ6Æ–6³Ò'&WGW&â6öæf—&Ò…Âu7F÷ÆÂ6†ææVÇ2öâF†—2æöFSõÂr’#å7F÷ÆÃÂö'WGFöãâr¢6VÆV7FVEö'WGFöç2æVæB‚sÆ'WGFöâ6Æ73ÖFævW"æÖSÖ7F–öâfÇVS×7F÷÷6VÆV7FVBFFÖæöFR×6VÆV7FVBÖ7F–öâF—6&ÆVCå7F÷6VÆV7FVCÂö'WGFöãâr¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6†ææVÇ2æVF—Br“ ¢6VvÖVçEö÷F–öç2Òrræ¦ö–â†bsÆ÷F–öâfÇVSÒ'·6V6öæG7Ò#ç·6V6öæG7×3Âö÷F–öãârf÷"6V6öæG2–â&ævRƒÂ#’¢6VÆV7FVEö'WGFöç2æVæB€¢sÆÆ&VÂ6Æ73ÖæöFRÖ'VÆ²×6VvÖVçCãÇ7ãä„Å26VvÖVçCÂ÷7ãâp¢bsÇ6VÆV7BæÖSÖ†Ç5÷6VvÖVçE÷F–ÖSç·6VvÖVçEö÷F–öç7ÓÂ÷6VÆV7CãÂöÆ&VÃâp¢sÆ'WGFöâæÖSÖ7F–öâfÇVSÖ†Ç5÷6VÆV7FVBFFÖæöFR×6VÆV7FVBÖ7F–öâF—6&ÆVCäÇ’„Å26VvÖVçCÂö'WGFöãâp¢¢'VÆ³Òrp¢–bvÆö&Åö'WGFöç2÷"6VÆV7FVEö'WGFöç3 ¢'VÆ³Ò€¢sÆf÷&Ò–CÖæöFRÖ6†ææVÂÖ'VÆ²Öf÷&ÒÖWF†öC×÷7B7F–öãÒ"÷æVÂöÖævRö6†ææVÇ2ö'VÆ²"6Æ73ÖæöFRÖ'VÆ²×FööÆ&#âp¢sÆF—bFFÖæöFRÖvÆö&ÂÖ7F–öç3ãÆ#äÆÂ6†ææVÇ3Âö#ãÆF—b6Æ73Ö7F–öç3âr²rræ¦ö–â†vÆö&Åö'WGFöç2’²sÂöF—cãÂöF—câp¢sÆF—bFFÖæöFR×6VÆV7FVBÖ7F–öç2†–FFVããÆ"FFÖæöFR×6VÆV7FVBÖ6÷VçCã6VÆV7FVCÂö#ãÆF—b6Æ73Ö7F–öç3âr²rræ¦ö–â‡6VÆV7FVEö'WGFöç2’²sÂöF—cãÂöF—câp¢sÂöf÷&Óâp¢¢25E$TÔdõ$tUôäôDUô4„ääTÅôd”ÅDU%ôäõôÄ”Ô•Eõcƒp¢6öçFVçCÖÆW'G2¶bsÇ7G–ÆSâææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç7·¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶fÆW‚×w&§w&×ÒææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3æ–çWBÂææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3ç6VÆV7BÂææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3âææöFRÖf–ÇFW"Ö6÷VçBÂææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3âæ'Fç·¶†V–v‡C£3‡ƒ¶Ö–âÖ†V–v‡C£3‡ƒ¶Ö&v–ã£×ÒææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3æ–çWG·¶Ö–â×v–GFƒ£#ƒƒ¶fÆWƒ£3#‡×ÒææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3ç6VÆV7G·¶Ö–â×v–GFƒ£3‡×ÒææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3âææöFRÖf–ÇFW"Ö6÷VçG·¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW'×ÔÖVF–†Ö‚×v–GFƒ£sc‚—·²ææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç7··v–GFƒ£W×ÒææöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç3æ–çWG·¶Ö–â×v–GFƒ£##‡×××ÓÂ÷7G–ÆSãÆF—b6Æ73×FööÆ&#ãÆF—cãÆƒ#äæöFR6†ææVÇ3Âöƒ#ãÇ6ÖÆÃå6V&6‚f–ÇFW'2–ç7FçFÇ’âÖ–â6W'fW"6†ææVÇ27F’&VBÖöæÇ’âÆö6ÂÆÆ÷væ6S¢¶‡FÖÂæW66R†Æ–Ö—E÷FW‡B—ÒãÂ÷6ÖÆÃãÂöF—cãÆF—b6Æ73Ò&7F–öç2æöFRÖ6†ææVÂÖf–ÇFW"Ö7F–öç2#ãÆ–çWBG—S×6V&6‚FFÖæöFRÖÖævR×6V&6‚Æ6V†öÆFW#Ò%G—RFòf–ÇFW"6†ææVÇ2#ãÇ6VÆV7BFFÖæöFR×7FGW2Öf–ÇFW"&–ÖÆ&VÃÒ$6†ææVÂ7FGW2#ãÆ÷F–öâfÇVSÒ"#äÆÂ7FGW3Âö÷F–öããÆ÷F–öâfÇVSÒ'W#åWÂö÷F–öããÆ÷F–öâfÇVSÒ'v—F–ær#åv—F–æsÂö÷F–öããÆ÷F–öâfÇVSÒ&F÷vâ#äF÷vãÂö÷F–öããÂ÷6VÆV7CãÆÆ&VÂ6Æ73ÖæöFRÖ6†ææVÂÖÆ–Ö—BÖf–ÇFW#å6†÷sÇ6VÆV7BFFÖæöFRÖ6†ææVÂ×vR×6—¦R&–ÖÆ&VÃÒ$6†ææVÇ2W"vR#ãÆ÷F–öããÂö÷F–öããÆ÷F–öãã#Âö÷F–öããÆ÷F–öããSÂö÷F–öããÆ÷F–öããÂö÷F–öããÆ÷F–öâfÇVSÒ&ÆÂ#äÄÃÂö÷F–öããÂ÷6VÆV7CãÂöÆ&VÃãÇ7â6Æ73ÖæöFRÖf–ÇFW"Ö6÷VçBFFÖæöFRÖÖævRÖ6÷VçCãÂ÷7ãç¶FEöÆ–æ·ÓÂöF—cãÂöF—cç¶'VÆ·ÓÆF—b6Æ73Ò'F&ÆR×w&æöFRÖ6†ææVÂ×F&ÆR×w&#ãÇF&ÆR6Æ73Ò&æöFRÖ6†ææVÂÖ6öçG&öÂ×F&ÆRæöFRÖÖævRÖ6†ææVÂ×F&ÆR#ãÆ6öÆw&÷WãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×6VÆV7B#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖ–B#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖ6†ææVÂ#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×6W'fW"#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖöæÆ–æR#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×7FGW2#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖ6öçG&öÇ2#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×7G&VÒ#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×f–FVò#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖVF–ò#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂ×7VVB#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖg2#ãÆ6öÂ6Æ73Ò&æöFRÖ6öÂÖÖ÷&R#ãÂö6öÆw&÷WãÇF†VCãÇG#ãÇF‚6Æ73Ò&æöFR×6VÆV7BÖ6VÆÂ#ãÆ–çWBG—SÖ6†V6¶&÷‚FFÖæöFR×6VÆV7BÖÆÂ&–ÖÆ&VÃÒ%6VÆV7BÆÂ#ãÂ÷FƒãÇF‚6Æ73Ò&æöFRÖÖ–âÖ–B#ä”CÂ÷FƒãÇF‚6Æ73Ò&æöFRÖ6†ææVÂÖ–FVçF—G’Ö6VÆÂ#ä6†ææVÃÂ÷FƒãÇF‚6Æ73Ò&æöFR×6W'fW"Ö6VÆÂ#å6W'fW#Â÷FƒãÇF‚6Æ73Ò&æöFRÖöæÆ–æRÖ6VÆÂ#äöæÆ–æSÂ÷FƒãÇF‚6Æ73Ò&æöFR×'VçF–ÖRÖ6VÆÂ#å7FGW2òWF–ÖSÂ÷FƒãÇF‚6Æ73Ò&æöFRÖ6öçG&öÇ2Ö6VÆÂ#ä6öçG&öÇ3Â÷FƒãÇF‚6Æ73Ò&æöFR×7G&VÒ×7VÖÖ'’#å7G&VÒ–æfóÂ÷FƒãÇF‚6Æ73Ò&æöFRÖ6öFV2#åf–FVóÂ÷FƒãÇF‚6Æ73Ò&æöFRÖ6öFV2#äVF–óÂ÷FƒãÇF‚6Æ73Ò&æöFR×7VVBÖ6VÆÂ#å7VVCÂ÷FƒãÇF‚6Æ73Ò&æöFRÖg2Ö6VÆÂ#äe3Â÷FƒãÇF‚6Æ73Ò&æöFRÖÖ÷&RÖ6VÆÂ#ãÂ÷FƒãÂ÷G#ãÂ÷F†VCãÇF&öG“âr²‚rræ¦ö–â‡&÷w2’÷"sÇG#ãÇFB6öÇ7ãÓ3äæò6†ææVÇ3Â÷FCãÂ÷G#âr’²sÂ÷F&öG“ãÂ÷F&ÆSãÂöF—cãÆæb6Æ73Ò&æöFRÖÆör×v–æF–öâ"FFÖæöFRÖ6†ææVÂ×v–æF–öâ&–ÖÆ&VÃÒ$6†ææVÂvW2"†–FFVããÆ'WGFöâ6Æ73Ò&'Fâ"G—SÒ&'WGFöâ"FFÖæöFRÖ6†ææVÂ×&Wcå&Wf–÷W3Âö'WGFöããÆF—b6Æ73Ò&æöFRÖÆör×vRÖçVÖ&W'2"FFÖæöFRÖ6†ææVÂ×vRÖçVÖ&W'3ãÂöF—cãÇ7âFFÖæöFRÖ6†ææVÂ×vR×7VÖÖ'“ãÂ÷7ããÆ'WGFöâ6Æ73Ò&'Fâ"G—SÒ&'WGFöâ"FFÖæöFRÖ6†ææVÂÖæW‡CäæW‡CÂö'WGFöããÂöæcâp¢67&—C×"""#Ç67&—Cà¢†gVæ7F–öâ‚—°¢6öç7BÆÃÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFR×6VÆV7BÖÆÅÒr“°¢6öç7B6†V6·3Õ²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚rææöFRÖ6†ææVÂ×6VÆV7Br•Ó°¢6öç7BvÆö&ÃÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖvÆö&ÂÖ7F–öç5Òr“°¢6öç7B6VÆV7FVCÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFR×6VÆV7FVBÖ7F–öç5Òr“°¢6öç7B6÷VçCÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFR×6VÆV7FVBÖ6÷VçEÒr“°¢6öç7B'WGFöç3Õ²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖæöFR×6VÆV7FVBÖ7F–öåÒr•Ó°¢6öç7Bf—6–&ÆT6†V6·3Ò‚“Óæ6†V6·2æf–ÇFW"‡ƒÓâ‚æ6Æ÷6W7B‚wG"r“òæ†–FFVâ“¶gVæ7F–öâ7–æ2‚—¶6öç7BãÖ6†V6·2æf–ÇFW"‡ƒÓç‚æ6†V6¶VB’æÆVæwFƒ¶6öç7Bf—6–&ÆS×f—6–&ÆT6†V6·2‚“¶6öç7Bf—6–&ÆU6VÆV7FVC×f—6–&ÆRæf–ÇFW"‡ƒÓç‚æ6†V6¶VB’æÆVæwFƒ¶–b†6÷VçB–6÷VçBçFW‡D6öçFVçCÖâ²r6VÆV7FVBs¶–b†vÆö&Â–vÆö&Âæ†–FFVãÖãã¶–b‡6VÆV7FVB—6VÆV7FVBæ†–FFVãÖãÓÓÓ¶'WGFöç2æf÷$V6‚‡ƒÓç‚æF—6&ÆVCÖãÓÓÓ“¶–b†ÆÂ—¶ÆÂæ6†V6¶VC×f—6–&ÆRæÆVæwFƒãbgf—6–&ÆU6VÆV7FVCÓÓ×f—6–&ÆRæÆVæwFƒ¶ÆÂæ–æFWFW&Ö–æFS×f—6–&ÆU6VÆV7FVCãbgf—6–&ÆU6VÆV7FVCÇf—6–&ÆRæÆVæwFƒ·Ö6†V6·2æf÷$V6‚‡ƒÓç‚æ6Æ÷6W7B‚wG"r“òæ6Æ74Æ—7BçFövvÆR‚w6VÆV7FVB×&÷rrÇ‚æ6†V6¶VB’“·Ð¢ÆÃòæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚“Óç·f—6–&ÆT6†V6·2‚’æf÷$V6‚‡ƒÓç‚æ6†V6¶VCÖÆÂæ6†V6¶VB“·7–æ2‚“·Ò“¶6†V6·2æf÷$V6‚‡ƒÓç‚æFDWfVçDÆ—7FVæW"‚v6†ævRrÇ7–æ2’“°¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅõt”äD”ôåõcs‚¢ð¢6öç7B6V&6ƒÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÖævR×6V&6…Òr“°¢6öç7B7FGW4f–ÇFW#ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFR×7FGW2Öf–ÇFW%Òr“°¢6öç7BvU6—¦U6VÆV7CÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂ×vR×6—¦UÒr“°¢6öç7B&÷w3Õ²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖæöFRÖ6†ææVÂ×&÷uÒr•Ó°¢6öç7Bf–ÇFW$6÷VçCÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÖævRÖ6÷VçEÒr“°¢6öç7Bv–æF–öãÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂ×v–æF–öåÒr“°¢6öç7B&WevS×v–æF–öãòçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂ×&WeÒr“°¢6öç7BæW‡EvS×v–æF–öãòçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂÖæW‡EÒr“°¢6öç7BvTçVÖ&W'3×v–æF–öãòçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂ×vRÖçVÖ&W'5Òr“°¢6öç7BvU7VÖÖ'“×v–æF–öãòçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6†ææVÂ×vR×7VÖÖ'•Òr“°¢6öç7BæöFU6V&6„¶W“Òw7G&VÖf÷&vRææöFRæ6†ææVÇ2æf–ÇFW'2çcrs°¢ÆWB6†ææVÅvSÓ°¢6öç7Bæ÷&Ó×cÓç·G'—·&WGW&â7G&–ær‡gÇÂrr’ææ÷&ÖÆ—¦R‚täd´Br’ç&WÆ6R‚õÇ´F–7&—F–7ÒöwRÂrr’çFôÆö6ÆTÆ÷vW$66R‚“·Ö6F6‚…öR—·&WGW&â7G&–ær‡gÇÂrr’çFôÆö6ÆTÆ÷vW$66R‚“·×Ó°¢G'—¶6öç7B6fVCÔ¥4ôâç'6R‡6W76–öå7F÷&vRævWD—FVÒ†æöFU6V&6„¶W’—ÇÂw·Òr“¶–b‡6V&6‚bb6V&6‚çfÇVR—6V&6‚çfÇVSÕ7G&–ær‡6fVBçÇÂrr“¶–b‡7FGW4f–ÇFW"—7FGW4f–ÇFW"çfÇVSÕ²wWrÂwv—F–ærrÂvF÷vâuÒæ–æ6ÇVFW2‡6fVBç7FGW2“÷6fVBç7FGW3¢rs¶–b‡vU6—¦U6VÆV7Bbe²ââçvU6—¦U6VÆV7Bæ÷F–öç5Òç6öÖR‡ƒÓç‚çfÇVSÓÓÕ7G&–ær‡6fVBæÆ–Ö—GÇÂrr’’—vU6—¦U6VÆV7BçfÇVSÕ7G&–ær‡6fVBæÆ–Ö—GÇÂsr“¶6†ææVÅvSÔÖF‚æÖ‚ƒÄçVÖ&W"‡6fVBçvR—ÇÃ“·Ö6F6‚…öR—·Ð¢6öç7B&VæFW$6†ææVÅvW3Ò‡vT6÷VçBÆÖF6†–ærÇvU6—¦R“Óç¶–b‚v–æF–öçÇÂ&WevWÇÂæW‡EvWÇÂvTçVÖ&W'7ÇÂvU7VÖÖ'’—&WGW&ã¶6öç7B7F—fS×vU6—¦SãbfÖF6†–æsçvU6—¦S·v–æF–öâæ†–FFVãÒ7F—fS·vTçVÖ&W'2ç&WÆ6T6†–ÆG&Vâ‚“¶–b‚7F—fR—&WGW&ã·&WevRæF—6&ÆVCÖ6†ææVÅvSÃÓ¶æW‡EvRæF—6&ÆVCÖ6†ææVÅvSã×vT6÷VçC¶6öç7BvU7F'CÔÖF‚æÖ‚ƒÄÖF‚æÖ–â†6†ææVÅvRÓ"ÄÖF‚æÖ‚ƒÇvT6÷VçBÓB’’“¶6öç7BvTVæCÔÖF‚æÖ–â‡vT6÷VçBÇvU7F'B³B“¶f÷"†ÆWBã×vU7F'C¶ãÃ×vTVæC¶â²²—¶6öç7B'WGFöãÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v'WGFöâr“¶'WGFöâçG—SÒv'WGFöâs¶'WGFöâæ6Æ74æÖSÒv'Fâr²†ãÓÓÖ6†ææVÅvSòr7F—fRs¢rr“¶'WGFöâæFF6WBææöFT6†ææVÅvSÕ7G&–ær†â“¶'WGFöâçFW‡D6öçFVçCÕ7G&–ær†â“·vTçVÖ&W'2æVæD6†–ÆB†'WGFöâ“·×vU7VÖÖ'’çFW‡D6öçFVçCÒuvRr¶6†ææVÅvR²röbr·vT6÷VçC·Ó°¢6öç7Bf–ÇFW%&÷w3Ò‡&W6WEvSÖfÇ6RÇ67&öÆÃÖfÇ6R“Óç¶–b‡&W6WEvR–6†ææVÅvSÓ¶6öç7BFö¶Vç3Öæ÷&Ò‡6V&6ƒòçfÇVWÇÂrr’çG&–Ò‚’ç7Æ—B‚õÇ2²ò’æf–ÇFW"„&ööÆVâ“¶6öç7B7FGW4ÖöFSÕ7G&–ær‡7FGW4f–ÇFW#òçfÇVWÇÂrr“¶6öç7BÖF6†VCÕµÓ·&÷w2æf÷$V6‚‡&÷sÓç¶6öç7B†“Öæ÷&Ò‡&÷ræFF6WBæ6†ææVÅ6V&6‡ÇÂrr“¶6öç7BFW‡DÖF6ƒÒFö¶Vç2æÆVæwF‡ÇÇFö¶Vç2æWfW'’‡Fö¶VãÓæ†’æ–æ6ÇVFW2‡Fö¶Vâ’“¶6öç7BFVÆ—fW'•7FGW3Õ7G&–ær‡&÷ræFF6WBç'VçF–ÖU7FGW7ÇÂvF÷vâr’çFôÆ÷vW$66R‚“¶6öç7B7FGW4ÖF6ƒÒ7FGW4ÖöFWÇÆFVÆ—fW'•7FGW3ÓÓ×7FGW4ÖöFS¶6öç7BÖF6ƒ×FW‡DÖF6‚bg7FGW4ÖF6ƒ¶–b†ÖF6‚–ÖF6†VBçW6‚‡&÷r“·&÷ræ†–FFVã×G'VS·Ò“¶6öç7BÆ–Ö—EfÇVSÕ7G&–ær‡vU6—¦U6VÆV7CòçfÇVWÇÂsr“¶6öç7BvU6—¦SÖÆ–Ö—EfÇVSÓÓÒvÆÂsó¤ÖF‚æÖ‚ƒÄçVÖ&W"†Æ–Ö—EfÇVR—ÇÃ“¶6öç7BvT6÷VçC×vU6—¦SãôÖF‚æÖ‚ƒÄÖF‚æ6V–Â†ÖF6†VBæÆVæwF‚÷vU6—¦R’“£¶6†ææVÅvSÔÖF‚æÖ–â„ÖF‚æÖ‚ƒÆ6†ææVÅvR’ÇvT6÷VçB“¶6öç7Bg&öÓ×vU6—¦Sãò†6†ææVÅvRÓ’§vU6—¦S£¶6öç7BFó×vU6—¦SãôÖF‚æÖ–â†ÖF6†VBæÆVæwF‚Æg&öÒ·vU6—¦R“¦ÖF6†VBæÆVæwFƒ¶ÖF6†VBç6Æ–6R†g&öÒÇFò’æf÷$V6‚‡&÷sÓç·&÷ræ†–FFVãÖfÇ6S·Ò“¶–b†f–ÇFW$6÷VçB–f–ÇFW$6÷VçBçFW‡D6öçFVçCÖÖF6†VBæÆVæwFƒò†g&öÒ³’²~(	2r·Fò²röbr¶ÖF6†VBæÆVæwF‚²r6†ææVÂr²†ÖF6†VBæÆVæwFƒÓÓÓòrs¢w2r“¢s6†ææVÇ2s·&VæFW$6†ææVÅvW2‡vT6÷VçBÆÖF6†VBæÆVæwF‚ÇvU6—¦R“·G'—·6W76–öå7F÷&vRç6WD—FVÒ†æöFU6V&6„¶W’Ä¥4ôâç7G&–æv–g’‡·§6V&6ƒòçfÇVWÇÂrrÇ7FGW3§7FGW4ÖöFRÆÆ–Ö—C¦Æ–Ö—EfÇVRÇvS¦6†ææVÅvWÒ’“·Ö6F6‚…öR—·Ö–b‡67&öÆÂ–Fö7VÖVçBçVW'•6VÆV7F÷"‚rææöFRÖÖævRÖ6†ææVÂ×F&ÆRr“òç67&öÆÄ–çFõf–Wr‡¶&Æö6³¢w7F'BrÆ&V†f–÷#¢w6Öö÷F‚wÒ“·7–æ2‚“·Ó°¢6V&6ƒòæFDWfVçDÆ—7FVæW"‚v–çWBrÂ‚“Óæf–ÇFW%&÷w2‡G'VR’“°¢6V&6ƒòæFDWfVçDÆ—7FVæW"‚w6V&6‚rÂ‚“Óæf–ÇFW%&÷w2‡G'VR’“°¢6V&6ƒòæFDWfVçDÆ—7FVæW"‚v¶W–F÷vârÆWfVçCÓç¶–b†WfVçBæ¶W“ÓÓÒtVçFW"r–WfVçBç&WfVçDFVfVÇB‚“¶–b†WfVçBæ¶W“ÓÓÒtW66Rrbg6V&6‚çfÇVR—¶WfVçBç&WfVçDFVfVÇB‚“·6V&6‚çfÇVSÒrs¶f–ÇFW%&÷w2‡G'VR“·×Ò“°¢7FGW4f–ÇFW#òæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚“Óæf–ÇFW%&÷w2‡G'VR’“°¢vU6—¦U6VÆV7CòæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚“Óæf–ÇFW%&÷w2‡G'VR’“°¢&WevSòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶–b†6†ææVÅvSã—¶6†ææVÅvRÓÓ¶f–ÇFW%&÷w2†fÇ6RÇG'VR“·×Ò“°¢æW‡EvSòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶6†ææVÅvR³Ó¶f–ÇFW%&÷w2†fÇ6RÇG'VR“·Ò“°¢vTçVÖ&W'3òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆWfVçCÓç¶6öç7B'WGFöãÖWfVçBçF&vWBæ6Æ÷6W7B‚u¶FFÖæöFRÖ6†ææVÂ×vUÒr“¶–b‚'WGFöâ—&WGW&ã¶6†ææVÅvSÔÖF‚æÖ‚ƒÄçVÖ&W"†'WGFöâæFF6WBææöFT6†ææVÅvR—ÇÃ“¶f–ÇFW%&÷w2†fÇ6RÇG'VR“·Ò“°¢f–ÇFW%&÷w2‚“·7–æ2‚“°¢ÆWB7FGW4'W7“ÖfÇ6S°¢6öç7BWCÒ‡&÷rÇ6VÆV7F÷"ÇfÇVR“Óç¶6öç7BæöFS×&÷sòçVW'•6VÆV7F÷"‡6VÆV7F÷"“¶–b†æöFR–æöFRçFW‡D6öçFVçC×fÇVS·Ó°¢6öç7B&Vg&W6…7FGW3Ö7–æ2‚“Óç¶–b‡7FGW4'W7’—&WGW&ã·7FGW4'W7“×G'VS·G'—¶6öç7B&W7öç6SÖv—BfWF6‚‚r÷æVÂ÷7FGW2æ§6öãõóÒr´FFRææ÷r‚’Ç¶66†S¢væò×7F÷&RwÒ“¶–b‚&W7öç6Ræö²—&WGW&ã¶6öç7BFFÖv—B&W7öç6Ræ§6öâ‚“¶f÷"†6öç7B¶¶W’Æ—FVÕÒöbö&¦V7BæVçG&–W2†FFæ6†ææVÇ7ÇÇ·Ò’—¶6öç7B&÷sÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖ6†ææVÂ×&÷sÒ"r´552æW66R†¶W’’²r%Òr“¶–b‚&÷r–6öçF–çVS¶6öç7B7FGW3Õ7G&–ær†—FVÒç7FGW7ÇÂvF÷vâr“¶6öç7B&ö6W757FGW3Õ7G&–ær†—FVÒç'VçF–ÖU÷7FGW7ÇÆ—FVÒç7FGW7ÇÂwVæ¶æ÷vâr’çFôÆ÷vW$66R‚“·&÷ræFF6WBç'VçF–ÖU7FGW3×7FGW2çFôÆ÷vW$66R‚“·&÷ræFF6WBç&ö6W757FGW3×&ö6W757FGW3°¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅô5D”ôåô%UEDôåõ5DDUõ5”ä5õc##S¢ð¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅô5D”ôåõd•4”$”Ä•E•ôd•…õc##S"¢ð¦6öç7B7F–öåWÔ&ööÆVâ†—FVÒæFW6—&VE÷'Vææ–ær—ÇÅ²w'Vææ–ærrÂw7F'F–ærrÂw&W7F'F–ærrÂvFVw&FVBuÒæ–æ6ÇVFW2‡&ö6W757FGW2—ÇÄ&ööÆVâ†—FVÒæÆ—fR“°§&÷rçVW'•6VÆV7F÷$ÆÂ‚vf÷&Õ¶FFÖæöFR×'VçF–ÖRÖ7F–öåÒr’æf÷$V6‚†f÷&ÓÓç¶6öç7B7F–öãÕ7G&–ær†f÷&ÒæFF6WBææöFU'VçF–ÖT7F–öçÇÂrr“¶6öç7B6†÷sÖ7F–öãÓÓÒw7F'Bsò7F–öåW¢‚†7F–öãÓÓÒw7F÷wÇÆ7F–öãÓÓÒw&W7F'Br“ö7F–öåW§G'VR“¶f÷&Òæ†–FFVãÒ6†÷s¶f÷&Òç7G–ÆRæF—7Æ“×6†÷sòrs¢væöæRs¶f÷&Òç6WDGG&–'WFR‚v&–Ö†–FFVârÇ6†÷sòvfÇ6Rs¢wG'VRr“·Ò“°¦6öç7B7FGW4æöFS×&÷rçVW'•6VÆV7F÷"‚u¶FFÖ6†ææVÂ×7FGW5Òr“¶–b‡7FGW4æöFR—·7FGW4æöFRçFW‡D6öçFVçC×7FGW3·7FGW4æöFRæ6Æ74æÖSÒvæöFR×'VçF–ÖR×7FGW2r·7FGW3·×WB‡&÷rÂu¶FFÖ6†ææVÂ×WF–ÖUÒrÂ‚‚“Óç¶ÆWB3ÔÖF‚æÖ‚ƒÄçVÖ&W"‡7FGW2çFôÆ÷vW$66R‚“ÓÓÒwv—F–ærsö—FVÒçv—F–æu÷6V6öæG3¦—FVÒçWF–ÖU÷6V6öæG2—ÇÃ“¶–b‡3Ãc—&WGW&âÖF‚æfÆö÷"‡2’²w2s¶ÆWBÓÔÖF‚æfÆö÷"‡2óc“¶–b†ÓÃc—&WGW&âÒ²vÒr´ÖF‚æfÆö÷"‡2Sc’²w2s¶ÆWBƒÔÖF‚æfÆö÷"†Òóc“·&WGW&â‚²v‚r²†ÒSc’²vÒs·Ò’‚’“·WB‡&÷rÂu¶FFÖ6†ææVÂ×&W6öÇWF–öåÒrÅ7G&–ær†—FVÒç&W6öÇWF–öçÇÂu6÷W&6Rr’“·WB‡&÷rÂu¶FFÖ6†ææVÂÖ&—G&FUÒrÅ7G&–ær„ÖF‚æÖ‚ƒÄçVÖ&W"†—FVÒæ&—G&FUö¶'2—ÇÃ’’²r¶"÷2r“·WB‡&÷rÂu¶FFÖ6†ææVÂ×7VVEÒrÄçVÖ&W"†—FVÒç7VVE÷‡ÇÃ“ãôçVÖ&W"†—FVÒç7VVE÷‚’çFôf—†VBƒ"’²w‚s¢~(	Br“·WB‡&÷rÂu¶FFÖ6†ææVÂÖg5ÒrÄçVÖ&W"†—FVÒæg7ÇÃ“ãõ7G&–ær„ÖF‚ç&÷VæB„çVÖ&W"†—FVÒæg2’£’ó“¢~(	Br“¶6öç7BöæÆ–æS×&÷rçVW'•6VÆV7F÷"‚rææöFRÖöæÆ–æRÖ&FvRr“¶–b†öæÆ–æR–öæÆ–æRçFW‡D6öçFVçCÕ7G&–ær„ÖF‚æÖ‚ƒÄçVÖ&W"†—FVÒæöæÆ–æU÷W6W'2—ÇÃ’“¶6öç7BÆW'C×&÷rçVW'•6VÆV7F÷"‚u¶FFÖæöFR×'VçF–ÖRÖÆW'EÒr“¶–b†ÆW'B—¶6öç7B†4W'&÷#Ô&ööÆVâ†—FVÒæÆ7EöW'&÷"—ÇÄçVÖ&W"†—FVÒæW'&÷%ö6÷VçGÇÃ“ã¶6öç7B–æF–6F÷%7FFSÖ†4W'&÷#òv†2ÖW'&÷"s¢‡7FGW2çFôÆ÷vW$66R‚“ÓÓÒwWsòv†VÇF‡’s¢v–FÆRr“¶ÆW'Bæ6Æ74æÖSÒvæöFR×'VçF–ÖRÖÆW'BÖF÷Br¶–æF–6F÷%7FFS¶ÆW'BæFF6WBæ†4W'&÷#Ö†4W'&÷#òss¢ss¶–b††4W'&÷"—¶ÆW'BæFF6WBçFööÇF—Ò‚„çVÖ&W"†—FVÒç&W7F'Eö6÷VçGÇÃ“ãôçVÖ&W"†—FVÒç&W7F'Eö6÷VçB’²r&W7F'G2s¤çVÖ&W"†—FVÒæW'&÷%ö6÷VçGÇÃ’²r&V6VçBW'&÷'2r’²r+r6Æ–6²f÷"FWF–Ç2r“¶ÆW'Bç6WDGG&–'WFR‚v&–ÖÆ&VÂrÆÆW'BæFF6WBçFööÇF—“·ÖVÇ6W¶ÆW'Bç&VÖ÷fTGG&–'WFR‚vFF×FööÇF—r“¶ÆW'Bç6WDGG&–'WFR‚v&–ÖÆ&VÂrÇ7FGW2çFôÆ÷vW$66R‚“ÓÓÒwWsòuWs¢‡7FGW2çFôÆ÷vW$66R‚“ÓÓÒwv—F–ærsòuv—F–ærf÷"„Å2÷WGWBs¢tF÷vâr’“·×Ö6öç7B‡GG7F–öã×&÷rçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ‡GGÖ7F–öåÒr“¶–b†‡GG7F–öâ—¶6öç7B&VG“×7FGW2çFôÆ÷vW$66R‚“ÓÓÒwWrbd&ööÆVâ†—FVÒæ†Ç5÷&VG’“¶‡GG7F–öâæ†–FFVãÒ&VG“¶‡GG7F–öâç6WDGG&–'WFR‚v&–Ö†–FFVârÇ&VG“òvfÇ6Rs¢wG'VRr“·×Öf–ÇFW%&÷w2‚“°¢Ö6F6‚…öR—·Öf–æÆÇ—·7FGW4'W7“ÖfÇ6S·×Ó°¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅô5D”ôåôäõõ$TÄôEõc##S¢ð¢Fö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚vf÷&Õ¶FFÖæöFR×'VçF–ÖRÖ7F–öåÒr’æf÷$V6‚†f÷&ÓÓç¶f÷&ÒæFDWfVçDÆ—7FVæW"‚w7V&Ö—BrÆ7–æ2WfVçCÓç¶WfVçBç&WfVçDFVfVÇB‚“¶–b†f÷&ÒæFF6WBæ'W7“ÓÓÒsr—&WGW&ã¶f÷&ÒæFF6WBæ'W7“Òss¶6öç7B'WGFöãÖf÷&ÒçVW'•6VÆV7F÷"‚v'WGFöâr“¶–b†'WGFöâ–'WGFöâæF—6&ÆVC×G'VS·G'—¶6öç7B&W7öç6SÖv—BfWF6‚†f÷&ÒævWDGG&–'WFR‚v7F–öâr—ÇÂrrÇ¶ÖWF†öC¢uõ5BrÆ&öG“¦æWrf÷&ÔFF†f÷&Ò’Æ7&VFVçF–Ç3¢w6ÖRÖ÷&–v–ârÆ66†S¢væò×7F÷&RrÆ†VFW'3§²t66WBs¢vÆ–6F–öâö§6öârÂu‚Õ7G&VÔf÷&vRÔ¦‚s¢sw×Ò“¶–b‚&W7öç6Ræö²—F‡&÷ræWrW'&÷"‚t…EEr·&W7öç6Rç7FGW2“¶6öç7B7F–öãÕ7G&–ær†f÷&ÒæFF6WBææöFU'VçF–ÖT7F–öçÇÂrr“¶6öç7B&÷sÖf÷&Òæ6Æ÷6W7B‚u¶FFÖ6†ææVÂ×&÷uÒr“¶–b‡&÷rbb†7F–öãÓÓÒw7F'BwÇÆ7F–öãÓÓÒw7F÷r’—¶6öç7B÷F–Ö—7F–5WÖ7F–öãÓÓÒw7F'Bs·&÷rçVW'•6VÆV7F÷$ÆÂ‚vf÷&Õ¶FFÖæöFR×'VçF–ÖRÖ7F–öåÒr’æf÷$V6‚†6æF–FFSÓç¶6öç7B6æF–FFT7F–öãÕ7G&–ær†6æF–FFRæFF6WBææöFU'VçF–ÖT7F–öçÇÂrr“¶6öç7B6†÷sÖ6æF–FFT7F–öãÓÓÒw7F'Bsò÷F–Ö—7F–5W¢‚†6æF–FFT7F–öãÓÓÒw7F÷wÇÆ6æF–FFT7F–öãÓÓÒw&W7F'Br“ö÷F–Ö—7F–5W§G'VR“¶6æF–FFRæ†–FFVãÒ6†÷s¶6æF–FFRç7G–ÆRæF—7Æ“×6†÷sòrs¢væöæRs¶6æF–FFRç6WDGG&–'WFR‚v&–Ö†–FFVârÇ6†÷sòvfÇ6Rs¢wG'VRr“·Ò“·Öv—B&Vg&W6…7FGW2‚“·6WEF–ÖV÷WB‡&Vg&W6…7FGW2Ãs“·6WEF–ÖV÷WB‡&Vg&W6…7FGW2Ãƒ“·Ö6F6‚†W'&÷"—¶6öç6öÆRæW'&÷"‚u7G&VÔf÷&vRæöFR6†ææVÂ7F–öâf–ÆVBrÆW'&÷"“·Öf–æÆÇ—¶f÷&ÒæFF6WBæ'W7“Òss¶–b†'WGFöâ–'WGFöâæF—6&ÆVCÖfÇ6S·×Ò“·Ò“°¢&Vg&W6…7FGW2‚“·6WD–çFW'fÂ‡&Vg&W6…7FGW2Ã3“°§Ò’‚“°£Â÷67&—Câ"" ¢&WGW&â…DÔÅ&W7öç6R…öæöFU÷æVÅöFö7VÖVçB‡W6W"Ât6†ææVÇ2rÆ6öçFVçBÆ7F—fSÒv6†ææVÇ2rÆ&öG•÷67&—C×67&—B’  ¤ç÷7B‚r÷æVÂöÖævRö6†ææVÇ2ö'VÆ²r¦7–æ2FVb–æFWVæFVçEö6†ææVÇ5ö'VÆ²‡&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6†ææVÇ2çf–Wrr¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢7F–öâÒ7G"†f÷&ÒævWB‚v7F–öâr’÷"rr’ç7G&—‚¢W&Ö—76–öâÒ°¢w7F'EöÆÂs¢v6†ææVÇ2ç7F'BrÂw7F'E÷6VÆV7FVBs¢v6†ææVÇ2ç7F'BrÀ¢w7F÷öÆÂs¢v6†ææVÇ2ç7F÷rÂw7F÷÷6VÆV7FVBs¢v6†ææVÇ2ç7F÷rÀ¢w&W7F'EöÆÂs¢v6†ææVÇ2ç&W7F'BrÂw&W7F'E÷6VÆV7FVBs¢v6†ææVÇ2ç&W7F'BrÀ¢v†Ç5÷6VÆV7FVBs¢v6†ææVÇ2æVF—BrÀ¢ÒævWB†7F–öâ¢–bæ÷BW&Ö—76–öã ¢&—6R…EEW†6WF–öâƒCÂuVæ¶æ÷vâ'VÆ²7F–öâr¢–bæ÷Bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"ÂW&Ö—76–öâ“ ¢&—6R…EEW†6WF–öâƒC2ÂuW&Ö—76–öâFVæ–VBr¢6VÆV7FVBÒ·7G"†—FVÒ÷"rr’ç7G&—‚’f÷"—FVÒ–âf÷&ÒævWFÆ—7B‚v6†ææVÅö¶W—2r’–b7G"†—FVÒ÷"rr’ç7G&—‚—Ð¢ÖævW"ç&VÆöEö6†ææVÅö6FÆöuö–eö6†ævVB‚¢6VÆV7FVEöÖöFRÒ7F–öâæVæG7v—F‚‚u÷6VÆV7FVBr¢–b6VÆV7FVEöÖöFRæBæ÷B6VÆV7FVC ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3öW'&÷#Òr·W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚u6VÆV7BBÆV7BöæR6†ææVÂr’Ã32¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖW2Ò²†¶W’Â'B’f÷"¶W’Â'B–âÖævW"æ6†ææVÇ2æ—FV×2‚’–bæ÷B6VÆV7FVEöÖöFR÷"¶W’–â6VÆV7FVEÐ¢–b7F–öâÓÒv†Ç5÷6VÆV7FVBs ¢G'“ ¢6V6öæG2Ò–çB‡7G"†f÷&ÒævWB‚v†Ç5÷6VvÖVçE÷F–ÖRr’÷"sr’¢W†6WBfÇVTW'&÷# ¢&—6R…EEW†6WF–öâƒCÂt–çfÆ–B„Å26VvÖVçB6V6öæG2r¢–bæ÷BÃÒ6V6öæG2ÃÒ# ¢&—6R…EEW†6WF–öâƒCÂt„Å26VvÖVçB6V6öæG2×W7B&R&WGvVVâæB#r¢Æö6Å÷'VçF–ÖW2Ò²†¶W’Â'B’f÷"¶W’Â'B–â'VçF–ÖW2–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒvÆö6ÂuÐ¢f÷"¶W’Â'B–âÆö6Å÷'VçF–ÖW3 ¢ÖævW"ç7–æ5ö6†ææVÂ†¶W’Â'Bæ6öæf–ræÖöFVÅö6÷’‡WFFS×²v†Ç5÷6VvÖVçE÷F–ÖRs¢6V6öæG7Ò’¢6¶—VBÒÆVâ‡'VçF–ÖW2’ÒÆVâ†Æö6Å÷'VçF–ÖW2¢ÖævW"æÆör†bt'VÆ²„Å26VvÖVçB6†ævVBFò·6V6öæG7×2f÷"¶ÆVâ†Æö6Å÷'VçF–ÖW2—ÒÆö6Â6†ææVÂ‡2’rÂ66÷SÒv6†ææVÂrÂFWF–Ç3Öbv7F÷#×·W6W"çW6W&æÖWÒr¢VW'’Ò²vÖW76vRs¢bt„Å26VvÖVçB6WBFò·6V6öæG7×2f÷"¶ÆVâ†Æö6Å÷'VçF–ÖW2—ÒÆö6Â6†ææVÂ‡2’wÐ¢–b6¶—VC ¢VW'•²vW'&÷"uÒÒbw·6¶—VGÒÖ–â6W'fW"6†ææVÂ‡2’6¶—VB&V6W6RF†W’&R&VBÖöæÇ’†W&Rp¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3òr²W&ÆÆ–"ç'6RçW&ÆVæ6öFR‡VW'’’Â32¢÷W&F–öâÒ7F–öâç7Æ—B‚uòrÃ•³Ð¢W'&÷'3ÕµÓ²6ö×ÆWFVCÓ ¢f÷"¶W’Â'B–â'VçF–ÖW3 ¢G'“ ¢–b÷W&F–öâ–â²w7F'BrÂw&W7F'BwÒæBæ÷B'Bæ6öæf–ræVæ&ÆVC ¢6öçF–çVP¢–b÷W&F–öâÓÒw7F'Bs¢ÖævW"ç7F'B†¶W’Â'Bæ6öæf–r¢VÆ–b÷W&F–öâÓÒw7F÷s¢ÖævW"ç7F÷†¶W’¢VÇ6S¢ÖævW"ç&W7F'B†¶W’Â'Bæ6öæf–r¢6ö×ÆWFVB³Ò¢W†6WBW†6WF–öâ2W†3 ¢W'&÷'2æVæB†bw·'Bæ6öæf–rææÖWÓ¢¶W†7Òr¢ÖævW"æÆör†bt'VÆ²¶÷W&F–öçÒ&WVW7FVBf÷"¶6ö×ÆWFVGÒ6†ææVÂ‡2’rÇ66÷SÒv6†ææVÂrÆFWF–Ç3Öbv7F÷#×·W6W"çW6W&æÖWÒr¢VW'“×²vÖW76vRs¦bw¶÷W&F–öâçF—FÆR‚—Ò6öÖÖæB6VçBFò¶6ö×ÆWFVGÒ6†ææVÂ‡2’wÐ¢–bW'&÷'3¢VW'•²vW'&÷"uÓÒs²ræ¦ö–â†W'&÷'2•³£ƒÐ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3òr·W&ÆÆ–"ç'6RçW&ÆVæ6öFR‡VW'’’Ã32  ¢25E$TÔdõ$tUôäôDUõ4õU$4Uõ44åõT$Ä”5õ$Td•…õcS# ¢2&ö÷BæVÂô’Æ–2Ö2÷6÷W&6R×66âæ§6öâFòF†RW†—7F–ær–çFW&æÂæVÀ¢266ææW"â&Vf—†VBÆ–6W2&R&Ww&—GFVâ'’66W72Ö–FFÆWv&RF†R6ÖRv’à¤ç÷7B‚r÷6÷W&6R×66âæ§6öâr¤ç÷7B‚r÷æVÂ÷6÷W&6R×66âæ§6öâr¦7–æ2FVbæöFU÷æVÅ÷6÷W&6U÷66â‡&WVW7C¢&WVW7B“ ¢æVÅ÷W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6†ææVÇ2çf–Wrr¢–bæ÷B…ö†5÷æVÅ÷W&Ö—76–öâ‡æVÅ÷W6W"Âv6†ææVÇ2æ7&VFRr’÷"ö†5÷æVÅ÷W&Ö—76–öâ‡æVÅ÷W6W"Âv6†ææVÇ2æVF—Br’“ ¢&—6R…EEW†6WF–öâƒC2Âu6÷W&6R66âW&Ö—76–öâFVæ–VBr¢G'“ ¢–ÆöBÒv—B&WVW7Bæ§6öâ‚¢W†6WBW†6WF–öâ2W†3 ¢&—6R…EEW†6WF–öâƒCÂt–çfÆ–B¥4ôâ–ÆöBr’g&öÒW†0¢F&vWBÒ7G"‡–ÆöBævWB‚v–çWE÷W&Âr’÷"rr’ç7G&—‚¢–bæ÷BF&vWC ¢&—6R…EEW†6WF–öâƒCÂt–çWBU$Â—2&WV—&VBr¢–bÆVâ‡F&vWB’âƒ ¢&—6R…EEW†6WF–öâƒCÂt–çWBU$Â—2FöòÆöærr¢25E$TÔdõ$tUôäôDUôäôä$Äô4´”äuõ4õU$4Uõ44åõc3c ¢2fg&ö&RÖ’&Æö6²VçF–ÂF–ÖV÷WC²¶VW—BöfbF†RæöFR4t’WfVçBÆö÷6ð¢2æVÂÂÆ–Æ—7BõvV"Æ–W"æBÖ–â†V'F&VBô’66W727F’&W7öç6—fRà¢&WGW&â¥4ôå&W7öç6R†v—B7–æ6–òçFõ÷F‡&VB‡&ö&UöæöFU÷6÷W&6RÂF&vWB’  ¤ævWB‚r÷æVÂöÖævRö6†ææVÇ2öæWrrÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçEö6†ææVÅöæWr‡&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöÆö6Åö6FÆöwVU÷W6W"‡&WVW7BÂv6†ææVÇ2æ7&VFRr¢G'“ ¢Væf÷&6UöÆö6Åö6†ææVÅö66—G’‚¢W†6WBfÇVTW'&÷"2W†3 ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‡7G"†W†2’’Â32¢&WGW&â…DÔÅ&W7öç6R…öÆö6Åö6†ææVÅöf÷&Ò‡W6W"’  ¤ç÷7B‚r÷æVÂöÖævRö6†ææVÇ2öæWrrÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦7–æ2FVb–æFWVæFVçEö6†ææVÅö7&VFR‡&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöÆö6Åö6FÆöwVU÷W6W"‡&WVW7BÂv6†ææVÇ2æ7&VFRr¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢G'“ ¢Væf÷&6UöÆö6Åö6†ææVÅö66—G’‚¢6frÒö6†ææVÅög&öÕöf÷&Ò†f÷&Ò¢v—F‚ÖævW"æÆö6³ ¢–b6fræ¶W’–âÖævW"æ6†ææVÇ3 ¢&—6RfÇVTW'&÷"‚t6†ææVÂ6ÇVrö¶W’Ç&VG’W†—7G2r¢–bç’‡'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒvÆö6ÂræB'Bæ6öæf–rç6ÇVrÓÒ6frç6ÇVrf÷"'B–âÖævW"æ6†ææVÇ2çfÇVW2‚’“ ¢&—6RfÇVTW'&÷"‚t6†ææVÂ6ÇVrÇ&VG’W†—7G2r¢6fræÆövõ÷W&ÂÒv—B÷&W6öÇfUöÆö6Åö6†ææVÅöÆövò‡&WVW7BÂf÷&ÒÂ6frç6ÇVrÂrr¢ÖævW"ç7–æ5ö6†ææVÂ†6fræ¶W’Â6fr¢ÖævW"æÆör‚tæöFRÖÆö6Â6†ææVÂ7&VFVBrÂ66÷SÒv6†ææVÂrÂ¶W“Ö6fræ¶W’¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚t6†ææVÂ7&VFVBr’Â32¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"’2W†3 ¢&WGW&â…DÔÅ&W7öç6R…öÆö6Åö6†ææVÅöf÷&Ò‡W6W"ÂæöæRÂ7G"†W†2’’ÂC  ¢25E$TÔdõ$tUôäôDUô4„ääTÅõ4dUô5”ä5õ$U5D%Eõcƒ ¢2æöFRÖÆö6ÂVF—BW'6—7G2F†RæWr6öæf–rf—'7BæB&WGW&ç2–ÖÖVF–FVÇ’â–`¢2F†R6†ææVÂv2'Vææ–ærÂ&W7F'B—B÷WG6–FRF†R'&÷w6W"&WVW7B6òådTä0¢2&ö&–ær÷&ö6W72†æFöfb6ææ÷BÆVfRF†R6fR'WGFöâ7–ææ–ærà¦FVb÷&W7F'EöæöFUö6†ææVÅögFW%÷6fR†¶W“¢7G"Â6fs¢6†ææVÄ6öæf–r’ÓâæöæS ¢G'“ ¢ÖævW"ç&W7F'B†¶W’Â6fr¢ÖævW"æÆör‚tæöFRÖÆö6Â6†ææVÂ&W7F'FVBgFW"6fRrÂ66÷SÒv6†ææVÂrÂ¶W“Ö¶W’¢W†6WBW†6WF–öâ2W†3 ¢ÖævW"æÆör€¢tæöFRÖÆö6Â6†ææVÂ&W7F'BgFW"6fRf–ÆVBrÀ¢66÷SÒv6†ææVÂrÂÆWfVÃÒvW'&÷"rÂ¶W“Ö¶W’ÂFWF–Ç3×7G"†W†2’À¢  ¤ævWB‚r÷æVÂöÖævRö6†ææVÇ2÷¶¶W—ÒöVF—BrÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçEö6†ææVÅöVF—B†¶W“¢7G"Â&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöÆö6Åö6FÆöwVU÷W6W"‡&WVW7BÂv6†ææVÇ2æVF—Br¢'BÒöÆö6Åö6†ææVÅ÷'VçF–ÖR†¶W’¢&WGW&â…DÔÅ&W7öç6R…öÆö6Åö6†ææVÅöf÷&Ò‡W6W"Â'Bæ6öæf–r’  ¤ç÷7B‚r÷æVÂöÖævRö6†ææVÇ2÷¶¶W—ÒöVF—BrÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦7–æ2FVb–æFWVæFVçEö6†ææVÅ÷6fR†¶W“¢7G"Â&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöÆö6Åö6FÆöwVU÷W6W"‡&WVW7BÂv6†ææVÇ2æVF—Br¢'BÒöÆö6Åö6†ææVÅ÷'VçF–ÖR†¶W’¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢G'“ ¢6frÒö6†ææVÅög&öÕöf÷&Ò†f÷&ÒÂ'Bæ6öæf–r¢6fræ¶W’Ò¶W¢v—F‚ÖævW"æÆö6³ ¢–bç’†÷F†W%ö¶W’Ò¶W’æB÷F†W"æ6öæf–ræ6FÆöuö÷væW"ÓÒvÆö6ÂræB÷F†W"æ6öæf–rç6ÇVrÓÒ6frç6ÇVrf÷"÷F†W%ö¶W’Â÷F†W"–âÖævW"æ6†ææVÇ2æ—FV×2‚’“ ¢&—6RfÇVTW'&÷"‚t6†ææVÂ6ÇVrÇ&VG’W†—7G2r¢6fræÆövõ÷W&ÂÒv—B÷&W6öÇfUöÆö6Åö6†ææVÅöÆövò‡&WVW7BÂf÷&ÒÂ6frç6ÇVrÂ'Bæ6öæf–ræÆövõ÷W&Â÷"rr¢&W7VÇBÒÖævW"ç7–æ5ö6†ææVÂ†¶W’Â6frÂ&W7F'E÷'Vææ–æsÔfÇ6R¢–b&ööÂ‡&W7VÇBævWB‚w&W7F'E÷&WV—&VBr’“ ¢F‡&VF–æråF‡&VB€¢F&vWCÕ÷&W7F'EöæöFUö6†ææVÅögFW%÷6fRÀ¢&w3Ò†¶W’Â6fræÖöFVÅö6÷’†FVWÕG'VR’’À¢FVÖöãÕG'VRÀ¢æÖSÖbw6bÖæöFR×6fR×&W7F'B×¶¶W•³£#E×ÒrÀ¢’ç7F'B‚¢ÖævW"æÆör‚tæöFRÖÆö6Â6†ææVÂWFFVBrÂ66÷SÒv6†ææVÂrÂ¶W“Ö¶W’¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚t6†ææVÂWFFVBr’Â32¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"’2W†3 ¢&WGW&â…DÔÅ&W7öç6R…öÆö6Åö6†ææVÅöf÷&Ò‡W6W"Â'Bæ6öæf–rÂ7G"†W†2’’ÂC  ¤ç÷7B‚r÷æVÂöÖævRö6†ææVÇ2÷¶¶W—ÒöFVÆWFRr¦FVb–æFWVæFVçEö6†ææVÅöFVÆWFR†¶W“¢7G"Â&WVW7C¢&WVW7B“ ¢&WV—&UöÆö6Åö6FÆöwVU÷W6W"‡&WVW7BÂv6†ææVÇ2æFVÆWFRr¢'BÒöÆö6Åö6†ææVÅ÷'VçF–ÖR†¶W’¢Æövõ÷F‚ÒöÆö6ÅöÆövõ÷F‚‡'Bæ6öæf–ræÆövõ÷W&Â÷"rr¢ÖævW"æFVÆWFR†¶W’¢–bÆövõ÷Fƒ ¢Æövõ÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6†ææVÇ3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚t6†ææVÂFVÆWFVBr’Â32  ¤ævWB‚r÷æVÂöÖævRö6FVv÷&–W2rÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçEö6FVv÷&–W2‡&WVW7C¢&WVW7BÂÖW76vS¢7G"ÒrrÂW'&÷#¢7G"Òrr“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6FVv÷&–W2çf–Wrr¢Ö–åö6÷VçG3¢F–7E·7G"Â–çEÒÒ·Ð¢Æö6Åö6÷VçG3¢F–7E·7G"Â–çEÒÒ·Ð¢v—F‚ÖævW"æÆö6³ ¢f÷"'B–âÖævW"æ6†ææVÇ2çfÇVW2‚“ ¢F&vWBÒÖ–åö6÷VçG2–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒvÖ–ârVÇ6RÆö6Åö6÷VçG0¢f÷"æÖR–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r“ ¢F&vWE¶æÖRæÆ÷vW"‚•ÒÒF&vWBævWB†æÖRæÆ÷vW"‚’Â’²¢Ö–åöæÖW2Ò¶—FVÒææÖRç7G&—‚’æÆ÷vW"‚’f÷"—FVÒ–âÖævW"æÖ–åö6FVv÷&–W2–b—FVÒææÖRç7G&—‚—Ð¢÷&FW&VEöæÖW2ÒöVffV7F—fUö6FVv÷'•ö÷&FW%öæÖW2‚¢&÷w3¢Æ—7E·7G%ÒÒµÐ¢f÷"–æFW‚ÂæÖR–âVçVÖW&FR†÷&FW&VEöæÖW2Â“ ¢Ö&¶W"ÒæÖRæÆ÷vW"‚¢–bÖ&¶W"ÓÒwVæ6FVv÷&—¦VBs ¢÷væW"Òu7—7FVÒs²÷&FW%÷FW‡BÒtÆ7Bs²7F–öç2Ò~(	Bp¢VÆ–bÖ&¶W"–âÖ–åöæÖW3 ¢÷væW"ÒtÖ–â6W'fW"+ræÖR&VBÖöæÇ’s²÷&FW%÷FW‡BÒ7G"†–æFW‚“²7F–öç2ÒsÇ7â6Æ73Ò&&FvR#äæöFR÷&FW"ÆÆ÷vVCÂ÷7ãâp¢VÇ6S ¢÷væW"ÒtæöFRÆö6Âs²÷&FW%÷FW‡BÒ7G"†–æFW‚“²7F–öç2Ò~(	Bp¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6FVv÷&–W2æÖævRr“ ¢6fRÒ‡FÖÂæW66R†æÖRÂV÷FSÕG'VR¢7F–öç2Ò€¢sÆF—b6Æ73Ò&æöFRÖ6FVv÷'’×&÷rÖ7F–öç2#âp¢bsÆf÷&Ò6Æ73Ò&æöFRÖ6FVv÷'’×&VæÖRÖf÷&Ò"ÖWF†öCÒ'÷7B"7F–öãÒ"÷æVÂöÖævRö6FVv÷&–W2÷&VæÖR#âp¢bsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&öÆEöæÖR"fÇVSÒ'·6fWÒ#âp¢bsÆ–çWB6Æ73Ò&æöFRÖ6FVv÷'’ÖæÖRÖ–çWB"æÖSÒ&æWuöæÖR"fÇVSÒ'·6fWÒ"&WV—&VB&–ÖÆ&VÃÒ$æWr6FVv÷'’æÖR#âp¢sÆ'WGFöâ6Æ73Ò&æöFRÖ6FVv÷'’×&VæÖRÖ'WGFöâ"G—SÒ'7V&Ö—B#å&VæÖSÂö'WGFöããÂöf÷&Óâp¢bsÆf÷&Ò6Æ73Ò&æöFRÖ6FVv÷'’ÖFVÆWFRÖf÷&Ò"ÖWF†öCÒ'÷7B"7F–öãÒ"÷æVÂöÖævRö6FVv÷&–W2öFVÆWFR"öç7V&Ö—CÒ'&WGW&â6öæf—&Ò…ÂtÖ÷fRF†W6R6†ææVÇ2FòVæ6FVv÷&—¦VCõÂr’#âp¢bsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&æÖR"fÇVSÒ'·6fWÒ#ãÆ'WGFöâ6Æ73Ò&FævW""G—SÒ'7V&Ö—B#å&VÖ÷fSÂö'WGFöããÂöf÷&Óâp¢sÂöF—câp¢¢6÷VçBÒÖ–åö6÷VçG2ævWB†Ö&¶W"Â’²Æö6Åö6÷VçG2ævWB†Ö&¶W"Â¢&÷w2æVæB€¢bsÇG#ãÇFB6Æ73Ò&æöFRÖ6FVv÷'’Ö÷&FW"Ö6VÆÂ#ç¶÷&FW%÷FW‡GÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6FVv÷'’ÖæÖRÖ6VÆÂ#ç¶‡FÖÂæW66R†æÖR—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6FVv÷'’Ö÷væW"Ö6VÆÂ#ç¶÷væW'ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6FVv÷'’Ö6÷VçBÖ6VÆÂ#ç¶6÷VçGÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖ6FVv÷'’Ö7F–öç2Ö6VÆÂ#ç¶7F–öç7ÓÂ÷FCãÂ÷G#âp¢¢ÆW'G2Ò†bsÆF—b6Æ73Öö³ç¶‡FÖÂæW66R†ÖW76vR—ÓÂöF—câr–bÖW76vRVÇ6Rrr’²†bsÆF—b6Æ73ÖW'#ç¶‡FÖÂæW66R†W'&÷"—ÓÂöF—câr–bW'&÷"VÇ6Rrr¢÷&FW%÷æVÂÒrp¢÷&FW%÷67&—BÒrp¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6FVv÷&–W2æÖævRr“ ¢Ö÷f&ÆRÒ¶æÖRf÷"æÖR–â÷&FW&VEöæÖW2–bæÖRæÆ÷vW"‚’ÒwVæ6FVv÷&—¦VBuÐ¢—FV×2Òrræ¦ö–â€¢bsÆÆ’FFÖæöFRÖ6FVv÷'’Ö¶W“Ò'¶‡FÖÂæW66R†æÖRæÆ÷vW"‚’ÂV÷FSÕG'VR—Ò#ãÇ7ããÆ#ç¶–æFW‡ÓÂö#ãÇ7ããÇ7G&öæsç¶‡FÖÂæW66R†æÖR—ÓÂ÷7G&öæsãÇ6ÖÆÃç²$Ö–â6FVv÷'’"–bæÖRæÆ÷vW"‚’–âÖ–åöæÖW2VÇ6R$æöFRÖÆö6Â6FVv÷'’'ÓÂ÷6ÖÆÃãÂ÷7ããÂ÷7ãâp¢sÇ7â6Æ73Ò&7F–öç2#ãÆ'WGFöâG—SÒ&'WGFöâ"FFÖæöFRÖ6FVv÷'’ÖÖ÷fSÒ'W#î(iÂö'WGFöããÆ'WGFöâG—SÒ&'WGFöâ"FFÖæöFRÖ6FVv÷'’ÖÖ÷fSÒ&F÷vâ#î(i3Âö'WGFöããÂ÷7ããÂöÆ“âp¢f÷"–æFW‚ÂæÖR–âVçVÖW&FR†Ö÷f&ÆRÂ¢’÷"sÆÆ’6Æ73Ò&V×G’#äæò6FVv÷&–W2&Rf–Æ&ÆRãÂöÆ“âp¢–æ—F–ÂÒ‡FÖÂæW66R†§6öâæGV×2…¶æÖRæÆ÷vW"‚’f÷"æÖR–âÖ÷f&ÆUÒÂ6W&F÷'3Ò‚rÂrÂs¢r’’ÂV÷FSÕG'VR¢÷&FW%÷æVÂÒbrrsÇ6V7F–öâ6Æ73Ò'æVÂÖf÷&ÒæöFRÖ6FVv÷'’Ö÷&FW"×æVÂ#ãÆF—b6Æ73Ò'FööÆ&"#ãÆF—cãÆƒ#ä6FVv÷'’÷&FW#Âöƒ#ãÇ6ÖÆÃåF†—2÷&FW"—27F÷&VBöæÇ’öâF†—2æöFRâÖ–âæVÂ6FVv÷'’æÖW2&VÖ–â&VBÖöæÇ’ãÂ÷6ÖÆÃãÂöF—cãÂöF—cãÆf÷&ÒÖWF†öCÒ'÷7B"7F–öãÒ"÷æVÂöÖævRö6FVv÷&–W2÷&V÷&FW"#ãÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&6FVv÷'•ö÷&FW""fÇVSÒ'¶–æ—F–ÇÒ"FFÖæöFRÖ6FVv÷'’Ö÷&FW"×fÇVSãÆöÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7B"FFÖæöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7Cç¶—FV×7ÓÂööÃãÆF—b6Æ73Ò&f÷&ÒÖ7F–öç2#ãÆ'WGFöãå6fR6FVv÷'’÷&FW#Âö'WGFöããÂöF—cãÂöf÷&ÓãÂ÷6V7F–öãârrp¢÷&FW%÷67&—BÒ"rrsÇ67&—Câ†gVæ7F–öâ‚—¶6öç7BÆ—7CÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7EÒr“¶6öç7BfÇVSÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖ6FVv÷'’Ö÷&FW"×fÇVUÒr“¶–b‚Æ—7GÇÂfÇVR—&WGW&ã¶gVæ7F–öâ7–æ2‚—¶6öç7B&÷w3Õ²ââæÆ—7BçVW'•6VÆV7F÷$ÆÂ‚vÆ•¶FFÖæöFRÖ6FVv÷'’Ö¶W•Òr•Ó·fÇVRçfÇVSÔ¥4ôâç7G&–æv–g’‡&÷w2æÖ‡&÷sÓç&÷ræFF6WBææöFT6FVv÷'”¶W’’“·&÷w2æf÷$V6‚‚‡&÷rÆ–æFW‚“Óç¶6öç7BçVÖ&W#×&÷rçVW'•6VÆV7F÷"‚s§66÷Râ7ã¦f—'7BÖ6†–ÆBâ"r“¶–b†çVÖ&W"–çVÖ&W"çFW‡D6öçFVçCÕ7G&–ær†–æFW‚³“·Ò“·ÖÆ—7BæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆWfVçCÓç¶6öç7B'WGFöãÖWfVçBçF&vWBæ6Æ÷6W7B‚v'WGFöå¶FFÖæöFRÖ6FVv÷'’ÖÖ÷fUÒr“¶–b‚'WGFöâ—&WGW&ã¶6öç7B&÷sÖ'WGFöâæ6Æ÷6W7B‚vÆ•¶FFÖæöFRÖ6FVv÷'’Ö¶W•Òr“¶–b‚&÷r—&WGW&ã¶–b†'WGFöâæFF6WBææöFT6FVv÷'”Ö÷fSÓÓÒwWrbg&÷rç&Wf–÷W4VÆVÖVçE6–&Æ–ær–Æ—7Bæ–ç6W'D&Vf÷&R‡&÷rÇ&÷rç&Wf–÷W4VÆVÖVçE6–&Æ–ær“¶–b†'WGFöâæFF6WBææöFT6FVv÷'”Ö÷fSÓÓÒvF÷vârbg&÷rææW‡DVÆVÖVçE6–&Æ–ær–Æ—7Bæ–ç6W'D&Vf÷&R‡&÷rææW‡DVÆVÖVçE6–&Æ–ærÇ&÷r“·7–æ2‚“·Ò“·7–æ2‚“·Ò’‚“³Â÷67&—Cârrp¢æ÷FRÒsÇãÇ6ÖÆÃäÖ–â6W'fW"6FVv÷'’æÖW2&VÖ–â7–æ6‡&öæ—¦VBæB&VBÖöæÇ’âW6W'2v—F‚6FVv÷&–W2ÖævRW&Ö—76–öâ6â6WBæöFRÖÆö6Â6FVv÷'’÷&FW"âF†RæöFR÷&FW"FöW2æ÷B7–æ2&6²FòF†RÖ–âæVÂãÂ÷6ÖÆÃãÂ÷âp¢F&ÆRÒ€¢sÆF—b6Æ73Ò'F&ÆR×w&æöFRÖ6FVv÷'’×F&ÆR×w&#ãÇF&ÆR6Æ73Ò&æöFRÖ6FVv÷'’×F&ÆR#âp¢sÆ6öÆw&÷WãÆ6öÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö6öÂÖ÷&FW"#ãÆ6öÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö6öÂÖæÖR#âp¢sÆ6öÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö6öÂÖ÷væW"#ãÆ6öÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö6öÂÖ6÷VçB#ãÆ6öÂ6Æ73Ò&æöFRÖ6FVv÷'’Ö6öÂÖ7F–öç2#ãÂö6öÆw&÷Wâp¢sÇF†VCãÇG#ãÇFƒä÷&FW#Â÷FƒãÇFƒäæÖSÂ÷FƒãÇFƒä÷væW#Â÷FƒãÇFƒä6†ææVÇ3Â÷FƒãÇFƒä7F–öç3Â÷FƒãÂ÷G#ãÂ÷F†VCâp¢sÇF&öG“âr²rræ¦ö–â‡&÷w2’²sÂ÷F&öG“ãÂ÷F&ÆSãÂöF—câp¢¢772ÒrrsÇ7G–ÆSà¢ò¢cããB6ö×7B6–ævÆRÖÆ–æRæöFR6FVv÷'’ÖævVÖVçBF&ÆR¢ð¢ææöFRÖ6FVv÷'’Ö÷&FW"×æVÇ¶Ö&v–âÖ&÷GFöÓ£g‡ÒææöFRÖ6FVv÷'’Ö÷&FW"×æVÂf÷&×¶F—7Æ“¦w&–C¶v£'‡ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7G¶Æ—7B×7G–ÆS¦æöæS¶Ö&v–ã£·FF–æs£¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£ƒ¶÷fW&fÆ÷s¦†–FFVçÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7BÆ—¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"WFó¶Æ–vâÖ—FV×3¦6VçFW#¶v£'ƒ·FF–æs£‚'ƒ¶&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"—ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7BÆ“¦f—'7BÖ6†–ÆG¶&÷&FW"×F÷£ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7BÆ“ç7ã¦f—'7BÖ6†–ÆG¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£‡ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7BÆ“ç7ã¦f—'7BÖ6†–ÆCæ'·v–GFƒ£#‡ƒ¶†V–v‡C£#‡ƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶&÷&FW"×&F—W3£wƒ¶&6¶w&÷VæC¢3s3S&c¶6öÆ÷#§f"‚ÒÖ66VçB—ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7B7G&öærÂææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7B6ÖÆÇ¶F—7Æ“¦&Æö6·Ð¢ææöFRÖ6FVv÷'’×F&ÆR×w&¶÷fW&fÆ÷r×ƒ¦WF÷ÒææöFRÖ6FVv÷'’×F&ÆW¶Ö–â×v–GFƒ£“Cƒ·F&ÆRÖÆ–÷WC¦f—†VGÒææöFRÖ6FVv÷'’Ö6öÂÖ÷&FW'·v–GFƒ£s‡‡ÒææöFRÖ6FVv÷'’Ö6öÂÖæÖW·v–GFƒ£“‡ÒææöFRÖ6FVv÷'’Ö6öÂÖ÷væW'·v–GFƒ£3‡ÒææöFRÖ6FVv÷'’Ö6öÂÖ6÷VçG·v–GFƒ£‡ÒææöFRÖ6FVv÷'’Ö6öÂÖ7F–öç7·v–GFƒ¦WF÷ÒææöFRÖ6FVv÷'’×F&ÆRF‚ÂææöFRÖ6FVv÷'’×F&ÆRFG·fW'F–6ÂÖÆ–vã¦Ö–FFÆWÒææöFRÖ6FVv÷'’Ö÷&FW"Ö6VÆÂÂææöFRÖ6FVv÷'’Ö6÷VçBÖ6VÆÇ·v†—FR×76S¦æ÷w&ÒææöFRÖ6FVv÷'’ÖæÖRÖ6VÆÇ¶föçB×vV–v‡C£sÒææöFRÖ6FVv÷'’Ö÷væW"Ö6VÆÇ¶6öÆ÷#§f"‚ÒÖ×WFVB—ÒææöFRÖ6FVv÷'’Ö7F–öç2Ö6VÆÇ¶Ö–â×v–GFƒ£ÒææöFRÖ6FVv÷'’×&÷rÖ7F–öç7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£‡ƒ¶fÆW‚×w&¦æ÷w&¶Ö–â×v–GFƒ£ÒææöFRÖ6FVv÷'’×&VæÖRÖf÷&×¶F—7Æ“¦fÆW‚–×÷'FçC¶Æ–vâÖ—FV×3¦6VçFW#¶v£‡ƒ¶fÆWƒ£WFó¶Ö–â×v–GFƒ£¶Ö&v–ã£ÒææöFRÖ6FVv÷'’ÖæÖRÖ–çWG·v–GFƒ¦WFò–×÷'FçC¶Ö–â×v–GFƒ£Sƒ¶fÆWƒ£#ƒ¶Ö&v–ã£ÒææöFRÖ6FVv÷'’×&VæÖRÖ'WGFöâÂææöFRÖ6FVv÷'’ÖFVÆWFRÖf÷&Ò'WGFöç¶fÆWƒ£WF÷ÒææöFRÖ6FVv÷'’ÖFVÆWFRÖf÷&×¶F—7Æ“¦–æÆ–æRÖfÆW‚–×÷'FçC¶Æ–vâÖ—FV×3¦6VçFW#¶fÆWƒ£WFó¶Ö&v–ã£ÒææöFRÖ6FVv÷'’Ö7F–öç2Ö6VÆÃâæ&FvW·v†—FR×76S¦æ÷w&ÔÖVF–†Ö‚×v–GFƒ£sc‚—²ææöFRÖ6FVv÷'’×F&ÆW¶Ö–â×v–GFƒ£“‡ÒææöFRÖ6FVv÷'’Ö÷&FW"ÖÆ—7BÆ—¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"’WF÷×Ð£Â÷7G–ÆSârrp¢&WGW&â…DÔÅ&W7öç6R…öæöFU÷æVÅöFö7VÖVçB‡W6W"Ât6FVv÷&–W2rÂÆW'G2²÷&FW%÷æVÂ²sÆƒ#ä6†ææVÂ6FVv÷&–W3Âöƒ#âr²æ÷FR²F&ÆRÂ7F—fSÒv6FVv÷&–W2rÂW‡G&ö†VCÖ772Â&öG•÷67&—CÖ÷&FW%÷67&—B’  ¤ç÷7B‚r÷æVÂöÖævRö6FVv÷&–W2÷&V÷&FW"r¦7–æ2FVbæöFUö6FVv÷'•÷&V÷&FW"‡&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6FVv÷&–W2æÖævRr¢–bæ÷Bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"Âv6FVv÷&–W2æÖævRr“ ¢&—6R…EEW†6WF–öâƒC2Ât6FVv÷&–W2ÖævRW&Ö—76–öâ—2&WV—&VBr¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢G'“ ¢'6VBÒ§6öâæÆöG2‡7G"†f÷&ÒævWB‚v6FVv÷'•ö÷&FW"r’÷"uµÒr’¢–bæ÷B—6–ç7Fæ6R‡'6VBÂÆ—7B“ ¢&—6RfÇVTW'&÷"‚t–çfÆ–B6FVv÷'’÷&FW"r¢f–Æ&ÆRÒ¶æÖRæÆ÷vW"‚’f÷"æÖR–âöæöFUö6FVv÷'•ö6FÆöuöæÖW2‚’–bæÖRæÆ÷vW"‚’ÒwVæ6FVv÷&—¦VBwÐ¢÷&FW#¢Æ—7E·7G%ÒÒµÐ¢f÷"—FVÒ–â'6VC ¢Ö&¶W"Ò7G"†—FVÒ÷"rr’ç7G&—‚’æÆ÷vW"‚¢–bÖ&¶W"–âf–Æ&ÆRæBÖ&¶W"æ÷B–â÷&FW# ¢÷&FW"æVæB†Ö&¶W"¢÷&FW"æW‡FVæB†æÖRæÆ÷vW"‚’f÷"æÖR–âöæöFUö6FVv÷'•ö6FÆöuöæÖW2‚’–bæÖRæÆ÷vW"‚’–âf–Æ&ÆRæBæÖRæÆ÷vW"‚’æ÷B–â÷&FW"¢v—F‚ÖævW"æÆö6³ ¢ÖævW"æ6FVv÷'•ö÷&FW%ö÷fW'&–FW2Ò÷&FW ¢25E$TÔdõ$tUôäôDUô4DTtõ%•õ$Tõ$DU%õU4U%ô”ÔÕUD$ÄUõc#s ¢ÖævW"ç6fUö6FVv÷&–W2‚¢ÖævW"æÆör‚tæöFRÖÆö6Â6FVv÷'’÷&FW"WFFVBrÂ66÷SÒw7—7FVÒrÂFWF–Ç3ÒrÂræ¦ö–â†÷&FW"’¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"’2W†3 ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‡7G"†W†2’’Â32¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚tæöFR6FVv÷'’÷&FW"6fVBr’Â32  ¤ç÷7B‚r÷æVÂöÖævRö6FVv÷&–W2÷&VæÖRr¦7–æ2FVb–æFWVæFVçEö6FVv÷'•÷&VæÖR‡&WVW7C¢&WVW7B“ ¢&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6FVv÷&–W2æÖævRr“²bÒv—B&WVW7Bæf÷&Ò‚¢öÆEöæÖRÒ7G"†bævWB‚vöÆEöæÖRr’÷"rr’ç7G&—‚“²æWuöæÖRÒ7G"†bævWB‚væWuöæÖRr’÷"rr’ç7G&—‚•³£#Ð¢Ö–åöæÖW2Ò¶—FVÒææÖRç7G&—‚’æÆ÷vW"‚’f÷"—FVÒ–âÖævW"æÖ–åö6FVv÷&–W7Ð¢–böÆEöæÖRæÆ÷vW"‚’–âÖ–åöæÖW3 ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚tÖ–â6W'fW"6FVv÷&–W2&R&VBÖöæÇ’r’Â32¢–bæ÷BöÆEöæÖR÷"æ÷BæWuöæÖS ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚t6FVv÷'’æÖR—2&WV—&VBr’Â32¢6†ævVBÒ ¢v—F‚ÖævW"æÆö6³¢—FV×2ÒÆ—7B†ÖævW"æ6†ææVÇ2æ—FV×2‚’¢f÷"¶W’Â'B–â—FV×3 ¢–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒvÆö6ÂræBöÆEöæÖR–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r“ ¢6FVv÷&–W2Ò¶æWuöæÖR–b—FVÒÓÒöÆEöæÖRVÇ6R—FVÒf÷"—FVÒ–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r•Ð¢6FVv÷&–W2ÒÆ—7B†F–7Bæg&öÖ¶W—2†6FVv÷&–W2’¢&–Ö'’Ò6FVv÷&–W5³Ò–b6FVv÷&–W2VÇ6RuVæ6FVv÷&—¦VBp¢÷&FW"ÒöÖ–åö6FVv÷'•ö÷&FW%öÖ‚’ævWB‡&–Ö'’æÆ÷vW"‚’Â¢ÖævW"ç7–æ5ö6†ææVÂ†¶W’Â'Bæ6öæf–ræÖöFVÅö6÷’‡WFFS×²v6FVv÷'’s¢&–Ö'’Âv6FVv÷&–W2s¢6FVv÷&–W2Âv6FVv÷'•ö÷&FW"s¢÷&FW"Âv6FÆöuö÷væW"s¢vÆö6ÂwÒ’“²6†ævVB³Ò¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2†bu&VæÖVB6FVv÷'’öâ¶6†ævVGÒ6†ææVÂ‡2’r’Â32  ¤ç÷7B‚r÷æVÂöÖævRö6FVv÷&–W2öFVÆWFRr¦7–æ2FVb–æFWVæFVçEö6FVv÷'•öFVÆWFR‡&WVW7C¢&WVW7B“ ¢&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂv6FVv÷&–W2æÖævRr“²bÒv—B&WVW7Bæf÷&Ò‚“²æÖRÒ7G"†bævWB‚væÖRr’÷"rr’ç7G&—‚¢Ö–åöæÖW2Ò¶—FVÒææÖRç7G&—‚’æÆ÷vW"‚’f÷"—FVÒ–âÖævW"æÖ–åö6FVv÷&–W7Ð¢–bæÖRæÆ÷vW"‚’–âÖ–åöæÖW3 ¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚tÖ–â6W'fW"6FVv÷&–W2&R&VBÖöæÇ’r’Â32¢6†ævVBÒ ¢v—F‚ÖævW"æÆö6³¢—FV×2ÒÆ—7B†ÖævW"æ6†ææVÇ2æ—FV×2‚’¢f÷"¶W’Â'B–â—FV×3 ¢–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒvÆö6ÂræBæÖR–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r“ ¢6FVv÷&–W2Ò¶—FVÒf÷"—FVÒ–âö6öæf–uö6FVv÷&–W2‡'Bæ6öæf–r’–b—FVÒÒæÖUÐ¢&–Ö'’Ò6FVv÷&–W5³Ò–b6FVv÷&–W2VÇ6RuVæ6FVv÷&—¦VBp¢ÖævW"ç7–æ5ö6†ææVÂ†¶W’Â'Bæ6öæf–ræÖöFVÅö6÷’‡WFFS×²v6FVv÷'’s¢&–Ö'’Âv6FVv÷&–W2s¢6FVv÷&–W2Âv6FVv÷'•ö÷&FW"s¢öÖ–åö6FVv÷'•ö÷&FW%öÖ‚’ævWB‡&–Ö'’æÆ÷vW"‚’Â’Âv6FÆöuö÷væW"s¢vÆö6ÂwÒ’“²6†ævVB³Ò¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂöÖævRö6FVv÷&–W3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2†btÖ÷fVB¶6†ævVGÒ6†ææVÂ‡2’FòVæ6FVv÷&—¦VBr’Â32  ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôEU$D”ôåõcCS ¢25E$TÔdõ$tUôäôDUô4Ä”TåEõ4U54”ôåôtUõU%4•5EõcCc¢&WF–æVBW"Õ4”BÆ–&6°¢2vVæW&F–öç27W'f—fRF—66öææV7G2âcãsBF—7Æ—2F†V—"7VÖÖVB7F—fRF–ÖRf÷ ¢2F†R7F&ÆRÆ—fR4”BÂ6ò7F÷÷W6Rg&VW¦W2vRæBÆFW"Æ–&6²&W7VÖW0¢2g&öÒF†R67V×VÆFVBGW&F–öâ–ç7FVBöb7&VF–æræWrÆöv–6Â6W76–öâà¦FVböæöFUö6Æ–VçEöÆöu÷6W76–öåö–B†—FVÓ¢F–7E·7G"Âç•Ò’Óâ7G# ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuô”äÄ”äUõ4U54”ôåôtUõcc¢†–FFVâÆ–&6°¢2ö'6W'fW"WfVçB6âFöæFR—G2&VÂÖVF–4”BFòF†R&V6VF–ærf—6–&ÆP¢2Æöv–â÷Æ–Æ—7B&÷rv†VâF†R6†ævW2W6W"ÔvVçB&WGvVVâ’æBÆ–W"à¢GF6†VBÒ&Rç7V"‡"%µäÕ¦×£Ó’å÷âÕÒ²"Â""Â7G"†—FVÒævWB‚uöGW&F–öå÷6W76–öåö–Br’÷"rr’ç7G&—‚’•³£“eÐ¢–bGF6†VC ¢&WGW&âGF6†V@¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr¢ÖF6‚Ò&Rç6V&6‚‡"rƒó¥çÅ³²ÅÇ5Ò’ƒó§6W76–öåö–GÇ6–B•Ç2¥³£ÕÕÇ2¥²%ÂuÓò…´Õ¦×£Ó’å÷âÕ×³Ã“gÒ’rÂ&rÂfÆw3×&Rä’¢&WGW&â7G"†ÖF6‚æw&÷Wƒ’–bÖF6‚VÇ6Rrr•³£“eÐ  ¦FVböæöFUö6Æ–VçEöÆöuövVæW&F–öåö–B†—FVÓ¢F–7E·7G"Âç•Ò’Óâ7G# ¢""%&WGW&âF†RVæ—VRÆ–&6²ÖvVæW&F–öâ–FVçF—G’GF6†VBFòF†—2Æör&÷râ"" ¢GF6†VBÒ&Rç7V"€¢"%µäÕ¦×£Ó’å÷âÕÒ²"Â""À¢7G"†—FVÒævWB‚uöGW&F–öåövVæW&F–öåö–Br’÷"rr’ç7G&—‚’À¢•³£“eÐ¢–bGF6†VC ¢&WGW&âGF6†V@¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr¢ÖF6‚Ò&Rç6V&6‚€¢"rƒó¥çÅ³²ÅÇ5Ò’ƒó§6W76–öåövVæW&F–öåö–GÆÆöu÷6W76–öåö–GÆvVæW&F–öåö–B•Ç2¥³£ÕÕÇ2¥²%ÂuÓò…´Õ¦×£Ó’å÷âÕ×³Ã“gÒ’rÀ¢&rÀ¢fÆw3×&Rä’À¢¢&WGW&â7G"†ÖF6‚æw&÷Wƒ’–bÖF6‚VÇ6Rrr•³£“eÐ  ¦FVböæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚†—FVÓ¢F–7E·7G"Âç•Ò’ÓâfÆöC ¢GF6†VBÒ—FVÒævWB‚uöGW&F–öåövVæW&F–öåöf—'7EöWö6‚r¢G'“ ¢–bGF6†VBæ÷B–â„æöæRÂrr“ ¢&WGW&âfÆöB†GF6†VB¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢70¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr¢ÖF6‚Ò&Rç6V&6‚€¢"rƒó¥çÅ³²ÅÇ5Ò’ƒó§6W76–öå÷7F'FVEöWö6‡ÆvVæW&F–öåöf—'7EöWö6‚•Ç2¥³£ÕÕÇ2¢…³Ó•Ò²ƒó¥Âå³Ó•Ò²“ò’rÀ¢&rÀ¢fÆw3×&Rä’À¢¢–bæ÷BÖF6ƒ ¢&WGW&âã ¢G'“ ¢&WGW&âfÆöB†ÖF6‚æw&÷Wƒ’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&WGW&âã   ¦FVböæöFUö6Æ–VçEöÆöuö–FVçF—G•ö—†—FVÓ¢F–7E·7G"Âç•Ò’Óâ7G# ¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr’ç7G&—‚¢G'“ ¢'6VBÒ§6öâæÆöG2‡&r’–b&rVÇ6RæöæP¢W†6WBW†6WF–öã ¢'6VBÒæöæP¢–b—6–ç7Fæ6R‡'6VBÂF–7B“ ¢f÷"æÖR–â‚v—rÂv6Æ–VçEö—rÂw&VÖ÷FUö—rÂvFG&W72r“ ¢fÇVRÒ7G"‡'6VBævWB†æÖR’÷"rr’ç7G&—‚¢–bfÇVS ¢&WGW&âfÇVU³£#Ð¢ÖF6‚Ò&Rç6V&6‚‡""ƒó¥çÅ³²ÇµÇ5Ò’ƒó¦—Æ6Æ–VçEö—Ç&VÖ÷FUö—ÆFG&W72•Ç2¥³£ÕÕÇ2¥µÂ"uÓò…µã²ÂuÂ'ÕÇ5Ò²’"Â&rÂfÆw3×&Rä’¢&WGW&âÖF6‚æw&÷Wƒ•³£#Ò–bÖF6‚VÇ6Rrp  ¦FVböæöFUö6Æ–VçEöÆöuö–FVçF—G•÷W6W"†—FVÓ¢F–7E·7G"Âç•Ò’Óâ7G# ¢F—&V7BÒ7G"†—FVÒævWB‚wW6W"r’÷"rr’ç7G&—‚¢–bF—&V7C ¢&WGW&âF—&V7E³£#Ð¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr’ç7G&—‚¢G'“ ¢'6VBÒ§6öâæÆöG2‡&r’–b&rVÇ6RæöæP¢W†6WBW†6WF–öã ¢'6VBÒæöæP¢–b—6–ç7Fæ6R‡'6VBÂF–7B“ ¢f÷"æÖR–â‚wW6W"rÂwW6W&æÖRrÂv6Æ–VçE÷W6W"rÂv7F÷"r“ ¢fÇVRÒ7G"‡'6VBævWB†æÖR’÷"rr’ç7G&—‚¢–bfÇVS ¢&WGW&âfÇVU³£#Ð¢ÖF6‚Ò&Rç6V&6‚‡""ƒó¥çÅ³²ÅÇ5Ò’ƒó§W6W'ÇW6W&æÖWÆ6Æ–VçE÷W6W'Æ7F÷"•Ç2¥³£ÕÕÇ2¥µÂ"uÓò…µã²ÅÂ"uÒ²’"Â&rÂfÆw3×&Rä’¢&WGW&âÖF6‚æw&÷Wƒ’ç7G&—‚•³£#Ò–bÖF6‚VÇ6Rrp  ¦FVböæöFUöföÆE÷Æ–&6µ÷6W76–öå÷&÷w2†—FV×3¢Æ—7E¶F–7E·7G"Âç•ÕÒ’ÓâÆ—7E¶F–7E·7G"Âç•ÕÓ ¢""$&–æBV6‚Æ–&6²vVæW&F–öâFòBÖ÷7BöæRf—6–&ÆR6Æ–VçBÖÆör&÷rà ¢5E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôÄ•dUõ4”EôUô4…ô$”äEõcs3¢F†RÆ—fR×6W76–öà¢4”B—2F†RöæÇ’6W76–öâ”B6†÷vâ÷7F÷&VBf÷"æWr6Æ–VçBÖÆör&÷w2â–b¢6Æ–VçB&WW6W2F†B4”BÆFW"Âf—'7E÷6VVâWö6‚&VÖ–ç2F†R–çFW&æÀ¢†—7F÷&–6ÂvVæW&F–öâ¶W’6òâöÆB&÷r6ææ÷B–æ†W&—BæWrÆ—fRvRà¢"" ¢6÷–VBÒ¶F–7B†—FVÒ’f÷"—FVÒ–â—FV×5Ð¢Æ–&6µ÷&÷w2Ò°¢—FVÒf÷"—FVÒ–â6÷–V@¢–b7G"†—FVÒævWB‚w66÷Rr’÷"rr’ç7G&—‚’æÆ÷vW"‚’ÓÒv6Æ–VçBp¢æB7G"†—FVÒævWB‚vÖW76vRr’÷"rr’ç7G&—‚’æÆ÷vW"‚’ÓÒwÆ–&6²6W76–öâ7F'FVBp¢Ð¢f—6–&ÆRÒ¶—FVÒf÷"—FVÒ–â6÷–VB–b—FVÒæ÷B–âÆ–&6µ÷&÷w5Ð¢–bæ÷BÆ–&6µ÷&÷w3 ¢&WGW&âf—6–&ÆP ¢&VfW'&VEöÖW76vW2Ò°¢w‡G&VÒ6Æ–VçBÆöv–ârÂv6Æ–VçBÆ–Æ—7B&WVW7FVBrÂwvV"Æ–W"Æöv–ârÀ¢wvV"Æ–W"V–6²Æöv–ârÂwvV"Æ–W"WFòÆöv–ârÀ¢Ð¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôôäUõ$õuõU%õ4”EõcsC¢cãs"÷cãs2Ö¢2Ç&VG’6öçF–â×VÇF—ÆR&V6öææV7Bö'6W'fW"&÷w2f÷"F†R6ÖR7F&ÆRÆ—fP¢24”BâföÆBöæÇ’F†RV&Æ–W7B&WF–æVBö'6W'fW"&÷rf÷"F†B4”B6òF†RT¢2&W6VçG2öæRÆöv–6Â6W76–öâ&÷rv†–ÆR—G27V×VÆF—fRvR¶VW2w&÷v–ærà¢6æöæ–6Å÷Æ–&6µ÷&÷w3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢&W6WE÷6V6öæG2ÒÖævW"æ6Æ–VçE÷6W76–öå÷&W6WE÷6V6öæG2‚¢Æ–&6µöÆ7Eö'•÷6–C¢F–7E·7G"ÂfÆöEÒÒ·Ð¢f÷"Æ–&6²–â6÷'FVB‡Æ–&6µ÷&÷w2Â¶W“ÕöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚“ ¢Æ–&6µ÷6–BÒöæöFUö6Æ–VçEöÆöu÷6W76–öåö–B‡Æ–&6²¢WfVçEöWö6‚ÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚‡Æ–&6²¢&Wf–÷W5öWfVçBÒÆ–&6µöÆ7Eö'•÷6–BævWB‡Æ–&6µ÷6–BÂã’–bÆ–&6µ÷6–BVÇ6Rã ¢–bÆ–&6µ÷6–BæB&Wf–÷W5öWfVçBâæBWfVçEöWö6‚âæBWfVçEöWö6‚Ò&Wf–÷W5öWfVçBÃÒ&W6WE÷6V6öæG3 ¢6öçF–çVP¢–bÆ–&6µ÷6–BæBWfVçEöWö6‚â ¢Æ–&6µöÆ7Eö'•÷6–E·Æ–&6µ÷6–EÒÒWfVçEöWö6€¢6æöæ–6Å÷Æ–&6µ÷&÷w2æVæB‡Æ–&6² ¢f÷"Æ–&6²–â6æöæ–6Å÷Æ–&6µ÷&÷w3 ¢6–BÒöæöFUö6Æ–VçEöÆöu÷6W76–öåö–B‡Æ–&6²¢vVæW&F–öåöf—'7BÒöæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚‡Æ–&6²¢Æ–&6µ÷W6W"ÒöæöFUö6Æ–VçEöÆöuö–FVçF—G•÷W6W"‡Æ–&6²’æ66VföÆB‚¢Æ–&6µö—ÒöæöFUö6Æ–VçEöÆöuö–FVçF—G•ö—‡Æ–&6²¢Æ–&6µöWö6‚ÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚‡Æ–&6²¢&W7C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢&W7E÷66÷&S¢GWÆU¶–çBÂ–çBÂfÆöEÒÂæöæRÒæöæP¢f÷"6æF–FFR–âf—6–&ÆS ¢–b7G"†6æF–FFRævWB‚w66÷Rr’÷"rr’ç7G&—‚’æÆ÷vW"‚’Òv6Æ–VçBs ¢6öçF–çVP¢2öæRf—6–&ÆR&÷r&W&W6VçG2öæRÆ–&6²vVæW&F–öâöæÇ’à¢–böæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚†6æF–FFR’â ¢6öçF–çVP¢ÖW76vRÒ7G"†6æF–FFRævWB‚vÖW76vRr’÷"rr’ç7G&—‚’æÆ÷vW"‚¢–bÖW76vRÓÒwÆ–&6²6W76–öâ7F'FVBr÷"vf–ÆVBr–âÖW76vR÷"vFVæ–VBr–âÖW76vR÷"wVæf–Æ&ÆRr–âÖW76vS ¢6öçF–çVP¢6æF–FFU÷W6W"ÒöæöFUö6Æ–VçEöÆöuö–FVçF—G•÷W6W"†6æF–FFR’æ66VföÆB‚¢6æF–FFUö—ÒöæöFUö6Æ–VçEöÆöuö–FVçF—G•ö—†6æF–FFR¢–bÆ–&6µ÷W6W"æB6æF–FFU÷W6W"ÒÆ–&6µ÷W6W# ¢6öçF–çVP¢–bÆ–&6µö—æB6æF–FFUö—æB6æF–FFUö—ÒÆ–&6µö— ¢6öçF–çVP¢6æF–FFUöWö6‚ÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚†6æF–FFR¢–bÆ–&6µöWö6‚âæB6æF–FFUöWö6‚â ¢FVÇFÒÆ–&6µöWö6‚Ò6æF–FFUöWö6€¢F—&V7F–öå÷VæÇG’Ò–bFVÇFãÒVÇ6R¢F—7Fæ6RÒ'2†FVÇF¢26W76–öâ×7F'B&VÆöæw2FòæV&'’Æöv–âö6FÆörWfVçBâ¶VW ¢2F†R6ö×F–&–Æ—G’v–æF÷rvVæW&÷W2f÷"2F†B66†R6FÆöwVW2à¢–bF—7Fæ6RâƒcCã ¢6öçF–çVP¢VÇ6S ¢F—&V7F–öå÷VæÇG’Ò ¢F—7Fæ6RÒã ¢&VfW&Væ6RÒ–bÖW76vR–â&VfW'&VEöÖW76vW2VÇ6R¢66÷&RÒ‡&VfW&Væ6RÂF—&V7F–öå÷VæÇG’ÂF—7Fæ6R¢–b&W7E÷66÷&R—2æöæR÷"66÷&RÂ&W7E÷66÷&S ¢&W7BÒ6æF–FFP¢&W7E÷66÷&RÒ66÷&P¢–b&W7B—2æöæR÷"æ÷B6–B÷"vVæW&F–öåöf—'7BÃÒ ¢2¶VWâVç—&VBö'6W'fW"&÷rf—6–&ÆR&F†W"F†â6–ÆVçFÇ’Æ÷6–æp¢2F†RW†7BÆ—fR×6W76–öâ4”BövRà¢f—6–&ÆRæVæB‡Æ–&6²¢6öçF–çVP¢&W7E²uöGW&F–öå÷6W76–öåö–BuÒÒ6–@¢&W7E²uöGW&F–öåövVæW&F–öåöf—'7EöWö6‚uÒÒvVæW&F–öåöf—'7@¢&W7E²uöGW&F–öåöGF6…öF—7Fæ6RuÒÒfÆöB†&W7E÷66÷&U³%Ò–b&W7E÷66÷&RVÇ6Rã ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôdôÄEõ5T44U55õ4”EõcsS ¢2F†R6Æ–VçBÖÆörT’—26W76–öâÖ÷&–VçFVBÂæ÷B&WVW7BÖ÷&–VçFVBâ‡G&VÒ0¢2Ö’6ÆÂÆ–W%ö’ç‡&WVFVFÇ’v†–ÆRF†R6ÖRgÒöFWbÒÆ—fR4”B—0¢27F—fR†÷"ÆFW"&W7VÖW2’â6öÆÆ6RWfW'’7V66W76gVÂ6Æ–VçBöÆöv–âö6FÆöp¢2&÷rF†B6'&–W2F†B7F&ÆR4”B–çFòöæRÆöv–6Âf—6–&ÆR&÷râf–ÆVB÷ ¢2FVæ–VBWF†VçF–6F–öâGFV×G2†fRæòfÆ–B6W76–öâæB&VÖ–â6W&FP¢2VF—B&÷w2âF†—2Ç6òföÆG2GWÆ–6FR&÷w2Ç&VG’&WF–æVB'’öÆFW ¢2fW'6–öç2Â6òWw&F–ær&W—'2F†RF—7Æ’–ÖÖVF–FVÇ’à¢7V66W75öÖW76vW2Ò°¢w‡G&VÒ6Æ–VçBÆöv–ârÂv6Æ–VçBÆ–Æ—7B&WVW7FVBrÂwvV"Æ–W"Æöv–ârÀ¢wvV"Æ–W"V–6²Æöv–ârÂwvV"Æ–W"WFòÆöv–ârÀ¢Ð¢6–Eöw&÷W3¢F–7E·7G"ÂÆ—7E¶F–7E·7G"Âç•ÕÕÒÒ·Ð¢77F‡&÷Vvƒ¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢f÷"—FVÒ–âf—6–&ÆS ¢–b7G"†—FVÒævWB‚w66÷Rr’÷"rr’ç7G&—‚’æÆ÷vW"‚’Òv6Æ–VçBs ¢77F‡&÷Vv‚æVæB†—FVÒ¢6öçF–çVP¢ÖW76vRÒ7G"†—FVÒævWB‚vÖW76vRr’÷"rr’ç7G&—‚’æÆ÷vW"‚¢–bÖW76vRæ÷B–â7V66W75öÖW76vW3 ¢77F‡&÷Vv‚æVæB†—FVÒ¢6öçF–çVP¢6–BÒöæöFUö6Æ–VçEöÆöu÷6W76–öåö–B†—FVÒ¢–bæ÷B6–C ¢77F‡&÷Vv‚æVæB†—FVÒ¢6öçF–çVP¢6–Eöw&÷W2ç6WFFVfVÇB‡6–BÂµÒ’æVæB†—FVÒ ¢f÷"6–BÂ&÷w2–â6–Eöw&÷W2æ—FV×2‚“ ¢&÷w2ç6÷'B†¶W“ÕöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚¢6ÇW7FW'3¢Æ—7E¶Æ—7E¶F–7E·7G"Âç•ÕÕÒÒµÐ¢f÷"&÷r–â&÷w3 ¢WfVçEöWö6‚ÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚‡&÷r¢–bæ÷B6ÇW7FW'3 ¢6ÇW7FW'2æVæB…·&÷uÒ¢6öçF–çVP¢&Wf–÷W5öWö6‚ÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚†6ÇW7FW'5²ÓÕ²ÓÒ¢–bWfVçEöWö6‚âæB&Wf–÷W5öWö6‚âæBWfVçEöWö6‚Ò&Wf–÷W5öWö6‚â&W6WE÷6V6öæG3 ¢6ÇW7FW'2æVæB…·&÷uÒ¢VÇ6S ¢6ÇW7FW'5²ÓÒæVæB‡&÷r¢f÷"6ÇW7FW"–â6ÇW7FW'3 ¢6æöæ–6ÂÒ6ÇW7FW%³Ð¢&÷VæBÒæW‡B‚‡&÷rf÷"&÷r–â6ÇW7FW"–böæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚‡&÷r’â’ÂæöæR¢–b&÷VæB—2æ÷BæöæS ¢6æöæ–6Å²uöGW&F–öå÷6W76–öåö–BuÒÒ6–@¢6æöæ–6Å²uöGW&F–öåövVæW&F–öåöf—'7EöWö6‚uÒÒöæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚†&÷VæB¢–buöGW&F–öåöGF6…öF—7Fæ6Rr–â&÷VæC ¢6æöæ–6Å²uöGW&F–öåöGF6…öF—7Fæ6RuÒÒ&÷VæE²uöGW&F–öåöGF6…öF—7Fæ6RuÐ¢77F‡&÷Vv‚æVæB†6æöæ–6Â ¢77F‡&÷Vv‚ç6÷'B†¶W“ÕöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚Â&WfW'6SÕG'VR¢&WGW&â77F‡&÷Vv€ ¦FVböæöFUöGW&F–öåöÆ&VÂ‡6V6öæG3¢ö&¦V7BÂöæÆ–æS¢&ööÂÒfÇ6R’Óâ7G# ¢G'“ ¢F÷FÂÒÖ‚ƒÂ–çB†fÆöB‡6V6öæG2÷"’’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&WGW&â~(	Bp¢F—2Â&VÒÒF—fÖöB‡F÷FÂÂƒcC¢†÷W'2Â&VÒÒF—fÖöB‡&VÒÂ3c¢Ö–çWFW2Â6V72ÒF—fÖöB‡&VÒÂc¢–bF—3 ¢FW‡BÒbw¶F—7ÖB¶†÷W'3£&GÓ§¶Ö–çWFW3£&GÓ§·6V73£&GÒp¢VÆ–b†÷W'3 ¢FW‡BÒbw¶†÷W'3£&GÓ§¶Ö–çWFW3£&GÓ§·6V73£&GÒp¢VÇ6S ¢FW‡BÒbw¶Ö–çWFW3£&GÓ§·6V73£&GÒp¢&WGW&â‚töæÆ–æR+rr–böæÆ–æRVÇ6Rrr’²FW‡@ ¦FVböæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚†—FVÓ¢F–7E·7G"Âç•Ò’ÓâfÆöC ¢&rÒ7G"†—FVÒævWB‚wF–ÖRr’÷"rr’ç7G&—‚¢–bæ÷B&s ¢&WGW&âã ¢G'“ ¢'6VBÒFFWF–ÖRæg&öÖ—6öf÷&ÖB‡&rç&WÆ6R‚u¢rÂr³£r’¢–b'6VBçG¦–æfò—2æöæS ¢'6VBÒ'6VBç&WÆ6R‡G¦–æfó×F–ÖW¦öæRçWF2¢&WGW&âfÆöB‡'6VBçF–ÖW7F×‚’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Âõ4W'&÷"Â÷fW&fÆ÷tW'&÷"“ ¢&WGW&âã  ¦FVböæöFU÷6W76–öåövVæW&F–öåö6ÇW7FW'2‡&÷w3¢Æ—7E¶F–7E·7G"ÂfÆöEÕÒÂ&W6WE÷6V6öæG3¢–çB’ÓâÆ—7E¶Æ—7E¶F–7E·7G"ÂfÆöEÕÕÓ ¢25E$TÔdõ$tUôäôDUô4Ä”TåEõ4U54”ôåô4ÅU5DU%õcƒ¢÷&FW&VBÒ6÷'FVB‡&÷w2Â¶W“ÖÆÖ&F&÷s¢fÆöB‡&÷u²vf—'7BuÒ’¢6ÇW7FW'3¢Æ—7E¶Æ—7E¶F–7E·7G"ÂfÆöEÕÕÒÒµÐ¢f÷"&÷r–â÷&FW&VC ¢–bæ÷B6ÇW7FW'3 ¢6ÇW7FW'2æVæB…·&÷uÒ¢6öçF–çVP¢&Wf–÷W5öÆ7BÒÖ‚†fÆöB†—FVÕ²vÆ7BuÒ’f÷"—FVÒ–â6ÇW7FW'5²ÓÒ¢–bfÆöB‡&÷u²vf—'7BuÒ’Ò&Wf–÷W5öÆ7BâÖ‚ƒcÂ–çB‡&W6WE÷6V6öæG2’“ ¢6ÇW7FW'2æVæB…·&÷uÒ¢VÇ6S ¢6ÇW7FW'5²ÓÒæVæB‡&÷r¢&WGW&â6ÇW7FW'0 ¦FVböæöFUö6Æ–VçEöÆöuöGW&F–öå÷7FFW2†—FV×3¢Æ—7E¶F–7E·7G"Âç•ÕÒ’ÓâÆ—7E¶F–7E·7G"Âç•ÒÂæöæUÓ ¢""%&W6öÇfR7V×VÆF—fR7F—fR6W76–öâvRf÷"V6‚7F&ÆRÆ—fR4”Bà ¢5E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuô5TÕTÄD•dUõ4”EôtUõcsC¢gÒöFWbÒ4”B—0¢öæRÆöv–6Â6Æ–VçBÖÆör6W76–öâ7&÷72Æ–&6²7F÷2÷&W7F'G2âV6‚&WF–æV@¢&VF—2öÆö6Â†—7F÷'’vVæW&F–öâ6öçG&–'WFW2öæÇ’—G27F—fRf—'7BÓæÆ7@¢GW&F–öâÂ6òöffÆ–æR÷W6Rv2&Ræ÷B6÷VçFVBâ–bF†RÆFW7BvVæW&F–öâ—0¢7W'&VçFÇ’Æ—fRÂ—G2GW&F–öâGfæ6W2g&öÒf—'7E÷6VVâFòæ÷ræB—2FFVBFð¢ÆÂ6ö×ÆWFVBvVæW&F–öç2à¢"" ¢&–æF–æw3¢Æ—7E·GWÆU·7G"ÂfÆöEÕÒÒµÐ¢vçFVE÷6–G3¢6WE·7G%ÒÒ6WB‚¢f÷"—FVÒ–â—FV×3 ¢6–BÒöæöFUö6Æ–VçEöÆöu÷6W76–öåö–B†—FVÒ¢vVæW&F–öåöf—'7BÒöæöFUö6Æ–VçEöÆöuövVæW&F–öåöWö6‚†—FVÒ¢ÖW76vRÒ7G"†—FVÒævWB‚vÖW76vRr’÷"rr’ç7G&—‚’æÆ÷vW"‚¢–bvVæW&F–öåöf—'7BÃÒæBÖW76vRÓÒwÆ–&6²6W76–öâ7F'FVBs ¢vVæW&F–öåöf—'7BÒöæöFUö6Æ–VçEöÆöuöWfVçEöWö6‚†—FVÒ¢2&W6W'fRF†RW†—7F–ær'VÆRF†BöæÇ’&÷rW‡Æ–6—FÇ’&÷VæBFò¢2Æ–&6²6W76–öâ&V6V—fW26W76–öâvRâ÷&F–æ'’WF‚ö6FÆör&÷w2Fð¢2æ÷B–æ†W&—BGW&F–öâ§W7B&V6W6RF†W’†VâFò6''’F†R6ÖR4”Bà¢–bæ÷B6–B÷"vVæW&F–öåöf—'7BÃÒ ¢&–æF–æw2æVæB‚‚rrÂã’¢6öçF–çVP¢&–æF–æw2æVæB‚‡6–BÂvVæW&F–öåöf—'7B’¢vçFVE÷6–G2æFB‡6–B ¢–bæ÷BvçFVE÷6–G3 ¢&WGW&â´æöæRf÷"ò–â—FV×5Ð ¢†—7F÷'•÷&÷w3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢7F—fU÷&÷w3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢–bæöFU÷&VF—2—2æ÷BæöæRæBæöFU÷&VF—2æf–Æ&ÆS ¢G'“ ¢†—7F÷'•÷&÷w2ÒæöFU÷&VF—2çf–WvW%÷6W76–öåö†—7F÷'•ö'•÷6–B‡6÷'FVB‡vçFVE÷6–G2’¢W†6WB'VçF–ÖTW'&÷# ¢†—7F÷'•÷&÷w2ÒµÐ¢G'“ ¢7F—fU÷&÷w2ÒæöFU÷&VF—2çf–WvW%÷6W76–öç5ö'•÷6–B‡6÷'FVB‡vçFVE÷6–G2’Âd”UtU%õEDÂ¢W†6WB'VçF–ÖTW'&÷# ¢7F—fU÷&÷w2ÒµÐ ¢–bæ÷B†—7F÷'•÷&÷w3 ¢v—F‚ÖævW"æÆö6³ ¢f÷"6–B–âvçFVE÷6–G3 ¢f÷"&÷r–âÆ—7B†ÖævW"çf–WvW%÷6W76–öåö†—7F÷'’ævWB‡6–B’÷"µÒ“ ¢†—7F÷'•÷&÷w2æVæB‡°¢w6–Bs¢6–BÀ¢vf—'7E÷6VVåöWö6‚s¢fÆöB‡&÷rævWB‚vf—'7E÷6VVåöWö6‚r’÷"ã’À¢vÆ7E÷6VVåöWö6‚s¢fÆöB‡&÷rævWB‚vÆ7E÷6VVåöWö6‚r’÷"ã’À¢Ò ¢2ÖW&vR&WF–æVB†—7F÷'’v—F‚7F—fRfÆÆ&6²&÷w2âöæ6R4”B†2&WF–æV@¢2†—7F÷'’Â&VfW"F†B†—7F÷'’W†6ÇW6—fVÇ“¢F†R7F—fRf–WvW"†6‚¶VW2—G0¢2÷&–v–æÂf—'7E÷6VVåöWö6‚f÷"6WfW&ÂÖ–çWFW2æBÖ’F†W&Vf÷&R6''’¢27FÆRf—'7E÷6VVâ7&÷72F—66öææV7B÷&V6öææV7BâF†R6öçG&öÂö'6W'fW"w0¢2†—7F÷'’7&VFW2F†R6÷'&V7BæWrvVæW&F–öâgFW"d”UtU%õEDÂÂ6òÖ—†–ærF†P¢27FÆR7F—fRf—'7E÷6VVâ&6²–âv÷VÆB–æ6÷'&V7FÇ’6÷VçBF†RöffÆ–æRvà¢vVæW&F–öç3¢F–7E·7G"ÂÆ—7E¶F–7E·7G"ÂfÆöEÕÕÒÒ·6–C¢µÒf÷"6–B–âvçFVE÷6–G7Ð¢†—7F÷'•÷6–G2Ò·7G"‡&÷rævWB‚w6–Br’÷"rr’f÷"&÷r–â†—7F÷'•÷&÷w2–b7G"‡&÷rævWB‚w6–Br’÷"rr—Ð¢ÖW&vVE÷&÷w2ÒÆ—7B††—7F÷'•÷&÷w2’²°¢&÷rf÷"&÷r–â7F—fU÷&÷w2–b7G"‡&÷rævWB‚w6–Br’÷"rr’æ÷B–â†—7F÷'•÷6–G0¢Ð¢f÷"&÷r–âÖW&vVE÷&÷w3 ¢6–BÒ7G"‡&÷rævWB‚w6–Br’÷"rr¢–b6–Bæ÷B–âvVæW&F–öç3 ¢6öçF–çVP¢G'“ ¢f—'7BÒfÆöB‡&÷rævWB‚vf—'7E÷6VVåöWö6‚r’÷"ã¢Æ7BÒfÆöB‡&÷rævWB‚vÆ7E÷6VVåöWö6‚r’÷"ã¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢6öçF–çVP¢–bf—'7BÃÒ÷"Æ7BÂf—'7C ¢6öçF–çVP¢W†—7F–ærÒæW‡B‚‡‚f÷"‚–âvVæW&F–öç5·6–EÒ–b'2†fÆöB‡…²vf—'7BuÒ’Òf—'7B’ÂãR’ÂæöæR¢–bW†—7F–ær—2æöæS ¢vVæW&F–öç5·6–EÒæVæB‡²vf—'7Bs¢f—'7BÂvÆ7Bs¢Æ7GÒ¢VÇ6S ¢W†—7F–æu²vÆ7BuÒÒÖ‚†fÆöB†W†—7F–æu²vÆ7BuÒ’ÂÆ7B ¢f÷"&÷w2–âvVæW&F–öç2çfÇVW2‚“ ¢&÷w2ç6÷'B†¶W“ÖÆÖ&F&÷s¢fÆöB‡&÷u²vf—'7BuÒ’ ¢æ÷uöWö6‚ÒF–ÖRçF–ÖR‚¢&W6WE÷6V6öæG2ÒÖævW"æ6Æ–VçE÷6W76–öå÷&W6WE÷6V6öæG2‚¢6ÇW7FW%÷7FFW3¢F–7E·GWÆU·7G"ÂfÆöEÒÂF–7E·7G"Âç•ÕÒÒ·Ð¢f÷"6–BÂ&÷w2–âvVæW&F–öç2æ—FV×2‚“ ¢f÷"6ÇW7FW"–âöæöFU÷6W76–öåövVæW&F–öåö6ÇW7FW'2‡&÷w2Â&W6WE÷6V6öæG2“ ¢–bæ÷B6ÇW7FW# ¢6öçF–çVP¢6ÇW7FW%÷7F'BÒfÆöB†6ÇW7FW%³Õ²vf—'7BuÒ¢F÷FÅ÷6V6öæG2Òã ¢öæÆ–æRÒfÇ6P¢f÷"&÷r–â6ÇW7FW# ¢f—'7BÒfÆöB‡&÷u²vf—'7BuÒ¢Æ7BÒfÆöB‡&÷u²vÆ7BuÒ¢&÷uööæÆ–æRÒæ÷uöWö6‚ÒÆ7BÃÒd”UtU%õEDÀ¢–b&÷uööæÆ–æS ¢öæÆ–æRÒG'VP¢F÷FÅ÷6V6öæG2³ÒÖ‚ƒãÂæ÷uöWö6‚Òf—'7B¢VÇ6S ¢F÷FÅ÷6V6öæG2³ÒÖ‚ƒãÂÆ7BÒf—'7B¢6ÇW7FW%÷7FFW5²‡6–BÂ6ÇW7FW%÷7F'B•ÒÒ°¢vGW&F–öå÷6V6öæG2s¢Ö‚ƒÂ–çB‡F÷FÅ÷6V6öæG2’’À¢vöæÆ–æRs¢öæÆ–æRÀ¢w6W76–öåö–Bs¢6–BÀ¢Ð ¢&W6öÇfVC¢Æ—7E¶F–7E·7G"Âç•ÒÂæöæUÒÒµÐ¢f÷"6–BÂvVæW&F–öåöf—'7B–â&–æF–æw3 ¢–bæ÷B6–C ¢&W6öÇfVBæVæB„æöæR¢6öçF–çVP¢6æF–FFW2Ò²‡7F'BÂ7FFR’f÷"‡7FFU÷6–BÂ7F'B’Â7FFR–â6ÇW7FW%÷7FFW2æ—FV×2‚’–b7FFU÷6–BÓÒ6–EÐ¢–bæ÷B6æF–FFW3 ¢&W6öÇfVBæVæB„æöæR¢6öçF–çVP¢2&–æBF†RÆör&÷rFòF†RÆöv–6Â6ÇW7FW"6öçF–æ–ær—G2Æ–&6°¢2vVæW&F–öââæWrçF‡&W6†öÆB&V6öææV7BF†W&Vf÷&R7F'G2B£ ¢2WfVâF†÷Vv‚F†R7F&ÆRgÒöFWbÒ4”B—G6VÆb—2Væ6†ævVBà¢6†÷6Vå÷7F'BÂ6†÷6Vå÷7FFRÒÖ–â†6æF–FFW2Â¶W“ÖÆÖ&F—#¢'2†fÆöB‡—%³Ò’ÒvVæW&F–öåöf—'7B’¢&W6öÇfVBæVæB†F–7B†6†÷6Vå÷7FFR’¢&WGW&â&W6öÇfV@ ¤ævWB‚r÷æVÂöÖævRöÆöw2rÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçEöÆöw2€¢&WVW7C¢&WVW7BÀ¢Æöu÷G—S¢7G"Òv7F—f—G’rÀ¢Æ–Ö—C¢7G"ÒsrÀ¢ÆWfVÃ¢7G"ÒrrÀ¢¢7G"ÒrrÀ¢vS¢7G"ÒsrÀ¢6÷'C¢7G"ÒwF–ÖRrÀ¢÷&FW#¢7G"ÒvFW62rÀ¢ÖW76vS¢7G"ÒrrÀ¢“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvÆöw2çf–Wrr¢2²‚v7F—f—G’rÂt7F—f—G’Æörr’Â‚v66W72rÂt66W72Æörr’Â‚v6Æ–VçBrÂt6Æ–VçBÆörr’Â‚w7—7FVÒrÂu7—7FVÒÆörr•Ð¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõU4U%ôäõôDUD”Å5õc“•#s¢6Æ–VçBÆör6†÷w2W6W"–ç7FVBöbFWF–Ç2à¢6VÆV7FVE÷G—RÒÆöu÷G—R–bÆöu÷G—R–â²v66W72rÂv6Æ–VçBrÂw7—7FVÒrÂv7F—f—G’wÒVÇ6Rv7F—f—G’p¢25E$TÔdõ$tUôäôDUõäTÅôÄôuõ4T$4…õc3s¢7FæFÆöæRæöFRÆöw2W6RF†P¢26ÖRgVÆÂ&–ærÖ'VffW"6V&6‚6VÖçF–722Ö–âÓâ&VÖ÷FRæöFRVW&–W2à¢25E$TÔdõ$tUôäôDUõäTÅôÄôuôÄ•dUô¤…õc3ƒ¢F†R7FæFÆöæRæVÂ¶VW2F†—0¢2VæGö–çB2F†R6÷W&6RöbG'WF‚v†–ÆRF†R'&÷w6W"7v2&W7VÇBg&vÖVçG0¢2–âÆ6Rf÷"WfW'’¶W—7G&ö¶RÂfö–F–ærgVÆÂ×vR&VÆöBöfö7W2Æ÷72à¢6VÆV7FVE÷6V&6‚Ò""æ¦ö–â‡7G"‡÷"""’ç7G&—‚’ç7Æ—B‚’•³£#CÐ¢25E$TÔdõ$tUôäôDUô4ôåDU…ETÅôÄôuôd”ÅDU%5õcc¢f—†VB×66÷RF'2öÖ—B&VGVæFçB66÷R6öÇVÖç2æBÆWfVÂ6†ö–6W26öÖRöæÇ’g&öÒF†—2F"à¢25E$TÔdõ$tUôäôDUôÄôuôÄ”Ô•Eõ4TÄT5Dõ%õcƒC ¢25E$TÔdõ$tUôäôDUôÄôuôUDõôd”ÅDU%ôU…DTäDTEôÄ”Ô•EõcƒS ¢25E$TÔdõ$tUôäôDUôÄôuõt”äD”ôåõ4õ%EôÄUdTÅõc“3 ¢26†÷r—2vR6—¦S²ÆÂÖF6†–ær&÷w2&VÖ–â&V6†&ÆRâÆWfVÂf–ÇFW&–æp¢2æB6Æ–6¶&ÆR6W'fW"×6–FR6÷'F–ær&R6†&VB'’WfW'’æöFRÆörF"à¢Æ–Ö—Eö÷F–öç2Ò²ss¢Âs#s¢#ÂsSs¢SÂss¢Âs#s¢#ÂsSs¢SÂss¢ÂvÆÂs¢Ð¢6VÆV7FVEöÆ–Ö—BÒ7G"†Æ–Ö—B÷"sr’ç7G&—‚’æÆ÷vW"‚¢–b6VÆV7FVEöÆ–Ö—Bæ÷B–âÆ–Ö—Eö÷F–öç3 ¢6VÆV7FVEöÆ–Ö—BÒsp¢vUöÆ–Ö—BÒ–çB†Æ–Ö—Eö÷F–öç5·6VÆV7FVEöÆ–Ö—EÒ¢6VÆV7FVEöÆWfVÂÒ7G"†ÆWfVÂ÷"rr’ç7G&—‚’æÆ÷vW"‚¢–b6VÆV7FVEöÆWfVÂæ÷B–â²rrÂv–æfòrÂwv&æ–ærrÂvW'&÷"wÓ ¢6VÆV7FVEöÆWfVÂÒrp¢ÆÆ÷vVE÷6÷'G2Ò²wF–ÖRrÂvÆWfVÂrÂw66÷RrÂv—rÂwW6W"rÂvÖW76vRrÂvFWF–Ç2wÐ¢6VÆV7FVE÷6÷'BÒ7G"‡6÷'B÷"wF–ÖRr’ç7G&—‚’æÆ÷vW"‚¢–b6VÆV7FVE÷6÷'Bæ÷B–âÆÆ÷vVE÷6÷'G3 ¢6VÆV7FVE÷6÷'BÒwF–ÖRp¢–b6VÆV7FVE÷G—RÓÒv6Æ–VçBræB6VÆV7FVE÷6÷'B–â²vFWF–Ç2rÂw66÷RwÓ ¢6VÆV7FVE÷6÷'BÒwF–ÖRp¢–b6VÆV7FVE÷G—RÓÒv66W72ræB6VÆV7FVE÷6÷'BÓÒw66÷Rs ¢6VÆV7FVE÷6÷'BÒwF–ÖRp¢6VÆV7FVEö÷&FW"Òv62r–b7G"†÷&FW"÷"rr’ç7G&—‚’æÆ÷vW"‚’ÓÒv62rVÇ6RvFW62p¢G'“ ¢&WVW7FVE÷vRÒÖ‚ƒÂ–çB‡7G"‡vR÷"sr’ç7G&—‚’’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&WVW7FVE÷vRÒ ¢66÷W2Ò²v66W72s¢²vWF‚wÒÂv6Æ–VçBs¢²v6Æ–VçBwÒÂw7—7FVÒs¢²w7—7FVÒrÂvæöFRrÂwWFFRrÂw6WGF–æw2w×ÒævWB‡6VÆV7FVE÷G—R¢WfVçEöÆöw5÷6æ6†÷BÒöæöFUöföÆE÷Æ–&6µ÷6W76–öå÷&÷w2†ÖævW"æWfVçEöÆöw5÷6æ6†÷B‚’’–b6VÆV7FVE÷G—RÓÒv6Æ–VçBrVÇ6RÖævW"æWfVçEöÆöw5÷6æ6†÷B‚¢G—UöÆöw2Ò°¢‚f÷"‚–âWfVçEöÆöw5÷6æ6†÷@¢–b‡7G"‡‚ævWB‚w66÷Rr’÷"w7—7FVÒr’–â66÷W2–b66÷W2—2æ÷BæöæRVÇ6R7G"‡‚ævWB‚w66÷Rr’÷"w7—7FVÒr’æ÷B–â²vWF‚rÂv6Æ–VçBrÂw7—7FVÒrÂvæöFRrÂwWFFRrÂw6WGF–æw2wÒ¢Ð¢&W6VçEöÆWfVÇ2Ò·fÇVRf÷"fÇVR–â²v–æfòrÂwv&æ–ærrÂvW'&÷"uÒ–bç’‡7G"‡‚ævWB‚vÆWfVÂr’÷"v–æfòr’ç7G&—‚’æÆ÷vW"‚’ÓÒfÇVRf÷"‚–âG—UöÆöw2•Ð¢–b6VÆV7FVEöÆWfVÂæB6VÆV7FVEöÆWfVÂæ÷B–â&W6VçEöÆWfVÇ3 ¢6VÆV7FVEöÆWfVÂÒrp¢6VÆV7FVEöÆöw5öÆÂÒ°¢‚f÷"‚–âG—UöÆöw0¢–b†æ÷B6VÆV7FVEöÆWfVÂ÷"7G"‡‚ævWB‚vÆWfVÂr’÷"v–æfòr’ç7G&—‚’æÆ÷vW"‚’ÓÒ6VÆV7FVEöÆWfVÂ¢æB†æ÷B6VÆV7FVE÷6V&6‚÷"öæöFUöÆöuöÖF6†W5÷6V&6‚‡‚Â6VÆV7FVE÷6V&6‚’¢Ð ¢FVbF—7Æ•÷F–ÖR‡fÇVS¢ö&¦V7B’Óâ7G# ¢&rÒ7G"‡fÇVR÷"rr’ç7G&—‚¢G'“ ¢'6VBÒFFWF–ÖRæg&öÖ—6öf÷&ÖB‡&rç&WÆ6R‚u¢rÂr³£r’¢&WGW&â'6VBæ7F–ÖW¦öæR‚’ç7G&gF–ÖR‚rU’ÒVÒÒVBTƒ¢TÓ¢U2r¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&WGW&â&p ¢FVbF–ÖW7F×÷fÇVR‡fÇVS¢ö&¦V7B’ÓâfÆöC ¢&rÒ7G"‡fÇVR÷"rr’ç7G&—‚¢G'“ ¢'6VBÒFFWF–ÖRæg&öÖ—6öf÷&ÖB‡&rç&WÆ6R‚u¢rÂr³£r’¢–b'6VBçG¦–æfò—2æöæS ¢'6VBÒ'6VBç&WÆ6R‡G¦–æfó×F–ÖW¦öæRçWF2¢&WGW&âfÆöB‡'6VBçF–ÖW7F×‚’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Âõ4W'&÷"Â÷fW&fÆ÷tW'&÷"“ ¢&WGW&âã  ¢25E$TÔdõ$tUôäôDUôÄôuôD•$T5EôDUD”Å5ô•õcs# ¢FVbÆöuö—‡fÇVS¢ö&¦V7B’Óâ7G# ¢&rÒ7G"‡fÇVR÷"rr’ç7G&—‚¢–bæ÷B&s ¢&WGW&ârp¢G'“ ¢'6VBÒ§6öâæÆöG2‡&r¢W†6WBW†6WF–öã ¢'6VBÒæöæP¢–b—6–ç7Fæ6R‡'6VBÂF–7B“ ¢f÷"æÖR–â‚v—rÂv6Æ–VçEö—rÂw&VÖ÷FUö—rÂvFG&W72r“ ¢6æF–FFRÒ7G"‡'6VBævWB†æÖR’÷"rr’ç7G&—‚¢–b6æF–FFS ¢&WGW&â6æF–FFU³£#Ð¢ÖF6‚Ò&Rç6V&6‚‡""ƒó¥çÅ³²ÇµÇ5Ò’ƒó¦—Æ6Æ–VçEö—Ç&VÖ÷FUö—ÆFG&W72•Ç2¥³£ÕÕÇ2¥µÂ"uÓò…µã²ÂuÂ'ÕÇ5Ò²’"Â&rÂfÆw3×&Rä’¢&WGW&âÖF6‚æw&÷Wƒ•³£#Ò–bÖF6‚VÇ6Rrp ¢FVbÆöu÷W6W"†—FVÓ¢F–7E·7G"Âç•Ò’Óâ7G# ¢F—&V7BÒ7G"†—FVÒævWB‚wW6W"r’÷"rr’ç7G&—‚¢–bF—&V7C ¢&WGW&âF—&V7E³£#Ð¢&rÒ7G"†—FVÒævWB‚vFWF–Ç2r’÷"rr’ç7G&—‚¢–b&s ¢G'“ ¢'6VBÒ§6öâæÆöG2‡&r¢W†6WBW†6WF–öã ¢'6VBÒæöæP¢–b—6–ç7Fæ6R‡'6VBÂF–7B“ ¢f÷"æÖR–â‚wW6W"rÂwW6W&æÖRrÂv6Æ–VçE÷W6W"rÂv7F÷"r“ ¢6æF–FFRÒ7G"‡'6VBævWB†æÖR’÷"rr’ç7G&—‚¢–b6æF–FFS ¢&WGW&â6æF–FFU³£#Ð¢ÖF6‚Ò&Rç6V&6‚‡""ƒó¥çÅ³²ÅÇ5Ò’ƒó§W6W'ÇW6W&æÖWÆ6Æ–VçE÷W6W'Æ7F÷"•Ç2¥³£ÕÕÇ2¥µÂ"uÓò…µã²ÅÂ"uÒ²’"Â&rÂfÆw3×&Rä’¢–bÖF6ƒ ¢&WGW&âÖF6‚æw&÷Wƒ’ç7G&—‚•³£#Ð¢&WGW&âtwVW7Br–b7G"†—FVÒævWB‚w66÷Rr’÷"rr’ç7G&—‚’æÆ÷vW"‚’ÓÒv6Æ–VçBrVÇ6Rrp ¢FVb6÷'E÷fÇVR†—FVÓ¢F–7E·7G"Âç•Ò“ ¢–b6VÆV7FVE÷6÷'BÓÒwF–ÖRs ¢&WGW&âF–ÖW7F×÷fÇVR†—FVÒævWB‚wF–ÖRr’¢–b6VÆV7FVE÷6÷'BÓÒvÆWfVÂs ¢6WfW&—G’Ò²wG&6Rs¢ÂvFV'Vrs¢Âv–æfòs¢"Âvæ÷F–6Rs¢2Âwv&æ–ærs¢BÂwv&âs¢BÂvW'&÷"s¢RÂv7&—F–6Âs¢bÂvfFÂs¢wÐ¢æÖRÒ7G"†—FVÒævWB‚vÆWfVÂr’÷"v–æfòr’æÆ÷vW"‚¢&WGW&â‡6WfW&—G’ævWB†æÖRÂ“’’ÂæÖR¢–b6VÆV7FVE÷6÷'BÓÒv—s ¢&WGW&âÆöuö—†—FVÒævWB‚vFWF–Ç2r’’æ66VföÆB‚¢–b6VÆV7FVE÷6÷'BÓÒwW6W"s ¢&WGW&âÆöu÷W6W"†—FVÒ’æ66VföÆB‚¢&WGW&â7G"†—FVÒævWB‡6VÆV7FVE÷6÷'B’÷"rr’æ66VföÆB‚ ¢6VÆV7FVEöÆöw5öÆÂç6÷'B†¶W“ÖÆÖ&F—FVÓ¢‡6÷'E÷fÇVR†—FVÒ’ÂF–ÖW7F×÷fÇVR†—FVÒævWB‚wF–ÖRr’’’Â&WfW'6SÒ‡6VÆV7FVEö÷&FW"ÓÒvFW62r’¢F÷FÅöÆöw2ÒÆVâ‡6VÆV7FVEöÆöw5öÆÂ¢vUö6÷VçBÒ–bvUöÆ–Ö—BÃÒVÇ6RÖ‚ƒÂ‡F÷FÅöÆöw2²vUöÆ–Ö—BÒ’òòvUöÆ–Ö—B¢6VÆV7FVE÷vRÒÖ–â‡&WVW7FVE÷vRÂvUö6÷VçB¢–bvUöÆ–Ö—BÃÒ ¢6VÆV7FVEöÆöw2Ò6VÆV7FVEöÆöw5öÆÀ¢VÇ6S ¢öfg6WBÒ‡6VÆV7FVE÷vRÒ’¢vUöÆ–Ö—@¢6VÆV7FVEöÆöw2Ò6VÆV7FVEöÆöw5öÆÅ¶öfg6WC¦öfg6WB²vUöÆ–Ö—EÐ¢6†÷våög&öÒÒ–bæ÷BF÷FÅöÆöw2VÇ6Rƒ–bvUöÆ–Ö—BÃÒVÇ6R‚‡6VÆV7FVE÷vRÒ’¢vUöÆ–Ö—B²’¢6†÷vå÷FòÒ–bæ÷BF÷FÅöÆöw2VÇ6R‡F÷FÅöÆöw2–bvUöÆ–Ö—BÃÒVÇ6RÖ–â‡F÷FÅöÆöw2Â6†÷våög&öÒ²ÆVâ‡6VÆV7FVEöÆöw2’Ò’ ¢–b6VÆV7FVE÷G—RÓÒv6Æ–VçBs ¢GW&F–öå÷7FFW2ÒöæöFUö6Æ–VçEöÆöuöGW&F–öå÷7FFW2‡6VÆV7FVEöÆöw2¢&÷w2Òrræ¦ö–â€¢bsÇG#ãÇFB6Æ73Ò&æöFRÖÆör×F–ÖR#ç¶‡FÖÂæW66R†F—7Æ•÷F–ÖR‡‚ævWB‚'F–ÖR"’’—ÓÂ÷FCâp¢bsÇFCãÇ7â6Æ73Ò&æöFRÖÆörÖ6†—æöFRÖÆörÖÆWfVÂ×¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’æÆ÷vW"‚’—Ò#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’çWW"‚’—ÓÂ÷7ããÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖ—#ç¶‡FÖÂæW66R†Æöuö—‡‚ævWB‚&FWF–Ç2"’’÷".(	B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆör×W6W"#ç¶‡FÖÂæW66R†Æöu÷W6W"‡‚’÷"$wVW7B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆör×6W76–öâ"F—FÆSÒ'¶‡FÖÂæW66R…öæöFUö6Æ–VçEöÆöu÷6W76–öåö–B‡‚’ÂV÷FSÕG'VR—Ò#ç¶‡FÖÂæW66R…öæöFUö6Æ–VçEöÆöu÷6W76–öåö–B‡‚’÷".(	B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖGW&F–öâ#ç¶‡FÖÂæW66R…öæöFUöGW&F–öåöÆ&VÂ‡7FFRævWB‚&GW&F–öå÷6V6öæG2"’Â&ööÂ‡7FFRævWB‚&öæÆ–æR"’’’–b7FFRVÇ6R.(	B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖÖW76vR#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÖW76vR"’÷"""’—ÓÂ÷FCãÂ÷G#âp¢f÷"‚Â7FFR–â¦—‡6VÆV7FVEöÆöw2ÂGW&F–öå÷7FFW2¢’÷"sÇG#ãÇFB6öÇ7ãÒ#r"6Æ73Ò&V×G’#äæòÆöw2f÷VæB–âF†—26V7F–öâãÂ÷FCãÂ÷G#âp¢VÆ–b6VÆV7FVE÷G—RÓÒv66W72s ¢&÷w2Òrræ¦ö–â€¢bsÇG#ãÇFB6Æ73Ò&æöFRÖÆör×F–ÖR#ç¶‡FÖÂæW66R†F—7Æ•÷F–ÖR‡‚ævWB‚'F–ÖR"’’—ÓÂ÷FCâp¢bsÇFCãÇ7â6Æ73Ò&æöFRÖÆörÖ6†—æöFRÖÆörÖÆWfVÂ×¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’æÆ÷vW"‚’—Ò#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’çWW"‚’—ÓÂ÷7ããÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖ—#ç¶‡FÖÂæW66R†Æöuö—‡‚ævWB‚&FWF–Ç2"’’÷".(	B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖÖW76vR#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÖW76vR"’÷"""’—ÓÂ÷FCâp¢bsÇFCãÆF—b6Æ73Ò&æöFRÖÆörÖFWF–Ç2ÖF—&V7B#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&FWF–Ç2"’÷".(	B"’—ÓÂöF—cãÂ÷FCãÂ÷G#âp¢f÷"‚–â6VÆV7FVEöÆöw0¢’÷"sÇG#ãÇFB6öÇ7ãÒ#R"6Æ73Ò&V×G’#äæòÆöw2f÷VæB–âF†—26V7F–öâãÂ÷FCãÂ÷G#âp¢VÇ6S ¢&÷w2Òrræ¦ö–â€¢bsÇG#ãÇFB6Æ73Ò&æöFRÖÆör×F–ÖR#ç¶‡FÖÂæW66R†F—7Æ•÷F–ÖR‡‚ævWB‚'F–ÖR"’’—ÓÂ÷FCâp¢bsÇFCãÇ7â6Æ73Ò&æöFRÖÆörÖ6†—æöFRÖÆörÖÆWfVÂ×¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’æÆ÷vW"‚’—Ò#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÆWfVÂ"’÷"&–æfò"’çWW"‚’—ÓÂ÷7ããÂ÷FCâp¢bsÇFCãÇ7â6Æ73Ò&æöFRÖÆör×66÷R#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚'66÷R"’÷"'7—7FVÒ"’—ÓÂ÷7ããÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖ—#ç¶‡FÖÂæW66R†Æöuö—‡‚ævWB‚&FWF–Ç2"’’÷".(	B"—ÓÂ÷FCâp¢bsÇFB6Æ73Ò&æöFRÖÆörÖÖW76vR#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&ÖW76vR"’÷"""’—ÓÂ÷FCâp¢bsÇFCãÆF—b6Æ73Ò&æöFRÖÆörÖFWF–Ç2ÖF—&V7B#ç¶‡FÖÂæW66R‡7G"‡‚ævWB‚&FWF–Ç2"’÷".(	B"’—ÓÂöF—cãÂ÷FCãÂ÷G#âp¢f÷"‚–â6VÆV7FVEöÆöw0¢’÷"sÇG#ãÇFB6öÇ7ãÒ#b"6Æ73Ò&V×G’#äæòÆöw2f÷VæB–âF†—26V7F–öâãÂ÷FCãÂ÷G#âp   ¢ÆW'BÒbsÆF—b6Æ73Öö³ç¶‡FÖÂæW66R†ÖW76vR—ÓÂöF—câr–bÖW76vRVÇ6Rrp¢6ÆV%öÆ&VÂÒ²v7F—f—G’s¢t7F—f—G’ÆörrÂv66W72s¢t66W72ÆörrÂv6Æ–VçBs¢t6Æ–VçBÆörrÂw7—7FVÒs¢u7—7FVÒÆörwÒævWB‡6VÆV7FVE÷G—RÂt7F—f—G’Æörr¢6ÆV%÷VW'’ÒW&ÆÆ–"ç'6RçW&ÆVæ6öFR‡²vÆöu÷G—Rs¢6VÆV7FVE÷G—WÒ¢6ÆV%ö7F–öâÒ€¢bsÆf÷&ÒÖWF†öCÒ'÷7B"7F–öãÒ"÷æVÂöÖævRöÆöw2ö6ÆV#÷¶6ÆV%÷VW'—Ò"p¢bvöç7V&Ö—CÒ'&WGW&â6öæf—&Ò…Ât6ÆV"¶‡FÖÂæW66R†6ÆV%öÆ&VÂ—ÒöæÇ“õÂr’#âp¢bsÆ'WGFöâ6Æ73Ò&FævW"#ä6ÆV#Âö'WGFöããÂöf÷&Óâp¢–bö†5÷æVÅ÷W&Ö—76–öâ‡W6W"ÂvÆöw2æÖævRr’VÇ6Rrp¢¢F%ö6öÖÖöâÒW&ÆÆ–"ç'6RçW&ÆVæ6öFR‡²vÆ–Ö—Bs¢6VÆV7FVEöÆ–Ö—BÂvÆWfVÂs¢6VÆV7FVEöÆWfVÂÂws¢6VÆV7FVE÷6V&6‚Âw6÷'Bs¢6VÆV7FVE÷6÷'BÂv÷&FW"s¢6VÆV7FVEö÷&FW'Ò¢25E$TÔdõ$tUôäôDUõäTÅôÄôuõ$•dDUôDE$U54$%õc3“¢–æ†W&—FVB&—f7’Ö&¶W"à¢25E$TÔdõ$tUôäôDUõäTÅôÄôuôd•„TEô4äôä”4ÅôDE$U54$%õcC¢Æöw2æWfW"w&—FW0¢2—G2–çFW&æÂ&÷WFR÷VW'’–çFòF†Rf—6–&ÆR'&÷w6W"&#²F†RæöFRæVÂw0¢26æöæ–6Â66W72&ö÷B&VÖ–ç2÷væVB'’F†RvÆö&Â†–FFVâ×&÷WFR&÷WFW"à¢F'2ÒsÆæb6Æ73Ò&æöFRÖÆör×F'2"&–ÖÆ&VÃÒ$ÆörG—R"FFÖæöFRÖÆör×F'3âr²rræ¦ö–â€¢bsÆ6Æ73Ò&æöFRÖÆör×F"²&7F—fR"–b¶W’ÓÒ6VÆV7FVE÷G—RVÇ6R"'Ò"‡&VcÒ"÷æVÂöÖævRöÆöw3öÆöu÷G—S×¶¶W—Ò#ç¶Æ&VÇÓÂöâp¢f÷"¶W’ÂÆ&VÂ–â²‚v7F—f—G’rÂt7F—f—G’Æörr’Â‚v66W72rÂt66W72Æörr’Â‚v6Æ–VçBrÂt6Æ–VçBÆörr’Â‚w7—7FVÒrÂu7—7FVÒÆörr•Ð¢’²sÂöæcâp¢Æ–Ö—Eö÷F–öç5ö‡FÖÂÒrræ¦ö–â€¢bsÆ÷F–öâfÇVSÒ'·fÇVWÒ"²'6VÆV7FVB"–bfÇVRÓÒ6VÆV7FVEöÆ–Ö—BVÇ6R"'Óç²$ÄÂ"–bfÇVRÓÒ&ÆÂ"VÇ6RfÇVWÓÂö÷F–öãâp¢f÷"fÇVR–â²srÂs#rÂsSrÂsrÂs#rÂsSrÂsrÂvÆÂuÐ¢¢ÆWfVÅ÷fÇVW2Ò&W6VçEöÆWfVÇ0¢ÆWfVÅöf–ÇFW%öæVVFVBÒÆVâ†ÆWfVÅ÷fÇVW2’â¢ÆWfVÅö÷F–öç5ö‡FÖÂÒsÆ÷F–öâfÇVSÒ"#äÆÂÆWfVÇ3Âö÷F–öãâr²rræ¦ö–â€¢bsÆ÷F–öâfÇVSÒ'¶‡FÖÂæW66R‡fÇVRÂV÷FSÕG'VR—Ò"²'6VÆV7FVB"–bfÇVRÓÒ6VÆV7FVEöÆWfVÂVÇ6R"'Óç¶‡FÖÂæW66R‡fÇVRçF—FÆR‚’—ÓÂö÷F–öãâp¢f÷"fÇVR–âÆWfVÅ÷fÇVW0¢¢v–æF–öå÷VW'’ÒW&ÆÆ–"ç'6RçW&ÆVæ6öFR‡²vÆöu÷G—Rs¢6VÆV7FVE÷G—RÂvÆ–Ö—Bs¢6VÆV7FVEöÆ–Ö—BÂvÆWfVÂs¢6VÆV7FVEöÆWfVÂÂws¢6VÆV7FVE÷6V&6‚Âw6÷'Bs¢6VÆV7FVE÷6÷'BÂv÷&FW"s¢6VÆV7FVEö÷&FW'Ò¢&We÷vRÒÖ‚ƒÂ6VÆV7FVE÷vRÒ¢æW‡E÷vRÒÖ–â‡vUö6÷VçBÂ6VÆV7FVE÷vR²¢v–æF–öâÒrp¢v–æF–öåö†÷7BÒsÆF—bFFÖæöFRÖÆör×v–æF–öâÖ†÷7CãÂöF—câp¢–b6VÆV7FVEöÆ–Ö—BÒvÆÂræBvUö6÷VçBâ ¢&WeöÆ–æ²ÒbsÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRöÆöw3÷·v–æF–öå÷VW'—ÒgvS×·&We÷vWÒ#å&Wf–÷W3Âöâr–b6VÆV7FVE÷vRâVÇ6RsÇ7â6Æ73Ò&'FâæöFRÖÆör×vRÖF—6&ÆVB#å&Wf–÷W3Â÷7ãâp¢æW‡EöÆ–æ²ÒbsÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRöÆöw3÷·v–æF–öå÷VW'—ÒgvS×¶æW‡E÷vWÒ#äæW‡CÂöâr–b6VÆV7FVE÷vRÂvUö6÷VçBVÇ6RsÇ7â6Æ73Ò&'FâæöFRÖÆör×vRÖF—6&ÆVB#äæW‡CÂ÷7ãâp¢vU÷7F'BÒÖ‚ƒÂÖ–â‡6VÆV7FVE÷vRÒ"ÂÖ‚ƒÂvUö6÷VçBÒB’’¢vUöVæBÒÖ–â‡vUö6÷VçBÂvU÷7F'B²B¢vUöÆ–æ·2ÒsÆF—b6Æ73Ò&æöFRÖÆör×vRÖçVÖ&W'2#âr²rræ¦ö–â€¢bsÆ6Æ73Ò&'Fâ²&7F—fR"–bçVÖ&W"ÓÒ6VÆV7FVE÷vRVÇ6R"'Ò"‡&VcÒ"÷æVÂöÖævRöÆöw3÷·v–æF–öå÷VW'—ÒgvS×¶çVÖ&W'Ò#ç¶çVÖ&W'ÓÂöâp¢f÷"çVÖ&W"–â&ævR‡vU÷7F'BÂvUöVæB²¢’²sÂöF—câp¢v–æF–öâÒbsÆæb6Æ73Ò&æöFRÖÆör×v–æF–öâ"&–ÖÆ&VÃÒ$ÆörvW2#ç·&WeöÆ–æ·×·vUöÆ–æ·7ÓÇ7ãåvRÆ#ç·6VÆV7FVE÷vWÓÂö#âöbÆ#ç·vUö6÷VçGÓÂö#ãÂ÷7ãç¶æW‡EöÆ–æ·ÓÂöæcâp¢v–æF–öåö†÷7BÒsÆF—bFFÖæöFRÖÆör×v–æF–öâÖ†÷7Câr²v–æF–öâ²sÂöF—câp ¢–b6VÆV7FVE÷G—RÓÒv6Æ–VçBs ¢6÷'EöÆ&VÇ2Ò²‚wF–ÖRrÂuF–ÖRr’Â‚vÆWfVÂrÂtÆWfVÂr’Â‚v—rÂt•r’Â‚wW6W"rÂuW6W"r’Â‚vÖW76vRrÂtÖW76vRr•Ð¢VÆ–b6VÆV7FVE÷G—RÓÒv66W72s ¢6÷'EöÆ&VÇ2Ò²‚wF–ÖRrÂuF–ÖRr’Â‚vÆWfVÂrÂtÆWfVÂr’Â‚v—rÂt•r’Â‚vÖW76vRrÂtÖW76vRr’Â‚vFWF–Ç2rÂtFWF–Ç2r•Ð¢VÇ6S ¢6÷'EöÆ&VÇ2Ò²‚wF–ÖRrÂuF–ÖRr’Â‚vÆWfVÂrÂtÆWfVÂr’Â‚w66÷RrÂu66÷Rr’Â‚v—rÂt•r’Â‚vÖW76vRrÂtÖW76vRr’Â‚vFWF–Ç2rÂtFWF–Ç2r•Ð¢–b6VÆV7FVE÷G—RÓÒv6Æ–VçBs ¢6÷'F&ÆUö6Æ–VçBÒ²‚wF–ÖRrÂuF–ÖRr’Â‚vÆWfVÂrÂtÆWfVÂr’Â‚v—rÂt•r’Â‚wW6W"rÂuW6W"r•Ð¢†VFW%ö6VÆÇ2Òrræ¦ö–â€¢bsÇFƒãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&æöFRÖÆör×6÷'BÖ'WGFöâ²&7F—fR"–b6VÆV7FVE÷6÷'BÓÒ¶W’VÇ6R"'Ò"FFÖæöFRÖÆör×6÷'CÒ'¶¶W—Ò#ç¶Æ&VÇ×²‚")k""–b6VÆV7FVEö÷&FW"ÓÒ&62"VÇ6R")kÂ"’–b6VÆV7FVE÷6÷'BÓÒ¶W’VÇ6R"'ÓÂö'WGFöããÂ÷Fƒâp¢f÷"¶W’ÂÆ&VÂ–â6÷'F&ÆUö6Æ–Vç@¢¢†VFW%ö6VÆÇ2³ÒsÇFƒå6W76–öâ”CÂ÷Fƒâp¢†VFW%ö6VÆÇ2³ÒsÇFƒå6W76–öâvSÂ÷Fƒâp¢†VFW%ö6VÆÇ2³ÒbsÇFƒãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&æöFRÖÆör×6÷'BÖ'WGFöâ²&7F—fR"–b6VÆV7FVE÷6÷'BÓÒ&ÖW76vR"VÇ6R"'Ò"FFÖæöFRÖÆör×6÷'CÒ&ÖW76vR#äÖW76vW²‚")k""–b6VÆV7FVEö÷&FW"ÓÒ&62"VÇ6R")kÂ"’–b6VÆV7FVE÷6÷'BÓÒ&ÖW76vR"VÇ6R"'ÓÂö'WGFöããÂ÷Fƒâp¢VÇ6S ¢†VFW%ö6VÆÇ2Òrræ¦ö–â€¢bsÇFƒãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&æöFRÖÆör×6÷'BÖ'WGFöâ²&7F—fR"–b6VÆV7FVE÷6÷'BÓÒ¶W’VÇ6R"'Ò"FFÖæöFRÖÆör×6÷'CÒ'¶¶W—Ò#ç¶Æ&VÇ×²‚")k""–b6VÆV7FVEö÷&FW"ÓÒ&62"VÇ6R")kÂ"’–b6VÆV7FVE÷6÷'BÓÒ¶W’VÇ6R"'ÓÂö'WGFöããÂ÷Fƒâp¢f÷"¶W’ÂÆ&VÂ–â6÷'EöÆ&VÇ0¢¢25E$TÔdõ$tUôäôDUõäTÅôÄôuôUDõõ4T$4…ô¥5õc3p¢25E$TÔdõ$tUôäôDUõäTÅôÄôuôÄ•dUô¤…ô¥5õc3ƒ¢æòFV&÷Væ6RæBæògVÆÀ¢2vRf÷&Ò7V&Ö—BâV6‚¶W—7G&ö¶R–ÖÖVF–FVÇ’fWF6†W2g&W6‚6W'fW"×6–FP¢2ÖF6†W2Â&÷'G2ç’7WW'6VFVB&WVW7BÂæB&WÆ6W2öæÇ’Æörg&vÖVçG2à¢25E$TÔdõ$tUôäôDUõäTÅôÄôuõ$•dDUôDE$U54$%ô¥5õc3¢25E$TÔdõ$tUôäôDUõäTÅôÄôuôd•„TEô4äôä”4ÅôDE$U54$%ô¥5õcC¢F†RÆ—fP¢2Æöw2T’æWfW"×WFFW2†—7F÷'’öÆö6F–öã²F†RæöFR†–FFVâ×&÷WFR&÷WFW"÷vç0¢2F†R6æöæ–6Âf—6–&ÆRFG&W72v†–ÆRfWF6‚U$Ç26''’–çFW&æÂ7FFRà¢6÷'E÷67&—BÒ""#Ç67&—Câ‚‚“Óç¶6öç7BæVÃÖFö7VÖVçBçVW'•6VÆV7F÷"‚rææöFRÖÆör×æVÂr’Æc×æVÃòçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÆörÖf–ÇFW"Öf÷&ÕÒr“¶–b‚æVÇÇÂb—&WGW&ã¶6öç7B3ÖbçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÆör×6÷'BÖ–çWEÒr’ÆóÖbçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÆörÖ÷&FW"Ö–çWEÒr’ÇÖbçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÆör×6V&6…Òr“¶ÆWB7G&ÃÖçVÆÂÇ6WÓ¶6öç7B'V–ÆCÒ‡vSÓ“Óç¶6öç7BÖæWrU$Å6V&6…&×2†æWrf÷&ÔFF†b’“·ç6WB‚wvRrÅ7G&–ær‡vR’“·&WGW&âbæ7F–öâ²sòr·çFõ7G&–ær‚—Ó¶6öç7B7vÒ†Fö2Ç6VÂ“Óç¶6öç7B×æVÂçVW'•6VÆV7F÷"‡6VÂ’Æ#ÖFö2çVW'•6VÆV7F÷"‡6VÂ“¶–b‚ÇÂ"—&WGW&âfÇ6S¶ç&WÆ6Uv—F‚†"“·&WGW&âG'VWÓ¶6öç7BÆöCÖ7–æ2‡W&Â“Óç¶7G&Ãòæ&÷'B‚“¶6öç7B3ÖæWr&÷'D6öçG&öÆÆW"‚“¶7G&ÃÖ3¶6öç7BãÒ²·6W·æVÂæ6Æ74Æ—7BæFB‚væöFRÖÆörÖÆ—fRÖÆöF–ærr“·G'—¶6öç7B#Öv—BfWF6‚‡W&ÂÇ¶7&VFVçF–Ç3¢w6ÖRÖ÷&–v–ârÆ66†S¢væò×7F÷&RrÆ†VFW'3§²u‚Õ7G&VÔf÷&vRÕ'F–Âs¢væöFRÖÆöw2wÒÇ6–væÃ¦2ç6–væÇÒ’ÇCÖv—B"çFW‡B‚“¶–b‚"æö²—F‡&÷ræWrW'&÷"‚t…EEr·"ç7FGW2“¶6öç7BCÖæWrDôÕ'6W"‚’ç'6Tg&öÕ7G&–ær‡BÂwFW‡Bö‡FÖÂr“¶–b‚BçVW'•6VÆV7F÷"‚u¶FFÖæöFRÖÆör×&W7VÇG5Òr’—¶Æö6F–öâæ76–vâ‡"çW&ÇÇÇW&Â“·&WGW&çÖ–b†âÓ×6W—&WGW&ã·7v†BÂu¶FFÖæöFRÖÆör×F'5Òr“·7v†BÂu¶FFÖæöFRÖÆör×7VÖÖ'•Òr“·7v†BÂu¶FFÖæöFRÖÆör×&W7VÇG5Òr“·7v†BÂu¶FFÖæöFRÖÆör×v–æF–öâÖ†÷7EÒr—Ö6F6‚†R—¶–b†SòææÖRÓÒt&÷'DW'&÷"r–6öç6öÆRæW'&÷"‚u7G&VÔf÷&vRæöFRÆ—fRÆör6V&6‚f–ÆVBrÆR—Öf–æÆÇ—¶–b†ãÓÓ×6W—æVÂæ6Æ74Æ—7Bç&VÖ÷fR‚væöFRÖÆörÖÆ—fRÖÆöF–ærr—×Ó·òæFDWfVçDÆ—7FVæW"‚v–çWBrÂ‚“ÓæÆöB†'V–ÆBƒ’’“·òæFDWfVçDÆ—7FVæW"‚w6V&6‚rÂ‚“ÓæÆöB†'V–ÆBƒ’’“·òæFDWfVçDÆ—7FVæW"‚v¶W–F÷vârÆSÓç¶–b†Ræ¶W“ÓÓÒtVçFW"r–Rç&WfVçDFVfVÇB‚—Ò“¶bçVW'•6VÆV7F÷$ÆÂ‚w6VÆV7Br’æf÷$V6‚‡ƒÓç‚æFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚“ÓæÆöB†'V–ÆBƒ’’’“¶bæFDWfVçDÆ—7FVæW"‚w7V&Ö—BrÆSÓç¶Rç&WfVçDFVfVÇB‚“¶ÆöB†'V–ÆBƒ’—Ò“·æVÂæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆSÓç¶6öç7B#ÖRçF&vWBæ6Æ÷6W7B‚u¶FFÖæöFRÖÆör×6÷'EÒr“¶–b†"—¶Rç&WfVçDFVfVÇB‚“¶6öç7B³Õ7G&–ær†"æFF6WBææöFTÆöu6÷'GÇÂwF–ÖRr’Ç6ÖS×2çfÇVSÓÓÖ³·2çfÇVSÖ³¶òçfÇVS×6ÖSò†òçfÇVSÓÓÒv62sòvFW62s¢v62r“¢†³ÓÓÒwF–ÖRsòvFW62s¢v62r“¶ÆöB†'V–ÆBƒ’“·&WGW&çÖ6öç7BÖRçF&vWBæ6Æ÷6W7B‚u¶FFÖæöFRÖÆör×v–æF–öâÖ†÷7EÒ¶‡&VeÒr“¶–b†bbæ6Æ74Æ—7Bæ6öçF–ç2‚væöFRÖÆör×vRÖF—6&ÆVBr’—¶Rç&WfVçDFVfVÇB‚“¶ÆöB†æ‡&Vb—×Ò—Ò’‚“³Â÷67&—Câ"" ¢6öçFVçBÒ€¢ÆW'B²sÇ6V7F–öâ6Æ73Ò&æöFRÖÆör×æVÂ#âr²F'0¢²sÆF—b6Æ73Ò&æöFRÖÆör×FööÆ&""FFÖæöFRÖÆör×7VÖÖ'“ãÆF—cãÆƒ#äæöFRÆöw3Âöƒ#ãÇå&Wf–Wr7F—f—G’Â66W72Â6Æ–VçBæB7—7FVÒWfVçG2öâF†—2æöFRãÂ÷ãÇãÆ#åF÷FÂÆöw3£Âö#âp¢²‡FÖÂæW66R‡7G"‡F÷FÅöÆöw2’’²r+rÆ#å6†÷v–æs£Âö#âr²‡FÖÂæW66R†bw·6†÷våög&ö×Þ(	7·6†÷vå÷F÷Òr–bF÷FÅöÆöw2VÇ6Rsr¢²‚‚r+rÆ#å6V&6ƒ£Âö#âr²‡FÖÂæW66R‡6VÆV7FVE÷6V&6‚’’–b6VÆV7FVE÷6V&6‚VÇ6Rrr’²sÂ÷ãÂöF—câp¢²6ÆV%ö7F–öâ²sÂöF—câp¢²sÆf÷&ÒÖWF†öCÒ&vWB"7F–öãÒ"÷æVÂöÖævRöÆöw2"6Æ73Ò&æöFRÖÆörÖf–ÇFW&&""FFÖæöFRÖÆörÖf–ÇFW"Öf÷&Óâp¢²bsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&Æöu÷G—R"fÇVSÒ'¶‡FÖÂæW66R‡6VÆV7FVE÷G—R—Ò#âp¢²bsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ'6÷'B"fÇVSÒ'¶‡FÖÂæW66R‡6VÆV7FVE÷6÷'B—Ò"FFÖæöFRÖÆör×6÷'BÖ–çWCâp¢²bsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&÷&FW""fÇVSÒ'¶‡FÖÂæW66R‡6VÆV7FVEö÷&FW"—Ò"FFÖæöFRÖÆörÖ÷&FW"Ö–çWCâp¢²sÆÆ&VÂ6Æ73Ò&æöFRÖÆör×6V&6‚Öf–VÆB#ãÇ7ãå6V&6ƒÂ÷7ããÆ–çWBG—SÒ'6V&6‚"æÖSÒ'"fÇVSÒ"r²‡FÖÂæW66R‡6VÆV7FVE÷6V&6‚ÂV÷FSÕG'VR’²r"Æ6V†öÆFW#Ò$•ÂW6W"ÂÖW76vRÂ6†ææVÂÂFWF–Ç>(
b"WFö6ö×ÆWFSÒ&öfb"FFÖæöFRÖÆör×6V&6ƒãÂöÆ&VÃâp¢²‚‚sÆÆ&VÃãÇ7ãäÆWfVÃÂ÷7ããÇ6VÆV7BæÖSÒ&ÆWfVÂ"âr²ÆWfVÅö÷F–öç5ö‡FÖÂ²sÂ÷6VÆV7CãÂöÆ&VÃâr’–bÆWfVÅöf–ÇFW%öæVVFVBVÇ6RsÆ–çWBG—SÒ&†–FFVâ"æÖSÒ&ÆWfVÂ"fÇVSÒ"#âr¢²sÆÆ&VÃãÇ7ãå6†÷sÂ÷7ããÇ6VÆV7BæÖSÒ&Æ–Ö—B"âr²Æ–Ö—Eö÷F–öç5ö‡FÖÂ²sÂ÷6VÆV7CãÂöÆ&VÃâp¢²sÆ6Æ73Ò&'Fâ"‡&VcÒ"÷æVÂöÖævRöÆöw2#å&W6WCÂöãÂöf÷&Óâp¢²sÆF—b6Æ73Ò'F&ÆR×w&æöFRÖÆör×F&ÆR×w&"FFÖæöFRÖÆör×&W7VÇG3ãÇF&ÆR6Æ73Ò&æöFRÖÆör×F&ÆR#âp¢²‚sÆ6öÆw&÷WãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×F–ÖR#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÆWfVÂ#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖ—#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×W6W"#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×6W76–öâ#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖGW&F–öâ#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÖW76vRÖ6Æ–VçB#ãÂö6öÆw&÷Wâr–b6VÆV7FVE÷G—RÓÒv6Æ–VçBrVÇ6R‚sÆ6öÆw&÷WãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×F–ÖR#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÆWfVÂ#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖ—#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÖW76vR#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖFWF–Ç2#ãÂö6öÆw&÷Wâr–b6VÆV7FVE÷G—RÓÒv66W72rVÇ6RsÆ6öÆw&÷WãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×F–ÖR#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÆWfVÂ#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂ×66÷R#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖ—#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖÖW76vR#ãÆ6öÂ6Æ73Ò&æöFRÖÆörÖ6öÂÖFWF–Ç2#ãÂö6öÆw&÷Wâr’¢²sÇF†VCãÇG#âr²†VFW%ö6VÆÇ2²sÂ÷G#ãÂ÷F†VCãÇF&öG“âr²&÷w2²sÂ÷F&öG“ãÂ÷F&ÆSãÂöF—câp¢²v–æF–öåö†÷7B²sÂ÷6V7F–öãâr²6÷'E÷67&—@¢¢&WGW&â…DÔÅ&W7öç6R…ö–æE÷vR‡W6W"ÂtÆöw2rÂ6öçFVçB’  ¤ç÷7B‚r÷æVÂöÖævRöÆöw2ö6ÆV"r¦FVb–æFWVæFVçEöÆöw5ö6ÆV"‡&WVW7C¢&WVW7BÂÆöu÷G—S¢7G"Òv7F—f—G’r“ ¢&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvÆöw2æÖævRr¢ÖævW"ç7–æ5öWfVçEöÆöw5ög&öÕöf–ÆR‚¢6VÆV7FVBÒ7G"†Æöu÷G—R÷"v7F—f—G’r’ç7G&—‚’æÆ÷vW"‚¢–b6VÆV7FVBæ÷B–â²v7F—f—G’rÂv66W72rÂv6Æ–VçBrÂw7—7FVÒwÓ ¢6VÆV7FVBÒv7F—f—G’p¢7—7FVÕ÷66÷W2Ò²w7—7FVÒrÂvæöFRrÂwWFFRrÂw6WGF–æw2wÐ¢W†6ÇVFVEö7F—f—G’Ò²vWF‚rÂv6Æ–VçBrÂ§7—7FVÕ÷66÷W7Ð ¢FVbÖF6†W5÷F"†—FVÓ¢F–7E·7G"Âç•Ò’Óâ&ööÃ ¢—FVÕ÷66÷RÒ7G"†—FVÒævWB‚w66÷Rr’÷"w7—7FVÒr’ç7G&—‚’æÆ÷vW"‚¢–b6VÆV7FVBÓÒv66W72s ¢&WGW&â—FVÕ÷66÷RÓÒvWF‚p¢–b6VÆV7FVBÓÒv6Æ–VçBs ¢&WGW&â—FVÕ÷66÷RÓÒv6Æ–VçBp¢–b6VÆV7FVBÓÒw7—7FVÒs ¢&WGW&â—FVÕ÷66÷R–â7—7FVÕ÷66÷W0¢&WGW&â—FVÕ÷66÷Ræ÷B–âW†6ÇVFVEö7F—f—G ¢v—F‚ÖævW"æÆö6³ ¢&Wf–÷W2ÒÆ—7B†ÖævW"æWfVçEöÆöw2¢&VÖ–æ–ærÒ¶—FVÒf÷"—FVÒ–â&Wf–÷W2–bæ÷BÖF6†W5÷F"†—FVÒ•Ð¢6ÆV&VBÒÆVâ‡&Wf–÷W2’ÒÆVâ‡&VÖ–æ–ær¢ÖævW"æWfVçEöÆöw2ÒFWVR‡&VÖ–æ–ærÂÖ†ÆVãÔäôDUôUdTåEôÄôuôÔ‚¢÷&Ww&—FUöæöFUöWfVçEöÆöuöf–ÆR‚¢–b6VÆV7FVBÓÒv6Æ–VçBs ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuô4ÄT%ôDTEUUõ$U4UEõcsS¢âW‡Æ–6—@¢26Æ–VçBÖÆör6ÆV"×W7BÆÆ÷rF†RæW‡BfÆ–B&WVW7Bf÷"7F–ÆÂ×7F&ÆP¢24”BFò7&VFRg&W6‚Æöv–6Â&÷r–ç7FVBöb&V–ær†–FFVâ'’F†P¢27&÷72×v÷&¶W"FVGWR¶W’&WF–æVB–â&VF—2à¢v—F‚ôäôDUô4Ä”TåEôÄôt”åôDTEUUôÄô4³ ¢ôäôDUô4Ä”TåEôÄôt”åôDTEURæ6ÆV"‚¢–bæöFU÷&VF—2—2æ÷BæöæRæBæöFU÷&VF—2æf–Æ&ÆRæBvWFGG"†æöFU÷&VF—2Âv6Æ–VçBrÂæöæR’—2æ÷BæöæS ¢G'“ ¢2cãƒW6W26Æ–VçBÖÆör×6W76–öã¢¢v†–ÆRöÆFW"&WF–æV@¢2&VÆV6W2W6VB6Æ–VçBÖÆös¢¢â6ÆV"&÷F‚æÖW76W2à¢&F6ƒ¢Æ—7E·7G%ÒÒµÐ¢f÷"GFW&â–â†æöFU÷&VF—2åö²‚v6Æ–VçBÖÆös¢¢r’ÂæöFU÷&VF—2åö²‚v6Æ–VçBÖÆör×6W76–öã¢¢r’“ ¢f÷"&VF—5ö¶W’–âæöFU÷&VF—2æ6Æ–VçBç66åö—FW"†ÖF6ƒ×GFW&âÂ6÷VçCÓS“ ¢&F6‚æVæB‡7G"‡&VF—5ö¶W’’¢–bÆVâ†&F6‚’ãÒS ¢æöFU÷&VF—2æ6Æ–VçBæFVÆWFR‚¦&F6‚¢&F6‚æ6ÆV"‚¢–b&F6ƒ ¢æöFU÷&VF—2æ6Æ–VçBæFVÆWFR‚¦&F6‚¢W†6WBW†6WF–öã ¢70¢Æ&VÂÒ²v7F—f—G’s¢t7F—f—G’rÂv66W72s¢t66W72rÂv6Æ–VçBs¢t6Æ–VçBrÂw7—7FVÒs¢u7—7FVÒwÕ·6VÆV7FVEÐ¢ÖW76vRÒbw¶Æ&VÇÒÆör6ÆV&VB‡¶6ÆV&VGÒ’p¢&WGW&â&VF—&V7E&W7öç6R€¢r÷æVÂöÖævRöÆöw3òr²W&ÆÆ–"ç'6RçW&ÆVæ6öFR‡²vÆöu÷G—Rs¢6VÆV7FVBÂvÖW76vRs¢ÖW76vWÒ’À¢32À¢  ¦FVböÖ6¶VE÷6fVE÷6V7&WB‡fÇVS¢7G"’Óâ7G# ¢æ÷&ÖÆ—¦VBÒ7G"‡fÇVR÷"""’ç7G&—‚¢–bæ÷Bæ÷&ÖÆ—¦VC ¢&WGW&â$æ÷B6fVB ¢7Vff—‚Òæ÷&ÖÆ—¦VE²ÓC¥Ò–bÆVâ†æ÷&ÖÆ—¦VB’ãÒBVÇ6Ræ÷&ÖÆ—¦V@¢&WGW&âb%6fVB(	B(
.(
.(
.(
'·7Vff—‡Ò    ¢25E$TÔdõ$tUôäôDUõ4UED”äu5õDÅ5õäTÅõcS¢W‡÷6R6fRFVÆVvFVBDå2Ó¢26W'F–f–6FR7FFRF—&V7FÇ’–âF†R7FæFÆöæRæöFR6WGF–æw2vRâ7&VFVçF–Ç0¢2&VÖ–â–â&ö÷BÖ÷væVB&Vv—7G&F–öâf–ÆW2æB&RæWfW"&VæFW&VBFòF†RæVÂà¦FVböæöFU÷6WGF–æw5÷FÇ5ö†÷7G2‚’ÓâÆ—7E·7G%Ó ¢†÷7G3¢Æ—7E·7G%ÒÒµÐ¢f÷"&r–â²¦Æ—7B†ÖævW"çæVÅ÷W&Ç2’Â¦Æ—7B†ÖævW"ç7G&VÕ÷W&Ç2•Ó ¢G'“ ¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B‡7G"‡&r÷"rr’ç7G&—‚’¢W†6WBfÇVTW'&÷# ¢6öçF–çVP¢†÷7BÒ7G"‡'6VBæ†÷7FæÖR÷"rr’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr¢–bæ÷B†÷7B÷"†÷7B–â†÷7G3 ¢6öçF–çVP¢G'“ ¢—FG&W72æ—öFG&W72††÷7B¢6öçF–çVP¢W†6WBfÇVTW'&÷# ¢70¢–b&RægVÆÆÖF6‚‡"u¶×£Ó•Òƒó¥¶×£Ó’âÕ×³Ã#SÕ¶×£Ó•Ò“òrÂ†÷7B“ ¢†÷7G2æVæB††÷7B¢&WGW&â†÷7G0  ¦FVböæöFU÷6WGF–æw5÷FÇ5ö—FVÒ††÷7C¢7G"’ÓâF–7E·7G"Âç•Ó ¢7FGW2ÒÖævW"çFÇ5÷7FGW2‚¢FVÆVvF–öç2Ò7FGW2ævWB‚vFVÆVvF–öç2r’–b—6–ç7Fæ6R‡7FGW2ævWB‚vFVÆVvF–öç2r’ÂF–7B’VÇ6R·Ð¢&rÒFVÆVvF–öç2ævWB††÷7B’–b—6–ç7Fæ6R†FVÆVvF–öç2ÂF–7B’VÇ6R·Ð¢—FVÒÒF–7B‡&r’–b—6–ç7Fæ6R‡&rÂF–7B’VÇ6R·Ð¢—FVÒç6WFFVfVÇB‚v†÷7FæÖRrÂ†÷7B¢—FVÒç6WFFVfVÇB‚v6æÖUöæÖRrÂbuö6ÖRÖ6†ÆÆVævRç¶†÷7GÒr¢&WGW&â—FVÐ  ¦FVböæöFU÷6WGF–æw5÷FÇ5÷æVÂ‚’Óâ7G# ¢–bU…DU$äÅõ$õ…•ôÔôDS ¢&WGW&âsÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3å54Âò…EE26W'F–f–6FW3Âöƒ3ãÇäW‡FW&æÂ&÷‡’ÖöFR÷vç2DÅ2f÷"F†—2æöFRãÂ÷ãÂöF—câp¢†÷7G2ÒöæöFU÷6WGF–æw5÷FÇ5ö†÷7G2‚¢–bæ÷B†÷7G3 ¢&WGW&âsÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3å54Âò…EE26W'F–f–6FW3Âöƒ3ãÇäFB†÷7FæÖR–âæVÂô’÷"Æ–Æ—7Bô66W72U$Ç2Fò6öæf–wW&RFVÆVvFVBDå2ÓDÅ2ãÂ÷ãÂöF—câp¢6&G3¢Æ—7E·7G%ÒÒµÐ¢f÷"†÷7B–â†÷7G3 ¢—FVÒÒöæöFU÷6WGF–æw5÷FÇ5ö—FVÒ††÷7B¢F&vWBÒ7G"†—FVÒævWB‚v6æÖU÷F&vWBr’÷"rr’ç7G&—‚¢æÖRÒ7G"†—FVÒævWB‚v6æÖUöæÖRr’÷"buö6ÖRÖ6†ÆÆVævRç¶†÷7GÒr’ç7G&—‚¢6W'E÷&VG’Ò&ööÂ†—FVÒævWB‚v6W'F–f–6FU÷&VG’r’¢6æÖU÷&VG’Ò&ööÂ†—FVÒævWB‚v6æÖU÷&VG’r’¢7FFRÒ7G"†—FVÒævWB‚w7FFRr’÷"rr’ç7G&—‚’æÆ÷vW"‚¢–b6W'E÷&VG’æB6æÖU÷&VG“ ¢&FvRÒsÇ7â6Æ73Ò&æöFR×FÇ2×7FFR&VG’"FFÖæöFR×FÇ2×7FFSä6W'F–f–6FR&VG“Â÷7ãâp¢VÆ–b6W'E÷&VG“ ¢&FvRÒsÇ7â6Æ73Ò&æöFR×FÇ2×7FFRv—F–ær"FFÖæöFR×FÇ2×7FFSä6W'F–f–6FR7F—fR+rDå2ÓVæF–æsÂ÷7ãâp¢VÆ–b6æÖU÷&VG“ ¢&FvRÒsÇ7â6Æ73Ò&æöFR×FÇ2×7FFRv—F–ær"FFÖæöFR×FÇ2×7FFSä4äÔR&VG’+r6W'F–f–6FRVæF–æsÂ÷7ãâp¢VÆ–b7FFRÓÒw&Vv—7G&F–öå÷7FFUöÖ—76–ærs ¢&FvRÒsÇ7â6Æ73Ò&æöFR×FÇ2×7FFRW'&÷""FFÖæöFR×FÇ2×7FFSå&Vv—7G&F–öâFFÖ—76–æsÂ÷7ãâp¢VÇ6S ¢&FvRÒsÇ7â6Æ73Ò&æöFR×FÇ2×7FFRv—F–ær"FFÖæöFR×FÇ2×7FFSäFB4äÔSÂ÷7ãâp¢F&vWE÷FW‡BÒF&vWB÷"‚u&Vv—7G&F–öâFFVæf–Æ&ÆRr–b7FFRÓÒw&Vv—7G&F–öå÷7FFUöÖ—76–ærrVÇ6Ru&Vv—7G&F–öâVæF–æ~(
br¢&W7VÇBÒ7G"†—FVÒævWB‚v6æÖUö6†V6²r’÷"—FVÒævWB‚vW'&÷"r’÷"‚t4äÔR—2&VG’âr–b6æÖU÷&VG’VÇ6RtFBF†R4äÔRöæ6RÂF†VâFW7B—B†W&Râr’’ç7G&—‚¢F—6&ÆVBÒrr–bF&vWBVÇ6RrF—6&ÆVBp¢6&G2æVæB€¢sÆ'F–6ÆR6Æ73Ò&æöFR×FÇ2Ö6&B"FFÖæöFR×FÇ2Ö6&BFFÖ†÷7CÒ"r²‡FÖÂæW66R††÷7BÂV÷FSÕG'VR’²r#âr°¢sÆF—b6Æ73Ò&æöFR×FÇ2Ö†VB#ãÆF—cãÆ#âr²‡FÖÂæW66R††÷7B’²sÂö#ãÇ6ÖÆÃä4äÔR+rFVÆVvFVBDå2ÓÂ÷6ÖÆÃãÂöF—câr²&FvR²sÂöF—câr°¢sÆF—b6Æ73Ò&æöFR×FÇ2×&V6÷&B#ãÇ7ãäæÖSÂ÷7ããÆ6öFRFFÖæöFR×FÇ2ÖæÖSâr²‡FÖÂæW66R†æÖR’²sÂö6öFSãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&'FâæöFR×FÇ2Ö6÷’"FFÖ6÷“Ò&æÖR#ä6÷“Âö'WGFöããÂöF—câr°¢sÆF—b6Æ73Ò&æöFR×FÇ2×&V6÷&B#ãÇ7ãåF&vWCÂ÷7ããÆ6öFRFFÖæöFR×FÇ2×F&vWCâr²‡FÖÂæW66R‡F&vWE÷FW‡B’²sÂö6öFSãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&'FâæöFR×FÇ2Ö6÷’"FFÖ6÷“Ò'F&vWB"r²F—6&ÆVB²sä6÷“Âö'WGFöããÂöF—câr°¢sÆF—b6Æ73Ò&æöFR×FÇ2Ö7F–öç2#ãÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&'Fâ"FFÖæöFR×FÇ2×FW7Br²F—6&ÆVB²såFW7B4äÔSÂö'WGFöããÆ'WGFöâG—SÒ&'WGFöâ"6Æ73Ò&'Fâ"FFÖæöFR×FÇ2×&WG'“å&WG'’6W'F–f–6FSÂö'WGFöããÂöF—câr°¢sÆF—b6Æ73Ò&æöFR×FÇ2×&W7VÇB"FFÖæöFR×FÇ2×&W7VÇCâr²‡FÖÂæW66R‡&W7VÇB’²sÂöF—cãÂö'F–6ÆSâp¢¢&WGW&â€¢sÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3å54Âò…EE26W'F–f–6FW3Âöƒ3ãÇäFVÆVvFVBDå2Óv÷&·2WfVâv†VâF†R†÷7FæÖR&W6öÇfW2Fò&—fFRöÆö6Â•âFBV6‚4äÔRöæÇ’öæ6S²7G&VÔf÷&vR&VæWw2F†R6W'F–f–6FRWFöÖF–6ÆÇ’ãÂ÷ãÂöF—câp¢sÆF—b6Æ73Ò'v–FRæöFR×FÇ2Öw&–B#âr²rræ¦ö–â†6&G2’²sÂöF—câp¢  ¦FVböæöFU÷6WGF–æw5öFç3÷FW7E÷7–æ2††÷7C¢7G"’ÓâF–7E·7G"Âç•Ó ¢6ÆVâÒ7G"††÷7B÷"rr’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr¢–b6ÆVâæ÷B–âöæöFU÷6WGF–æw5÷FÇ5ö†÷7G2‚“ ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢v–çfÆ–Eö†÷7BrÂv6W'F–f–6FU÷&VG’s¢fÇ6RÂvÖW76vRs¢t†÷7FæÖR—2æ÷B6öæf–wW&VBöâF†—2æöFRâwÐ¢—FVÒÒöæöFU÷6WGF–æw5÷FÇ5ö—FVÒ†6ÆVâ¢6W'F–f–6FU÷&VG’Ò&ööÂ†—FVÒævWB‚v6W'F–f–6FU÷&VG’r’¢æÖRÒ7G"†—FVÒævWB‚v6æÖUöæÖRr’÷"buö6ÖRÖ6†ÆÆVævRç¶6ÆVçÒr’ç7G&—‚’ç'7G&—‚râr¢W‡V7FVBÒ7G"†—FVÒævWB‚v6æÖU÷F&vWBr’÷"rr’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr¢–bæ÷BW‡V7FVC ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢w&Vv—7G&F–öå÷VæF–ærrÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvÖW76vRs¢tDå2Ó&Vv—7G&F–öâF&vWB—2æ÷B&VG’–WBâwÐ¢–bæ÷B6‡WF–Âçv†–6‚‚vF–rr“ ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢vF–uöÖ—76–ærrÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvW‡V7FVBs¢W‡V7FVB²rârÂvÖW76vRs¢vFç7WF–Ç2öF–r—2æ÷B–ç7FÆÆVBöâF†—2æöFRâwÐ¢G'“ ¢&ö2Ò7V'&ö6W72ç'Vâ€¢²vF–rrÂr·F–ÖSÓ2rÂr·G&–W3ÓrÂr·6†÷'BrÂt4äÔRrÂæÖUÒÀ¢6GW&Uö÷WGWCÕG'VRÂFW‡CÕG'VRÂF–ÖV÷WCÓRÂ6†V6³ÔfÇ6RÀ¢¢W†6WB„õ4W'&÷"Â7V'&ö6W72åF–ÖV÷WDW‡—&VB’2W†3 ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢vÆöö·Wöf–ÆVBrÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvW‡V7FVBs¢W‡V7FVB²rârÂvÖW76vRs¢bt4äÔRÆöö·Wf–ÆVC¢¶W†7ÒwÐ¢f÷VæEö—FV×2Ò¶Æ–æRç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr’f÷"Æ–æR–â&ö2ç7FF÷WBç7Æ—FÆ–æW2‚’–bÆ–æRç7G&—‚•Ð¢f÷VæBÒf÷VæEö—FV×5³Ò–bf÷VæEö—FV×2VÇ6Rrp¢–bf÷VæBÓÒW‡V7FVC ¢&WGW&â²vö²s¢G'VRÂw7FFRs¢w&VG’rÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvW‡V7FVBs¢W‡V7FVB²rârÂvf÷VæBs¢f÷VæB²rârÂvÖW76vRs¢t4äÔR—26÷'&V7BæBV&Æ–6Ç’f—6–&ÆRâwÐ¢–bæ÷Bf÷VæC ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢vÖ—76–ærrÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvW‡V7FVBs¢W‡V7FVB²rârÂvf÷VæBs¢rrÂvÖW76vRs¢t4äÔR—2æ÷BV&Æ–6Ç’f—6–&ÆR–WBâwÐ¢&WGW&â²vö²s¢fÇ6RÂw7FFRs¢ww&öæu÷F&vWBrÂv6W'F–f–6FU÷&VG’s¢6W'F–f–6FU÷&VG’ÂvæÖRs¢æÖRÂvW‡V7FVBs¢W‡V7FVB²rârÂvf÷VæBs¢f÷VæB²rârÂvÖW76vRs¢buw&öær4äÔRF&vWC¢¶f÷VæGÒâW‡V7FVB¶W‡V7FVGÒâwÐ  ¦FVb÷6WGF–æw5÷vR‡W6W#¢æVÄ66W75W6W"ÂÖW76vS¢7G"ÒrrÂW'&÷#¢7G"Òrr’Óâ7G# ¢ÖævW"æÆöEövVõ÷6WGF–æw2‚¢&÷f–FW%ö÷F–öç2Òö÷F–öåöÆ—7B€¢ÖævW"ævVõ÷&÷f–FW"À¢°¢‚vWFòrÂtWFò(	B•–æfòf—'7BÂÖ„Ö–æBfÆÆ&6²r’À¢‚v—–æfòrÂt•–æfò’öæÇ’r’À¢‚vÖ†Ö–æBrÂtÖ„Ö–æBFF&6W2öæÇ’r’À¢ÒÀ¢¢ÆW'BÒ†bsÆF—b6Æ73Öö³ç¶‡FÖÂæW66R†ÖW76vR—ÓÂöF—câr–bÖW76vRVÇ6Rrr’²†bsÆF—b6Æ73ÖW'#ç¶‡FÖÂæW66R†W'&÷"—ÓÂöF—câr–bW'&÷"VÇ6Rrr¢æVÅ÷W&Ç5÷fÇVRÒ‡FÖÂæW66R‚uÆâræ¦ö–â†ÖævW"çæVÅ÷W&Ç2’ÂV÷FSÔfÇ6R¢7G&VÕ÷W&Ç5÷fÇVRÒ‡FÖÂæW66R‚uÆâræ¦ö–â†ÖævW"ç7G&VÕ÷W&Ç2’ÂV÷FSÔfÇ6R¢—–æfõ÷6fVBÒ‡FÖÂæW66R…öÖ6¶VE÷6fVE÷6V7&WB†ÖævW"æ—–æfõ÷Fö¶Vâ’¢Ö†Ö–æE÷6fVBÒ‡FÖÂæW66R…öÖ6¶VE÷6fVE÷6V7&WB†ÖævW"æÖ†Ö–æEöÆ–6Vç6Uö¶W’’¢Ö†Ö–æEö66÷VçBÒ‡FÖÂæW66R†ÖævW"æÖ†Ö–æEö66÷VçEö–BÂV÷FSÕG'VR¢—–æfõ÷7FFRÒt6öæf–wW&VBr–bÖævW"æ—–æfõ÷Fö¶VâVÇ6RtÖ—76–ærp¢Ö†Ö–æE÷7FFRÒt6öæf–wW&VBr–bÖævW"æÖ†Ö–æEö66÷VçEö–BæBÖævW"æÖ†Ö–æEöÆ–6Vç6Uö¶W’VÇ6RtÖ—76–ærp¢ÖWG&–75÷&WFVçF–öåöF—2ÒÖ‚ƒÂÖ–âƒ3cSÂ–çB…öæöFUö÷W&F–öç5÷6WGF–æw2‚’ævWB‚vÖWG&–75÷&WFVçF–öåöF—2r’÷"3’’¢Æöu÷&WFVçF–öåöF—2ÒÖ‚ƒÂÖ–âƒ3cSÂ–çB…öæöFUö÷W&F–öç5÷6WGF–æw2‚’ævWB‚vÆöu÷&WFVçF–öåöF—2r’÷"3’’¢ÖWG&–75öFVfVÇE÷7åö†÷W'2ÒÖ‚ƒÂÖ–â†ÖWG&–75÷&WFVçF–öåöF—2¢#BÂ–çB…öæöFUö÷W&F–öç5÷6WGF–æw2‚’ævWB‚vÖWG&–75öFVfVÇE÷7åö†÷W'2r’÷"#B’’¢ÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG2ÒÖ‚ƒRÂÖ–âƒ3cÂ–çB…öæöFUö÷W&F–öç5÷6WGF–æw2‚’ævWB‚vÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG2r’÷"c’’¢6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW2ÒÖ‚ƒÂÖ–âƒƒÂ–çB†ÖævW"æ6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW2÷"c’’¢–÷WGV&Uö6öö¶–W5÷7FFRÒt6öæf–wW&VBr–b–÷WGV&Uö6öö¶–Uö6öæf–wW&VB‚’VÇ6Rtæ÷B6öæf–wW&VBp¢–÷WGV&Uö6öö¶–W5÷&VÖ÷fRÒsÆÆ&VÂ6Æ73Ò&6†V6²#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ'&VÖ÷fU÷–÷WGV&Uö6öö¶–W5öf–ÆR#ãÇ7ãå&VÖ÷fR6fVB–÷UGV&R6öö¶–W3Â÷7ããÂöÆ&VÃâr–b–÷WGV&Uö6öö¶–Uö6öæf–wW&VB‚’VÇ6Rrp¢6öçFVçBÒÆW'B²brrsÆF—b6Æ73Ò&f÷&ÒÖÆ–÷WBæöFR×6WGF–æw2ÖÆ–÷WB#à£Ç6V7F–öâ6Æ73Ò'æVÂÖf÷&Òf÷&Ò×6V7F–öâ#à£ÆF—b6Æ73Ò'æVÂÖ†VB#ãÆF—cãÆƒ#äæöFR66W726WGF–æw3Âöƒ#ãÇä6öæf–wW&RF†RV&Æ–2æVÂô’æBÆ–Æ—7BôÆ–6W27–æ6‡&öæ—¦VBv—F‚F†RÖ–â6W'fW"ãÂ÷ãÂöF—cãÂöF—cà£Æf÷&ÒÖWF†öCÒ'÷7B"Væ7G—SÒ&×VÇF—'Böf÷&ÒÖFF"6Æ73Ò&f÷&ÒÖw&–B"–CÒ&æöFR×6WGF–æw2Öf÷&Ò#à£ÆÆ&VÂ6Æ73Ò'v–FR#äæöFRæVÂô’66W72U$Ç3ÇFW‡F&VæÖSÒ'æVÅ÷W&Ç2"&WV—&VCç·æVÅ÷W&Ç5÷fÇVWÓÂ÷FW‡F&VãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#äöæRgVÆÂU$ÂW"Æ–æRâv—F†÷WBâW‡Æ–6—BV&Æ–2÷'BÂ…EEW6W2ƒæB…EE2W6W2CC3²F†R–çFW&æÂæöFRÆ—7FVæW"—26W&FRâF†R6fVB66†VÖR—26æöæ–6ÂæB÷÷6—FR×&÷Fö6öÂ&WVW7G2&VF—&V7BWFöÖF–6ÆÇ’â7FæFÆöæRæöFW2W6RFVÆVvFVBDå2ÓÖævVBDÅ3²WfVââ…EEÖ6æöæ–6Â†÷7FæÖR¶VW26W'F–f–6FRöâCC26ò…EE2Ö—7F¶W26â&VF—&V7BFò…EEv—F†÷WB6W'F–f–6FRv&æ–ærãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò'v–FR#åÆ–Æ—7Bô66W72U$Ç3ÇFW‡F&VæÖSÒ'7G&VÕ÷W&Ç2"&WV—&VCç·7G&VÕ÷W&Ç5÷fÇVWÓÂ÷FW‡F&VãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#äöæRgVÆÂU$ÂW"Æ–æRâV&Æ–2…EEFVfVÇG2FòƒæB…EE2FòCC2v†Vâæò÷'B—2w&—GFVã²&6¶VæBÆ—7FVæW"÷'G2&VÖ–â6W&FRâF†R6fVB66†VÖR—26æöæ–6ÂæB÷÷6—FR×&÷Fö6öÂ&WVW7G2&VF—&V7BWFöÖF–6ÆÇ’âFVÆVvFVBDå2Ó6W'F–f–6FW2&R–æFWVæFVçBöbv†WF†W"F†R†÷7FæÖR&W6öÇfW2FòV&Æ–2÷"&—fFRöÆö6Â•ãÂ÷6ÖÆÃãÂöÆ&VÃà§µöæöFU÷6WGF–æw5÷FÇ5÷æVÂ‚—Ð£ÆÆ&VÃäÆör&WFVçF–öãÇ6VÆV7BæÖSÒ&Æöu÷&WFVçF–öåöF—2#ç²rræ¦ö–â†bsÆ÷F–öâfÇVSÒ'¶F—7Ò"²'6VÆV7FVB"–bF—2ÓÒÆöu÷&WFVçF–öåöF—2VÇ6R"'Óç¶F—7ÒF—²'2"–bF—2ÒVÇ6R"'ÓÂö÷F–öãârf÷"F—2–â³Ã2ÃrÃBÃ3ÃcÃ“ÃƒÃ3cUÒ—ÓÂ÷6VÆV7CãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#ä7F—f—G’Â66W72Â6Æ–VçBæB7—7FVÒÆöw2öÆFW"F†âF†—2&R&VÖ÷fVBWFöÖF–6ÆÇ’ãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆÆ&VÃäÖWG&–72†—7F÷'’&WFVçF–öâ†F—2“Æ–çWBG—SÒ&çVÖ&W""Ö–ãÒ#"ÖƒÒ#3cS"æÖSÒ&ÖWG&–75÷&WFVçF–öåöF—2"fÇVSÒ'¶ÖWG&–75÷&WFVçF–öåöF—7Ò#ãÂöÆ&VÃà£ÆÆ&VÃäæWr6W76–öâgFW"öffÆ–æR†Ö–çWFW2“Æ–çWBG—SÒ&çVÖ&W""Ö–ãÒ#"ÖƒÒ#ƒ"æÖSÒ&6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW2"fÇVSÒ'¶6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW7Ò#ãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#å6ÖRFWf–6Rõ4”B&W7VÖW27V×VÆF—fR6W76–öâvRv—F†–âF†—2vâÆöævW"öffÆ–æRv27F'BæWrÆöv–6Â6W76–öââFVfVÇC¢cÖ–çWFW2ãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆÆ&VÃäFVfVÇBw&‚F–ÖR7ãÇ6VÆV7BæÖSÒ&ÖWG&–75öFVfVÇE÷7åö†÷W'2#ç²rræ¦ö–â†bsÆ÷F–öâfÇVSÒ'¶†÷W'7Ò"²'6VÆV7FVB"–b†÷W'2ÓÒÖWG&–75öFVfVÇE÷7åö†÷W'2VÇ6R"'Óç¶Æ&VÇÓÂö÷F–öãârf÷"†÷W'2ÆÆ&VÂ–â²ƒÂtÆ7B†÷W"r’ÂƒbÂtÆ7Bb†÷W'2r’Âƒ#BÂtÆ7B#B†÷W'2r’Âƒs"ÂtÆ7B2F—2r’Âƒc‚ÂtÆ7BrF—2r’Âƒs#ÂtÆ7B3F—2r•Ò—ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÃäw&‚6×ÆR–çFW'fÂ‡6V6öæG2“Æ–çWBG—SÒ&çVÖ&W""Ö–ãÒ#R"ÖƒÒ#3c"æÖSÒ&ÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG2"fÇVSÒ'¶ÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG7Ò#ãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#ä†÷rögFVâw&‚ö–çB—26fVBãÂ÷6ÖÆÃãÂöÆ&VÃà £ÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3å–÷UGV&RÆ—fR6÷W&6RWF†VçF–6F–öãÂöƒ3ãÇä÷F–öæÂ6W'fW"ÖÆö6Â6öö¶–W2f÷"–÷UGV&Rv†VâF†—2æöFR•—26†ÆÆVævVB'’–÷UGV&RãÂ÷ãÂöF—cà£ÆÆ&VÂ6Æ73Ò'v–FR#åWÆöB–÷UGV&R6öö¶–W2çG‡CÆ–çWBG—SÒ&f–ÆR"æÖSÒ'–÷WGV&Uö6öö¶–W5öf–ÆR"66WCÒ"çG‡BÇFW‡B÷Æ–â#ãÇ6ÖÆÂ6Æ73Ò&f–VÆBÖ†VÇ#äg&W6‚æWG66RÖf÷&ÖB6öö¶–W2çG‡Bâ7F÷&VBöæÇ’öâF†—2æöFRB÷f"öÆ–"÷7G&VÖf÷&vRÖæöFR÷–÷WGV&RÖ6öö¶–W2çG‡Bv—F‚÷væW"ÖöæÇ’W&Ö—76–öç2ãÂ÷6ÖÆÃãÂöÆ&VÃà£ÆF—b6Æ73Ò&7&VFVçF–ÂÖ6&B#ãÇ7ãå–÷UGV&R6öö¶–W3Â÷7ããÆ#ç·–÷WGV&Uö6öö¶–W5÷7FFWÓÂö#ãÇ6ÖÆÃç²r÷f"öÆ–"÷7G&VÖf÷&vRÖæöFR÷–÷WGV&RÖ6öö¶–W2çG‡Br–b–÷WGV&Uö6öö¶–Uö6öæf–wW&VB‚’VÇ6RuV&Æ–27G&V×2&RGFV×FVBv—F†÷WB6öö¶–W2f—'7BâwÓÂ÷6ÖÆÃãÂöF—cà§·–÷WGV&Uö6öö¶–W5÷&VÖ÷fWÐ £ÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3ävVô•’–æf÷&ÖF–öãÂöƒ3ãÇ–CÒ&vVò×&÷f–FW"ÖFW67&—F–öâ#äWFò6†V6·2•–æfòf—'7BæBW6W2Æö6ÂÖ„Ö–æBFF&6W2öæÇ’v†Vâ•–æfò†2æò&W7VÇBãÂ÷ãÂöF—cà£ÆÆ&VÃäÆöö·W&÷f–FW#Ç6VÆV7BæÖSÒ'&÷f–FW""–CÒ&vVö—×&÷f–FW"#ç·&÷f–FW%ö÷F–öç7ÓÂ÷6VÆV7CãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&6†V6²vVòÖÖ†Ö–æBÖf–VÆB#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ&vVõöWFõ÷WFFR"²v6†V6¶VBr–bÖævW"ævVõöWFõ÷WFFRVÇ6RrwÓãÇ7ãäWFöÖF–6ÆÇ’WFFRÖ„Ö–æBFF&6W3Â÷7ããÂöÆ&VÃà £ÆÆ&VÂ6Æ73Ò&vVòÖ—–æfòÖf–VÆB#äæWr•–æfò’Fö¶VãÆ–çWBG—SÒ'77v÷&B"æÖSÒ&—–æfõ÷Fö¶Vâ"Æ6V†öÆFW#Ò$ÆVfR&Ææ²Fò¶VW6fVBFö¶Vâ"WFö6ö×ÆWFSÒ&æWr×77v÷&B#ãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&6†V6²vVòÖ—–æfòÖf–VÆB#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ'&VÖ÷fUö—–æfõ÷Fö¶Vâ#ãÇ7ãå&VÖ÷fR6fVB•–æfòFö¶VãÂ÷7ããÂöÆ&VÃà£ÆF—b6Æ73Ò&7&VFVçF–ÂÖ6&BvVòÖ—–æfòÖf–VÆB#ãÇ7ãä•–æfò“Â÷7ããÆ#ç¶—–æfõ÷7FFWÓÂö#ãÇ6ÖÆÃç¶—–æfõ÷6fVGÓÂ÷6ÖÆÃãÂöF—cà £ÆÆ&VÂ6Æ73Ò&vVòÖÖ†Ö–æBÖf–VÆB#äÖ„Ö–æB66÷VçB”CÆ–çWBæÖSÒ&Ö†Ö–æEö66÷VçEö–B"fÇVSÒ'¶Ö†Ö–æEö66÷VçGÒ"WFö6ö×ÆWFSÒ&öfb#ãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&vVòÖÖ†Ö–æBÖf–VÆB#äæWrÖ„Ö–æBÆ–6Vç6R¶W“Æ–çWBG—SÒ'77v÷&B"æÖSÒ&Ö†Ö–æEöÆ–6Vç6Uö¶W’"Æ6V†öÆFW#Ò$ÆVfR&Ææ²Fò¶VW6fVB¶W’"WFö6ö×ÆWFSÒ&æWr×77v÷&B#ãÂöÆ&VÃà£ÆÆ&VÂ6Æ73Ò&6†V6²vVòÖÖ†Ö–æBÖf–VÆB#ãÆ–çWBG—SÒ&6†V6¶&÷‚"æÖSÒ'&VÖ÷fUöÖ†Ö–æEö¶W’#ãÇ7ãå&VÖ÷fR6fVBÖ„Ö–æB¶W“Â÷7ããÂöÆ&VÃà£ÆF—b6Æ73Ò&7&VFVçF–ÂÖ6&BvVòÖÖ†Ö–æBÖf–VÆB#ãÇ7ãäÖ„Ö–æB7&VFVçF–Ç3Â÷7ããÆ#ç¶Ö†Ö–æE÷7FFWÓÂö#ãÇ6ÖÆÃä66÷VçB”C¢¶Ö†Ö–æEö66÷VçB÷"tæ÷B6fVBwÒ+r¶Ö†Ö–æE÷6fVGÓÂ÷6ÖÆÃãÂöF—cà £ÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3åæVÂ66W72'VÆW3Âöƒ3ãÇä–æFWVæFVçB'&÷w6W"æVÂöÆ–7’âÆÂ6öæf–wW&VB'VÆW2×W7B72ãÂ÷ãÂöF—cà£ÆF—b6Æ73Ò'v–FR66W72×'VÆR×&÷r#à£ÆÆ&VÃåæVÂ•v†—FVÆ—7CÇFW‡F&VæÖSÒ'æVÅö—÷v†—FVÆ—7B#ç¶‡FÖÂæW66R†ÖævW"çæVÅö—÷v†—FVÆ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃåæVÂ•&Æ6¶Æ—7CÇFW‡F&VæÖSÒ'æVÅö—ö&Æ6¶Æ—7B#ç¶‡FÖÂæW66R†ÖævW"çæVÅö—ö&Æ6¶Æ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃåæVÂ4âv†—FVÆ—7CÇFW‡F&VæÖSÒ'æVÅö6å÷v†—FVÆ—7B#ç¶‡FÖÂæW66R†ÖævW"çæVÅö6å÷v†—FVÆ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃåæVÂ4â&Æ6¶Æ—7CÇFW‡F&VæÖSÒ'æVÅö6åö&Æ6¶Æ—7B#ç¶‡FÖÂæW66R†ÖævW"çæVÅö6åö&Æ6¶Æ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÂöF—cà£ÆF—b6Æ73Ò'v–FR6WGF–æw2ÖF—f–FW"#ãÆƒ3åÆ–&6²b6FÆör66W72'VÆW3Âöƒ3ãÇåÆ–Æ—7BÂ‡G&VÒÂvV"Æ–W"6FÆöwVRæBÆ–&6²öÆ–7’âÆÂ6öæf–wW&VB'VÆW2×W7B72ãÂ÷ãÂöF—cà£ÆF—b6Æ73Ò'v–FR66W72×'VÆR×&÷r#à£ÆÆ&VÃåÆ–&6²•v†—FVÆ—7CÇFW‡F&VæÖSÒ&—÷v†—FVÆ—7B#ç¶‡FÖÂæW66R†ÖævW"æ—÷v†—FVÆ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃåÆ–&6²•&Æ6¶Æ—7CÇFW‡F&VæÖSÒ&—ö&Æ6¶Æ—7B#ç¶‡FÖÂæW66R†ÖævW"æ—ö&Æ6¶Æ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃä4âv†—FVÆ—7CÇFW‡F&VæÖSÒ&6å÷v†—FVÆ—7B#ç¶‡FÖÂæW66R†ÖævW"æ6å÷v†—FVÆ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÆÆ&VÃä4â&Æ6¶Æ—7CÇFW‡F&VæÖSÒ&6åö&Æ6¶Æ—7B#ç¶‡FÖÂæW66R†ÖævW"æ6åö&Æ6¶Æ—7B—ÓÂ÷FW‡F&VãÂöÆ&VÃà£ÂöF—cà£ÆF—b6Æ73Ò&f÷&ÒÖ7F–öç2v–FR#ãÆ'WGFöãå6fR6WGF–æw3Âö'WGFöããÂöF—cà£Âöf÷&Óà£Â÷6V7F–öãà£ÂöF—cà£Ç7G–ÆSà¢ææöFR×6WGF–æw2ÖÆ–÷WG·¶Ö‚×v–GFƒ£ƒƒ¶Ö&v–ã£WF÷×ÒææöFR×6WGF–æw2ÖÆ–÷WBæf÷&Ò×6V7F–öç·¶÷fW&fÆ÷s¦†–FFVã·FF–æs£×Òç6WGF–æw2ÖF—f–FW'··FF–ær×F÷£Gƒ¶&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖÆ–æR“¶Ö&v–â×F÷£G‡×Òç6WGF–æw2ÖF—f–FW"ƒ2Âç6WGF–æw2ÖF—f–FW"·¶Ö&v–ã£×Òç6WGF–æw2ÖF—f–FW"·¶Ö&v–â×F÷£Gƒ¶6öÆ÷#§f"‚ÒÖ×WFVB—×Òæ7&VFVçF–ÂÖ6&G·¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶§W7F–g’Ö6öçFVçC¦6VçFW#¶v£Gƒ¶Ö–âÖ†V–v‡C£s‡ƒ·FF–æs£'‚Gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£—ƒ¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"—×Òæ7&VFVçF–ÂÖ6&B7âÂæ7&VFVçF–ÂÖ6&B6ÖÆÇ·¶6öÆ÷#§f"‚ÒÖ×WFVB—×Òæ7&VFVçF–ÂÖ6&B'·¶föçB×6—¦S£W‡×Òç6fVBÖ7&VFVçF–Ç·¶föçBÖfÖ–Ç“§V’ÖÖöæ÷76RÅ4dÖöæòÕ&VwVÆ"ÄÖVæÆòÆÖöæ÷76S¶6öÆ÷#¢6#†C–6c¶&6¶w&÷VæC¢3#W×ÒævVò×&÷f–FW"Ö†–FFVç·¶F—7Æ“¦æöæR–×÷'FçG×Òæ66W72×'VÆR×&÷w·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒBÆÖ–æÖ‚ƒ##‚Ãg"’“¶v£'ƒ¶Æ–vâÖ—FV×3§7F'C¶Ö‚×v–GFƒ£S¶÷fW&fÆ÷r×ƒ¦WFó·FF–ærÖ&÷GFöÓ£G‡×Òæ66W72×'VÆR×&÷rÆ&VÇ·¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶v£wƒ¶Ö–â×v–GFƒ£##ƒ¶†V–v‡C£W×Òæ66W72×'VÆR×&÷rFW‡F&V·¶†V–v‡C£‡ƒ¶Ö–âÖ†V–v‡C£‡ƒ·&W6—¦S§fW'F–6Ç×Ð¢ææöFR×FÇ2Öw&–G·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVB†WFòÖf—BÆÖ–æÖ‚ƒ3c‚Ãg"’“¶v£'‡×ÒææöFR×FÇ2Ö6&G·¶F—7Æ“¦w&–C¶v£ƒ·FF–æs£Gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC§f"‚Ò×7W&f6RÓ"—×ÒææöFR×FÇ2Ö†VG·¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦fÆW‚×7F'C¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£'‡×ÒææöFR×FÇ2Ö†VCæF—g·¶F—7Æ“¦w&–C¶v£7ƒ¶Ö–â×v–GFƒ£×ÒææöFR×FÇ2Ö†VB'·¶föçB×6—¦S£Gƒ¶÷fW&fÆ÷r×w&¦ç—v†W&W×ÒææöFR×FÇ2Ö†VB6ÖÆÇ·¶6öÆ÷#§f"‚ÒÖ×WFVB—×ÒææöFR×FÇ2×7FFW·¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶Ö–âÖ†V–v‡C£#gƒ·FF–æs£G‚‡ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£““—ƒ¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£ƒ·v†—FR×76S¦æ÷w&×ÒææöFR×FÇ2×7FFRç&VG—·¶6öÆ÷#¢3sFFfc¶&÷&FW"Ö6öÆ÷#§&v&ƒbÃ##2ÃsRÂã3B“¶&6¶w&÷VæC§&v&ƒbÃ##2ÃsRÂã‚—×ÒææöFR×FÇ2×7FFRçv—F–æw·¶6öÆ÷#¢6cv3ƒf¶&÷&FW"Ö6öÆ÷#§&v&ƒ#CrÃ#ÃbÂã3R“¶&6¶w&÷VæC§&v&ƒ#CrÃ#ÃbÂã‚—×ÒææöFR×FÇ2×7FFRæW'&÷'·¶6öÆ÷#¢6fc“c–c¶&÷&FW"Ö6öÆ÷#§&v&ƒ#SRÃSÃS’Âã3R“¶&6¶w&÷VæC§&v&ƒ#SRÃSÃS’Âã‚—×ÒææöFR×FÇ2×&V6÷&G·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£cG‚Ö–æÖ‚ƒÃg"’WFó¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#·FF–æs£—‚ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£—ƒ¶&6¶w&÷VæC¢3#‡×ÒææöFR×FÇ2×&V6÷&Cç7ç·¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£ƒ¶föçB×vV–v‡C£ƒ·FW‡B×G&ç6f÷&Ó§WW&66S¶ÆWGFW"×76–æs¢ãVV××ÒææöFR×FÇ2×&V6÷&B6öFW·¶Ö–â×v–GFƒ£¶÷fW&fÆ÷r×w&¦ç—v†W&S¶6öÆ÷#§f"‚Ò×FW‡B“¶föçB×6—¦S£‡×ÒææöFR×FÇ2Ö6÷—·¶Ö–âÖ†V–v‡C£3‚–×÷'FçC¶†V–v‡C£3‚–×÷'FçC·FF–æs£—‚–×÷'FçC¶föçB×6—¦S£‚–×÷'FçG×ÒææöFR×FÇ2Ö7F–öç7·¶F—7Æ“¦fÆWƒ¶fÆW‚×w&§w&¶v£‡‡×ÒææöFR×FÇ2Ö7F–öç2æ'Fç·¶Ö–âÖ†V–v‡C£3G‚–×÷'FçC¶†V–v‡C£3G‚–×÷'FçC¶föçB×6—¦S£‚–×÷'FçG×ÒææöFR×FÇ2×&W7VÇG·¶Ö–âÖ†V–v‡C£‡ƒ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£ƒ¶Æ–æRÖ†V–v‡C£ãG×ÒææöFR×FÇ2×&W7VÇBæö··¶6öÆ÷#¢3sFFfg×ÒææöFR×FÇ2×&W7VÇBæ&G·¶6öÆ÷#¢6fc“c–g×ÒææöFR×FÇ2×&W7VÇBæ'W7—·¶6öÆ÷#¢6cv3ƒf×ÔÖVF–†Ö‚×v–GFƒ£s‚—·²ææöFR×FÇ2Öw&–G·¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'×ÒææöFR×FÇ2×&V6÷&G·¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'×ÒææöFR×FÇ2Ö6÷—·¶§W7F–g’×6VÆc§7F'G×××Ð£Â÷7G–ÆSà£Ç67&—Cà¢‚‚’Óâ·°¢6öç7B&÷f–FW"ÒFö7VÖVçBævWDVÆVÖVçD'”–B‚vvVö—×&÷f–FW"r“°¢6öç7BFW67&—F–öâÒFö7VÖVçBævWDVÆVÖVçD'”–B‚vvVò×&÷f–FW"ÖFW67&—F–öâr“°¢–b‚&÷f–FW"’&WGW&ã°¢6öç7BÖ†Ö–æD—FV×2ÒFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚rævVòÖÖ†Ö–æBÖf–VÆBr“°¢6öç7B—–æfô—FV×2ÒFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚rævVòÖ—–æfòÖf–VÆBr“°¢6öç7B6WD†–FFVâÒ†—FV×2Â†–FFVâ’Óâ·°¢—FV×2æf÷$V6‚‚†VÆVÖVçB’Óâ·°¢VÆVÖVçBæ6Æ74Æ—7BçFövvÆR‚vvVò×&÷f–FW"Ö†–FFVârÂ†–FFVâ“°¢VÆVÖVçBçVW'•6VÆV7F÷$ÆÂ‚v–çWBÂ6VÆV7BÂ'WGFöâÂFW‡F&Vr’æf÷$V6‚‚†6öçG&öÂ’Óâ·°¢–b††–FFVâ’6öçG&öÂç6WDGG&–'WFR‚vF—6&ÆVBrÂvF—6&ÆVBr“°¢VÇ6R6öçG&öÂç&VÖ÷fTGG&–'WFR‚vF—6&ÆVBr“°¢×Ò“°¢×Ò“°¢×Ó°¢6öç7BWFFRÒ‚’Óâ·°¢6öç7BÖöFRÒ&÷f–FW"çfÇVS°¢6WD†–FFVâ†Ö†Ö–æD—FV×2ÂÖöFRÓÓÒv—–æfòr“°¢6WD†–FFVâ†—–æfô—FV×2ÂÖöFRÓÓÒvÖ†Ö–æBr“°¢–b†ÖöFRÓÓÒv—–æfòr’FW67&—F–öâçFW‡D6öçFVçBÒt•–æfòW&f÷&×2Æ—fR’Æöö·W3²Ö„Ö–æB7&VFVçF–Ç2æBFF&6RWFFW2&Ræ÷B&WV—&VBâs°¢VÇ6R–b†ÖöFRÓÓÒvÖ†Ö–æBr’FW67&—F–öâçFW‡D6öçFVçBÒtÖ„Ö–æBW6W2Æö6Â4âæB6÷VçG'’FF&6W2v—F‚F†R6fVB66÷VçB”BæBÆ–6Vç6R¶W’âs°¢VÇ6RFW67&—F–öâçFW‡D6öçFVçBÒtWFò6†V6·2•–æfòf—'7BæBW6W2Æö6ÂÖ„Ö–æBFF&6W2öæÇ’v†Vâ•–æfò†2æò&W7VÇBâs°¢×Ó°¢&÷f–FW"æFDWfVçDÆ—7FVæW"‚v6†ævRrÂWFFR“°¢WFFR‚“°§×Ò’‚“° ¢ò¢5E$TÔdõ$tUôäôDUõ4UED”äu5õDÅ5ô5D”ôå5õcS¢ð¢‚‚’Óâ·°¢6öç7B6&G2Ò²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖæöFR×FÇ2Ö6&EÒr•Ó°¢–b‚6&G2æÆVæwF‚’&WGW&ã°¢6öç7B÷7BÒ7–æ2‡W&ÂÂ†÷7B’Óâ·°¢6öç7B&öG’ÒæWrU$Å6V&6…&×2‚“²&öG’ç6WB‚v†÷7BrÂ†÷7B“°¢6öç7B&W7öç6RÒv—BfWF6‚‡W&ÂÂ·¶ÖWF†öC¢uõ5BrÂ&öG’Â7&VFVçF–Ç3¢w6ÖRÖ÷&–v–ârÂ66†S¢væò×7F÷&RrÂ†VFW'3§·²t66WBs¢vÆ–6F–öâö§6öâw×××Ò“°¢ÆWBFFÒ··×Ó²G'’·²FFÒv—B&W7öç6Ræ§6öâ‚“²×Ò6F6‚…öR’··×Ð¢–b‚&W7öç6Ræö²’F‡&÷ræWrW'&÷"…7G&–ær†FFæFWF–ÂÇÂFFæÖW76vRÇÂ‚t…EEr·&W7öç6Rç7FGW2’’“°¢&WGW&âFF°¢×Ó°¢6öç7B6WE&W7VÇBÒ†6&BÂFW‡BÂ6Ç3Òrr’Óâ·²6öç7B&÷ƒÖ6&BçVW'•6VÆV7F÷"‚u¶FFÖæöFR×FÇ2×&W7VÇEÒr“²–b‚&÷‚—&WGW&ã²&÷‚çFW‡D6öçFVçC×FW‡C²&÷‚æ6Æ74æÖSÒvæöFR×FÇ2×&W7VÇBr²†6Ç3òrr¶6Ç3¢rr“²×Ó°¢Fö7VÖVçBæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2†WfVçB’Óâ·°¢6öç7B'WGFöâÒWfVçBçF&vWBæ6Æ÷6W7B‚u¶FFÖæöFR×FÇ2Ö6&EÒ'WGFöâr“²–b‚'WGFöâ’&WGW&ã°¢6öç7B6&BÒ'WGFöâæ6Æ÷6W7B‚u¶FFÖæöFR×FÇ2Ö6&EÒr“²6öç7B†÷7CÕ7G&–ær†6&CòæFF6WBæ†÷7GÇÂrr“²–b‚6&GÇÂ†÷7B—&WGW&ã°¢–b†'WGFöâæÖF6†W2‚u¶FFÖ6÷•Òr’’·°¢6öç7BæöFRÒ6&BçVW'•6VÆV7F÷"†'WGFöâæFF6WBæ6÷“ÓÓÒvæÖRsòu¶FFÖæöFR×FÇ2ÖæÖUÒs¢u¶FFÖæöFR×FÇ2×F&vWEÒr“°¢6öç7BfÇVSÕ7G&–ær†æöFSòçFW‡D6öçFVçGÇÂrr’çG&–Ò‚“²–b‚fÇVR—&WGW&ã°¢G'’·²v—Bæf–vF÷"æ6Æ—&ö&Bçw&—FUFW‡B‡fÇVR“²6WE&W7VÇB†6&BÂt6÷–VBFò6Æ—&ö&BârÂvö²r“²×Ò6F6‚…öR’·²6WE&W7VÇB†6&BÂt6÷’f–ÆVC²6VÆV7BF†RfÇVRÖçVÆÇ’ârÂv&Br“²×Ð¢&WGW&ã°¢×Ð¢–b†'WGFöâæÖF6†W2‚u¶FFÖæöFR×FÇ2×FW7EÒr’’·°¢–b†'WGFöâæF—6&ÆVB—&WGW&ã²'WGFöâæF—6&ÆVC×G'VS²6WE&W7VÇB†6&BÂt6†V6¶–ærV&Æ–2Då>(
brÂv'W7’r“°¢G'’·²6öç7BFFÖv—B÷7B‚r÷æVÂöÖævR÷6WGF–æw2öFç3÷FW7BrÆ†÷7B“²6WE&W7VÇB†6&BÅ7G&–ær†FFæÖW76vWÇÂt4äÔR6†V6²6ö×ÆWFRâr’ÆFFæö³òvö²s¢v&Br“²–b†FFæ—77Væ6U÷VWVVB—·²6öç7B&FvSÖ6&BçVW'•6VÆV7F÷"‚u¶FFÖæöFR×FÇ2×7FFUÒr“²–b†&FvR—·¶&FvRçFW‡D6öçFVçCÒt—77V–ær6W'F–f–6FRs¶&FvRæ6Æ74æÖSÒvæöFR×FÇ2×7FFRv—F–ærs·×Ò6WEF–ÖV÷WB‚‚“ÓæÆö6F–öâç&VÆöB‚’ÃS“²×Ò×Ð¢6F6‚†W'&÷"—·²6WE&W7VÇB†6&BÅ7G&–ær†W'&÷"æÖW76vWÇÆW'&÷"’Âv&Br“²×Ð¢f–æÆÇ’·²'WGFöâæF—6&ÆVCÖfÇ6S²×Ð¢&WGW&ã°¢×Ð¢–b†'WGFöâæÖF6†W2‚u¶FFÖæöFR×FÇ2×&WG'•Òr’’·°¢'WGFöâæF—6&ÆVC×G'VS²6WE&W7VÇB†6&BÂt6W'F–f–6FR&WG'’VWVVN(
brÂv'W7’r“°¢G'’·²6öç7BFFÖv—B÷7B‚r÷æVÂöÖævR÷6WGF–æw2öFç3÷&WG'’rÆ†÷7B“²6WE&W7VÇB†6&BÅ7G&–ær†FFæÖW76vWÇÂt6W'F–f–6FR&WG'’VWVVBâr’Âvö²r“²6WEF–ÖV÷WB‚‚“ÓæÆö6F–öâç&VÆöB‚’Ãƒ“²×Ð¢6F6‚†W'&÷"—·²6WE&W7VÇB†6&BÅ7G&–ær†W'&÷"æÖW76vWÇÆW'&÷"’Âv&Br“²'WGFöâæF—6&ÆVCÖfÇ6S²×Ð¢×Ð¢×Ò“°§×Ò’‚“°£Â÷67&—Cârrp¢&WGW&âö–æE÷vR‡W6W"Âu6WGF–æw2rÂ6öçFVçB  ¢25E$TÔdõ$tUôäôDUõäTÅôÄôt”åôÄôtõõ$õUDUõc##ƒƒ ¤ævWB‚"÷æVÂöÆövò"¦FVbæöFU÷æVÅöÆövò‚“ ¢öæöFUöæÖRÂÆövòÒöæöFUö–FVçF—G•÷6æ6†÷B‚¢–bæ÷BÆövòç7F'G7v—F‚‚"öæöFRÖÆöv÷2ò"“ ¢&—6R…EEW†6WF–öâƒCB¢f–ÆVæÖRÒF‚†Æövõ¶ÆVâ‚"öæöFRÖÆöv÷2ò"“¥Ò’ææÖP¢–bæ÷Bf–ÆVæÖR÷"f–ÆVæÖRÒÆövõ¶ÆVâ‚"öæöFRÖÆöv÷2ò"“¥Ó ¢&—6R…EEW†6WF–öâƒCB¢F‚ÒäôDUôÄôtõõ$ôõBòf–ÆVæÖP¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–×²"çær#¢&–ÖvR÷ær"Â"æ§r#¢&–ÖvRö§Vr"Â"æ§Vr#¢&–ÖvRö§Vr"Â"çvV'#¢&–ÖvR÷vV'"Â"æv–b#¢&–ÖvRöv–b'Ð¢&WGW&âf–ÆU&W7öç6R‡F‚ÆÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Æ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æòÖ66†R'Ò  ¤ævWB‚"÷æVÂöff–6öâ"¦FVbæöFU÷æVÅöff–6öâ‚“ ¢F‚ÒöæöFUöff–6öå÷F‚‚¢–bæ÷BFƒ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–×²"æ–6ò#¢&–ÖvR÷‚Ö–6öâ"Â"çær#¢&–ÖvR÷ær"Â"æ§r#¢&–ÖvRö§Vr"Â"æ§Vr#¢&–ÖvRö§Vr"Â"çvV'#¢&–ÖvR÷vV'"Â"æv–b#¢&–ÖvRöv–b'Ð¢&WGW&âf–ÆU&W7öç6R‡F‚ÂÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æòÖ66†R'Ò ¤ç÷7B‚r÷æVÂöÖævR÷6WGF–æw2öFç3÷FW7Br¦7–æ2FVb–æFWVæFVçE÷6WGF–æw5öFç3÷FW7B‡&WVW7C¢&WVW7B“ ¢&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvæöFW2æVF—Br¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢†÷7BÒ7G"†f÷&ÒævWB‚v†÷7Br’÷"rr’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr¢&W7VÇBÒv—B7–æ6–òçFõ÷F‡&VB…öæöFU÷6WGF–æw5öFç3÷FW7E÷7–æ2Â†÷7B¢25E$TÔdõ$tUôäôDUôDå3õDU5EôUDõô•55TUõc#c¢öæ6RF†R÷W&F÷"w24äÔP¢2—2V&Æ–6Ç’6÷'&V7BÂFòæ÷Bv—BWFòF†RW&–öF–2DÅ2F–ÖW"–çFW'fÂà¢–b&W7VÇBævWB‚vö²r’æBæ÷B&W7VÇBævWB‚v6W'F–f–6FU÷&VG’r’æBæ÷BU…DU$äÅõ$õ…•ôÔôDS ¢ÖævW"ç&WVW7E÷FÇ5÷&V6öæ6–ÆR††÷7BÂf÷&6U÷&WG'“ÕG'VR¢&W7VÇBÒF–7B‡&W7VÇB¢&W7VÇE²v—77Væ6U÷VWVVBuÒÒG'VP¢&W7VÇE²vÖW76vRuÒÒt4äÔR—26÷'&V7BæBV&Æ–6Ç’f—6–&ÆRâ6W'F–f–6FR—77Væ6RVWVVBWFöÖF–6ÆÇ’âp¢&WGW&â¥4ôå&W7öç6R‡&W7VÇB  ¤ç÷7B‚r÷æVÂöÖævR÷6WGF–æw2öFç3÷&WG'’r¦7–æ2FVb–æFWVæFVçE÷6WGF–æw5öFç3÷&WG'’‡&WVW7C¢&WVW7B“ ¢&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvæöFW2æVF—Br¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢†÷7BÒ7G"†f÷&ÒævWB‚v†÷7Br’÷"rr’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚râr¢–b†÷7Bæ÷B–âöæöFU÷6WGF–æw5÷FÇ5ö†÷7G2‚“ ¢&—6R…EEW†6WF–öâƒCÂt†÷7FæÖR—2æ÷B6öæf–wW&VBöâF†—2æöFRr¢–bU…DU$äÅõ$õ…•ôÔôDS ¢&WGW&â¥4ôå&W7öç6R‡²vö²s¢G'VRÂwVWVVBs¢fÇ6RÂvÖW76vRs¢tW‡FW&æÂ&÷‡’ÖöFR÷vç2DÅ2âwÒ¢ÖævW"ç&WVW7E÷FÇ5÷&V6öæ6–ÆR††÷7BÂf÷&6U÷&WG'“ÕG'VR¢&WGW&â¥4ôå&W7öç6R‡²vö²s¢G'VRÂwVWVVBs¢G'VRÂvÖW76vRs¢t6W'F–f–6FR&WG'’VWVVBâ&Wf–÷W24ÔR&6¶öfbf÷"F†—2†÷7FæÖRv–ÆÂ&R6ÆV&VBâwÒ  ¤ævWB‚r÷æVÂöÖævR÷6WGF–æw2rÂ&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVb–æFWVæFVçE÷6WGF–æw2‡&WVW7C¢&WVW7BÂÖW76vS¢7G"ÒrrÂW'&÷#¢7G"Òrr“ ¢W6W#×&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvæöFW2æVF—Br¢&WGW&â…DÔÅ&W7öç6R…÷6WGF–æw5÷vR‡W6W"ÆÖW76vRÆW'&÷"’  ¤ç÷7B‚r÷æVÂöÖævR÷6WGF–æw2r¦7–æ2FVb–æFWVæFVçE÷6WGF–æw5÷6fR‡&WVW7C¢&WVW7B“ ¢W6W#×&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂvæöFW2æVF—Br“²cÖv—B&WVW7Bæf÷&Ò‚¢G'“ ¢–bbævWB‚w&VÖ÷fU÷–÷WGV&Uö6öö¶–W5öf–ÆRr’—2æ÷BæöæS ¢÷&VÖ÷fUöæöFU÷–÷WGV&Uö6öö¶–W2‚¢÷6fUöæöFU÷–÷WGV&Uö6öö¶–W2†bævWB‚w–÷WGV&Uö6öö¶–W5öf–ÆRr’¢W†6WB…fÇVTW'&÷"Âõ4W'&÷"’2W†3 ¢&WGW&â…DÔÅ&W7öç6R…÷6WGF–æw5÷vR‡W6W"ÆW'&÷#×7G"†W†2’’ÃC¢&Wf–÷W5ö66W72Ò66W756WGF–æw5–ÆöB€¢æVÅ÷W&Ç3ÖÆ—7B†ÖævW"çæVÅ÷W&Ç2’Â7G&VÕ÷W&Ç3ÖÆ—7B†ÖævW"ç7G&VÕ÷W&Ç2’À¢66W75÷6ÇVsÖÖævW"æ66W75÷6ÇVrÂ7G&VÕ÷6ÇVsÖÖævW"ç7G&VÕ÷6ÇVrÂ7G&VÕ÷÷'CÖÖævW"ç7G&VÕ÷÷'BÀ¢F÷FÅöÖ…ö6öææV7F–öç3ÖÖævW"çF÷FÅöÖ…ö6öææV7F–öç2À¢6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW3ÖÖævW"æ6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW2À¢æVÅö—÷v†—FVÆ—7CÖÖævW"çæVÅö—÷v†—FVÆ—7BÀ¢æVÅö—ö&Æ6¶Æ—7CÖÖævW"çæVÅö—ö&Æ6¶Æ—7BÀ¢æVÅö6å÷v†—FVÆ—7CÖÖævW"çæVÅö6å÷v†—FVÆ—7BÀ¢æVÅö6åö&Æ6¶Æ—7CÖÖævW"çæVÅö6åö&Æ6¶Æ—7BÀ¢—÷v†—FVÆ—7CÖÖævW"æ—÷v†—FVÆ—7BÂ—ö&Æ6¶Æ—7CÖÖævW"æ—ö&Æ6¶Æ—7BÀ¢6å÷v†—FVÆ—7CÖÖævW"æ6å÷v†—FVÆ—7BÂ6åö&Æ6¶Æ—7CÖÖævW"æ6åö&Æ6¶Æ—7BÀ¢¢G'“ ¢25E$TÔdõ$tUôäôDUõ4UED”äu5õõ5Eõ$U5ôå4Uõ$T4ôä4”ÄUõcS#¢W'6—7Bæ@¢2&6²×7–æ2f—'7C²V&Æ–2Æ—7FVæW"õDÅ26†ævW2&R&V6öæ6–ÆVBgFW"F†P¢2&W7öç6R6ò6f–ær6WGF–æw26ææ÷BFV"F÷vâ—G2÷vâ…EE2&WVW7Bà¢ÖævW"ç7–æ5ö66W72„66W756WGF–æw5–ÆöB€¢æVÅ÷W&Ç3×7G"†bævWB‚wæVÅ÷W&Ç2r’÷"rr’ç&WÆ6R‚uÇ"rÂrr’ç7Æ—B‚uÆâr’À¢7G&VÕ÷W&Ç3×7G"†bævWB‚w7G&VÕ÷W&Ç2r’÷"rr’ç&WÆ6R‚uÇ"rÂrr’ç7Æ—B‚uÆâr’À¢66W75÷6ÇVsÒ""À¢7G&VÕ÷6ÇVsÒ""À¢7G&VÕ÷÷'CÔæöæRÀ¢25E$TÔdõ$tUôäôDUõ4UED”äu5ô„”DUõDõDÅôÔ…õcS¢F†—26ÇW7FW"×v–FP¢2Æ–Ö—B—2Ö–âÖÖævVC²F†R7FæFÆöæRæöFR6WGF–æw2T’æòÆöævW ¢2&Ww&—FW2—Bv†Vâ6f–ærVç&VÆFVB6WGF–æw2à¢F÷FÅöÖ…ö6öææV7F–öç3Ö–çB†ÖævW"çF÷FÅöÖ…ö6öææV7F–öç2’À¢6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW3ÖÖ‚ƒÂÖ–âƒƒÂ–çB†bævWB‚v6Æ–VçE÷6W76–öå÷&W6WEööffÆ–æUöÖ–çWFW2r’÷"c’’’À¢æVÅö—÷v†—FVÆ—7C×7G"†bævWB‚wæVÅö—÷v†—FVÆ—7Br’÷"rr’À¢æVÅö—ö&Æ6¶Æ—7C×7G"†bævWB‚wæVÅö—ö&Æ6¶Æ—7Br’÷"rr’À¢æVÅö6å÷v†—FVÆ—7C×7G"†bævWB‚wæVÅö6å÷v†—FVÆ—7Br’÷"rr’À¢æVÅö6åö&Æ6¶Æ—7C×7G"†bævWB‚wæVÅö6åö&Æ6¶Æ—7Br’÷"rr’À¢—÷v†—FVÆ—7C×7G"†bævWB‚v—÷v†—FVÆ—7Br’÷"rr’Â—ö&Æ6¶Æ—7C×7G"†bævWB‚v—ö&Æ6¶Æ—7Br’÷"rr’À¢6å÷v†—FVÆ—7C×7G"†bævWB‚v6å÷v†—FVÆ—7Br’÷"rr’Â6åö&Æ6¶Æ—7C×7G"†bævWB‚v6åö&Æ6¶Æ—7Br’÷"rr’À¢’ÂÇ•öÆ—7FVæW'3ÔfÇ6R¢G'“ ¢ÖævW"æ&6µ÷7–æ5ö66W75÷FõöÖ–â‚¢W†6WB'VçF–ÖTW'&÷# ¢ÖævW"ç7–æ5ö66W72‡&Wf–÷W5ö66W72¢&—6P¢ÖævW"ç6fUövVõ÷6WGF–æw2„vVõ6WGF–æw5–ÆöB€¢&÷f–FW#×7G"†bævWB‚w&÷f–FW"r’÷"ÖævW"ævVõ÷&÷f–FW"’ÂWFõ÷WFFSÖbævWB‚vvVõöWFõ÷WFFRr’—2æ÷BæöæRÀ¢Ö†Ö–æEö66÷VçEö–C×7G"†bævWB‚vÖ†Ö–æEö66÷VçEö–Br’÷"ÖævW"æÖ†Ö–æEö66÷VçEö–B’À¢Ö†Ö–æEöÆ–6Vç6Uö¶W“×7G"†bævWB‚vÖ†Ö–æEöÆ–6Vç6Uö¶W’r’÷"rr’Â—–æfõ÷Fö¶Vã×7G"†bævWB‚v—–æfõ÷Fö¶Vâr’÷"rr’À¢&VÖ÷fUöÖ†Ö–æEö¶W“ÖbævWB‚w&VÖ÷fUöÖ†Ö–æEö¶W’r’—2æ÷BæöæRÂ&VÖ÷fUö—–æfõ÷Fö¶VãÖbævWB‚w&VÖ÷fUö—–æfõ÷Fö¶Vâr’—2æ÷BæöæRÀ¢’¢÷W&F–öç2ÒöæöFUö÷W&F–öç5÷6WGF–æw2‚¢÷W&F–öç5²vÆöu÷&WFVçF–öåöF—2uÒÒÖ‚ƒÂÖ–âƒ3cSÂ–çB†bævWB‚vÆöu÷&WFVçF–öåöF—2r’÷"3’’¢÷W&F–öç5²vÖWG&–75÷&WFVçF–öåöF—2uÒÒÖ‚ƒÂÖ–âƒ3cSÂ–çB†bævWB‚vÖWG&–75÷&WFVçF–öåöF—2r’÷"3’’¢÷W&F–öç5²vÖWG&–75öFVfVÇE÷7åö†÷W'2uÒÒÖ‚ƒÂÖ–â†÷W&F–öç5²vÖWG&–75÷&WFVçF–öåöF—2uÒ¢#BÂ–çB†bævWB‚vÖWG&–75öFVfVÇE÷7åö†÷W'2r’÷"#B’’¢÷W&F–öç5²vÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG2uÒÒÖ‚ƒRÂÖ–âƒ3cÂ–çB†bævWB‚vÖWG&–75÷6×ÆUö–çFW'fÅ÷6V6öæG2r’÷"c’’¢õU$D”ôå5õ4UED”äu5ôd”ÄRç&VçBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢FV×÷&'’ÒõU$D”ôå5õ4UED”äu5ôd”ÄRçv—F…÷7Vff—‚‚rçF×r¢FV×÷&'’çw&—FU÷FW‡B†§6öâæGV×2†÷W&F–öç2Â6W&F÷'3Ò‚rÂrÂs¢r’’ÂVæ6öF–æsÒwWFbÓ‚r¢FV×÷&'’ç&WÆ6R„õU$D”ôå5õ4UED”äu5ôd”ÄR¢ÖævW"ç'VæUöWfVçEöÆöw2†f÷&6SÕG'VR¢25E$TÔdõ$tUôäôDUõ4UED”äu5õ4dUõ$UEU$åõcS3¢Fòæ÷B&W7F'Bç¢2–â×&ö6W72Æ—7FVæW"g&öÒF†R&WVW7Bv÷&¶W"âF†R&ö÷BDÅ2†VÇW"—0¢2G&–vvW&VB–æFWVæFVçFÇ’æBæv–ç‚&VÆöG2w&6VgVÆÇ’à¢ÖævW"ç&WVW7E÷FÇ5÷&V6öæ6–ÆR‚¢ÖævW"æÆör‚tæöFR66W726WGF–æw2WFFVBæB7–æ6‡&öæ—¦VBFòÖ–ârÇ66÷SÒw6WGF–æw2rÆFWF–Ç3Öbv7F÷#×·W6W"çW6W&æÖWÒr¢ÖF6†VE÷&Vf—‚Òô5U%$TåEõäTÅõ$Td•‚ævWB‚¢V&Æ–5÷&ö÷BÒÖF6†VE÷&Vf—‚–bÖF6†VE÷&Vf—‚—2æ÷BæöæRVÇ6R†br÷¶ÖævW"æ66W75÷6ÇVwÒr–bÖævW"æ66W75÷6ÇVrVÇ6Rròr¢V&Æ–5÷&ö÷BÒV&Æ–5÷&ö÷B÷"ròp¢F&vWBÒr÷æVÂöÖævR÷6WGF–æw3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚u6WGF–æw26fVBæB7–æ6‡&öæ—¦VBFòÖ–âr¢2F†R&W7öç6R—26VçBf—'7BâF†R&6¶w&÷VæBF6²w&—FW27&÷72×&ö6W70¢2&V6öæ6–ÆR&WVW7C²F†RæF—fR6öçG&öÂvF6†W"&VÆöG266W72æ§6öâæ@¢2W&f÷&×2F†RÆ—7FVæW"õDÅ2Ç’v—F†÷WB&V7W'6—fRÆö÷&6²…EEà¢&W7öç6RÒ&VF—&V7E&W7öç6R€¢V&Æ–5÷&ö÷BÂ32À¢&6¶w&÷VæCÔ&6¶w&÷VæEF6²†ÖævW"çVWVUö6öçG&öÅö66W75÷&V6öæ6–ÆUögFW%÷&W7öç6R’À¢¢÷6WE÷æVÅ÷&÷WFUö6öö¶–R‡&W7öç6RÂ&WVW7BÂF&vWB¢&WGW&â&W7öç6P¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&WGW&â…DÔÅ&W7öç6R…÷6WGF–æw5÷vR‡W6W"ÆW'&÷#×7G"†W†2’’ÃC   ¢25E$TÔdõ$tUôäôDUôÄ•dUõ4U54”ôåô´”ÄÅôäõôäeõcC3 ¦FVb÷æVÅ÷6W76–öåö¶–ÆÅögFW%÷&W7öç6R†7F÷#¢7G"Â6W76–öåö–C¢7G"Â6÷W&6Uö¶W“¢7G"ÂW6W%ö–C¢7G"’ÓâæöæS ¢""%'Vâ÷FVçF–ÆÇ’6Æ÷röF—7'WF—fR6W76–öâ6ÆVçWöæÇ’gFW"…EE6²â"" ¢6ÆVæVE÷6÷W&6RÒ7G"‡6÷W&6Uö¶W’÷"vF—&V7EöæöFRr¢G'“ ¢–b6ÆVæVE÷6÷W&6RÓÒvF—&V7EöæöFRs ¢¶–ÆÆVBÒÖævW"æ¶–ÆÅ÷f–WvW%÷6W76–öâ‡6W76–öåö–B¢VÇ6S ¢&W7VÇBÒÖævW"çæVÅö6öçG&öÅ÷&WVW7B‚uõ5BrÂrö’÷cöæöFRÖ6öçG&öÂ÷f–WvW'2ö¶–ÆÂrÂ°¢v7F÷"s¢7F÷"Âw6W76–öåö–Bs¢6W76–öåö–BÂw6÷W&6Uö¶W’s¢6ÆVæVE÷6÷W&6RÂwW6W%ö–Bs¢W6W%ö–BÀ¢ÒÂF–ÖV÷WCÓ"ã¢¶–ÆÆVBÒ–çB‡&W7VÇBævWB‚v¶–ÆÆVBr’÷"¢ÖævW"æÆör€¢tÆ—fR6W76–öâ¶–ÆÂ6ö×ÆWFVBrÂ66÷SÒwW6W"rÂÆWfVÃÒwv&æ–ærrÀ¢FWF–Ç3Öbv7F÷#×¶7F÷'Ó²6W76–öåö–C×·6W76–öåö–GÓ²6÷W&6S×¶6ÆVæVE÷6÷W&6WÓ²¶–ÆÆVC×¶¶–ÆÆVGÒrÀ¢¢W†6WBW†6WF–öâ2W†3 ¢2F†R'&÷w6W"†2Ç&VG’&V6V—fVB—G26¶æ÷vÆVFvVÖVçBÂ6òG&ç6–Vç@¢2&VF—2ôÖ–â6ÆVçWf–ÇW&R—2&W÷'FVBFòæöFRÆöw2–ç7FVBöbFV&–æp¢2F÷vâF†RæVÂ&WVW7BæB7W&f6–ærâæv–ç‚S"vRà¢ÖævW"æÆör€¢tÆ—fR6W76–öâ¶–ÆÂf–ÆVBrÂ66÷SÒwW6W"rÂÆWfVÃÒvW'&÷"rÀ¢FWF–Ç3Öbv7F÷#×¶7F÷'Ó²6W76–öåö–C×·6W76–öåö–GÓ²6÷W&6S×¶6ÆVæVE÷6÷W&6WÓ²W'&÷#×¶W†7ÒrÀ¢  ¤ç÷7B‚r÷æVÂ÷6W76–öç2÷·6W76–öåö–GÒö¶–ÆÂr¦7–æ2FVb–æFWVæFVçE÷6W76–öåö¶–ÆÂ‡6W76–öåö–C¢7G"Â&WVW7C¢&WVW7B“ ¢W6W"Ò&WV—&UöæöFU÷æVÅ÷W6W"‡&WVW7BÂw7G&VÕ÷W6W'2æ¶–ÆÂr¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢6÷W&6Uö¶W’Ò7G"†f÷&ÒævWB‚w6÷W&6Uö¶W’r’÷"vF—&V7EöæöFRr¢W6W%ö–BÒ7G"†f÷&ÒævWB‚wW6W%ö–Br’÷"rr¢6ÆVæVE÷6W76–öâÒö6ÆVåöÆ—fU÷6W76–öåö¶–ÆÅö–B‡6W76–öåö–B¢–bæ÷B6ÆVæVE÷6W76–öã ¢–b7G"‡&WVW7Bæ†VFW'2ævWB‚w‚×7G&VÖf÷&vRÖÆ—fRÖ¶–ÆÂr’÷"rr’ÓÒss ¢&WGW&â¥4ôå&W7öç6R‡²vö²s¢fÇ6RÂvW'&÷"s¢u6W76–öâ”B—2&WV—&VBwÒÂ7FGW5ö6öFSÓC¢&WGW&â&VF—&V7E&W7öç6R‚r÷æVÂ÷6W76–öç3öW'&÷#Òr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚u6W76–öâ”B—2&WV—&VBr’Â32¢F6²Ò&6¶w&÷VæEF6²…÷æVÅ÷6W76–öåö¶–ÆÅögFW%÷&W7öç6RÂW6W"çW6W&æÖRÂ6ÆVæVE÷6W76–öâÂ6÷W&6Uö¶W’ÂW6W%ö–B¢vçG5ö§6öâÒ€¢7G"‡&WVW7Bæ†VFW'2ævWB‚w‚×7G&VÖf÷&vRÖÆ—fRÖ¶–ÆÂr’÷"rr’ç7G&—‚’ÓÒsp¢÷"vÆ–6F–öâö§6öâr–â7G"‡&WVW7Bæ†VFW'2ævWB‚v66WBr’÷"rr’æÆ÷vW"‚¢¢–bvçG5ö§6öã ¢&WGW&â¥4ôå&W7öç6R€¢²vö²s¢G'VRÂv66WFVBs¢G'VRÂw6W76–öåö–Bs¢6ÆVæVE÷6W76–öçÒÀ¢7FGW5ö6öFSÓ#"Â&6¶w&÷VæC×F6²À¢¢&WGW&â&VF—&V7E&W7öç6R€¢r÷æVÂ÷6W76–öç3öÖW76vSÒr²W&ÆÆ–"ç'6RçV÷FU÷ÇW2‚t¶–ÆÂ&WVW7B66WFVBr’À¢32Â&6¶w&÷VæC×F6²À¢  ¦FVböæöFU÷‡G&VÕ÷W6W"‡W6W&æÖS¢7G"Â77v÷&C¢7G"’ÓâæöFUW6W$6öæf–rÂæöæS ¢–bæ÷BÖævW"çæVÅö6öææV7FVB‚’æBæ÷BÖævW"æ–æFWVæFVçEöÖöFS ¢&WGW&âæöæP¢25E$TÔdõ$tUôäôDUõÄ”Ä•5EõU4U%ôUD…õ$TÄôEõc#3 ¢2V&Æ–2wVæ–6÷&âv÷&¶W'2&R6W&FR&ö6W76W2g&öÒF†RæöFRæVÂâ&VÆö@¢2F†RWF†÷&—FF—fR6æ6†÷B¦&Vf÷&R¢W6W&æÖRÆöö·W6òæWvÇ’Ö7&VFVB÷ ¢2&VæÖVB66÷VçB—2–ÖÖVF–FVÇ’f—6–&ÆRöâvWBç‡÷Æ–W%ö’õvV%Æ–W"à¢ÖævW"ç&VÆöE÷W6W'5ö–eö6†ævVB‚¢6ÆVæVBÒ7G"‡W6W&æÖR÷"""’ç7G&—‚’æÆ÷vW"‚¢v—F‚ÖævW"æÆö6³ ¢6æF–FFW2ÒÆ—7B†ÖævW"çW6W'2çfÇVW2‚’¢f÷"W6W"–â6æF–FFW3 ¢–bæ÷BW6W"çW6W&æÖR÷"W6W"çW6W&æÖRç7G&—‚’æÆ÷vW"‚’Ò6ÆVæVC ¢6öçF–çVP¢7W'&VçBÒÖævW"çfÆ–E÷W6W"‡W6W"çFö¶Vâ¢–b7W'&VçBæB7W'&VçBç77v÷&Eö†6‚æBfW&–g•÷67'—E÷77v÷&B‡77v÷&BÂ7W'&VçBç77v÷&Eö†6‚“ ¢&WGW&â7W'&Vç@¢&WGW&âæöæP  ¦FVböVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W#¢æöFUW6W$6öæf–r’ÓâÆ—7E´æöFUW6W$6†ææVÅÓ ¢ÖævW"ç&VÆöEö6†ææVÅö6FÆöuö–eö6†ævVB‚¢&öf–ÆRÒæöæP¢&öf–ÆUö–BÒ7G"†vWFGG"‡W6W"Â'Æ–Æ—7E÷&öf–ÆUö–B"Â""’÷"""’ç7G&—‚¢–b&öf–ÆUö–C ¢v—F‚ÖævW"æÆö6³ ¢&öf–ÆRÒÖævW"çÆ–Æ—7G2ævWB‡&öf–ÆUö–B¢–b&öf–ÆRæBæ÷B&öf–ÆRæVæ&ÆVC ¢&öf–ÆRÒæöæP¢ÆÅöæöFUö6†ææVÇ2Ò&ööÂ‡&öf–ÆRæÆÅöæöFUö6†ææVÇ2’–b&öf–ÆRVÇ6R&ööÂ‡W6W"æÆÅöæöFUö6†ææVÇ2¢6÷W&6Uö6†ææVÇ2ÒÆ—7B‡&öf–ÆRæ6†ææVÇ2’–b&öf–ÆRVÇ6RÆ—7B‡W6W"æ6†ææVÇ2¢&WVW7FVEö6†ææVÅö÷&FW"ÒÆ—7B‡&öf–ÆRçÆ–Æ—7Eö÷&FW"’–b&öf–ÆRVÇ6RÆ—7B‡W6W"çÆ–Æ—7Eö÷&FW"¢&WVW7FVEö6FVv÷'•ö÷&FW"ÒÆ—7B‡&öf–ÆRçÆ–Æ—7Eö6FVv÷'•ö÷&FW"’–b&öf–ÆRVÇ6RÆ—7B‡W6W"çÆ–Æ—7Eö6FVv÷'•ö÷&FW"¢–bæ÷BÆÅöæöFUö6†ææVÇ3 ¢6†ææVÇ2Ò6÷W&6Uö6†ææVÇ0¢–bæ÷BÖævW"æ–æFWVæFVçEöÖöFS ¢v—F‚ÖævW"æÆö6³ ¢Æö6Å÷'VçF–ÖW2Ò°¢'Bf÷"'B–âÖævW"æ6†ææVÇ2çfÇVW2‚¢–b'Bæ6öæf–ræ6FÆöuö÷væW"ÓÒ&Æö6Â"æB'Bæ6öæf–ræVæ&ÆVBæB'Bæ6öæf–ræ÷WGWE÷G—RÓÒ&†Ç2 ¢Ð¢¶æ÷vâÒ¶—FVÒæ¶W’f÷"—FVÒ–â6†ææVÇ7Ð¢6†ææVÇ2æW‡FVæB€¢æöFUW6W$6†ææVÂ€¢¶W“×'Bæ6öæf–ræ¶W’ÂæÖS×'Bæ6öæf–rææÖRÂ6ÇVs×'Bæ6öæf–rç6ÇVr÷"'Bæ6öæf–ræ¶W’À¢6FVv÷'“Ò‡'Bæ6öæf–ræ6FVv÷'’÷"%Væ6FVv÷&—¦VB"’ç7G&—‚’÷"%Væ6FVv÷&—¦VB"Â6FVv÷&–W3ÖÆ—7B†vWFGG"‡'Bæ6öæf–rÂv6FVv÷&–W2rÂµÒ’÷"µÒ’À¢6FVv÷'•ö÷&FW#Ö–çB‡'Bæ6öæf–ræ6FVv÷'•ö÷&FW"÷"’Â6†ææVÅö÷&FW#Ö–çB†vWFGG"‡'Bæ6öæf–rÂv6†ææVÅö÷&FW"rÂ’÷"’ÂÆövõ÷W&Ã×'Bæ6öæf–ræÆövõ÷W&Â÷"""À¢¢f÷"'B–âÆö6Å÷'VçF–ÖW2–b'Bæ6öæf–ræ¶W’æ÷B–â¶æ÷và¢¢VÇ6S ¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖW2ÒÆ—7B†ÖævW"æ6†ææVÇ2çfÇVW2‚’¢6†ææVÇ2Ò°¢æöFUW6W$6†ææVÂ€¢¶W“×'Bæ6öæf–ræ¶W’ÂæÖS×'Bæ6öæf–rææÖRÂ6ÇVs×'Bæ6öæf–rç6ÇVr÷"'Bæ6öæf–ræ¶W’À¢6FVv÷'“Ò‡'Bæ6öæf–ræ6FVv÷'’÷"%Væ6FVv÷&—¦VB"’ç7G&—‚’÷"%Væ6FVv÷&—¦VB"Â6FVv÷&–W3ÖÆ—7B†vWFGG"‡'Bæ6öæf–rÂv6FVv÷&–W2rÂµÒ’÷"µÒ’À¢6FVv÷'•ö÷&FW#Ö–çB‡'Bæ6öæf–ræ6FVv÷'•ö÷&FW"÷"’Â6†ææVÅö÷&FW#Ö–çB†vWFGG"‡'Bæ6öæf–rÂv6†ææVÅö÷&FW"rÂ’÷"’ÂÆövõ÷W&Ã×'Bæ6öæf–ræÆövõ÷W&Â÷"""À¢¢f÷"'B–â'VçF–ÖW2–b'Bæ6öæf–ræVæ&ÆVBæB'Bæ6öæf–ræ÷WGWE÷G—RÓÒ&†Ç2 ¢Ð ¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖUöÖÒF–7B†ÖævW"æ6†ææVÇ2¢F—7Æ•ö–G2ÒöæöFUö6†ææVÅöF—7Æ•ö–G2‚ ¢2Ö–âÆ–Æ—7B6æ6†÷G26''’&÷F‚F†RÖ–â6†ææVÂ”BæBF†R¶W’F†@¢2W†—7FVBv†VâF†R6æ6†÷Bv26÷–VBâF†R¶W’6öçF–ç2F†R6†ææVÂ6ÇVp¢2‡6bÓÆ–CâÓÇ6ÇVsâ’Â6òÆFW"6ÇVr&VæÖR6âÆVfRF†RæöFR'VçF–ÖRöâà¢2öÆFW"¶W’v†–ÆRÖ–âGfW'F—6W2F†RæWr¶W’â&W6öÇfR'’7F&ÆR7G&VÒ”@¢2f—'7BÂF†Vâ'’6ÇVröæÖRÂæB&Ww&—FRF†R6æ6†÷B—FVÒFòF†RÆ—fR¶W’à¢2F†—2&W—'2W†—7F–ærW6W'2B&VBF–ÖRv—F†÷WB&WV—&–ær&V7&VF–öâà¢'VçF–ÖUö'•÷7G&VÕö–C¢F–7E¶–çBÂç•ÒÒ·Ð¢'VçF–ÖUö'•÷6ÇVs¢F–7E·7G"Âç•ÒÒ·Ð¢'VçF–ÖUö'•öæÖS¢F–7E·7G"Âç•ÒÒ·Ð¢f÷"'VçF–ÖUö¶W’Â'VçF–ÖR–â'VçF–ÖUöÖæ—FV×2‚“ ¢ÖF6‚Ò&RæÖF6‚‡"%ç6bÒ…ÆB²’Ò"Â7G"‡'VçF–ÖUö¶W’÷"""’¢–bÖF6ƒ ¢'VçF–ÖUö'•÷7G&VÕö–Bç6WFFVfVÇB†–çB†ÖF6‚æw&÷Wƒ’’Â'VçF–ÖR¢6ÇVuö¶W’Ò7G"†vWFGG"‡'VçF–ÖRæ6öæf–rÂ'6ÇVr"Â""’÷"""’ç7G&—‚’æÆ÷vW"‚¢æÖUö¶W’Ò7G"†vWFGG"‡'VçF–ÖRæ6öæf–rÂ&æÖR"Â""’÷"""’ç7G&—‚’æÆ÷vW"‚¢–b6ÇVuö¶W“ ¢'VçF–ÖUö'•÷6ÇVrç6WFFVfVÇB‡6ÇVuö¶W’Â'VçF–ÖR¢–bæÖUö¶W“ ¢'VçF–ÖUö'•öæÖRç6WFFVfVÇB†æÖUö¶W’Â'VçF–ÖR ¢Vç&–6†VC¢Æ—7E´æöFUW6W$6†ææVÅÒÒµÐ¢6VVã¢6WE·7G%ÒÒ6WB‚¢Ö–åö÷&FW"ÒöÖ–åö6FVv÷'•ö÷&FW%öÖ‚¢f÷"—FVÒ–â6†ææVÇ3 ¢'BÒ'VçF–ÖUöÖævWB†—FVÒæ¶W’¢7F&ÆU÷7G&VÕö–C¢–çBÂæöæRÒæöæP¢–b—FVÒç7G&VÕö–B—2æ÷BæöæS ¢G'“ ¢7F&ÆU÷7G&VÕö–BÒ–çB†—FVÒç7G&VÕö–B¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢7F&ÆU÷7G&VÕö–BÒæöæP¢–b7F&ÆU÷7G&VÕö–B—2æöæS ¢6fVEö¶W•öÖF6‚Ò&RæÖF6‚‡"%ç6bÒ…ÆB²’Ò"Â7G"†—FVÒæ¶W’÷"""’¢–b6fVEö¶W•öÖF6ƒ ¢7F&ÆU÷7G&VÕö–BÒ–çB‡6fVEö¶W•öÖF6‚æw&÷Wƒ’¢–bæ÷B'BæB7F&ÆU÷7G&VÕö–B—2æ÷BæöæS ¢'BÒ'VçF–ÖUö'•÷7G&VÕö–BævWB‡7F&ÆU÷7G&VÕö–B¢–bæ÷B'C ¢'BÒ'VçF–ÖUö'•÷6ÇVrævWB‡7G"†—FVÒç6ÇVr÷"""’ç7G&—‚’æÆ÷vW"‚’¢–bæ÷B'C ¢'BÒ'VçF–ÖUö'•öæÖRævWB‡7G"†—FVÒææÖR÷"""’ç7G&—‚’æÆ÷vW"‚’ ¢&W6öÇfVEö¶W’Ò7G"‡'Bæ6öæf–ræ¶W’’–b'BVÇ6R7G"†—FVÒæ¶W’¢–b&W6öÇfVEö¶W’–â6VVã ¢6öçF–çVP¢6VVâæFB‡&W6öÇfVEö¶W’¢–b'BæB'Bæ6öæf–ræVæ&ÆVBæB'Bæ6öæf–ræ÷WGWE÷G—RÓÒv†Ç2s ¢6FVv÷'’Ò‡'Bæ6öæf–ræ6FVv÷'’÷"uVæ6FVv÷&—¦VBr’ç7G&—‚’÷"uVæ6FVv÷&—¦VBp¢&V6V—fVEö÷&FW"Ò–çB†—FVÒæ6FVv÷'•ö÷&FW"–b—FVÒæ6FVv÷'•ö÷&FW"—2æ÷BæöæRVÇ6R¢&V6V—fVEö÷&FW"ÒÖ–åö÷&FW"ævWB†6FVv÷'’æÆ÷vW"‚’Â&V6V—fVEö÷&FW"–b&V6V—fVEö÷&FW"ÂVÇ6R–çB‡'Bæ6öæf–ræ6FVv÷'•ö÷&FW"÷"’¢Vç&–6†VBæVæB†—FVÒæÖöFVÅö6÷’‡WFFS×°¢w7G&VÕö–Bs¢F—7Æ•ö–G2ævWB‡&W6öÇfVEö¶W’Â—FVÒç7G&VÕö–B’À¢v¶W’s¢&W6öÇfVEö¶W’À¢væÖRs¢'Bæ6öæf–rææÖRÂw6ÇVrs¢'Bæ6öæf–rç6ÇVr÷"'Bæ6öæf–ræ¶W’À¢v6FVv÷'’s¢6FVv÷'’Âv6FVv÷&–W2s¢Æ—7B†vWFGG"‡'Bæ6öæf–rÂv6FVv÷&–W2rÂµÒ’÷"vWFGG"†—FVÒÂv6FVv÷&–W2rÂµÒ’÷"µÒ’Âv6FVv÷'•ö÷&FW"s¢&V6V—fVEö÷&FW"À¢v6†ææVÅö÷&FW"s¢–çB†vWFGG"‡'Bæ6öæf–rÂv6†ææVÅö÷&FW"rÂvWFGG"†—FVÒÂv6†ææVÅö÷&FW"rÂ’’÷"’À¢vÆövõ÷W&Âs¢'Bæ6öæf–ræÆövõ÷W&Â÷"—FVÒæÆövõ÷W&ÂÀ¢Ò’¢VÆ–bæ÷B'C ¢6FVv÷'’Ò†—FVÒæ6FVv÷'’÷"uVæ6FVv÷&—¦VBr’ç7G&—‚’÷"uVæ6FVv÷&—¦VBp¢&V6V—fVEö÷&FW"Ò–çB†—FVÒæ6FVv÷'•ö÷&FW"–b—FVÒæ6FVv÷'•ö÷&FW"—2æ÷BæöæRVÇ6R¢&V6V—fVEö÷&FW"ÒÖ–åö÷&FW"ævWB†6FVv÷'’æÆ÷vW"‚’Â&V6V—fVEö÷&FW"¢Vç&–6†VBæVæB†—FVÒæÖöFVÅö6÷’‡WFFS×²v6FVv÷'’s¢6FVv÷'’Âv6FVv÷&–W2s¢Æ—7B†vWFGG"†—FVÒÂv6FVv÷&–W2rÂµÒ’÷"µÒ’Âv6FVv÷'•ö÷&FW"s¢&V6V—fVEö÷&FW"Âv6†ææVÅö÷&FW"s¢–çB†vWFGG"†—FVÒÂv6†ææVÅö÷&FW"rÂ’÷"—Ò’ ¢6FVv÷'•÷&æ·3¢F–7E·7G"Â–çEÒÒ·Ð¢6FVv÷'•öæÖW3¢F–7E·7G"Â7G%ÒÒ·Ð¢f÷"—FVÒ–âVç&–6†VC ¢F—7Æ•öæÖRÒ†—FVÒæ6FVv÷'’÷"uVæ6FVv÷&—¦VBr’ç7G&—‚’÷"uVæ6FVv÷&—¦VBp¢6FVv÷'•öæÖRÒF—7Æ•öæÖRæÆ÷vW"‚’÷"wVæ6FVv÷&—¦VBp¢6FVv÷'•öæÖW2ç6WFFVfVÇB†6FVv÷'•öæÖRÂF—7Æ•öæÖR¢&æ²Ò–çB†—FVÒæ6FVv÷'•ö÷&FW"–b—FVÒæ6FVv÷'•ö÷&FW"—2æ÷BæöæRVÇ6R¢6FVv÷'•÷&æ·5¶6FVv÷'•öæÖUÒÒÖ–â‡&æ²Â6FVv÷'•÷&æ·2ævWB†6FVv÷'•öæÖRÂ&æ²’ ¢&WVW7FVEö6FVv÷&–W3¢Æ—7E·7G%ÒÒµÐ¢f÷"fÇVR–â&WVW7FVEö6FVv÷'•ö÷&FW"÷"µÓ ¢6ÆVæVBÒ7G"‡fÇVR÷"rr’ç7G&—‚’æÆ÷vW"‚’÷"wVæ6FVv÷&—¦VBp¢–b6ÆVæVB–â6FVv÷'•öæÖW2æB6ÆVæVBæ÷B–â&WVW7FVEö6FVv÷&–W3 ¢&WVW7FVEö6FVv÷&–W2æVæB†6ÆVæVB¢FVfVÇEö6FVv÷&–W2Ò6÷'FVB€¢6FVv÷'•öæÖW2À¢¶W“ÖÆÖ&F¶W“¢†6FVv÷'•÷&æ·2ævWB†¶W’Â’Âö6FVv÷'•÷6÷'E÷fÇVR†6FVv÷'•öæÖW5¶¶W•Ò’Â¶W’’À¢¢&WVW7FVEö6FVv÷&–W2æW‡FVæB†¶W’f÷"¶W’–âFVfVÇEö6FVv÷&–W2–b¶W’æ÷B–â&WVW7FVEö6FVv÷&–W2¢6FVv÷'•÷÷6—F–öâÒ¶¶W“¢–æFW‚f÷"–æFW‚Â¶W’–âVçVÖW&FR‡&WVW7FVEö6FVv÷&–W2—Ð ¢÷&FW#¢Æ—7E·7G%ÒÒµÐ¢f÷"¶W’–â&WVW7FVEö6†ææVÅö÷&FW"÷"µÓ ¢6ÆVæVBÒ7G"†¶W’÷"rr’ç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–â÷&FW# ¢÷&FW"æVæB†6ÆVæVB¢÷&FW%öÖÒ¶¶W“¢–æFW‚f÷"–æFW‚Â¶W’–âVçVÖW&FR†÷&FW"—Ð¢&WGW&â6÷'FVB†Vç&–6†VBÂ¶W“ÖÆÖ&F—FVÓ¢€¢6FVv÷'•÷÷6—F–öâævWB‚†—FVÒæ6FVv÷'’÷"uVæ6FVv÷&—¦VBr’ç7G&—‚’æÆ÷vW"‚’÷"wVæ6FVv÷&—¦VBrÂ’À¢–b—FVÒæ¶W’–â÷&FW%öÖVÇ6RÀ¢÷&FW%öÖævWB†—FVÒæ¶W’Â“““““’’À¢–çB†vWFGG"†—FVÒÂv6†ææVÅö÷&FW"rÂ’÷"’À¢—FVÒææÖRæÆ÷vW"‚’Â—FVÒæ¶W’À¢’  ¦FVbö6†ææVÅ÷Æ–&6µ÷&VG’†6†ææVÅö¶W“¢7G"’Óâ&ööÃ ¢""%&WGW&â7W'&VçB„Å2&VF–æW72v—F†÷WB7WW'f—6÷"%2à ¢5E$TÔdõ$tUôäôDUõ4”ätÄUô4„ääTÅõ$TE•õccP¢5E$TÔdõ$tUôäôDUô4DÄôuôe$U4…ô„Å5ôUD„õ$•E•õc#€¢5E$TÔdõ$tUôäôDUô4DÄôuôäõõ5UU%d•4õ%õt•Eõc##3 ¢F†RÆ–Æ—7BæBvV"Æ–W"6FÆöwVR&R6W'fVB'’F†RV&Æ–2v÷&¶W"öà¢F†R6ÖR„Å2f–ÆW7—7FVÒâg&W6‚Æ–Æ—7BöæWvW7B6VvÖVçB—2WF†÷&—FF—fRÀ¢v†–ÆRÖævW"ç7FGW2‚’Ö’&Æö6²WFòF†R7WW'f—6÷"%2F–ÖV÷WBà¢"" ¢6VvÖVçE÷F–ÖRÒ¢G'“ ¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB†6†ææVÅö¶W’¢–b'VçF–ÖR—2æ÷BæöæS ¢6VvÖVçE÷F–ÖRÒÖ‚ƒÂ–çB‡'VçF–ÖRæ6öæf–ræ†Ç5÷6VvÖVçE÷F–ÖR÷"’¢&VG’Âg&W6‚ÒÖævW"åö†Ç5ö÷WGWE÷7FFR†6†ææVÅö¶W’Â6VvÖVçE÷F–ÖR¢W†6WB„¶W”W'&÷"ÂfÇVTW'&÷"ÂG—TW'&÷"Âõ4W'&÷"“ ¢&WGW&âfÇ6P¢&WGW&â&ööÂ‡&VG’æBg&W6‚ ¥ôäôDUô4DÄôuõ$TE•ôU„T5UDõ"ÒF‡&VEööÄW†V7WF÷"€¢Ö…÷v÷&¶W'3ÖÖ‚ƒBÂÖ–âƒbÂ–çB†÷2ævWFVçb‚%5E$TÔdõ$tUôäôDUô4DÄôuõ$TE•õtõ$´U%2"Â#""’÷""’’’À¢F‡&VEöæÖU÷&Vf—ƒÒ&æöFRÖ6FÆör×&VG’"À¢  ¦FVbööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W#¢æöFUW6W$6öæf–r’ÓâÆ—7E´æöFUW6W$6†ææVÅÓ ¢""$f–ÇFW"F†RæöFR6FÆöwVRv—F‚&÷VæFVB&ÆÆVÂÆö6ÂÔ„Å26†V6·2à ¢5E$TÔdõ$tUôäôDUõ$ÄÄTÅô4DÄôuõ$TE•õc##C ¢Æ&vRÆ–æWW2&Wf–÷W6Ç’÷VæVBWfW'’Æ–Æ—7BæBæWvW7B6VvÖVç@¢6WVVçF–ÆÇ’â&W6W'fR6fVB÷&FW"v†–ÆR÷fW&Æ–ær–æFWVæFVçBÆö6À¢f–ÆW7—7FVÒ&VG2Âv—F‚æò7WW'f—6÷"÷"&VÖ÷FRæöFRæWGv÷&²v—Bà¢"" ¢6†ææVÇ2ÒöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"¢–bæ÷B6†ææVÇ3 ¢&WGW&âµÐ¢&VG’ÒÆ—7B…ôäôDUô4DÄôuõ$TE•ôU„T5UDõ"æÖ€¢ö6†ææVÅ÷Æ–&6µ÷&VG’À¢†6†ææVÂæ¶W’f÷"6†ææVÂ–â6†ææVÇ2’À¢’¢&WGW&â¶6†ææVÂf÷"6†ææVÂÂ—5÷&VG’–â¦—†6†ææVÇ2Â&VG’’–b—5÷&VG•Ð  ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õ%TåD”ÔUõ4õU$4UôÄôôµUõc#C ¦FVböæöFUö6†ææVÅö—5÷–÷WGV&U÷6÷W&6R†—FVÓ¢ç’’Óâ&ööÃ ¢""%&W6öÇfR6÷W&6RG—Rg&öÒF†RÆ—fR'VçF–ÖRÂæ÷BF†R6FÆöwVREDòà ¢æöFUW6W$6†ææVÂ–çFVçF–öæÆÇ’6'&–W26FÆöwVRÖWFFFöæÇ’æB†2æð¢–çWE÷W&Âö–çWE÷W&Ç2f–VÆG2âF†RvF6‚vR×W7BF†W&Vf÷&RÆöö²WF†P¢6÷'&W7öæF–ærÖævVB'VçF–ÖR&Vf÷&R6¶–ær6öæf–uö–çWG2‚’&÷WB6÷W&6W2à¢"" ¢¶W’Ò7G"†vWFGG"†—FVÒÂ&¶W’"Â""’÷"""’ç7G&—‚¢–bæ÷B¶W“ ¢&WGW&âfÇ6P¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB†¶W’¢–b'VçF–ÖR—2æöæS ¢&WGW&âfÇ6P¢G'“ ¢&WGW&âç’†—5÷–÷WGV&U÷W&Â‡6÷W&6R’f÷"6÷W&6R–âÖævW"æ6öæf–uö–çWG2‡'VçF–ÖRæ6öæf–r’¢W†6WB„GG&–'WFTW'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"“ ¢&WGW&âfÇ6P  ¦FVböæöFUö6†ææVÅö6FVv÷&–W2†—FVÓ¢ç’’ÓâÆ—7E·7G%Ó ¢fÇVW3¢Æ—7E·7G%ÒÒµÐ¢&–Ö'’Ò7G"†vWFGG"†—FVÒÂ&6FVv÷'’"Â""’÷"""’ç7G&—‚¢–b&–Ö'“ ¢fÇVW2æVæB‡&–Ö'’¢f÷"&r–âÆ—7B†vWFGG"†—FVÒÂ&6FVv÷&–W2"ÂµÒ’÷"µÒ“ ¢6ÆVæVBÒ7G"‡&r÷"""’ç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–âfÇVW3 ¢fÇVW2æVæB†6ÆVæVB¢&WGW&âfÇVW2÷"²%Væ6FVv÷&—¦VB%Ð  ¦FVböæöFUö6FVv÷'•öÖ‡W6W#¢æöFUW6W$6öæf–r’ÓâF–7E·7G"Â7G%Ó ¢æÖW3¢Æ—7E·7G%ÒÒµÐ¢f÷"—FVÒ–âööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"“ ¢f÷"æÖR–âöæöFUö6†ææVÅö6FVv÷&–W2†—FVÒ“ ¢–bæÖRæ÷B–âæÖW3 ¢æÖW2æVæB†æÖR¢&WGW&â¶æÖS¢7G"†–æFW‚²’f÷"–æFW‚ÂæÖR–âVçVÖW&FR†æÖW2—Ð  ¦FVböæöFU÷7G&VÕö–B†6†ææVÃ¢æöFUW6W$6†ææVÂ’Óâ–çC ¢–b6†ææVÂç7G&VÕö–BæB–çB†6†ææVÂç7G&VÕö–B’â ¢&WGW&â–çB†6†ææVÂç7G&VÕö–B¢&WGW&â–çB††6†Æ–"ç6†#Sb†6†ææVÂæ¶W’æVæ6öFR‚'WFbÓ‚"’’æ†W†F–vW7B‚•³£uÒÂb  ¦FVböF—&V7Eö6†ææVÅö†Ç5÷&VG’†6†ææVÃ¢æöFUW6W$6†ææVÂ’Óâ&ööÃ ¢25E$TÔdõ$tUôäôDUôD•$T5Eõ5E$TÕô„Å5ôd5Eõ$TE•õc# ¢2V&Æ–2F—&V7B×Æ’7F'GW×W7Bæ÷Bv—Bf÷"7WW'f—6÷"%2âF†RÖVF–¢2F†Bæv–ç‚v–ÆÂ7GVÆÇ’6W'fR—2F†RWF†÷&—FF—fR†÷B×F‚6–væÃ¢à¢2W†—7F–ærÆ–Æ—7Bv†÷6RæWvW7B6VvÖVçB—2g&W6‚âF†—26†V6²—2Æö6À¢2f–ÆW7—7FVÒ’ôòöæÇ’æBfÆ–FFW2W†7FÇ’F†R6VÆV7FVB6†ææVÂà¢6VvÖVçE÷F–ÖRÒ¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB†6†ææVÂæ¶W’¢–b'VçF–ÖR—2æ÷BæöæS ¢G'“ ¢6VvÖVçE÷F–ÖRÒÖ‚ƒÂ–çB‡'VçF–ÖRæ6öæf–ræ†Ç5÷6VvÖVçE÷F–ÖR÷"’¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢6VvÖVçE÷F–ÖRÒ¢&VG’Âg&W6‚ÒÖævW"åö†Ç5ö÷WGWE÷7FFR†6†ææVÂæ¶W’Â6VvÖVçE÷F–ÖR¢&WGW&â&ööÂ‡&VG’æBg&W6‚  ¦FVböæöFUö6†ææVÅöf÷%÷7G&VÕö–B‡W6W#¢æöFUW6W$6öæf–rÂ7G&VÕö–C¢–çB’ÓâæöFUW6W$6†ææVÂÂæöæS ¢25E$TÔdõ$tUôäôDUôD•$T5Eõ5E$TÕõ4”ätÄUõ$TE•õc# ¢2F—&V7BöÆ—fR÷"÷W6W"÷72ö–B&WVW7BæVVG2öæR6†ææVÂâc"ã’6ÆÆV@¢2ööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‚’Âv†–6‚W&f÷&ÖVBÖævW"ç7FGW2‚’f÷ ¢2WfW'’76–væVB6†ææVÂ&Vf÷&R6ö×&–ærF†R&WVW7FVB7G&VÕö–Bâv—F€¢2²6†ææVÇ2æB7WW'f—6÷"Ö&6¶VB7FGW2&VG2F†—26÷VÆBGW&â6–ævÆP¢26Æ–6²–çFò×VÇF’×6V6öæB†ö666–öæÆÇ’ÓW2’&WVW7Bâ&W6öÇfRF†P¢276–væVB6FÆöwVR–âÖVÖ÷'’f—'7BÂF†VâfÆ–FFRöæÇ’F†B6†ææVÂw0¢2g&W6‚„Å2÷WGWBv—F†÷WB7WW'f—6÷"&÷VæB×G&—à¢vçFVBÒ–çB‡7G&VÕö–B¢6†ææVÂÒæW‡B€¢†—FVÒf÷"—FVÒ–âöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"’–böæöFU÷7G&VÕö–B†—FVÒ’ÓÒvçFVB’À¢æöæRÀ¢¢–b6†ææVÂ—2æöæR÷"æ÷BöF—&V7Eö6†ææVÅö†Ç5÷&VG’†6†ææVÂ“ ¢&WGW&âæöæP¢&WGW&â6†ææVÀ  ¦FVböæöFU÷6W'fW%ö–æfò‡&WVW7C¢&WVW7B’ÓâF–7E·7G"Âç•Ó ¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B…öæöFU÷V&Æ–5ö&6R‡&WVW7B’¢&WGW&â°¢'W&Â#¢'6VBæ†÷7FæÖR÷"÷&WVW7Eö†÷7B‡&WVW7B’À¢'÷'B#¢7G"‡'6VBç÷'B÷"ƒCC2–b'6VBç66†VÖRÓÒ&‡GG2"VÇ6Rƒ’’À¢&‡GG5÷÷'B#¢#CC2"À¢'6W'fW%÷&÷Fö6öÂ#¢'6VBç66†VÖR÷"&‡GG"À¢''F×÷÷'B#¢#"À¢'F–ÖW¦öæR#¢%UD2"À¢'F–ÖW7F×öæ÷r#¢–çB‡F–ÖRçF–ÖR‚’’À¢'F–ÖUöæ÷r#¢F–ÖRç7G&gF–ÖR‚"U’ÒVÒÒVBTƒ¢TÓ¢U2"ÂF–ÖRæv×F–ÖR‚’’À¢Ð   ¥ôäôDUõtT%õÄ”U%ô552Ò""#§&ö÷G¶6öÆ÷"×66†VÖS¦F&³²Ò×æVÃ¢3##s²Ò×æVÃ#¢3c##3²ÒÖÆ–æS¢3#ƒ3ƒF²Ò×FW‡C¢6VVcVf#²ÒÖ×WFVC¢3“6F#ƒ²ÒÖ66VçC¢3F#†6fgÒ§¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‡Ö&öG—¶Ö&v–ã£¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ3ƒ’Â3CS#CRRÂ3“’“¶6öÆ÷#§f"‚Ò×FW‡B“¶föçBÖfÖ–Ç“¤–çFW"Ç7—7FVÒ×V’Ç6ç2×6W&–c¶Ö–âÖ†V–v‡C£f‡Ö¶6öÆ÷#¦–æ†W&—C·FW‡BÖFV6÷&F–öã¦æöæWÒç6†VÆÇ¶Ö‚×v–GFƒ£Sƒ¶Ö&v–ã£WFó·FF–æs£3‚#g‚C‡ÒçF÷¶F—7Æ“¦fÆWƒ¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶Æ–vâÖ—FV×3¦6VçFW#¶v£gƒ¶Ö&v–âÖ&÷GFöÓ£Gƒ·FF–æs£G‚gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£gƒ¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶&÷‚×6†F÷s£G‚3‚&v&ƒÃÃÂãb—Òæ'&æG¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£Gƒ¶Ö–â×v–GFƒ£Òæ'&æBÖ6÷—¶Ö–â×v–GFƒ£ÒæÖ&²Âæ'&æBÖÆöv÷·v–GFƒ£CGƒ¶†V–v‡C£CGƒ¶&÷&FW"×&F—W3£7ƒ¶fÆWƒ¦æöæWÒæÖ&·¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒ3VFVrÂ33Ss†fbÂ3cffb“¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶föçB×vV–v‡C£“Òæ'&æBÖÆöv÷¶&6¶w&÷VæC¢3##¶&÷&FW#£‚6öÆ–B3#C3#CC¶ö&¦V7BÖf—C¦6öçF–ã·FF–æs£g‡Òæ'&æB'¶F—7Æ“¦&Æö6³¶föçB×6—¦S£—ƒ¶Æ–æRÖ†V–v‡C£ã‡Òæ'&æB6ÖÆÇ¶F—7Æ“¦&Æö6³¶6öÆ÷#§f"‚ÒÖ×WFVB“¶Ö&v–â×F÷£Gƒ¶föçB×6—¦S£7‡ÒæÆöv÷WG¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶†V–v‡C£C'ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“·FF–æs£gƒ¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC§f"‚Ò×æVÃ"“¶föçB×vV–v‡C£ƒ¶&÷‚×6†F÷s¦–ç6WB‚&v&ƒ#SRÃ#SRÃ#SRÂã2—Òæ†VFW"Ö7F–öç7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦fÆW‚ÖVæC¶v£ƒ¶Ö–â×v–GFƒ£Òò¢5E$TÔdõ$tUôäôDUô4„ääTÅô„TDU%ô”ädõôu$õU5õc##C¢òæ†VFW"×f–WvW'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3§7G&WF6ƒ¶v£ƒ¶Ö–â×v–GFƒ£Òæ†VFW"Ö–æfòÖw&÷W¶F—7Æ“¦w&–C¶Æ–vâÖ6öçFVçC¦6VçFW#¶v£Gƒ¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£w‚'ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC§f"‚Ò×æVÃ"—Òæ†VFW"Ö–æfòÖw&÷WçW6W'¶Ö–â×v–GFƒ£“‡Òæ†VFW"Ö–æfòÖw&÷Wæ6öææV7F–öç¶Ö–â×v–GFƒ£#‡Òæ†VFW"Ö–æfòÖw&÷WF—g¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£Gƒ¶föçB×6—¦S£'‡Òæ†VFW"Ö–æfòÖw&÷WÆ&VÇ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×vV–v‡C£s·v†—FR×76S¦æ÷w&Òæ†VFW"Ö–æfòÖw&÷W'¶föçB×6—¦S£'ƒ¶Æ–æRÖ†V–v‡C£ã·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—3¶Ö‚×v–GFƒ£S‡Òæ†VFW"Ö–æfòÖw&÷WæW‡—'—¶6öÆ÷#¢3†FS6C7ÒæÆöv–â×67&VVç¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#·FF–æs£#‡‡ÒæÆöv–ç·v–GFƒ¦Ö–âƒCc‚Æ6Æ2ƒRÒ3‚’“¶Ö&v–ã£WFó¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£#ƒ·FF–æs£3Gƒ¶&÷‚×6†F÷s£#'‚ƒ‚&v&ƒÃÃÂã3R—ÒæÆöv–âæ'&æBÒÖÆöv–ç¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Æ–vâÖ—FV×3¦6VçFW#¶v£Gƒ¶Ö&v–ã£‡ƒ·FW‡BÖÆ–vã¦6VçFW'ÒæÆöv–âæ'&æBÒÖÆöv–âæ'&æBÖ6÷—¶F—7Æ“¦w&–C¶§W7F–g’Ö—FV×3¦6VçFW'ÒæÆöv–âæ'&æBÒÖÆöv–â'¶föçB×6—¦S£3ƒ¶Æ–æRÖ†V–v‡C£ã‡ÒæÆöv–âæ'&æBÒÖÆöv–âæÖ&²ÂæÆöv–âæ'&æBÒÖÆöv–âæ'&æBÖÆöv÷·v–GFƒ£sGƒ¶†V–v‡C£sGƒ¶&÷&FW"×&F—W3£‡‡ÒæÆöv–âæ'&æBÒÖÆöv–âæ'&æBÖÆöv÷·FF–æs£‡ÒæÆöv–âƒ¶föçB×6—¦S£#Wƒ¶Ö&v–ã£‡‚‡ƒ·FW‡BÖÆ–vã¦6VçFW'ÒæÆöv–âÆ&VÇ¶F—7Æ“¦w&–C¶v£wƒ¶Ö&v–ã£G‚¶föçB×6—¦S£7ƒ¶föçB×vV–v‡C£sÒæÆöv–âf÷&×¶F—7Æ“¦w&–C¶v£Ö–çWBÇ6VÆV7G·v–GFƒ£S¶†V–v‡C£Cgƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC¢3#Cc¶6öÆ÷#§f"‚Ò×FW‡B“·FF–æs£Gƒ¶&÷‚×6†F÷s¦–ç6WB‚&v&ƒ#SRÃ#SRÃ#SRÂã"—Ö'WGFöç¶†V–v‡C£C7ƒ¶&÷&FW#£¶&÷&FW"×&F—W3£—ƒ¶&6¶w&÷VæC§f"‚ÒÖ66VçB“¶6öÆ÷#§v†—FS¶föçB×vV–v‡C£ƒ·FF–æs£‡‡ÒæÆöv–â'WGFöç¶Ö&v–â×F÷£g‡ÒæÆW'G¶&6¶w&÷VæC§&v&ƒ#SRÃrÃrÂã“¶&÷&FW#£‚6öÆ–B&v&ƒ#SRÃrÃrÂã3R“¶6öÆ÷#¢6ff##·FF–æs£‚'ƒ¶&÷&FW"×&F—W3£—ƒ¶Ö&v–âÖ&÷GFöÓ£G‡ÒçFööÆ&'¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒ#c‚Ãg"’WFó¶v£ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶Ö&v–âÖ&÷GFöÓ£'ƒ·FF–æs£ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£Gƒ¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶&÷‚×6†F÷s£‚#G‚&v&ƒÃÃÂã2—Òæ6÷VçG¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£7ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£ƒ¶&6¶w&÷VæC§f"‚Ò×æVÃ"“¶6öÆ÷#§f"‚Ò×FW‡B“¶föçB×vV–v‡C£ƒ·v†—FR×76S¦æ÷w&¶föçB×6—¦S£7‡Òò¢5E$TÔdõ$tUôäôDUô4DTtõ%•ô4„ääTÅõäTÅõc##Cr¢òæ6†ææVÂÖÆ–'&'—·FF–æs£gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£‡ƒ¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶&÷‚×6†F÷s£G‚3‚&v&ƒÃÃÂãB—Òæ6FVv÷'’×7G&—¶F—7Æ“¦fÆWƒ¶v£‡ƒ¶÷fW&fÆ÷r×ƒ¦WFó·FF–æs£'‚'‚Gƒ¶Ö&v–âÖ&÷GFöÓ£Gƒ¶&÷&FW"Ö&÷GFöÓ£‚6öÆ–Bf"‚ÒÖÆ–æR“·67&öÆÆ&"×v–GFƒ§F†–çÒæ6FVv÷'’Ö6†—¶†V–v‡C£3‡ƒ¶Ö–â×v–GFƒ¦Ö‚Ö6öçFVçC·FF–æs£Wƒ¶&÷&FW"×&F—W3£““—ƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×vV–v‡C£sS¶7W'6÷#§ö–çFW'Òæ6FVv÷'’Ö6†—¦†÷fW'¶&÷&FW"Ö6öÆ÷#¢3CScCƒ“¶6öÆ÷#§f"‚Ò×FW‡B“¶&6¶w&÷VæC§f"‚Ò×æVÃ"—Òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ô4DTtõ%•ôtÄõuôd•…õc###¢òæ6FVv÷'’Ö6†—æ7F—fW¶&6¶w&÷VæC§f"‚ÒÖ66VçB“¶&÷&FW"Ö6öÆ÷#§f"‚ÒÖ66VçB“¶6öÆ÷#¢6ffc¶&÷‚×6†F÷s¦æöæWÕ¶FFÖ6†ææVÅÕ¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÒæw&–G¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVB†WFòÖf–ÆÂÆÖ–æÖ‚ƒ#CW‚Ãg"’“¶v£'‡Òæ6&G¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£'ƒ·FF–æs£‚'ƒ¶&6¶w&÷VæC§f"‚Ò×æVÂ“¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£7ƒ¶Ö–âÖ†V–v‡C£sgƒ¶&÷‚×6†F÷s£‡‚‡‚&v&ƒÃÃÂã“·G&ç6—F–öã§G&ç6f÷&ÒãW2V6RÆ&÷&FW"Ö6öÆ÷"ãW2V6RÆ&6¶w&÷VæBãW2V6RÆ&÷‚×6†F÷rãW2V6WÒæ6&C¦†÷fW'¶&÷&FW"Ö6öÆ÷#¢3CScCƒ“¶&6¶w&÷VæC§f"‚Ò×æVÃ"“·G&ç6f÷&Ó§G&ç6ÆFU’‚Ó‚“¶&÷‚×6†F÷s£g‚#g‚&v&ƒÃÃÂã‚—ÒæÆövòÂæfÆÆ&6··v–GFƒ£SGƒ¶†V–v‡C£SGƒ¶&÷&FW"×&F—W3£—ƒ¶&6¶w&÷VæC¢3##¶&÷&FW#£‚6öÆ–B3#C3#CC¶fÆWƒ¦æöæWÒæÆöv÷¶ö&¦V7BÖf—C¦6öçF–çÒæfÆÆ&6·¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶föçB×6—¦S£#‡ÒæÖWF¶Ö–â×v–GFƒ£ÒæÖWF"ÂæÖWF6ÖÆÇ¶F—7Æ“¦&Æö6³·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7ÒæÖWF6ÖÆÇ¶6öÆ÷#§f"‚ÒÖ×WFVB“¶Ö&v–â×F÷£W‡ÒæV×G—¶w&–BÖ6öÇVÖã£òÓ·FF–æs£3'ƒ·FW‡BÖÆ–vã¦6VçFW#¶6öÆ÷#§f"‚ÒÖ×WFVB“¶&÷&FW#£‚F6†VBf"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£'‡ÔÖVF–†Ö‚×v–GFƒ£s‚—²ç6†VÆÇ·FF–æs£‡‡ÒçF÷·FF–æs£7‚Gƒ¶fÆW‚×w&§w&¶Æ–vâÖ—FV×3¦fÆW‚×7F'GÒçFööÆ&'¶w&–B×FV×ÆFRÖ6öÇVÖç3£g#·FF–æs£'‡Òæ6÷VçG¶§W7F–g’×6VÆc§7F'C·v–GFƒ£WÒæ6FVv÷'’×7G&—¶Ö&v–âÖÆVgC¢Ó'ƒ¶Ö&v–â×&–v‡C¢Ó'‡Òæ6FVv÷'’Ö6†—¶†V–v‡C£3gƒ·FF–æs£7‡Òæw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'ÒæÆöv–ç·FF–æs£#‡‚#'‡ÒæÆöv–âæ'&æBÒÖÆöv–â'¶föçB×6—¦S£#w‡×Ò"" ¥ôäôDUõtT%õÄ”U%ô552³Ò"""ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õT”4µôõ%ôÔåTÅôÄôt”åõc3C¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õT”4µôÄôt”åôÄ”õUEõc3C"¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õT”4µô5$TDTåD”Åõ5DDUõc3C2¢òæÆöv–ç·v–GFƒ¦Ö–âƒCc‚Æ6Æ2ƒgrÒ3'‚’“·FF–æs£3'‡ÒæÆöv–â'WGFöç¶†V–v‡C£Cgƒ¶&÷&FW"×&F—W3£‡ÒçV–6²ÖÆöv–âÖ6†ö–6W¶F—7Æ“¦w&–C¶v£‡ÒçV–6²ÖÆöv–âÖ6†ö–6U¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÒçV–6²ÖÆöv–â×W6W"'WGFöç·v–GFƒ£S¶†V–v‡C£S'ƒ·FF–æs£gƒ¶F—7Æ“¦w&–C·Æ6RÖ—FV×3¦6VçFW#¶7W'6÷#§ö–çFW'ÒçV–6²ÖÆöv–â×W6W"'WGFöâ7ç¶föçB×6—¦S£W‡ÒæÖçVÂÖÆöv–â×FövvÆRÂæÖçVÂÖÆöv–âÖ&6··v–GFƒ£S¶&6¶w&÷VæC§f"‚Ò×æVÃ"“¶&÷&FW#£‚6öÆ–Bf"‚ÒÖÆ–æR“¶6öÆ÷#§f"‚Ò×FW‡B“¶7W'6÷#§ö–çFW'ÒæÖçVÂÖÆöv–âÖf÷&×¶v£ÒæÖçVÂÖÆöv–âÖf÷&Õ¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÒæÖçVÂÖÆöv–âÖ7F–öç7¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3£g#¶v£ƒ¶Ö&v–â×F÷£‡‡ÒæÖçVÂÖÆöv–âÖ7F–öç2çv—F‚Ö&6·¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"g'ÒæÖçVÂÖÆöv–âÖ7F–öç2'WGFöç·v–GFƒ£S¶Ö&v–â×F÷£ÒæÆöv–âÖF—f–FW'¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶Ö&v–ã£G‚¶6öÆ÷#§f"‚ÒÖ×WFVB“¶föçB×6—¦S£'‡ÒæÆöv–âÖF—f–FW#¦&Vf÷&RÂæÆöv–âÖF—f–FW#¦gFW'¶6öçFVçC¢"#¶†V–v‡C£ƒ¶fÆWƒ£¶&6¶w&÷VæC§f"‚ÒÖÆ–æR—ÒæÆöv–âæÆW'E¶†–FFVå×¶F—7Æ“¦æöæR–×÷'FçGÒæF÷væÆöBÖ'WGFöç¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Ö–âÖ†V–v‡C£C'ƒ·FF–æs£gƒ¶&÷&FW#£‚6öÆ–Bf"‚ÒÖ66VçB“¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC§f"‚ÒÖ66VçB“¶6öÆ÷#¢6ffc¶föçB×vV–v‡C£ƒS·v†—FR×76S¦æ÷w&¶7W'6÷#§ö–çFW#·W6W"×6VÆV7C¦æöæWÔÖVF–†Ö‚×v–GFƒ£s‚—²æÆöv–ç·FF–æs£#‡‚#'‡×ÔÖVF–†Ö‚×v–GFƒ£C#‚—²æÆöv–ç·FF–æs£#'‚g‡×Ò"" ¥ôäôDUõtT%õÄ”U%ô552³Ò"""ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ô”åDU$5D•dUõô”åDU%õc3Cr¢ö¶‡&VeÒÆ·&öÆSÖÆ–æµÒÆ'WGFöã¦æ÷Bƒ¦F—6&ÆVB’Ç6VÆV7C¦æ÷Bƒ¦F—6&ÆVB’Æ÷F–öâÆ–çWE·G—SÖ6†V6¶&÷…Ó¦æ÷Bƒ¦F—6&ÆVB’Æ–çWE·G—S×&F–õÓ¦æ÷Bƒ¦F—6&ÆVB’Å·&öÆSÖ'WGFöåÓ¦æ÷B…¶&–ÖF—6&ÆVC×G'VUÒ—¶7W'6÷#§ö–çFW"–×÷'FçGÖ'WGFöã¦F—6&ÆVBÇ6VÆV7C¦F—6&ÆVBÆ–çWC¦F—6&ÆVBÅ¶&–ÖF—6&ÆVC×G'VU×¶7W'6÷#¦æ÷BÖÆÆ÷vVB–×÷'FçGÒ"" ¥ôäôDUõtT%õÄ”U%ô552³Ò"""æ†VFW"Ö'WGFöç7¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3§7G&WF6ƒ¶v£ƒ¶Ö–â×v–GFƒ£Òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôÔô$”ÄUô„TDU%ôÄ”õUEõc3S¢ôÖVF–†Ö‚×v–GFƒ£s‚—²çF÷¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"“¶v£'‡Òæ'&æG·v–GFƒ£S¶Ö‚×v–GFƒ£WÒæ†VFW"Ö7F–öç7¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"“·v–GFƒ£S¶v£—‡Òæ†VFW"×f–WvW'¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’“·v–GFƒ£S¶v£‡‡Òæ†VFW"Ö–æfòÖw&÷WÂæ†VFW"Ö–æfòÖw&÷WçW6W"Âæ†VFW"Ö–æfòÖw&÷Wæ6öææV7F–öç¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷ƒ·v–GFƒ£S¶Ö–â×v–GFƒ£¶Ö‚×v–GFƒ£WÒæ†VFW"Ö–æfòÖw&÷WF—g¶v£‡ƒ¶Ö–â×v–GFƒ£Òæ†VFW"Ö–æfòÖw&÷W'¶Ö–â×v–GFƒ£Òæ†VFW"Ö'WGFöç7¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’“·v–GFƒ£S¶v£—‡Òæ†VFW"Ö'WGFöç3â§¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷ƒ·v–GFƒ£S¶Ö–â×v–GFƒ£¶Ö‚×v–GFƒ£WÒæ†VFW"Ö'WGFöç3ã¦öæÇ’Ö6†–ÆG¶w&–BÖ6öÇVÖã£òÓ×ÔÖVF–†Ö‚×v–GFƒ£3ƒ‚—²æ†VFW"×f–WvW"Âæ†VFW"Ö'WGFöç7¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'×Ò"" ¥ôäôDUõtT%õÄ”U%ôd”ÅDU%ô¥2Ò"""‚‚“Óç¶6öç7BÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FF×6V&6…Òr’Æ6†—3Õ²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖ6FVv÷'’Ö6†—Òr•ÒÆ—FV×3Õ²ââæFö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖ6†ææVÅÒr•ÒÆ6÷VçCÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FF×f—6–&ÆUÒr“¶ÆWB7F—fSÒrs¶6öç7B6G3×ƒÓç·G'—·&WGW&â¥4ôâç'6R‡‚æFF6WBæ6FVv÷&–W7ÇÂuµÒr—Ö6F6‚…ò—·&WGW&åµ××Ó¶6öç7BÇ“Ò‚“Óç¶6öç7BFW&ÓÒ‡òçfÇVWÇÂrr’çG&–Ò‚’çFôÆ÷vW$66R‚“¶ÆWB6†÷vãÓ¶—FV×2æf÷$V6‚‡ƒÓç¶6öç7Bö´æÖSÒFW&×ÇÂ‡‚æFF6WBææÖWÇÂrr’æ–æ6ÇVFW2‡FW&Ò’Æö´6CÒ7F—fWÇÆ6G2‡‚’æ–æ6ÇVFW2†7F—fR’Æö³Öö´æÖRbfö´6C·‚æ†–FFVãÒö³·‚ç7G–ÆRæF—7Æ“Öö³òrs¢væöæRs¶–b†ö²—6†÷vâ²·Ò“¶–b†6÷VçB–6÷VçBçFW‡D6öçFVçC×6†÷vçÓ¶6†—2æf÷$V6‚†6†—Óæ6†—æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“Óç¶7F—fSÒ†6†—æFF6WBæ6FVv÷'”6†—ÇÂrr’çFôÆ÷vW$66R‚“¶6†—2æf÷$V6‚‡ƒÓç¶6öç7Böã×ƒÓÓÖ6†—·‚æ6Æ74Æ—7BçFövvÆR‚v7F—fRrÆöâ“·‚ç6WDGG&–'WFR‚v&–×&W76VBrÆöãòwG'VRs¢vfÇ6Rr—Ò“¶Ç’‚—Ò’“·òæFDWfVçDÆ—7FVæW"‚v–çWBrÆÇ’“¶Ç’‚—Ò’‚“²""   ¦FVböæöFU÷vV%ö6öö¶–U÷fÇVR‡W6W#¢æöFUW6W$6öæf–rÂÆöv–åö¶–æC¢7G"Ò&ÖçVÂ"’Óâ7G# ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õT”4µõU4U%õ$•d5•õc3Sc ¢26–vâF†RÆöv–â÷&–v–âFövWF†W"v—F‚F†RFö¶Vâ6òT’&—f7’6ææ÷B&P¢2FövvÆVBW6–ær6W&FR6Æ–VçBÖ6öçG&öÆÆVB6öö¶–Rà¢Fö¶VâÒ7G"‡W6W"çFö¶Vâ÷"""¢¶–æBÒ'V–6²"–b7G"†Æöv–åö¶–æB’ç7G&—‚’æÆ÷vW"‚’ÓÒ'V–6²"VÇ6R&ÖçVÂ ¢–ÆöBÒb'·Fö¶VçÒç¶¶–æGÒ ¢6–væGW&RÒ†Ö2ææWr…Dô´TâæVæ6öFR‚'WFbÓ‚"’Â–ÆöBæVæ6öFR‚'WFbÓ‚"’Â†6†Æ–"ç6†#Sb’æ†W†F–vW7B‚•³£3%Ð¢&WGW&âb'·–ÆöGÒç·6–væGW&WÒ   ¦FVböæöFU÷vV%ö6öö¶–U÷7FFR‡&WVW7C¢&WVW7B’ÓâGWÆU´æöFUW6W$6öæf–rÂæöæRÂ7G%Ó ¢&rÒ7G"‡&WVW7Bæ6öö¶–W2ævWB‚'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W""’÷"""¢–ÆöBÂ6WÂ6–væGW&RÒ&rç''F—F–öâ‚"â"¢–bæ÷B6W÷"æ÷B–ÆöB÷"æ÷BDô´Tã ¢&WGW&âæöæRÂ" ¢Fö¶VâÂ¶–æE÷6WÂ¶–æBÒ–ÆöBç''F—F–öâ‚"â"¢W‡V7FVBÒ†Ö2ææWr…Dô´TâæVæ6öFR‚'WFbÓ‚"’Â–ÆöBæVæ6öFR‚'WFbÓ‚"’Â†6†Æ–"ç6†#Sb’æ†W†F–vW7B‚•³£3%Ð¢–bæ÷B†¶–æE÷6WæBFö¶VâæB¶–æB–â²&ÖçVÂ"Â'V–6²'ÒæB†Ö2æ6ö×&UöF–vW7B‡6–væGW&RÂW‡V7FVB’“ ¢2&6·v&B6ö×F–&–Æ—G“¢6öö¶–W2—77VVB&Vf÷&Rc2ããSb6–væVBöæÇ’F†P¢2Fö¶VââF†W’&WF–âF†Rf—6–&ÆRÖ–æf÷&ÖF–öâ&V†f–÷"öbÖçVÂÆöv–âà¢Fö¶VâÒ–Æö@¢ÆVv7•öW‡V7FVBÒ†Ö2ææWr…Dô´TâæVæ6öFR‚'WFbÓ‚"’ÂFö¶VâæVæ6öFR‚'WFbÓ‚"’Â†6†Æ–"ç6†#Sb’æ†W†F–vW7B‚•³£3%Ð¢–bæ÷B†Ö2æ6ö×&UöF–vW7B‡6–væGW&RÂÆVv7•öW‡V7FVB“ ¢&WGW&âæöæRÂ" ¢¶–æBÒ&ÖçVÂ ¢25E$TÔdõ$tUôäôDUõtT%ô4ôô´”UõdÄ”D•E•õc#“S ¢2æöFUW6W$6öæf–r†2æò—5÷fÆ–B‚’ÖWF†öBâW6RF†RvVçDÖævW"w26æöæ–6À¢2fÆ–F—G’6†V6²6òVæ&ÆVBöW‡—'’'VÆW27F’–FVçF–6ÂWfW'—v†W&Rà¢&WGW&âÖævW"çfÆ–E÷W6W"‡Fö¶Vâ’Â¶–æ@  ¦FVböæöFU÷vV%ö6öö¶–U÷W6W"‡&WVW7C¢&WVW7B’ÓâæöFUW6W$6öæf–rÂæöæS ¢&WGW&âöæöFU÷vV%ö6öö¶–U÷7FFR‡&WVW7B•³Ð  ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEôTddT5D•dUô4ôåE$ôÅ5õcS“ ¦FVböæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7C¢&WVW7BÂæöæR’ÓâF–7E·7G"Âç•Ó ¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð¢&WGW&â°¢'6†÷u÷W6W%ö–æfò#¢&ööÂ†'&æBævWB‚'6†÷u÷W6W%ö–æfò"ÂÖævW"çvV'Æ–W%÷6†÷u÷W6W%ö–æfò’’À¢'6†÷uö6öææV7F–öåö–æfò#¢&ööÂ†'&æBævWB‚'6†÷uö6öææV7F–öåö–æfò"ÂÖævW"çvV'Æ–W%÷6†÷uö6öææV7F–öåö–æfò’’À¢&Æöv–åöÖöFR#¢öæ÷&ÖÆ—¦VE÷vV'Æ–W%öÆöv–åöÖöFR†'&æBævWB‚&Æöv–åöÖöFR"’÷"ÖævW"çvV'Æ–W%öÆöv–åöÖöFR’À¢&WFõ÷W6W%ö–B#¢Ö‚ƒÂ–çB†'&æBævWB‚&WFõ÷W6W%ö–B"ÂÖævW"çvV'Æ–W%öWFõ÷W6W%ö–B’÷"’’À¢&WFõ÷W6W%÷Fö¶Vâ#¢7G"†'&æBævWB‚&WFõ÷W6W%÷Fö¶Vâ"ÂÖævW"çvV'Æ–W%öWFõ÷W6W%÷Fö¶Vâ’÷"""’ç7G&—‚’À¢Ð  ¦FVböæöFU÷vV%ö†–FU÷V–6µ÷W6W%ö–æfò‡&WVW7C¢&WVW7B’Óâ&ööÃ ¢6öçG&öÇ2ÒöæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B¢&WGW&â6öçG&öÇ5²&Æöv–åöÖöFR%ÒÓÒ'V–6²"æBöæöFU÷vV%ö6öö¶–U÷7FFR‡&WVW7B•³ÒÓÒ'V–6²   ¦FVböæöFU÷vV%÷6VÆV7FVE÷W6W"‡&WVW7C¢&WVW7BÂæöæRÒæöæR’ÓâæöFUW6W$6öæf–rÂæöæS ¢25E$TÔdõ$tUôäôDUôUDõôÄôt”åõDô´Tåõc###C ¢ÖævW"ç&VÆöE÷W6W'5ö–eö6†ævVB‚¢6öçG&öÇ2ÒöæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B¢vçFVE÷Fö¶VâÒ7G"†6öçG&öÇ2ævWB‚&WFõ÷W6W%÷Fö¶Vâ"’÷"""’ç7G&—‚¢–bvçFVE÷Fö¶Vã ¢W6W"ÒÖævW"çfÆ–E÷W6W"‡vçFVE÷Fö¶Vâ¢–bW6W"æB7G"‡W6W"ç6÷W&6R÷"""’ç7G&—‚’æÆ÷vW"‚’ÓÒ&æöFUöÆö6Â# ¢&WGW&âW6W ¢&WGW&âæöæP¢vçFVBÒ–çB†6öçG&öÇ2ævWB‚&WFõ÷W6W%ö–B"’÷"¢–bvçFVC ¢f÷"W6W"–âÖævW"çW6W'2çfÇVW2‚“ ¢–b€¢–çB‡W6W"çW6W%ö–B÷"’ÓÒvçFV@¢æB7G"‡W6W"ç6÷W&6R÷"""’ç7G&—‚’æÆ÷vW"‚’ÓÒ&æöFUöÆö6Â ¢æBÖævW"çfÆ–E÷W6W"‡W6W"çFö¶Vâ¢“ ¢&WGW&âW6W ¢&WGW&âæöæP  ¦FVböæöFU÷vV%ö7W'&VçE÷W6W"‡&WVW7C¢&WVW7B’ÓâæöFUW6W$6öæf–rÂæöæS ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôÄôt”åôÔôDUõc#### ¢6öçG&öÇ2ÒöæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B¢–b6öçG&öÇ5²&Æöv–åöÖöFR%ÒÓÒ&WFò# ¢&WGW&âöæöFU÷vV%÷6VÆV7FVE÷W6W"‡&WVW7B¢&WGW&âöæöFU÷vV%ö6öö¶–U÷W6W"‡&WVW7B  ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôUDõôÄôt”åõcs ¥ôäôDUõtT%ôUDõôÄôuô4ôô´”RÒ'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W%öWFõöÆör  ¦FVböæöFU÷vV%öWFõöÆöv–åöÆöuöÖ&¶W"‡W6W#¢æöFUW6W$6öæf–rÂæöæR’Óâ7G# ¢–bW6W"—2æöæS ¢&WGW&â'Væf–Æ&ÆR ¢–FVçF—G’Òb'¶–çB‡W6W"çW6W%ö–B÷"—Ó§·7G"‡W6W"çW6W&æÖR÷"wW6W"r—Ò ¢&WGW&â'W6W"Ò"²†6†Æ–"ç6†#Sb†–FVçF—G’æVæ6öFR‚'WFbÓ‚"ÂW'&÷'3Ò&–væ÷&R"’’æ†W†F–vW7B‚•³£#EÐ ¦FVböæöFU÷vV%öÆöuöWFõöÆöv–åööæ6R‡&WVW7C¢&WVW7BÂW6W#¢æöFUW6W$6öæf–rÂæöæR’Óâ7G"ÂæöæS ¢""$ÆörWFòÆöv–âöæ6RW"'&÷w6W"Ö&¶W"v—F†÷WB7F÷&–ær7&VFVçF–Ç2â"" ¢–böæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B•²&Æöv–åöÖöFR%ÒÒ&WFò# ¢&WGW&âæöæP¢Ö&¶W"ÒöæöFU÷vV%öWFõöÆöv–åöÆöuöÖ&¶W"‡W6W"¢–b7G"‡&WVW7Bæ6öö¶–W2ævWB…ôäôDUõtT%ôUDõôÄôuô4ôô´”R’÷"""’ÓÒÖ&¶W# ¢&WGW&âæöæP¢6Æ–VçBÒ&WVW7Bæ†VFW'2ævWB‚'W6W"ÖvVçB"Â""•³£3Ð¢—ÒÖævW"åö6Æ–VçEö—‡&WVW7B¢–bW6W"—2æ÷BæöæS ¢6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢ÖævW"æÆör€¢%vV"Æ–W"WFòÆöv–â"À¢66÷SÒ&6Æ–VçB"À¢FWF–Ç3Öb'W6W#×·W6W"çW6W&æÖWÓ²—×¶—Ó²6Æ–VçC×¶6Æ–VçGÓ²ÖöFSÖWFó²6W76–öåö–C×·6W76–öåö–GÒ"À¢W6W#×W6W"çW6W&æÖRÀ¢¢VÇ6S ¢ÖævW"æÆör€¢%vV"Æ–W"WFòÆöv–âVæf–Æ&ÆR"À¢66÷SÒ&6Æ–VçB"À¢ÆWfVÃÒ'v&æ–ær"À¢FWF–Ç3Öb&—×¶—Ó²6Æ–VçC×¶6Æ–VçGÓ²ÖöFSÖWFò"À¢W6W#Ò$wVW7B"À¢¢&WGW&âÖ&¶W   ¦FVböæöFU÷vV%ö'&æEö76WE÷F‚†'&æC¢F–7E·7G"Âç•ÒÂ¶W“¢7G"’ÓâF‚ÂæöæS ¢æÖRÒF‚‡7G"†'&æBævWB†¶W’’÷"""’’ææÖP¢–bæ÷BæÖR÷"æÖRÒ7G"†'&æBævWB†¶W’’÷"""“ ¢&WGW&âæöæP¢&ö÷BÒtT%Ä”U%ô%$äEõ$ôõBç&W6öÇfR‚¢6æF–FFRÒ‡&ö÷BòæÖR’ç&W6öÇfR‚¢G'“ ¢6æF–FFRç&VÆF—fU÷Fò‡&ö÷B¢W†6WBfÇVTW'&÷# ¢&WGW&âæöæP¢&WGW&â6æF–FFR–b6æF–FFRæ—5öf–ÆR‚’VÇ6RæöæP  ¦FVböæöFU÷vV%öF÷væÆöE÷6æ6†÷B‡&WVW7C¢&WVW7B’ÓâGWÆUµF‚ÂæöæRÂ7G"Â7G"Â7G%Ó ¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B¢–b'&æB—2æ÷BæöæS ¢'&æE÷F‚ÒöæöFU÷vV%ö'&æEö76WE÷F‚†'&æBÂ&F÷væÆöEö76WB"¢'&æEöæÖRÒF‚‡7G"†'&æBævWB‚&F÷væÆöEöæÖR"’÷"""’’ææÖP¢–b'&æE÷F‚—2æ÷BæöæRæB'&æEöæÖS ¢&WGW&â†'&æE÷F‚Â'&æEöæÖRÂ7G"†'&æBævWB‚&æG&ö–E÷fW'6–öåöæÖR"’÷"""’ç7G&—‚’Â7G"†'&æBævWB‚&æG&ö–EöFW67&—F–öâ"’÷"""’ç7G&—‚’¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEô54UEô•4ôÄD”ôåõc#3 ¢2&Ææ²W"Ö'&æBÖVç2æòöF÷væÆöBf÷"F†B'&æBà¢&WGW&â„æöæRÂ""Â""Â""¢–bÖævW"çvV'Æ–W%öF÷væÆöEöæÖRæBtT%Ä”U%ôDõtäÄôEôd”ÄRæ—5öf–ÆR‚“ ¢&WGW&â…tT%Ä”U%ôDõtäÄôEôd”ÄRÂF‚‡7G"†ÖævW"çvV'Æ–W%öF÷væÆöEöæÖR’’ææÖRÂ7G"†ÖævW"ææG&ö–E÷fW'6–öåöæÖR÷"""’ç7G&—‚’Â7G"†ÖævW"ææG&ö–EöFW67&—F–öâ÷"""’ç7G&—‚’¢&WGW&â„æöæRÂ""Â""Â""  ¦FVböæöFU÷vV%öF÷væÆöEöf–Æ&ÆR‡&WVW7C¢&WVW7B’Óâ&ööÃ ¢F‚ÂæÖRÂ÷fW'6–öâÂöFW67&—F–öâÒöæöFU÷vV%öF÷væÆöE÷6æ6†÷B‡&WVW7B¢&WGW&â&ööÂ‡F‚—2æ÷BæöæRæBæÖR  ¦FVböæöFU÷vV%÷&Vf—‚‡&WVW7C¢&WVW7B’Óâ7G# ¢""%&WGW&âF†RÖF6†VBÆ–Æ—7Bô&Vf—‚ÂæWfW"F†RæVÂô’&Vf—‚â"" ¢ÖF6†VBÂ÷F‚Â&Vf—‚Òö66W75÷W&ÅöÖF6‚‡&WVW7BÂÖævW"ç7G&VÕ÷W&Ç2¢–bÖF6†VC ¢&WGW&â&Vf—‚ç'7G&—‚"ò"¢†÷7BÂ&WVW7E÷÷'BÒ÷&WVW7EöWF†÷&—G’‡&WVW7B¢6æF–FFW3¢Æ—7E·7G%ÒÒµÐ¢f÷"fÇVR–âÆ—7B†ÖævW"ç7G&VÕ÷W&Ç2÷"µÒ“ ¢'6VBÒW&ÆÆ–"ç'6RçW&Ç7Æ—B‡7G"‡fÇVR÷"""’ç7G&—‚’¢W‡V7FVEö†÷7BÒ‡'6VBæ†÷7FæÖR÷"""’æÆ÷vW"‚’ç'7G&—‚"â"¢FVfVÇE÷÷'BÒCC2–b'6VBç66†VÖRÓÒ&‡GG2"VÇ6Rƒ ¢–bW‡V7FVEö†÷7BÓÒ†÷7BæB–çB‡&WVW7E÷÷'B÷"FVfVÇE÷÷'B’ÓÒ–çB‡'6VBç÷'B÷"FVfVÇE÷÷'B“ ¢6æF–FFW2æVæB‡'6VBçF‚ç'7G&—‚"ò"’¢–b""–â6æF–FFW3 ¢&WGW&â" ¢Væ—VRÒ6÷'FVB‡6WB†6æF–FFW2’¢&WGW&âVæ—VU³Ò–bÆVâ‡Væ—VR’ÓÒVÇ6R"   ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô4ÄTåô4äôä”4Åõ$ôõEõcc5#S ¦FVböæöFU÷vV%ö†öÖU÷W&Â‡&WVW7C¢&WVW7B’Óâ7G# ¢""%&WGW&âF†R'&÷w6W"Öf6–ær6æöæ–6ÂæöFRvV%Æ–W"&ö÷Bà ¢F†R6öæf–wW&VBÆ–Æ—7BôÆ–2—G6VÆb—2F†Rf—6–&ÆRvV%Æ–W"U$Âà¢–çFW&æÂ÷vV"×Æ–W"&÷WFW2&VÖ–âf–Æ&ÆRf÷"Æöv–âö7F–öç2÷vF6‚æ@¢6ö×F–&–Æ—G’Â'WB&VF—&V7G2ö†—7F÷'’×W7B&WGW&âFòF†R6ÆVâÆ–2&ö÷Bà¢"" ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B’ç'7G&—‚"ò"¢&WGW&â&Vf—‚÷""ò   ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôdÄ4…ôU%$õ%õcƒ# ¢2æöFRV&Æ–2v÷&¶W'2Fòæ÷BW6RF†RÖ–â6W76–öäÖ–FFÆWv&R6öö¶–RÂ6ò¶VW¢2F–ç’öæR×6†÷BW'&÷"¦6öFR¢–ââ‡GGöæÇ’6öö¶–RâöæÇ’6W'fW"ÖFVf–æVBFW‡B—0¢2&VæFW&VC²F†R6öö¶–RæWfW"7F÷&W2&&—G&'’W6W"7WÆ–VB…DÔÂ÷FW‡Bà¥ôäôDUõtT%ôdÄ4…ô4ôô´”RÒ'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W%öfÆ6‚ ¥ôäôDUõtT%ôdÄ4…ôÔU54tU2Ò°¢&–çfÆ–Eö7&VFVçF–Ç2#¢$–çfÆ–BW6W&æÖR÷"77v÷&B"À¢'6–våö–åöf—'7B#¢%ÆV6R6–vâ–âf—'7B"À¢'V–6µ÷Væf–Æ&ÆR#¢%V–6²Æöv–âW6W"—2Væf–Æ&ÆR"À¢&Æöv–åöW'&÷"#¢%Væ&ÆRFò6–vâ–â"À§Ð  ¦FVböæöFU÷vV%öfÆ6…ö6öFR†ÖW76vS¢7G"’Óâ7G# ¢fÇVRÒ7G"†ÖW76vR÷"""’ç7G&—‚’æÆ÷vW"‚¢–bfÇVRÓÒ&–çfÆ–BW6W&æÖR÷"77v÷&B# ¢&WGW&â&–çfÆ–Eö7&VFVçF–Ç2 ¢–bfÇVRÓÒ'ÆV6R6–vâ–âf—'7B# ¢&WGW&â'6–våö–åöf—'7B ¢–bfÇVRÓÒ'V–6²Æöv–âW6W"—2Væf–Æ&ÆR# ¢&WGW&â'V–6µ÷Væf–Æ&ÆR ¢&WGW&â&Æöv–åöW'&÷"   ¦FVböæöFU÷vV%öfÆ6…öÖW76vR‡&WVW7C¢&WVW7B’Óâ7G# ¢6öFRÒ7G"‡&WVW7Bæ6öö¶–W2ævWB…ôäôDUõtT%ôdÄ4…ô4ôô´”R’÷"""’ç7G&—‚¢&WGW&âôäôDUõtT%ôdÄ4…ôÔU54tU2ævWB†6öFRÂ""  ¦FVböæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7C¢&WVW7BÂÖW76vS¢7G"’Óâ&VF—&V7E&W7öç6S ¢&W7öç6RÒ&VF—&V7E&W7öç6R€¢öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’À¢7FGW5ö6öFSÓ32À¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFRÂÖ‚ÖvSÓ'ÒÀ¢¢&W7öç6Rç6WEö6öö¶–R€¢ôäôDUõtT%ôdÄ4…ô4ôô´”RÀ¢öæöFU÷vV%öfÆ6…ö6öFR†ÖW76vR’À¢‡GGöæÇ“ÕG'VRÀ¢6ÖW6—FSÒ&Æ‚"À¢6V7W&S×&WVW7BçW&Âç66†VÖRÓÒ&‡GG2"À¢Ö…övSÓ3À¢FƒÕöæöFU÷vV%÷&Vf—‚‡&WVW7B’ç'7G&—‚"ò"’÷""ò"À¢¢&WGW&â&W7öç6P  ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô„”DUô„õdU%õU$Å5õc##“3 ¦FVböæöFU÷vV%ö†–FUö†÷fW%÷67&—B‚’Óâ7G# ¢–bæ÷BÖævW"æ†–FU÷æVÅö†÷fW%÷W&Ç3 ¢&WGW&â" ¢&WGW&â""#Ç67&—Câ‚‚“Óç¶6öç7BFW7CÖÓç¶6öç7B&sÖævWDGG&–'WFR‚v‡&Vbr—ÇÂrs¶–b‚&wÇÇ&rç7F'G5v—F‚‚r2r—ÇÇ&rç7F'G5v—F‚‚v¦f67&—C¢r—ÇÇ&rç7F'G5v—F‚‚vÖ–ÇFó¢r—ÇÇ&rç7F'G5v—F‚‚wFVÃ¢r’—&WGW&ârs·G'—¶6öç7BSÖæWrU$Â‡&rÆÆö6F–öâæ‡&Vb“·&WGW&âRæ÷&–v–ãÓÓÖÆö6F–öâæ÷&–v–ã÷RçF†æÖR·Rç6V&6‚·Ræ†6ƒ¢rwÖ6F6‚…ò—·&WGW&ârw×Ó¶6öç7B6öçfW'CÖÓç¶–b‚†–ç7Fæ6Vöb…DÔÄæ6†÷$VÆVÖVçB—ÇÆæFF6WBç6d†÷fW$†–FFVãÓÓÒsr—&WGW&ã¶6öç7BW&ÃÖFW7B†“¶–b‚W&Â—&WGW&ã¶æFF6WBç6d†÷fW$†–FFVãÒss¶æFF6WBç6dæeW&Ã×W&Ã¶ç&VÖ÷fTGG&–'WFR‚v‡&Vbr“¶ç6WDGG&–'WFR‚w&öÆRrÂvÆ–æ²r“¶–b‚æ†4GG&–'WFR‚wF&–æFW‚r’–çF$–æFWƒÓ¶æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆSÓç¶–b†RæFVfVÇE&WfVçFVB—&WGW&ã¶Rç&WfVçDFVfVÇB‚“¶Æö6F–öâæ76–vâ†æFF6WBç6dæeW&Â—Ò“¶æFDWfVçDÆ—7FVæW"‚v¶W–F÷vârÆSÓç¶–b†Ræ¶W“ÓÓÒtVçFW"wÇÆRæ¶W“ÓÓÒrr—¶Rç&WfVçDFVfVÇB‚“¶Æö6F–öâæ76–vâ†æFF6WBç6dæeW&Â—×Ò—Ó¶6öç7B&–æC×#Óç¶–b‡"æÖF6†W2bg"æÖF6†W2‚v¶‡&VeÒr’–6öçfW'B‡"“¶–b‡"çVW'•6VÆV7F÷$ÆÂ—"çVW'•6VÆV7F÷$ÆÂ‚v¶‡&VeÒr’æf÷$V6‚†6öçfW'B—Ó¶6öç7B7F'CÒ‚“Óç¶&–æB†Fö7VÖVçB“¶æWr×WFF–öäö'6W'fW"‡'3Óç'2æf÷$V6‚‡#Óç"æFFVDæöFW2æf÷$V6‚†ãÓç¶–b†âææöFUG—SÓÓÓ–&–æB†â—Ò’’’æö'6W'fR†Fö7VÖVçBæFö7VÖVçDVÆVÖVçBÇ¶6†–ÆDÆ—7C§G'VRÇ7V'G&VS§G'VWÒ—Ó¶Fö7VÖVçBç&VG•7FFSÓÓÒvÆöF–ærsöFö7VÖVçBæFDWfVçDÆ—7FVæW"‚tDôÔ6öçFVçDÆöFVBrÇ7F'BÇ¶öæ6S§G'VWÒ“§7F'B‚—Ò’‚“³Â÷67&—Câ""   ¦FVböæöFU÷vV'Æ–W%öÖö&–ÆUö772‚’Óâ7G# ¢&WGW&â""#Ç7G–ÆSâò¢5E$TÔdõ$tUõ$ô¤T5EôÔô$”ÄUôTD•EôäôDUõtT%Ä”U%õc##“‚¢ôÖVF–†Ö‚×v–GFƒ£s‚—¶‡FÖÂÆ&öG—·v–GFƒ£S¶Ö‚×v–GFƒ£S¶÷fW&fÆ÷r×ƒ¦†–FFVçÒç6†VÆÂÂçF÷ÂçFööÆ&"Âæw&–BÂæ6†ææVÂÖÆ–'&'’ÂæÆöv–âÂçf–WvW"Ö–æfòÖw&÷W¶Ö‚×v–GFƒ£S¶Ö–â×v–GFƒ£ÒçF÷Âæ†VFW"Ö7F–öç2Âæ†VFW"×f–WvW'¶fÆW‚×w&§w&¶v£—‡Ö–çWBÇ6VÆV7BÆ'WGFöç¶Ö‚×v–GFƒ£WÒæw&–G¶w&–B×FV×ÆFRÖ6öÇVÖç3£g"–×÷'FçGÒæ6†ææVÂÖ6&BÂæ6&G¶Ö–â×v–GFƒ£¶Ö‚×v–GFƒ£W×f–FVòÆ–Öw¶Ö‚×v–GFƒ£S¶†V–v‡C¦WF÷×ÓÂ÷7G–ÆSâ""   ¦FVböæöFU÷vV'Æ–W%ö'&æB‡&WVW7C¢&WVW7BÂæöæR’ÓâF–7E·7G"Âç•ÒÂæöæS ¢–b&WVW7B—2æöæS ¢&WGW&âæöæP¢†÷7BÒvVçDÖævW"åöæ÷&ÖÆ—¦U÷vV'Æ–W%ö'&æEöFöÖ–â‡&WVW7BçW&Âæ†÷7FæÖR÷"&WVW7Bæ†VFW'2ævWB‚&†÷7B"Â""’¢–bæ÷B†÷7C ¢&WGW&âæöæP¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEôD•4µôUD„õ$•DD•dUõc#S ¢2'&æB6VÆV7F–öâ—2F–ç’æB†÷7B×66÷VBâ&VBF†R7W'&VçBFöÖ–266W72æ§6öà¢26æ6†÷BF—&V7FÇ’6òæWvÇ’7&VFVBöFVÆWFVB'&æB6âæWfW"FWVæBöâv†–6€¢2ÆöærÖÆ—fVBV&Æ–2v÷&¶W"†æFÆVBF†—2&WVW7BâF†RÖævW"&VÆöB&VÖ–ç2¢2fÆÆ&6²f÷"G&ç6–VçB&VB&6Rà¢G'“ ¢&rÒ§6öâæÆöG2„44U55ôd”ÄRç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’¢'&æG2ÒvVçDÖævW"åöæ÷&ÖÆ—¦U÷vV'Æ–W%ö'&æG2‡&rævWB‚'vV'Æ–W%ö'&æG2"’’–b—6–ç7Fæ6R‡&rÂF–7B’VÇ6RµÐ¢W†6WB„õ4W'&÷"ÂfÇVTW'&÷"ÂG—TW'&÷"“ ¢ÖævW"ç&Vg&W6…÷'VçF–ÖUö66W75ö–eö6†ævVB‚¢'&æG2ÒÆ—7B†ÖævW"çvV'Æ–W%ö'&æG2÷"µÒ¢f÷"—FVÒ–â'&æG3 ¢–b†÷7B–âÆ—7B†—FVÒævWB‚&FöÖ–ç2"’÷"µÒ“ ¢&WGW&â—FVÐ¢&WGW&âæöæP  ¦FVböæöFU÷vV'Æ–W%ö'&æEöæÖR‡&WVW7C¢&WVW7BÂæöæR’Óâ7G# ¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B¢&WGW&â7G"‚†'&æB÷"·Ò’ævWB‚&æÖR"’÷"ÖævW"ææöFUöæÖR÷"$æöFR"’ç7G&—‚  ¦FVböæöFU÷vV'Æ–W%÷F†VÖU÷6æ6†÷B‡&WVW7C¢&WVW7BÂæöæRÒæöæR’ÓâF–7E·7G"Â7G%Ó ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õD„TÔUô5$õ55õ$ô4U55õcƒƒ ¢2V&Æ–2wVæ–6÷&âv÷&¶W'2†fR–æFWVæFVçB—F†öâÖVÖ÷'’â&VBF†RF–ç¢26†&VB66W72æ§6öâF†VÖR6æ6†÷BW"vR6òÆ—7BæBvF6‚f–Ww2Çv—0¢2W6RF†RW†7B6ÖR§W7B×6fVB6öÆ÷'2öÇ†fÇVW2&Vv&FÆW72öbv÷&¶W"à¢&s¢F–7E·7G"Âç•ÒÒ·Ð¢G'“ ¢ÆöFVBÒ§6öâæÆöG2„44U55ôd”ÄRç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’¢–b—6–ç7Fæ6R†ÆöFVBÂF–7B“ ¢&rÒÆöFV@¢W†6WB„õ4W'&÷"ÂfÇVTW'&÷"ÂG—TW'&÷"“ ¢&rÒ·Ð¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð¢FVbfÇVR†'&æEö¶W“¢7G"Â6öÆ÷%ö¶W“¢7G"ÂÇ†ö¶W“¢7G"ÂfÆÆ&6µö6öÆ÷#¢7G"ÂfÆÆ&6µöÇ†¢–çB’Óâ7G# ¢6öÆ÷"ÒvVçDÖævW"å÷vV'Æ–W%ö6öÆ÷"†'&æBævWB†'&æEö¶W’’÷"&rævWB†6öÆ÷%ö¶W’’ÂfÆÆ&6µö6öÆ÷"¢Ç†ÒvVçDÖævW"å÷vV'Æ–W%öÇ††'&æBævWB†Ç†ö¶W’ç&WÆ6R‚'vV'Æ–W%ò"Â""’’–bÇ†ö¶W’ç7F'G7v—F‚‚'vV'Æ–W%ò"’VÇ6RæöæRÂvVçDÖævW"å÷vV'Æ–W%öÇ†‡&rævWB†Ç†ö¶W’’ÂfÆÆ&6µöÇ†’¢&WGW&âvVçDÖævW"å÷vV'Æ–W%÷&v&†6öÆ÷"ÂÇ†¢&WGW&â°¢'vR#¢fÇVR‚'vUö6öÆ÷""Â'vV'Æ–W%÷vUö6öÆ÷""Â'vV'Æ–W%÷vUöÇ†"ÂÖævW"çvV'Æ–W%÷vUö6öÆ÷"ÂÖævW"çvV'Æ–W%÷vUöÇ†’À¢'æVÂ#¢fÇVR‚'æVÅö6öÆ÷""Â'vV'Æ–W%÷æVÅö6öÆ÷""Â'vV'Æ–W%÷æVÅöÇ†"ÂÖævW"çvV'Æ–W%÷æVÅö6öÆ÷"ÂÖævW"çvV'Æ–W%÷æVÅöÇ†’À¢&66VçB#¢fÇVR‚&66VçEö6öÆ÷""Â'vV'Æ–W%ö66VçEö6öÆ÷""Â'vV'Æ–W%ö66VçEöÇ†"ÂÖævW"çvV'Æ–W%ö66VçEö6öÆ÷"ÂÖævW"çvV'Æ–W%ö66VçEöÇ†’À¢'FW‡B#¢fÇVR‚'FW‡Eö6öÆ÷""Â'vV'Æ–W%÷FW‡Eö6öÆ÷""Â'vV'Æ–W%÷FW‡EöÇ†"ÂÖævW"çvV'Æ–W%÷FW‡Eö6öÆ÷"ÂÖævW"çvV'Æ–W%÷FW‡EöÇ†’À¢Ð  ¦FVböæöFUö–FVçF—G•÷6æ6†÷B‚’ÓâGWÆU·7G"Â7G%Ó ¢""%&VBÖ–âÖ÷væVBæöFR–FVçF—G’g&öÒF†R6†&VBFöÖ–2æVÂ6æ6†÷Bâ"" ¢æöFUöæÖRÒ7G"†ÖævW"ææöFUöæÖR÷"""’ç7G&—‚¢æöFUöÆövõ÷W&ÂÒ7G"†ÖævW"ææöFUöÆövõ÷W&Â÷"""’ç7G&—‚¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô”DTåD•E•ôD•4µôUD„õ$•DD•dUõc## ¢2ÆövòöæÖR7–æ2—2&V6V—fVB'’F†R6–ævÆR6öçG&öÂv÷&¶W"Âv†–ÆRvV"Æ–W ¢2&WVW7G2'Vâ–â6W&FRÆöærÖÆ—fVBV&Æ–2v÷&¶W'2â&VBæVÂ×W6W'2æ§6öà¢2W"vRö76WB&WVW7B6ò'&æF–ær6†ævW2&R–ÖÖVF–FVÇ’7&÷72×&ö6W72à¢G'“ ¢&rÒ§6öâæÆöG2…äTÅõU4U%5ôd”ÄRç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’¢–b—6–ç7Fæ6R‡&rÂF–7B“ ¢æöFUöæÖRÒ7G"‡&rævWB‚&æöFUöæÖR"’÷"æöFUöæÖR’ç7G&—‚¢æöFUöÆövõ÷W&ÂÒ7G"‡&rævWB‚&æöFUöÆövõ÷W&Â"’÷"""’ç7G&—‚¢W†6WB„õ4W'&÷"ÂfÇVTW'&÷"ÂG—TW'&÷"“ ¢70¢&WGW&âæöFUöæÖRÂæöFUöÆövõ÷W&À  ¦FVböæöFU÷vV'Æ–W%÷WFFUöÆövõ÷W&Â‚’Óâ7G# ¢25E$TÔdõ$tUôäôDUõUDDUô¥4ôåõ4U%dU%ôÄô4ÅôÄôtõõc ¢27–æ6‡&öæ—¦VBæöFRÆövò—2W‡÷6VBF‡&÷Vv‚F†R7G&VÒ×&öÆRÆövò&÷WFRÀ¢2¶VW–ærWFFRæ§6öâ&÷VæBFòF†RæöFRF†B7GVÆÇ’6W'fVBF†R&WVW7Bà¢öæöFUöæÖRÂÆövòÒöæöFUö–FVçF—G•÷6æ6†÷B‚¢–bÆövòç7F'G7v—F‚‚"öæöFRÖÆöv÷2ò"“ ¢&uöæÖRÒÆövõ¶ÆVâ‚"öæöFRÖÆöv÷2ò"“¥Ð¢f–ÆVæÖRÒF‚‡&uöæÖR’ææÖP¢–bf–ÆVæÖRæBf–ÆVæÖRÓÒ&uöæÖRæB„äôDUôÄôtõõ$ôõBòf–ÆVæÖR’æ—5öf–ÆR‚“ ¢&WGW&â"÷vV"×Æ–W"öÆövò ¢&WGW&â" ¢–bÆövòç7F'G7v—F‚‚‚&‡GG3¢òò"Â&‡GG¢òò"’“ ¢&WGW&âÆövð¢&WGW&â"   ¦FVböæöFU÷vV'Æ–W%÷WFFU÷F†VÖU÷6æ6†÷B‡&WVW7C¢&WVW7BÂæöæRÒæöæR’ÓâF–7E·7G"Âç•Ó ¢25E$TÔdõ$tUôäôDUõUDDUô¥4ôåôÄô4ÅõtT%Ä”U%õD„TÔUõc“ ¢25E$TÔdõ$tUôäôDUõUDDUô¥4ôåôÕTÅD•ô%$äEõD„TÔUõcSƒ ¢2WFFRæ§6öâ—2†÷7B×66÷VC¢Ç’F†—2'&æBw2WÆöFVBöW‡FW&æÂÆövòæ@¢26öÆ÷'2öÇ†v†–ÆR&WF–æ–ær6W'fW"ÖÆWfVÂfÇVW2öæÇ’2fÆÆ&6²à¢&s¢F–7E·7G"Âç•ÒÒ·Ð¢G'“ ¢ÆöFVBÒ§6öâæÆöG2„44U55ôd”ÄRç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’¢–b—6–ç7Fæ6R†ÆöFVBÂF–7B“ ¢&rÒÆöFV@¢W†6WB„õ4W'&÷"ÂfÇVTW'&÷"ÂG—TW'&÷"“ ¢&rÒ·Ð¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð ¢FVb—FVÒ†'&æEö¶W“¢7G"Â6öÆ÷%ö¶W“¢7G"ÂÇ†ö¶W“¢7G"ÂfÆÆ&6µö6öÆ÷#¢7G"ÂfÆÆ&6µöÇ†¢–çB’ÓâGWÆU·7G"Â–çBÂ7G%Ó ¢6öÆ÷"ÒvVçDÖævW"å÷vV'Æ–W%ö6öÆ÷"†'&æBævWB†'&æEö¶W’’÷"&rævWB†6öÆ÷%ö¶W’’ÂfÆÆ&6µö6öÆ÷"¢Ç†ÒvVçDÖævW"å÷vV'Æ–W%öÇ††'&æBævWB†Ç†ö¶W’ç&WÆ6R‚'vV'Æ–W%ò"Â""’’–bÇ†ö¶W’ç7F'G7v—F‚‚'vV'Æ–W%ò"’VÇ6RæöæRÂvVçDÖævW"å÷vV'Æ–W%öÇ†‡&rævWB†Ç†ö¶W’’ÂfÆÆ&6µöÇ†’¢&WGW&â6öÆ÷"ÂÇ†ÂvVçDÖævW"å÷vV'Æ–W%÷&v&†6öÆ÷"ÂÇ† ¢vUö6öÆ÷"ÂvUöÇ†ÂvU÷&v&Ò—FVÒ‚'vUö6öÆ÷""Â'vV'Æ–W%÷vUö6öÆ÷""Â'vV'Æ–W%÷vUöÇ†"ÂÖævW"çvV'Æ–W%÷vUö6öÆ÷"ÂÖævW"çvV'Æ–W%÷vUöÇ†¢æVÅö6öÆ÷"ÂæVÅöÇ†ÂæVÅ÷&v&Ò—FVÒ‚'æVÅö6öÆ÷""Â'vV'Æ–W%÷æVÅö6öÆ÷""Â'vV'Æ–W%÷æVÅöÇ†"ÂÖævW"çvV'Æ–W%÷æVÅö6öÆ÷"ÂÖævW"çvV'Æ–W%÷æVÅöÇ†¢66VçEö6öÆ÷"Â66VçEöÇ†Â66VçE÷&v&Ò—FVÒ‚&66VçEö6öÆ÷""Â'vV'Æ–W%ö66VçEö6öÆ÷""Â'vV'Æ–W%ö66VçEöÇ†"ÂÖævW"çvV'Æ–W%ö66VçEö6öÆ÷"ÂÖævW"çvV'Æ–W%ö66VçEöÇ†¢FW‡Eö6öÆ÷"ÂFW‡EöÇ†ÂFW‡E÷&v&Ò—FVÒ‚'FW‡Eö6öÆ÷""Â'vV'Æ–W%÷FW‡Eö6öÆ÷""Â'vV'Æ–W%÷FW‡EöÇ†"ÂÖævW"çvV'Æ–W%÷FW‡Eö6öÆ÷"ÂÖævW"çvV'Æ–W%÷FW‡EöÇ†¢'&æEöÆövòÒ7G"†'&æBævWB‚&Æövõ÷W&Â"’÷"""’ç7G&—‚¢–böæöFU÷vV%ö'&æEö76WE÷F‚†'&æBÂ&Æövõö76WB"’—2æ÷BæöæS ¢Æövõ÷W&ÂÒ"÷vV"×Æ–W"öÆövò ¢VÆ–b'&æEöÆövòç7F'G7v—F‚‚‚&‡GG3¢òò"Â&‡GG¢òò"’“ ¢Æövõ÷W&ÂÒ'&æEöÆövð¢VÆ–b'&æC ¢Æövõ÷W&ÂÒ" ¢VÇ6S ¢Æövõ÷W&ÂÒöæöFU÷vV'Æ–W%÷WFFUöÆövõ÷W&Â‚¢&WGW&â°¢&Æövõ÷W&Â#¢Æövõ÷W&ÂÀ¢'vUö6öÆ÷"#¢vUö6öÆ÷"Â'vUöÇ†#¢vUöÇ†Â'vU÷&v&#¢vU÷&v&À¢'æVÅö6öÆ÷"#¢æVÅö6öÆ÷"Â'æVÅöÇ†#¢æVÅöÇ†Â'æVÅ÷&v&#¢æVÅ÷&v&À¢&66VçEö6öÆ÷"#¢66VçEö6öÆ÷"Â&66VçEöÇ†#¢66VçEöÇ†Â&66VçE÷&v&#¢66VçE÷&v&À¢'FW‡Eö6öÆ÷"#¢FW‡Eö6öÆ÷"Â'FW‡EöÇ†#¢FW‡EöÇ†Â'FW‡E÷&v&#¢FW‡E÷&v&À¢Ð  ¦FVböæöFU÷vV'Æ–W%÷F†VÖUö772‡&WVW7C¢&WVW7BÂæöæRÒæöæR’Óâ7G# ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õD„TÔUô4õ$Uõc###“ ¢25E$TÔdõ$tUôäôDUõd•5TÅô$Ää4Uõc##3S ¢266VçB—2&W6W'fVBf÷"7F—fRö†÷fW"V×†6—3²æ÷&ÖÂ7W&f6W2W6R¢2æWWG&Â&÷&FW"FW&—fVBg&öÒF†RÖævVBFW‡B6öÆ÷"à¢F†VÖRÒöæöFU÷vV'Æ–W%÷F†VÖU÷6æ6†÷B‡&WVW7B¢&WGW&â€¢#§&ö÷G²Ò×6b×vS¢"²F†VÖU²'vR%Ð¢²#²Ò×6b×æVÃ¢"²F†VÖU²'æVÂ%Ð¢²#²Ò×6bÖ66VçC¢"²F†VÖU²&66VçB%Ð¢²#²Ò×6b×FW‡C¢"²F†VÖU²'FW‡B%Ð¢²#²Ò×6b×6ögBÖÆ–æS¦6öÆ÷"ÖÖ—‚†–â7&v"Çf"‚Ò×6b×FW‡B’RRÇG&ç7&VçB“²Ò×6b×6ögBÖf–ÆÃ¦6öÆ÷"ÖÖ—‚†–â7&v"Çf"‚Ò×6b×æVÂ’ƒ‚RÇG&ç7&VçB—Ò ¢²&&öG—¶&6¶w&÷VæC§f"‚Ò×6b×vR’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçGÒ ¢²"ò¢5E$TÔdõ$tUôäôDUô4„ääTÅõtUõD„TÔUõ5”ä5õc##S"¢òçF÷ÂçFööÆ&"ÂæÆöv–âÂæ6&BÂæÆöv÷WBÂæ6÷VçBÆ–çWBÇ6VÆV7BÂæ6FVv÷'’Ö6†—Âæ6†ææVÂÖÆ–'&'’Âæ†VFW"Ö–æfòÖw&÷WÂæÖçVÂÖÆöv–â×FövvÆRÂæÖçVÂÖÆöv–âÖ&6·¶&6¶w&÷VæC§f"‚Ò×6b×æVÂ’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçC¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6b×6ögBÖÆ–æR’–×÷'FçGÒ ¢²"çF÷ÂçFööÆ&'¶&÷‚×6†F÷s£G‚3'‚&v&ƒÃÃÂãb’–×÷'FçGÒ ¢²"æÆöv÷WBÂæ6÷VçBÆ–çWBÇ6VÆV7BÂæÖçVÂÖÆöv–â×FövvÆRÂæÖçVÂÖÆöv–âÖ&6·¶&6¶w&÷VæC§f"‚Ò×6b×6ögBÖf–ÆÂ’–×÷'FçGÒ ¢²&ƒÆƒ"Æƒ2Âæ'&æBÂæ'&æBÖ6÷’Âæ'&æBÖ6÷’"Âæ'&æBÖ6÷’6ÖÆÂÂæÖWFÂæÖWF"ÂæÖWF6ÖÆÂÂæ6÷VçBÂæV×G’ÆÆ&VÂÂæ†VFW"Ö–æfòÖw&÷WÂæ†VFW"Ö–æfòÖw&÷WÆ&VÂÂæ†VFW"Ö–æfòÖw&÷W'¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçGÒ ¢²"æ6FVv÷'’Ö6†—æ7F—fRÂæÖ&²Æ'WGFöå·G—S×7V&Ö—E×¶&6¶w&÷VæC§f"‚Ò×6bÖ66VçB’–×÷'FçC¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6bÖ66VçB’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçGÒ ¢²"æF÷væÆöBÖ'WGFöç¶&6¶w&÷VæC§f"‚Ò×6bÖ66VçB’–×÷'FçC¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6bÖ66VçB’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçGÒ ¢²"æÆöv–âÖF—f–FW'¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçGÒæÆöv–âÖF—f–FW#¦&Vf÷&RÂæÆöv–âÖF—f–FW#¦gFW'¶&6¶w&÷VæC§f"‚Ò×6b×6ögBÖÆ–æR’–×÷'FçGÒ ¢²"æÆöv÷WC¦†÷fW"Âæ6FVv÷'’Ö6†—¦†÷fW"Âæ6&C¦†÷fW'¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6bÖ66VçB’–×÷'FçGÒ ¢²"æ6&C¦†÷fW'·G&ç6f÷&Ó§G&ç6ÆFU’‚Ó‚“¶&÷‚×6†F÷s£W‚#‡‚&v&ƒÃÃÂã‚’–×÷'FçGÒ ¢²"æ6FVv÷'’Ö6†—æ7F—fW¶&÷‚×6†F÷s¦æöæR–×÷'FçGÒ ¢  ¦FVböæöFU÷vV%÷vR‡W6W#¢æöFUW6W$6öæf–rÂæöæRÂ&WVW7C¢&WVW7BÂW'&÷#¢7G"Ò""’Óâ7G# ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢25E$TÔdõ$tUôäôDUôUDõôÄôt”åô„”DUõ4”täõUEõc###c ¢6öçG&öÇ2ÒöæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B¢Æöv÷WEö‡FÖÂÒ""–b6öçG&öÇ5²&Æöv–åöÖöFR%ÒÓÒ&WFò"VÇ6R€¢sÆ6Æ73Ò&Æöv÷WB"‡&VcÒ"r²‡FÖÂæW66R‡&Vf—‚’²r÷vV"×Æ–W"öÆöv÷WB#å6–vâ÷WCÂöâp¢¢F÷væÆöEö‡FÖÂÒ€¢sÆ6Æ73Ò&F÷væÆöBÖ'WGFöâ"‡&VcÒ"r²‡FÖÂæW66R‡&Vf—‚’²r÷vV"×Æ–W"öF÷væÆöB"F÷væÆöCäF÷væÆöCÂöâp¢–böæöFU÷vV%öF÷væÆöEöf–Æ&ÆR‡&WVW7B’VÇ6R" ¢¢†VFW%ö'WGFöç5ö‡FÖÂÒ€¢sÆF—b6Æ73Ò&†VFW"Ö'WGFöç2#âr²F÷væÆöEö‡FÖÂ²Æöv÷WEö‡FÖÂ²sÂöF—câp¢–bF÷væÆöEö‡FÖÂ÷"Æöv÷WEö‡FÖÂVÇ6R" ¢¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð¢7–æ6‡&öæ—¦VEöæöFUöæÖRÂ7–æ6‡&öæ—¦VEöæöFUöÆövõ÷W&ÂÒöæöFUö–FVçF—G•÷6æ6†÷B‚¢'&æEöæÖRÒ7G"†'&æBævWB‚&æÖR"’÷"7–æ6‡&öæ—¦VEöæöFUöæÖR÷"$æöFR"’ç7G&—‚¢ff–6öâÒöæöFU÷vV%öff–6öåöÆ–æ²‡&Vf—‚Â&WVW7B¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%ôÄôt”åô4TåDU%õc## ¢2W6RF†R6öæf–wW&VBæöFRÆövòöâF†RÆöv–âvRv†Vâf–Æ&ÆRÂ&VÖ÷fRF†P¢2&VGVæFçB7V'F—FÆRö†VÇW"6÷’ÂæB6VçFW"F†RVçF—&RÆöv–â6†VÆÂà¢Æöv–åöÆövõ÷W&ÂÒ7G"†'&æBævWB‚&Æövõ÷W&Â"’÷"""’ç7G&—‚’–b'&æBVÇ6R7–æ6‡&öæ—¦VEöæöFUöÆövõ÷W&À¢–b7G"†'&æBævWB‚&Æövõö76WB"’÷"""’ç7G&—‚“ ¢Æöv–åöÆövõ÷W&ÂÒb'·&Vf—‚ç'7G&—‚ròr—Ò÷vV"×Æ–W"öÆövò ¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%ôÄôtõõU$Åõc### ¢2Æö6Â7–æ6‡&öæ—¦VBæöFRÆöv÷2×W7B&R&WVW7FVBF‡&÷Vv‚F†RvV"Æ–W"w0¢2Æ–Æ—7Bô&öÆRÂæ÷BF‡&÷Vv‚F†R&÷FV7FVBöæöFRÖÆöv÷2æVÂ&÷WFRà¢–bÆöv–åöÆövõ÷W&Âç7F'G7v—F‚‚"öæöFRÖÆöv÷2ò"“ ¢66W75÷&Vf—‚Ò&Vf—‚ç'7G&—‚"ò"¢Æöv–åöÆövõ÷W&ÂÒb'¶66W75÷&Vf—‡Ò÷vV"×Æ–W"öÆövò ¢–bW6W"—2æöæS ¢V–6µ÷W6W"ÒöæöFU÷vV%÷6VÆV7FVE÷W6W"‡&WVW7B’–b6öçG&öÇ5²&Æöv–åöÖöFR%ÒÓÒ'V–6²"VÇ6RæöæP¢–b6öçG&öÇ5²&Æöv–åöÖöFR%ÒÓÒ'V–6²"æBV–6µ÷W6W"—2æöæRæBæ÷BW'&÷# ¢W'&÷"Ò%V–6²Æöv–âW6W"—2Væf–Æ&ÆRâ6VÆV7BfÆ–BW6W"–âvV"Æ–W"ÖævRâ ¢ÆW'BÒ€¢sÆF—b6Æ73Ò&ÆW'B"r²‚rFFÖÆöv–âÖÆW'Br–bV–6µ÷W6W"—2æ÷BæöæRVÇ6Rrr’²sâp¢²‡FÖÂæW66R†W'&÷"’²sÂöF—câp¢–bW'&÷"VÇ6R" ¢¢Æöv–å÷f—7VÂÒ€¢sÆ–Ör6Æ73Ò&'&æBÖÆövò"7&3Ò"r²‡FÖÂæW66R†Æöv–åöÆövõ÷W&ÂÂV÷FSÕG'VR’²r"ÇCÒ"r²‡FÖÂæW66R†'&æEöæÖRÂV÷FSÕG'VR’²r"ÆöF–æsÒ&VvW"#âp¢–bÆöv–åöÆövõ÷W&ÂVÇ6RsÇ7â6Æ73Ò&Ö&²#î)kcÂ÷7ãâp¢¢V–6µöÆöv–åö‡FÖÂÒ" ¢V–6µ÷FövvÆU÷67&—BÒ" ¢ÖçVÅöf÷&Õö†–FFVâÒ" ¢ÖçVÅöWFöfö7W2Ò"WFöfö7W2 ¢–bV–6µ÷W6W"—2æ÷BæöæS ¢6†ö–6Uö†–FFVâÒ"†–FFVâ"–bW'&÷"VÇ6R" ¢V–6µöÆöv–åö‡FÖÂÒ€¢sÆF—b6Æ73Ò'V–6²ÖÆöv–âÖ6†ö–6R"FF×V–6²ÖÆöv–âÖ6†ö–6Rr²6†ö–6Uö†–FFVà¢²sãÆf÷&Ò6Æ73Ò'V–6²ÖÆöv–â×W6W""ÖWF†öCÒ'÷7B"7F–öãÒ"p¢²‡FÖÂæW66R‡&Vf—‚ÂV÷FSÕG'VR’²r÷vV"×Æ–W"÷V–6²ÖÆöv–â#ãÆ'WGFöâG—SÒ'7V&Ö—B#ãÇ7ãä6öçF–çVR2p¢²‡FÖÂæW66R‡V–6µ÷W6W"ææÖR’²sÂ÷7ããÂö'WGFöããÂöf÷&Óâp¢²sÆF—b6Æ73Ò&Æöv–âÖF—f–FW"#ãÇ7ãæ÷#Â÷7ããÂöF—câp¢²sÆ'WGFöâ6Æ73Ò&ÖçVÂÖÆöv–â×FövvÆR"G—SÒ&'WGFöâ"FFÖÖçVÂÖÆöv–â×FövvÆR&–ÖW‡æFVCÒ"p¢²‚'G'VR"–bW'&÷"VÇ6R&fÇ6R"’²r#äÆöv–âv—F‚W6W&æÖRf×²77v÷&CÂö'WGFöããÂöF—câp¢¢ÖçVÅöf÷&Õö†–FFVâÒ""–bW'&÷"VÇ6R"†–FFVâ ¢ÖçVÅöWFöfö7W2Ò""–bÖçVÅöf÷&Õö†–FFVâVÇ6R"WFöfö7W2 ¢V–6µ÷FövvÆU÷67&—BÒ€¢#Ç67&—Câ‚‚“Óç¶6öç7B3ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FF×V–6²ÖÆöv–âÖ6†ö–6UÒr’ÇCÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖÖçVÂÖÆöv–â×FövvÆUÒr’Â ¢&cÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖÖçVÂÖÆöv–âÖf÷&ÕÒr’Æ#ÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖÖçVÂÖÆöv–âÖ&6µÒr’ÆÖFö7VÖVçBçVW'•6VÆV7F÷"‚u¶FFÖÆöv–âÖÆW'EÒr“² ¢&6öç7B÷VãÒ‚“Óç¶–b‚7ÇÂb—&WGW&ã¶2æ†–FFVã×G'VS¶bæ†–FFVãÖfÇ6S·Còç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂwG'VRr“¶bçVW'•6VÆV7F÷"‚v–çWBr“òæfö7W2‚—Ó² ¢&6öç7B6Æ÷6SÒ‚“Óç¶–b‚7ÇÂb—&WGW&ã¶bæ†–FFVã×G'VS¶2æ†–FFVãÖfÇ6S·Còç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂvfÇ6Rr“¶bç&W6WB‚“¶–b†–æ†–FFVã×G'VS·Còæfö7W2‚—Ó² ¢'CòæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆ÷Vâ“¶#òæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆ6Æ÷6R—Ò’‚“³Â÷67&—Câ ¢¢7F–öåö6Æ72Ò&ÖçVÂÖÆöv–âÖ7F–öç2v—F‚Ö&6²"–bV–6µ÷W6W"—2æ÷BæöæRVÇ6R&ÖçVÂÖÆöv–âÖ7F–öç2 ¢&6µö'WGFöâÒ€¢sÆ'WGFöâ6Æ73Ò&ÖçVÂÖÆöv–âÖ&6²"G—SÒ&'WGFöâ"FFÖÖçVÂÖÆöv–âÖ&6³ä&6³Âö'WGFöãâp¢–bV–6µ÷W6W"—2æ÷BæöæRVÇ6R" ¢¢ÖçVÅöÆöv–åö‡FÖÂÒ€¢sÆf÷&Ò6Æ73Ò&ÖçVÂÖÆöv–âÖf÷&Ò"FFÖÖçVÂÖÆöv–âÖf÷&ÒÖWF†öCÒ'÷7B"7F–öãÒ"p¢²‡FÖÂæW66R‡&Vf—‚ÂV÷FSÕG'VR’²r÷vV"×Æ–W"öÆöv–â"r²ÖçVÅöf÷&Õö†–FFVâ²sâp¢²sÆÆ&VÃåW6W&æÖSÆ–çWBæÖSÒ'W6W&æÖR"WFö6ö×ÆWFSÒ'W6W&æÖR"&WV—&VBr²ÖçVÅöWFöfö7W2²sãÂöÆ&VÃâp¢²sÆÆ&VÃå77v÷&CÆ–çWBG—SÒ'77v÷&B"æÖSÒ'77v÷&B"WFö6ö×ÆWFSÒ&7W'&VçB×77v÷&B"&WV—&VCãÂöÆ&VÃâp¢²sÆF—b6Æ73Ò"r²7F–öåö6Æ72²r#ãÆ'WGFöâG—SÒ'7V&Ö—B#äÆöv–ãÂö'WGFöãâp¢²&6µö'WGFöâ²sÂöF—cãÂöf÷&Óâp¢¢&WGW&â€¢sÂFö7G—R‡FÖÃãÆ‡FÖÃãÆ†VCãÆÖWF6†'6WCÒ'WFbÓ‚#ãÆÖWFæÖSÒ'f–Ww÷'B"6öçFVçCÒ'v–GFƒÖFWf–6R×v–GF‚Æ–æ—F–Â×66ÆSÓ#âp¢²ff–6öâ²sÇF—FÆSâr²‡FÖÂæW66R†'&æEöæÖR’²r+rvV"Æ–W#Â÷F—FÆSãÇ7G–ÆSâr²ôäôDUõtT%õÄ”U%ô552²öæöFU÷vV'Æ–W%÷F†VÖUö772‡&WVW7B’²sÂ÷7G–ÆSãÂö†VCãÆ&öG’6Æ73Ò&Æöv–â×67&VVâ#âp¢²sÇ6V7F–öâ6Æ73Ò&Æöv–â#ãÆF—b6Æ73Ò&'&æB'&æBÒÖÆöv–â#âr²Æöv–å÷f—7VÂ²sÆF—b6Æ73Ò&'&æBÖ6÷’#ãÆ#âp¢²‡FÖÂæW66R†'&æEöæÖR¢²sÂö#ãÂöF—cãÂöF—cãÆƒå6–vâ–âFòvF6ƒÂöƒâp¢²ÆW'B²V–6µöÆöv–åö‡FÖÂ²ÖçVÅöÆöv–åö‡FÖÂ²sÂ÷6V7F–öãâr²V–6µ÷FövvÆU÷67&—@¢²sÆF—b7G–ÆSÒ'÷6—F–öã¦f—†VC·&–v‡C£ƒ¶&÷GFöÓ£‡ƒ¶6öÆ÷#¢3Vcsƒc¶föçC£‚7—7FVÒ×V’#çbr²‡FÖÂæW66R…dU%4”ôâ’²sÂöF—câr²öæöFU÷vV%ö†–FUö†÷fW%÷67&—B‚’²sÂö&öG“ãÂö‡FÖÃâp¢¢6†ææVÇ2ÒööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%ô4DTtõ%•ô4„•5õc##S ¢2æöFUW6W$6†ææVÂ6â&VÆöærFò×VÇF—ÆR6FVv÷&–W2â'V–ÆBF†R6FVv÷'¢2f–ÇFW"g&öÒÆÂ7–æ6‡&öæ—¦VB76–væÖVçG2–ç7FVBöböæÇ’F†RÆVv7¢26–ævÆR6FVv÷'–f–VÆBà¢6FVv÷&–W3¢Æ—7E·7G%ÒÒµÐ¢6†ææVÅö6FVv÷&–W3¢F–7E·7G"ÂÆ—7E·7G%ÕÒÒ·Ð¢f÷"6‚–â6†ææVÇ3 ¢æÖW3¢Æ—7E·7G%ÒÒµÐ¢&–Ö'’Ò†6‚æ6FVv÷'’÷"""’ç7G&—‚¢–b&–Ö'“ ¢æÖW2æVæB‡&–Ö'’¢f÷"&r–âÆ—7B†vWFGG"†6‚Â&6FVv÷&–W2"ÂµÒ’÷"µÒ“ ¢6ÆVæVBÒ7G"‡&r÷"""’ç7G&—‚¢–b6ÆVæVBæB6ÆVæVBæ÷B–âæÖW3 ¢æÖW2æVæB†6ÆVæVB¢–bæ÷BæÖW3 ¢æÖW2Ò²%Væ6FVv÷&—¦VB%Ð¢6†ææVÅö6FVv÷&–W5¶6‚æ¶W•ÒÒæÖW0¢f÷"6B–âæÖW3 ¢–b6Bæ÷B–â6FVv÷&–W3 ¢6FVv÷&–W2æVæB†6B¢25E$TÔdõ$tUôäôDUô4„ääTÅô„TDU%õd”UtU%ô”ädõõc##3“ ¢25E$TÔdõ$tUôäôDUô4„ääTÅô„TDU%ôU…•%•ôd•…õc##C3 ¢†VFW%÷Æ–W%öW‡—&W2Ò$æWfW" ¢†VFW%÷&uöW‡—&W2Ò7G"‡W6W"æW‡—&W5öB÷"""’ç7G&—‚¢–b†VFW%÷&uöW‡—&W3 ¢G'“ ¢†VFW%÷'6VEöW‡—&W2ÒFFWF–ÖRæg&öÖ—6öf÷&ÖB††VFW%÷&uöW‡—&W2ç&WÆ6R‚%¢"Â"³£"’¢†VFW%÷Æ–W%öW‡—&W2Ò†VFW%÷'6VEöW‡—&W2ç7G&gF–ÖR‚"U’ÒVÒÒVBTƒ¢TÒ"¢W†6WBfÇVTW'&÷# ¢†VFW%÷Æ–W%öW‡—&W2Ò†VFW%÷&uöW‡—&W0 ¢†–FU÷V–6µ÷W6W%ö–æfòÒöæöFU÷vV%ö†–FU÷V–6µ÷W6W%ö–æfò‡&WVW7B¢†VFW%÷f–WvW%÷'G3¢Æ—7E·7G%ÒÒµÐ¢–b6öçG&öÇ5²'6†÷u÷W6W%ö–æfò%ÒæBæ÷B†–FU÷V–6µ÷W6W%ö–æfó ¢†VFW%÷f–WvW%÷'G2æVæB€¢sÆF—b6Æ73Ò&†VFW"Ö–æfòÖw&÷WW6W"#âp¢sÆF—cãÆÆ&VÃåW6W&æÖSÂöÆ&VÃãÆ#âr²‡FÖÂæW66R‡W6W"çW6W&æÖR÷"W6W"ææÖR’²sÂö#ãÂöF—câp¢sÆF—cãÆÆ&VÃäW‡—&SÂöÆ&VÃãÆ"6Æ73Ò&W‡—'’#âr²‡FÖÂæW66R††VFW%÷Æ–W%öW‡—&W2’²sÂö#ãÂöF—câp¢sÂöF—câp¢¢–b6öçG&öÇ5²'6†÷uö6öææV7F–öåö–æfò%ÒæBæ÷B†–FU÷V–6µ÷W6W%ö–æfó ¢†VFW%÷f–WvW%÷'G2æVæB€¢sÆF—b6Æ73Ò&†VFW"Ö–æfòÖw&÷W6öææV7F–öâ#âp¢sÆF—cãÆÆ&VÃäÖ‚6öææV7F–öç3ÂöÆ&VÃãÆ#âr²‚uVæÆ–Ö—FVBr–bÖ‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’ÓÒVÇ6R7G"†Ö‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’’’²sÂö#ãÂöF—câp¢sÆF—cãÆÆ&VÃäöæÆ–æRW6SÂöÆ&VÃãÆ"–CÒ&†VFW"ÖöæÆ–æR×W6R#âr²7G"†Ö‚ƒÂ–çB†ÖævW"æ7F—fUö6öææV7F–öç5öf÷%÷W6W"‡W6W"’’’’²sÂö#ãÂöF—câp¢sÂöF—câp¢¢†VFW%÷f–WvW%ö‡FÖÂÒ€¢sÆF—b6Æ73Ò&†VFW"×f–WvW"#âr²rræ¦ö–â††VFW%÷f–WvW%÷'G2’²sÂöF—câp¢–b†VFW%÷f–WvW%÷'G2VÇ6R" ¢ ¢6&G2ÒµÐ¢f÷"6‚–â6†ææVÇ3 ¢æÖW2Ò6†ææVÅö6FVv÷&–W2ævWB†6‚æ¶W’’÷"²%Væ6FVv÷&—¦VB%Ð¢ÆövòÒöæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†6‚æÆövõ÷W&ÂÂ&WVW7B¢f—7VÂÒ‚sÆ–Ör6Æ73Ò&Æövò"7&3Ò"r²‡FÖÂæW66R†ÆövòÂV÷FSÕG'VR’²r"ÇCÒ""ÆöF–æsÒ&Æ§’#âr’–bÆövòVÇ6RsÇ7â6Æ73Ò&fÆÆ&6²#î)kcÂ÷7ãâp¢6&G2æVæB€¢sÆ6Æ73Ò&6&B"‡&VcÒ"r²‡FÖÂæW66R‡&Vf—‚’²r÷vV"×Æ–W"÷vF6‚òr²W&ÆÆ–"ç'6RçV÷FR†6‚æ¶W’Â6fSÒ""’²r"FFÖ6†ææVÂFFÖæÖSÒ"p¢²‡FÖÂæW66R†6‚ææÖRæÆ÷vW"‚’ÂV÷FSÕG'VR’²r"FFÖ6FVv÷&–W3Ò"r²‡FÖÂæW66R†§6öâæGV×2…·‚æÆ÷vW"‚’f÷"‚–âæÖW5Ò’ÂV÷FSÕG'VR’²r#âp¢²f—7VÂ²sÆF—b6Æ73Ò&ÖWF#ãÆ#âr²‡FÖÂæW66R†6‚ææÖR’²sÂö#ãÇ6ÖÆÃâr²‡FÖÂæW66R‚"+r"æ¦ö–â†æÖW2’’²sÂ÷6ÖÆÃãÂöF—cãÂöâp¢¢–bæ÷B6&G3 ¢6&G2Ò²sÆF—b6Æ73Ò&V×G’#äæòöæÆ–æR„Å26†ææVÇ2&Rf–Æ&ÆRf÷"F†—2W6W"ãÂöF—câuÐ¢6†—2ÒsÆ'WGFöâ6Æ73Ò&6FVv÷'’Ö6†—7F—fR"G—SÒ&'WGFöâ"FFÖ6FVv÷'’Ö6†—Ò""&–×&W76VCÒ'G'VR#äÆÃÂö'WGFöãâr²rræ¦ö–â€¢sÆ'WGFöâ6Æ73Ò&6FVv÷'’Ö6†—"G—SÒ&'WGFöâ"FFÖ6FVv÷'’Ö6†—Ò"r²‡FÖÂæW66R†6BæÆ÷vW"‚’ÂV÷FSÕG'VR’²r"&–×&W76VCÒ&fÇ6R#âr²‡FÖÂæW66R†6B’²sÂö'WGFöãâp¢f÷"6B–â6FVv÷&–W0¢¢&WGW&â€¢sÂFö7G—R‡FÖÃãÆ‡FÖÃãÆ†VCãÆÖWF6†'6WCÒ'WFbÓ‚#ãÆÖWFæÖSÒ'f–Ww÷'B"6öçFVçCÒ'v–GFƒÖFWf–6R×v–GF‚Æ–æ—F–Â×66ÆSÓ#âp¢²ff–6öâ²sÇF—FÆSâr²‡FÖÂæW66R†'&æEöæÖR’²r+rvV"Æ–W#Â÷F—FÆSãÇ7G–ÆSâr²ôäôDUõtT%õÄ”U%ô552²rò¢5E$TÔdõ$tUôäôDUõõ%DÅõD„TÔUôÅ•õc###‚¢òr²öæöFU÷vV'Æ–W%÷F†VÖUö772‡&WVW7B’²sÂ÷7G–ÆSãÂö†VCãÆ&öG“âp¢²sÆÖ–â6Æ73Ò'6†VÆÂ#ãÆ†VFW"6Æ73Ò'F÷#ãÆF—b6Æ73Ò&'&æB#âp¢²‚‚sÆ–Ör6Æ73Ò&'&æBÖÆövò"7&3Ò"r²‡FÖÂæW66R†Æöv–åöÆövõ÷W&ÂÂV÷FSÕG'VR’²r"ÇCÒ""ÆöF–æsÒ&VvW"#âr’–bÆöv–åöÆövõ÷W&ÂVÇ6RsÇ7â6Æ73Ò&Ö&²#î)kcÂ÷7ãâr¢²sÆF—b6Æ73Ò&'&æBÖ6÷’#ãÆ#âp¢²‡FÖÂæW66R†'&æEöæÖR’²rvV"Æ–W#Âö#ãÂöF—cãÂöF—câp¢²sÆF—b6Æ73Ò&†VFW"Ö7F–öç2#âr²†VFW%÷f–WvW%ö‡FÖÂ²†VFW%ö'WGFöç5ö‡FÖÂ²sÂöF—cãÂö†VFW#âr ¢²sÇ6V7F–öâ6Æ73Ò'FööÆ&"#ãÆ–çWBG—SÒ'6V&6‚"Æ6V†öÆFW#Ò%6V&6‚6†ææVÇ>(
b"FF×6V&6ƒãÇ7â6Æ73Ò&6÷VçB#ãÇ7âFF×f—6–&ÆSâr²7G"†ÆVâ†6†ææVÇ2’’²sÂ÷7ãâ6†ææVÂ‡2“Â÷7ããÂ÷6V7F–öãâp¢²sÇ6V7F–öâ6Æ73Ò&6†ææVÂÖÆ–'&'’#ãÆæb6Æ73Ò&6FVv÷'’×7G&—"&–ÖÆ&VÃÒ$6†ææVÂ6FVv÷&–W2#âr²6†—2²sÂöæcâp¢²sÇ6V7F–öâ6Æ73Ò&w&–B#âr²rræ¦ö–â†6&G2’²sÂ÷6V7F–öããÂ÷6V7F–öããÂöÖ–ããÇ67&—Câr²ôäôDUõtT%õÄ”U%ôd”ÅDU%ô¥0¢²rò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ô„TDU%ôÄ•dUôôäÄ”äUõc##Cb¢òp¢²r‚‚“Óç¶6öç7BVÃÖFö7VÖVçBævWDVÆVÖVçD'”–B‚&†VFW"ÖöæÆ–æR×W6R"“¶–b‚VÂ—&WGW&ã¶6öç7BVæGö–çCÒr²§6öâæGV×2†b'·&Vf—‡Ò÷vV"×Æ–W"ööæÆ–æR×W6R"’²s¶ÆWB'W7“ÖfÇ6S²p¢²v7–æ2gVæ7F–öâ&Vg&W6‚‚—¶–b†'W7’—&WGW&ã¶'W7“×G'VS·G'—¶6öç7B#Öv—BfWF6‚†VæGö–çBÇ¶66†S¢&æò×7F÷&R"Æ7&VFVçF–Ç3¢'6ÖRÖ÷&–v–â"Æ†VFW'3§²$66WB#¢&Æ–6F–öâö§6öâ'×Ò“¶–b‡"æö²—¶6öç7BCÖv—B"æ§6öâ‚“¶–b„çVÖ&W"æ—4f–æ—FR„çVÖ&W"†BæöæÆ–æU÷W6R’’–VÂçFW‡D6öçFVçCÕ7G&–ær„ÖF‚æÖ‚ƒÄçVÖ&W"†BæöæÆ–æU÷W6R’’—×Ö6F6‚…ò—²Öf–æÆÇ—¶'W7“ÖfÇ6W××&Vg&W6‚‚“·6WD–çFW'fÂ‡&Vg&W6‚Ã#—Ò’‚“²p¢²sÂ÷67&—CãÆF—b7G–ÆSÒ'÷6—F–öã¦f—†VC·&–v‡C£ƒ¶&÷GFöÓ£‡ƒ¶6öÆ÷#¢3Vcsƒc¶föçC£‚7—7FVÒ×V’#çbr²‡FÖÂæW66R…dU%4”ôâ’²sÂöF—câr²öæöFU÷vV%ö†–FUö†÷fW%÷67&—B‚’²sÂö&öG“ãÂö‡FÖÃâp¢  ¤ævWB‚"÷vV"×Æ–W"ö†Ç2æÖ–âæ§2"¦FVbæöFU÷vV%÷Æ–W%ö†Ç6§2‚“ ¢""%6W'fRF†RæöFRÖÆö6Â„Å2æ§2Væv–æR6ò'&÷w6W"Æ–&6²—24DâÖ–æFWVæFVçBâ"" ¢–bæ÷BtT%Ä”U%ô„Å4¥5ôd”ÄRæ—5öf–ÆR‚’÷"tT%Ä”U%ô„Å4¥5ôd”ÄRç7FB‚’ç7E÷6—¦RÂó ¢&—6R…EEW†6WF–öâƒCB¢&WGW&âf–ÆU&W7öç6R€¢tT%Ä”U%ô„Å4¥5ôd”ÄRÀ¢ÖVF–÷G—SÒ&Æ–6F–öâö¦f67&—B"À¢†VFW'3×²$66†RÔ6öçG&öÂ#¢'V&Æ–2ÂÖ‚ÖvSÓƒcCÂ–Ö×WF&ÆR'ÒÀ¢  ¤ævWB‚"÷vV"×Æ–W"öff–6öâ"¦FVbæöFU÷vV%÷Æ–W%öff–6öâ‡&WVW7C¢&WVW7B“ ¢""%6W'fRF†R†÷7B×7V6–f–2'&æBff–6öâ÷"7–æ6‡&öæ—¦VBæöFRff–6öââ"" ¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð¢W‡FW&æÂÒ7G"†'&æBævWB‚&ff–6öå÷W&Â"’÷"""’ç7G&—‚¢–bW‡FW&æÃ ¢&WGW&â&VF—&V7E&W7öç6R†W‡FW&æÂÂ7FGW5ö6öFSÓ3"Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò¢'&æE÷F‚ÒöæöFU÷vV%ö'&æEö76WE÷F‚†'&æBÂ&ff–6öåö76WB"¢–b'&æE÷F‚—2æ÷BæöæS ¢ÖVF–Ò²"æ–6ò#¢&–ÖvR÷‚Ö–6öâ"Â"çær#¢&–ÖvR÷ær"Â"æ§r#¢&–ÖvRö§Vr"Â"æ§Vr#¢&–ÖvRö§Vr"Â"çvV'#¢&–ÖvR÷vV'"Â"æv–b#¢&–ÖvRöv–b'Ð¢&WGW&âf–ÆU&W7öç6R†'&æE÷F‚ÂÖVF–÷G—SÖÖVF–ævWB†'&æE÷F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Â†VFW'3×²$66†RÔ6öçG&öÂ#¢'V&Æ–2ÂÖ‚ÖvSÓ3c'Ò¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEô54UEô•4ôÄD”ôåõc#3 ¢–b'&æC ¢&—6R…EEW†6WF–öâƒCB¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%ôdd”4ôåõ$õUDUõc##3 ¢2¶VWF†RæVÂff–6öâVæGö–çB&÷FV7FVBv†–ÆRW‡÷6–ærF†R6ÖRÆö6À¢2ff–6öâf–ÆRg&öÒ7G&VÒ×&öÆRF‚f÷"vV"Æ–W"'&÷w6W"F'2à¢F‚ÒöæöFUöff–6öå÷F‚‚¢–bF‚—2æöæS ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–Ò°¢"æ–6ò#¢&–ÖvR÷‚Ö–6öâ"À¢"çær#¢&–ÖvR÷ær"À¢"æ§r#¢&–ÖvRö§Vr"À¢"æ§Vr#¢&–ÖvRö§Vr"À¢"çvV'#¢&–ÖvR÷vV'"À¢"æv–b#¢&–ÖvRöv–b"À¢Ð¢&WGW&âf–ÆU&W7öç6R€¢F‚À¢ÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’À¢†VFW'3×²$66†RÔ6öçG&öÂ#¢'V&Æ–2ÂÖ‚ÖvSÓ3c'ÒÀ¢  ¤ævWB‚"÷vV"×Æ–W"öÆövò"¦FVbæöFU÷vV%÷Æ–W%öÆövò‡&WVW7C¢&WVW7B“ ¢""%6W'fRF†R†÷7B×7V6–f–2'&æBÆövò÷"7–æ6‡&öæ—¦VBÆö6ÂæöFRÆövòâ"" ¢'&æBÒöæöFU÷vV'Æ–W%ö'&æB‡&WVW7B’÷"·Ð¢W‡FW&æÂÒ7G"†'&æBævWB‚&Æövõ÷W&Â"’÷"""’ç7G&—‚¢–bW‡FW&æÃ ¢&WGW&â&VF—&V7E&W7öç6R†W‡FW&æÂÂ7FGW5ö6öFSÓ3"Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò¢'&æE÷F‚ÒöæöFU÷vV%ö'&æEö76WE÷F‚†'&æBÂ&Æövõö76WB"¢–b'&æE÷F‚—2æ÷BæöæS ¢ÖVF–Ò²"çær#¢&–ÖvR÷ær"Â"æ§r#¢&–ÖvRö§Vr"Â"æ§Vr#¢&–ÖvRö§Vr"Â"çvV'#¢&–ÖvR÷vV'"Â"æv–b#¢&–ÖvRöv–b'Ð¢&WGW&âf–ÆU&W7öç6R†'&æE÷F‚ÂÖVF–÷G—SÖÖVF–ævWB†'&æE÷F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Â†VFW'3×²$66†RÔ6öçG&öÂ#¢'V&Æ–2ÂÖ‚ÖvSÓ3c'Ò¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEô54UEô•4ôÄD”ôåõc#3 ¢–b'&æC ¢&—6R…EEW†6WF–öâƒCB¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%ôÄôtõõ$õUDUõc### ¢2öæöFRÖÆöv÷2ò¢&VÆöæw2FòF†RæVÂ&öÆRÂ6ò&ö÷B÷6W&FRÆ–Æ—7Bô ¢2U$Â6â&Æö6²—BâF†—2VæGö–çBW‡÷6W2F†R6ÖRÆö6Âf–ÆR27G&VÒ×&öÆP¢26öçFVçBv—F†÷WBvV¶Væ–æræVÂô’&÷WFR—6öÆF–öâà¢ÆövòÒ7G"†ÖævW"ææöFUöÆövõ÷W&Â÷"""’ç7G&—‚¢–bæ÷BÆövòç7F'G7v—F‚‚"öæöFRÖÆöv÷2ò"“ ¢&—6R…EEW†6WF–öâƒCB¢f–ÆVæÖRÒF‚†Æövõ¶ÆVâ‚"öæöFRÖÆöv÷2ò"“¥Ò’ææÖP¢–bæ÷Bf–ÆVæÖR÷"f–ÆVæÖRÒÆövõ¶ÆVâ‚"öæöFRÖÆöv÷2ò"“¥Ó ¢&—6R…EEW†6WF–öâƒCB¢F‚ÒäôDUôÄôtõõ$ôõBòf–ÆVæÖP¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–Ò°¢"çær#¢&–ÖvR÷ær"Â"æ§r#¢&–ÖvRö§Vr"Â"æ§Vr#¢&–ÖvRö§Vr"À¢"çvV'#¢&–ÖvR÷vV'"Â"æv–b#¢&–ÖvRöv–b"À¢Ð¢&WGW&âf–ÆU&W7öç6R€¢F‚À¢ÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’À¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFRÂÖ‚ÖvSÓ'ÒÀ¢  ¢25E$TÔdõ$tUôäôDUôäE$ô”EõUDDUôÔä”dU5Eõ$õUDUõc3c ¢25E$TÔdõ$tUôäôDUõUDDUô¥4ôåôÄô4ÅõtT%Ä”U%õD„TÔUõc“ ¤ævWB‚"÷WFFRæ§6öâ"¦FVbæöFUöæG&ö–E÷WFFUöÖæ–fW7B‡&WVW7C¢&WVW7B“ ¢F‚ÂF—7Æ•öæÖRÂfW'6–öåöæÖRÂFW67&—F–öâÒöæöFU÷vV%öF÷væÆöE÷6æ6†÷B‡&WVW7B¢æG&ö–E÷&VG’Ò&ööÂ‡fW'6–öåöæÖRæBF—7Æ•öæÖRæÆ÷vW"‚’æVæG7v—F‚‚"æ²"’æBF‚—2æ÷BæöæRæBF‚æ—5öf–ÆR‚’¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B’ç'7G&—‚"ò"¢&WGW&â¥4ôå&W7öç6R€¢°¢'fW'6–öåöæÖR#¢fW'6–öåöæÖR–bæG&ö–E÷&VG’VÇ6R""À¢'WFFU÷W&Â#¢b'¶&6WÒ÷·W&ÆÆ–"ç'6RçV÷FR†F—7Æ•öæÖRÂ6fSÒrr—Ò"–bæG&ö–E÷&VG’VÇ6R""À¢&FW67&—F–öâ#¢FW67&—F–öâ–bæG&ö–E÷&VG’VÇ6R""À¢'vV'Æ–W"#¢öæöFU÷vV'Æ–W%÷WFFU÷F†VÖU÷6æ6†÷B‡&WVW7B’À¢ÒÀ¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFRÂÖ‚ÖvSÓ"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'ÒÀ¢  ¤ævWB‚"÷¶µöæÖWÒæ²"¦FVbæöFUöæG&ö–E÷WFFUö²†µöæÖS¢7G"Â&WVW7C¢&WVW7B“ ¢F‚ÂF—7Æ•öæÖRÂ÷fW'6–öåöæÖRÂöFW67&—F–öâÒöæöFU÷vV%öF÷væÆöE÷6æ6†÷B‡&WVW7B¢&WVW7FVEöæÖRÒF‚†b'¶µöæÖWÒæ²"’ææÖP¢–bæ÷BF—7Æ•öæÖRæÆ÷vW"‚’æVæG7v—F‚‚"æ²"’÷"&WVW7FVEöæÖRÒF—7Æ•öæÖR÷"F‚—2æöæR÷"æ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCBÂ$æG&ö–B²—2æ÷Bf–Æ&ÆR"¢&WGW&âf–ÆU&W7öç6R€¢F‚À¢ÖVF–÷G—SÒ&Æ–6F–öâ÷fæBææG&ö–Bç6¶vRÖ&6†—fR"À¢f–ÆVæÖSÖF—7Æ•öæÖRÀ¢†VFW'3×²$66†RÔ6öçG&öÂ#¢'V&Æ–2ÂÖ‚ÖvSÓ3"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'ÒÀ¢  ¤ævWB‚"÷vV"×Æ–W""Â&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVbæöFU÷vV%÷Æ–W"‡&WVW7C¢&WVW7BÂW'&÷#¢7G"Ò""“ ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô4ÄTåô4äôä”4Åõ$ôõEõcc5#S ¢2F†RW†7B6öæf–wW&VBÆ–Æ—7Bô&ö÷B—2&VæFW&VBF—&V7FÇ’'’66W70¢2Ö–FFÆWv&RâÆVv7’÷vV"×Æ–W"tUBF†W&Vf÷&R6æöæ–6Æ—¦W2&6²Fò—Bà¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôdÄ4…ôU%$õ%õcƒ# ¢2æWfW"6''’WF†VçF–6F–öâW'&÷'2–çFòF†Rf—6–&ÆR6æöæ–6ÂU$Âà¢–b7G"†W'&÷"÷"""’ç7G&—‚“ ¢&WGW&âöæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7BÂ7G"†W'&÷"÷"""’¢&WGW&â&VF—&V7E&W7öç6R€¢öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’À¢7FGW5ö6öFSÓ3rÀ¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â%‚Õ7G&VÔf÷&vRÕ&÷WFR#¢&æöFRÖ6ÆVâ×vV"×Æ–W"×&ö÷B'ÒÀ¢  ¤ævWB‚"÷vV"×Æ–W"öF÷væÆöB"¦FVbæöFU÷vV%÷Æ–W%öF÷væÆöB‡&WVW7C¢&WVW7B“ ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôUD„TåD”4DTEôDõtäÄôEõc3CS ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢–bæ÷BöæöFU÷vV%ö7W'&VçE÷W6W"‡&WVW7B“ ¢&WGW&âöæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7BÂ%ÆV6R6–vâ–âf—'7B"¢F‚ÂF—7Æ•öæÖRÂ÷fW'6–öåöæÖRÂöFW67&—F–öâÒöæöFU÷vV%öF÷væÆöE÷6æ6†÷B‡&WVW7B¢–bF‚—2æöæR÷"æ÷BF—7Æ•öæÖS ¢&—6R…EEW†6WF–öâƒCBÂ$F÷væÆöB—2æ÷Bf–Æ&ÆR"¢&WGW&âf–ÆU&W7öç6R€¢F‚À¢ÖVF–÷G—SÒ&Æ–6F–öâöö7FWB×7G&VÒ"À¢f–ÆVæÖSÖF—7Æ•öæÖRÀ¢†VFW'3×²$66†RÔ6öçG&öÂ#¢'&—fFRÂæò×7F÷&RÂÖ‚ÖvSÓ'ÒÀ¢  ¤ç÷7B‚"÷vV"×Æ–W"öÆöv–â"¦7–æ2FVbæöFU÷vV%÷Æ–W%öÆöv–â‡&WVW7C¢&WVW7B“ ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôÄôt”åõ$Td•…õc###S ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõtT%Ä”U%õcs ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢–böæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B•²&Æöv–åöÖöFR%ÒÓÒ&WFò# ¢&WGW&â&VF—&V7E&W7öç6R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’Â7FGW5ö6öFSÓ32¢f÷&ÒÒv—B&WVW7Bæf÷&Ò‚¢W6W&æÖRÒ7G"†f÷&ÒævWB‚'W6W&æÖR"’÷"""’ç7G&—‚¢77v÷&BÒ7G"†f÷&ÒævWB‚'77v÷&B"’÷"""¢W6W"ÒöæöFU÷‡G&VÕ÷W6W"‡W6W&æÖRÂ77v÷&B¢–bæ÷BW6W# ¢ÖævW"æÆör€¢%vV"Æ–W"Æöv–âf–ÆVB"À¢66÷SÒ&6Æ–VçB"À¢ÆWfVÃÒ'v&æ–ær"À¢FWF–Ç3Ò€¢b'W6W#×·W6W&æÖR÷"wVæ¶æ÷vâwÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó² ¢b&6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²ÖöFSÖÖçVÂ ¢’À¢W6W#×W6W&æÖR÷"$wVW7B"À¢¢&WGW&âöæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7BÂ$–çfÆ–BW6W&æÖR÷"77v÷&B"¢6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢ÖævW"æÆör€¢%vV"Æ–W"Æöv–â"À¢66÷SÒ&6Æ–VçB"À¢FWF–Ç3Ò€¢b'W6W#×·W6W"çW6W&æÖWÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó² ¢b&6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²ÖöFSÖÖçVÃ²6W76–öåö–C×·6W76–öåö–GÒ ¢’À¢W6W#×W6W"çW6W&æÖRÀ¢¢&W7öç6RÒ&VF—&V7E&W7öç6R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’Â7FGW5ö6öFSÓ32¢&W7öç6Rç6WEö6öö¶–R‚'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W""ÂöæöFU÷vV%ö6öö¶–U÷fÇVR‡W6W"Â&ÖçVÂ"’Â‡GGöæÇ“ÕG'VRÂ6ÖW6—FSÒ&Æ‚"Â6V7W&S×&WVW7BçW&Âç66†VÖRÓÒ&‡GG2"ÂÖ…övSÓC3#ÂFƒ×&Vf—‚÷""ò"¢&WGW&â&W7öç6P  ¤ç÷7B‚"÷vV"×Æ–W"÷V–6²ÖÆöv–â"¦FVbæöFU÷vV%÷Æ–W%÷V–6µöÆöv–â‡&WVW7C¢&WVW7B“ ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õT”4µôõ%ôÔåTÅôÄôt”åõc3C ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõT”4µôÄôt”åõcs ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢–böæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B•²&Æöv–åöÖöFR%ÒÒ'V–6²# ¢&WGW&â&VF—&V7E&W7öç6R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’Â7FGW5ö6öFSÓ32¢W6W"ÒöæöFU÷vV%÷6VÆV7FVE÷W6W"‡&WVW7B¢–bæ÷BW6W# ¢ÖævW"æÆör€¢%vV"Æ–W"V–6²Æöv–âVæf–Æ&ÆR"À¢66÷SÒ&6Æ–VçB"À¢ÆWfVÃÒ'v&æ–ær"À¢FWF–Ç3Öb&—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²ÖöFS×V–6²"À¢W6W#Ò$wVW7B"À¢¢&WGW&âöæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7BÂ%V–6²Æöv–âW6W"—2Væf–Æ&ÆR"¢6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢ÖævW"æÆör€¢%vV"Æ–W"V–6²Æöv–â"À¢66÷SÒ&6Æ–VçB"À¢FWF–Ç3Ò€¢b'W6W#×·W6W"çW6W&æÖWÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó² ¢b&6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²ÖöFS×V–6³²6W76–öåö–C×·6W76–öåö–GÒ ¢’À¢W6W#×W6W"çW6W&æÖRÀ¢¢&W7öç6RÒ&VF—&V7E&W7öç6R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’Â7FGW5ö6öFSÓ32¢&W7öç6Rç6WEö6öö¶–R€¢'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W""À¢öæöFU÷vV%ö6öö¶–U÷fÇVR‡W6W"Â'V–6²"’À¢‡GGöæÇ“ÕG'VRÀ¢6ÖW6—FSÒ&Æ‚"À¢6V7W&S×&WVW7BçW&Âç66†VÖRÓÒ&‡GG2"À¢Ö…övSÓC3#À¢Fƒ×&Vf—‚÷""ò"À¢¢&WGW&â&W7öç6P  ¤ævWB‚"÷vV"×Æ–W"ööæÆ–æR×W6R"¦FVbæöFU÷vV%÷Æ–W%ööæÆ–æU÷W6R‡&WVW7C¢&WVW7B“ ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôÄ•dUôôäÄ”äUõc##Cc ¢W6W"ÒöæöFU÷vV%ö7W'&VçE÷W6W"‡&WVW7B¢–bæ÷BW6W# ¢&—6R…EEW†6WF–öâƒCÂ%vV"Æ–W"6W76–öâW‡—&VB"¢&WGW&â¥4ôå&W7öç6R€¢°¢&öæÆ–æU÷W6R#¢Ö‚ƒÂ–çB†ÖævW"æ7F—fUö6öææV7F–öç5öf÷%÷W6W"‡W6W"’’’À¢&Ö…ö6öææV7F–öç2#¢Ö‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’À¢ÒÀ¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFR'ÒÀ¢  ¤ævWB‚"÷vV"×Æ–W"öÆöv÷WB"¦FVbæöFU÷vV%÷Æ–W%öÆöv÷WB‡&WVW7C¢&WVW7B“ ¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢&W7öç6RÒ&VF—&V7E&W7öç6R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’Â7FGW5ö6öFSÓ32¢&W7öç6RæFVÆWFUö6öö¶–R‚'7G&VÖf÷&vUöæöFU÷vV%÷Æ–W""ÂFƒ×&Vf—‚÷""ò"¢&WGW&â&W7öç6P  ¤ævWB‚"÷vV"×Æ–W"÷vF6‚÷¶6†ææVÅö¶W—Ò"Â&W7öç6Uö6Æ73Ô…DÔÅ&W7öç6R¦FVbæöFU÷vV%÷Æ–W%÷vF6‚†6†ææVÅö¶W“¢7G"Â&WVW7C¢&WVW7B“ ¢W6W"ÒöæöFU÷vV%ö7W'&VçE÷W6W"‡&WVW7B¢&Vf—‚ÒöæöFU÷vV%÷&Vf—‚‡&WVW7B¢–bæ÷BW6W# ¢&WGW&âöæöFU÷vV%öfÆ6…÷&VF—&V7B‡&WVW7BÂ%ÆV6R6–vâ–âf—'7B"¢6†ææVÇ2ÒööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"¢6†ææVÂÒæW‡B‚†—FVÒf÷"—FVÒ–â6†ææVÇ2–b—FVÒæ¶W’ÓÒ6†ææVÅö¶W’’ÂæöæR¢–bæ÷B6†ææVÃ ¢&—6R…EEW†6WF–öâƒCB¢6–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢Æ–&6µö¶W’Ò—77VUöæöFU÷Æ–&6µö¶W’‡W6W"Â6†ææVÂÂ&WVW7BÂ6–B¢7G&VÕö–BÒöæöFU÷7G&VÕö–B†6†ææVÂ¢7G&VÕ÷W&ÂÒb'·&Vf—‡ÒöæöFR×Æ’÷·W&ÆÆ–"ç'6RçV÷FR‡Æ–&6µö¶W’Â6fSÒrr—Ò÷·7G&VÕö–GÒöÖ7FW"æÓ7S‚ ¢6&Eö—FV×2ÒµÐ¢f÷"—FVÒ–â6†ææVÇ3 ¢æÖW2ÒöæöFUö6†ææVÅö6FVv÷&–W2†—FVÒ’÷"²%Væ6FVv÷&—¦VB%Ð¢ÆövòÒöæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†—FVÒæÆövõ÷W&ÂÂ&WVW7B¢6FVv÷&–W5öGG"Ò‡FÖÂæW66R‚'Â"æ¦ö–â†æÖW2’ÂV÷FSÕG'VR¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õ4”ätÄUõtUôu$åEõc##C ¢2F†Rw&çB—26W76–öâ×v–FR†6†ææVÅö¶W“Ò"¢"“²&WW6RF†RöæR¶W’Ç&VG¢2—77VVB&÷fR–ç7FVBöbÖ¶–æröæR&VF—2&÷VæB×G&—W"6–FV&"6&Bà¢—FVÕ÷Æ–&6µö¶W’ÒÆ–&6µö¶W¢—FVÕ÷7G&VÕö–BÒöæöFU÷7G&VÕö–B†—FVÒ¢—FVÕ÷7G&VÕ÷W&ÂÒb'·&Vf—‡ÒöæöFR×Æ’÷·W&ÆÆ–"ç'6RçV÷FR†—FVÕ÷Æ–&6µö¶W’Â6fSÒrr—Ò÷¶—FVÕ÷7G&VÕö–GÒöÖ7FW"æÓ7S‚ ¢—FVÕ÷vF6…÷W&ÂÒb'·&Vf—‡Ò÷vV"×Æ–W"÷vF6‚÷·W&ÆÆ–"ç'6RçV÷FR†—FVÒæ¶W’Â6fSÒrr—Ò ¢–bÆövó ¢F‡VÖ"ÒsÆF—b6Æ73Ò&6†ææVÂ×F‡VÖ"#ãÆ–Ör7&3Ò"r²‡FÖÂæW66R†ÆövòÂV÷FSÕG'VR’²r"ÇCÒ""ÆöF–æsÒ&Æ§’#ãÂöF—câp¢VÇ6S ¢F‡VÖ"ÒsÆF—b6Æ73Ò&6†ææVÂ×F‡VÖ"ÖfÆÆ&6²#î)kcÂöF—câp¢6&Eö—FV×2æVæB€¢sÆFFÖ6†ææVÂÖ6&Bp¢²rFFÖ6FVv÷&–W3Ò"r²6FVv÷&–W5öGG"²r"p¢²rFFÖ6†ææVÂÖæÖSÒ"r²‡FÖÂæW66R†—FVÒææÖRÂV÷FSÕG'VR’²r"p¢²rFFÖÆövò×W&ÃÒ"r²‡FÖÂæW66R†Æövò÷"""ÂV÷FSÕG'VR’²r"p¢²rFF×–÷WGV&R×6÷W&6SÒ"r²‚sr–böæöFUö6†ææVÅö—5÷–÷WGV&U÷6÷W&6R†—FVÒ’VÇ6Rsr’²r"p¢²rFF×7G&VÒ×W&ÃÒ"r²‡FÖÂæW66R†—FVÕ÷7G&VÕ÷W&ÂÂV÷FSÕG'VR’²r"p¢²rFF×vF6‚×W&ÃÒ"r²‡FÖÂæW66R†—FVÕ÷vF6…÷W&ÂÂV÷FSÕG'VR’²r"p¢²r6Æ73Ò&6†ææVÂÖ6&Br²‚r7F—fRr–b—FVÒæ¶W’ÓÒ6†ææVÂæ¶W’VÇ6Rrr’²r"p¢²r‡&VcÒ"r²‡FÖÂæW66R†—FVÕ÷vF6…÷W&ÂÂV÷FSÕG'VR’²r#âp¢²sÆF—b6Æ73Ò&6†ææVÂ×F–ÆR#âr²F‡VÖ"²sÆF—b6Æ73Ò&6†ææVÂÖæÖR#âr²‡FÖÂæW66R†—FVÒææÖR’²sÂöF—cãÂöF—cãÂöâp¢¢6&G5ö‡FÖÂÒrræ¦ö–â†6&Eö—FV×2¢7W'&VçEöÆövòÒöæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†6†ææVÂæÆövõ÷W&ÂÂ&WVW7B¢7W'&VçEöæÖW2ÒöæöFUö6†ææVÅö6FVv÷&–W2†6†ææVÂ’÷"²%Væ6FVv÷&—¦VB%Ð¢æ÷u÷f—7VÂÒ‚sÇ7â6Æ73Ò&æ÷rÖÆövò#ãÆ–Ör7&3Ò"r²‡FÖÂæW66R†7W'&VçEöÆövòÂV÷FSÕG'VR’²r"ÇCÒ""ÆöF–æsÒ&Æ§’#ãÂ÷7ãâr’–b7W'&VçEöÆövòVÇ6RsÇ7â6Æ73Ò&æ÷rÖÆövòÖfÆÆ&6²#î)kcÂ÷7ãâp¢25E$TÔdõ$tUôäôDUõtT%õÄ”U%õU4U%ôU…•%•õc### ¢Æ–W%÷W6W&æÖRÒ7G"‡W6W"çW6W&æÖR÷"W6W"ææÖR÷"""’ç7G&—‚¢Æ–W%öW‡—&W2Ò$æWfW" ¢&uöW‡—&W2Ò7G"‡W6W"æW‡—&W5öB÷"""’ç7G&—‚¢–b&uöW‡—&W3 ¢G'“ ¢'6VEöW‡—&W2ÒFFWF–ÖRæg&öÖ—6öf÷&ÖB‡&uöW‡—&W2ç&WÆ6R‚%¢"Â"³£"’¢Æ–W%öW‡—&W2Ò'6VEöW‡—&W2ç7G&gF–ÖR‚"U’ÒVÒÒVBTƒ¢TÒ"¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢Æ–W%öW‡—&W2Ò&uöW‡—&W0¢6öçG&öÇ2ÒöæöFU÷vV'Æ–W%öVffV7F—fUö6öçG&öÇ2‡&WVW7B¢†–FU÷V–6µ÷W6W%ö–æfòÒöæöFU÷vV%ö†–FU÷V–6µ÷W6W%ö–æfò‡&WVW7B¢f–WvW%ö–æfòÒ" ¢f–WvW%öw&÷W3¢Æ—7E·7G%ÒÒµÐ¢–b6öçG&öÇ5²'6†÷u÷W6W%ö–æfò%ÒæBæ÷B†–FU÷V–6µ÷W6W%ö–æfó ¢f–WvW%öw&÷W2æVæB€¢sÆF—b6Æ73Ò'f–WvW"Ö–æfòÖw&÷WW6W"#âp¢sÆF—cãÆÆ&VÃåW6W&æÖSÂöÆ&VÃãÆ#âr²‡FÖÂæW66R‡Æ–W%÷W6W&æÖR’²sÂö#ãÂöF—câp¢sÆF—cãÆÆ&VÃäW‡—&SÂöÆ&VÃãÆ"6Æ73Ò&W‡—'’#âr²‡FÖÂæW66R‡Æ–W%öW‡—&W2’²sÂö#ãÂöF—câp¢sÂöF—câp¢¢–b6öçG&öÇ5²'6†÷uö6öææV7F–öåö–æfò%ÒæBæ÷B†–FU÷V–6µ÷W6W%ö–æfó ¢25E$TÔdõ$tUôäôDUõÄ”U%ô4ôääT5D”ôåô”ädõõT•õc##3ƒ ¢f–WvW%öw&÷W2æVæB€¢sÆF—b6Æ73Ò'f–WvW"Ö–æfòÖw&÷W6öææV7F–öâ#âp¢sÆF—cãÆÆ&VÃäÖ‚6öææV7F–öç3ÂöÆ&VÃãÆ#âr²‚uVæÆ–Ö—FVBr–bÖ‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’ÓÒVÇ6R7G"†Ö‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’’’²sÂö#ãÂöF—câp¢sÆF—cãÆÆ&VÃäöæÆ–æRW6SÂöÆ&VÃãÆ"–CÒ'Æ–W"ÖöæÆ–æR×W6R#âr²7G"†Ö‚ƒÂ–çB†ÖævW"æ7F—fUö6öææV7F–öç5öf÷%÷W6W"‡W6W"’’’’²sÂö#ãÂöF—câp¢sÂöF—câp¢¢–bf–WvW%öw&÷W3 ¢f–WvW%ö–æfòÒsÆF—b6Æ73Ò'f–WvW"Ö–æfò#âr²rræ¦ö–â‡f–WvW%öw&÷W2’²sÂöF—câp¢Æ–W%öF÷væÆöEö‡FÖÂÒ€¢sÆ6Æ73Ò'Æ–W"ÖF÷væÆöBÖ'WGFöâ"‡&VcÒ"p¢²‡FÖÂæW66R‡&Vf—‚ÂV÷FSÕG'VR’²r÷vV"×Æ–W"öF÷væÆöB"F÷væÆöCäF÷væÆöCÂöâp¢–böæöFU÷vV%öF÷væÆöEöf–Æ&ÆR‡&WVW7B’VÇ6R" ¢¢Æ–W%ö–æfõö7F–öç2Ò€¢sÆF—b6Æ73Ò&æ÷rÖ7F–öç2#âr²f–WvW%ö–æfò²Æ–W%öF÷væÆöEö‡FÖÂ²sÂöF—câp¢–bf–WvW%ö–æfò÷"Æ–W%öF÷væÆöEö‡FÖÂVÇ6R" ¢¢F†VÖU÷6æ6†÷BÒöæöFU÷vV'Æ–W%÷F†VÖU÷6æ6†÷B‡&WVW7B¢vRÒ""#ÂFö7G—R‡FÖÃãÆ‡FÖÃãÆ†VCãÆÖWF6†'6WCÒ'WFbÓ‚#ãÆÖWFæÖSÒ'f–Ww÷'B"6öçFVçCÒ'v–GFƒÖFWf–6R×v–GF‚Æ–æ—F–Â×66ÆSÓ#ç¶ff–6öçÓÇF—FÆSç·F—FÆWÓÂ÷F—FÆSà£Ç7G–ÆSà¢ò¢5E$TÔdõ$tUôäôDUõtT%õÄ”U%õtD4…õ4”DT$%õc##b¢ð¢ò¢5E$TÔdõ$tUôäôDUõtT%õÄ”U%ô4”äTÔôÄ”õUEõc##r¢ð¢ò¢5E$TÔdõ$tUôäôDUõtT%õÄ”U%ôeTÄÅõt”ED…õc##‚¢ð¦&öG—·¶Ö&v–ã£¶&6¶w&÷VæC§&F–ÂÖw&F–VçB†6—&6ÆRBF÷Â3336"RÂ3sr#‚RÂ3CƒBs‚R“¶6öÆ÷#¢6cff&fc¶föçBÖfÖ–Ç“§7—7FVÒ×V—×Ð¦··FW‡BÖFV6÷&F–öã¦æöæW×Ð¢ò¢5E$TÔdõ$tUôäôDUõtD4…ô”åDU$5D•dUõô”åDU%õc3Cr¢ö¶‡&VeÒÆ·&öÆSÒ&Æ–æ²%ÒÆ'WGFöã¦æ÷Bƒ¦F—6&ÆVB’Ç7VÖÖ'“¦æ÷B…¶&–ÖF—6&ÆVCÒ'G'VR%Ò’Ç6VÆV7C¦æ÷Bƒ¦F—6&ÆVB’Æ÷F–öâÆ–çWE·G—SÒ&6†V6¶&÷‚%Ó¦æ÷Bƒ¦F—6&ÆVB’Æ–çWE·G—SÒ'&F–ò%Ó¦æ÷Bƒ¦F—6&ÆVB’Å·&öÆSÒ&'WGFöâ%Ó¦æ÷B…¶&–ÖF—6&ÆVCÒ'G'VR%Ò—·¶7W'6÷#§ö–çFW"–×÷'FçG×Ö'WGFöã¦F—6&ÆVBÇ6VÆV7C¦F—6&ÆVBÆ–çWC¦F—6&ÆVBÅ¶&–ÖF—6&ÆVCÒ'G'VR%×·¶7W'6÷#¦æ÷BÖÆÆ÷vVB–×÷'FçG×Ð¢çw&··v–GFƒ£S¶Ö‚×v–GFƒ¦æöæS¶Ö&v–ã£·FF–æs£‡‚#'‚3ƒ¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‡×Ð¢çvF6‚Öw&–G·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"’6Æ×ƒ3c‚Ã#ggrÃS#‚“¶v£#ƒ¶Æ–vâÖ—FV×3§7F'C·v–GFƒ£W×Ð¢çÆ–W"×æVÂÂæ'&÷w6W"×æVÇ·¶&6¶w&÷VæC§&v&ƒrÃ"Ã‚Âã“"“¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“ÂãB“¶&÷&FW"×&F—W3£#'ƒ¶&÷‚×6†F÷s£‡‚C‡‚&v&ƒÃÃÂã#‚—×Ð¢çÆ–W"×æVÇ··FF–æs£‡ƒ¶Ö–â×v–GFƒ£×ÒçÆ–W"×F÷·¶F—7Æ“¦fÆWƒ¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶Æ–vâÖ—FV×3¦fÆW‚×7F'C¶v£'ƒ¶Ö&v–âÖ&÷GFöÓ£G‡×ÒçÆ–W"Ö&6··¶6öÆ÷#¢3vfS6C3·FW‡BÖFV6÷&F–öã¦æöæS¶föçB×vV–v‡C£s¶föçB×6—¦S£G‡×ÒçÆ–W"×F÷ƒ·¶Ö&v–ã£g‚¶föçB×6—¦S£#'ƒ¶Æ–æRÖ†V–v‡C£ãS·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7×Ð¢çvF6‚Ög&ÖW··÷6—F–öã§&VÆF—fS¶&6¶w&÷VæC¢3¶&÷&FW"×&F—W3£#ƒ¶÷fW&fÆ÷s¦†–FFVã¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“ÂãB—××f–FV÷·¶F—7Æ“¦&Æö6³·v–GFƒ£S¶7V7B×&F–ó£bó“¶Ö–âÖ†V–v‡C¦Ö–âƒc‡f‚Ãsc‚“¶Ö‚Ö†V–v‡C£ƒ'fƒ¶ö&¦V7BÖf—C¦6öçF–ã¶&6¶w&÷VæC¢3×Ð¢ææ÷rÖ6&G·¶F—7Æ“¦fÆWƒ¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶Æ–vâÖ—FV×3¦6VçFW#¶v£gƒ¶Ö&v–â×F÷£Gƒ·FF–æs£g‚‡ƒ¶&÷&FW"×&F—W3£‡ƒ¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÇ&v&ƒÃbÃ#BÂã“‚’Ç&v&ƒbÃÃ‚Âã“‚’“¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“ÂãB—×Òææ÷rÖÖWF·¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£Gƒ¶Ö–â×v–GFƒ£×Òææ÷rÖÆövòÂææ÷rÖÆövòÖfÆÆ&6···v–GFƒ£S'ƒ¶†V–v‡C£S'ƒ¶&÷&FW"×&F—W3£gƒ¶&6¶w&÷VæC¢3#¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âãb“¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶÷fW&fÆ÷s¦†–FFVã¶fÆWƒ£S'‡×Òò¢5E$TÔdõ$tUôäôDUõÄ”U%ô”ädõôÄôtõô4ôåD”åõc##B¢òææ÷rÖÆövò–Öw·¶F—7Æ“¦&Æö6³·v–GFƒ£S¶†V–v‡C£S¶ö&¦V7BÖf—C¦6öçF–ã¶&6¶w&÷VæC¢3ƒ·FF–æs£Gƒ¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‡×Òææ÷rÖÆövòÖfÆÆ&6··¶föçB×6—¦S£#'ƒ¶6öÆ÷#¢3†#Ffc¶föçB×vV–v‡C£ƒ×Òææ÷rÖ6÷—·¶Ö–â×v–GFƒ£×Òææ÷rÖ6÷’7G&öæw·¶F—7Æ“¦&Æö6³¶föçB×6—¦S£Wƒ·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7×Òææ÷rÖ6÷’7ç·¶F—7Æ“¦&Æö6³¶Ö&v–â×F÷£7ƒ¶6öÆ÷#¢3#†SC3¶föçB×6—¦S£7ƒ¶föçB×vV–v‡C£s·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7×Òò¢5E$TÔdõ$tUôäôDUõÄ”U%ô”ädõôu$õU5õc##C¢ð¢ææ÷rÖ7F–öç7·¶Ö&v–âÖÆVgC¦WFó¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3§7G&WF6ƒ¶§W7F–g’Ö6öçFVçC¦fÆW‚ÖVæC¶v£ƒ¶Ö–â×v–GFƒ£¶fÆW‚×w&§w&×Òçf–WvW"Ö–æf÷··FW‡BÖÆ–vã§&–v‡C¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3§7G&WF6ƒ¶§W7F–g’Ö6öçFVçC¦fÆW‚ÖVæC¶v£ƒ¶Ö–â×v–GFƒ£¶fÆW‚×w&§w&×Òçf–WvW"Ö–æfòÖw&÷W·¶F—7Æ“¦w&–C¶Æ–vâÖ6öçFVçC¦6VçFW#¶v£Gƒ¶Ö–â×v–GFƒ£sƒ·FF–æs£—‚'ƒ¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âãb“¶&÷&FW"×&F—W3£7ƒ¶&6¶w&÷VæC§&v&ƒRÃ"Ã’ÂãCR—×Òçf–WvW"Ö–æfòÖw&÷Wæ6öææV7F–öç·¶Ö–â×v–GFƒ£#‡×Òçf–WvW"Ö–æfòÖw&÷WF—g·¶F—7Æ“¦fÆWƒ¶§W7F–g’Ö6öçFVçC§76RÖ&WGvVVã¶v£Gƒ¶Æ–vâÖ—FV×3¦6VçFW#¶föçB×6—¦S£'‡×Òçf–WvW"Ö–æfòÆ&VÇ·¶6öÆ÷#¢3sƒ“S¶föçB×vV–v‡C£s×Òçf–WvW"Ö–æfò'·¶6öÆ÷#¢6ccvfC¶föçB×vV–v‡C£ƒ¶Ö‚×v–GFƒ£#cƒ¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—3·v†—FR×76S¦æ÷w&×Òçf–WvW"Ö–æfòæW‡—'—·¶6öÆ÷#¢3†FS6C7×Òææ÷rÖ6÷’6ÖÆÇ·¶F—7Æ“¦&Æö6³¶Ö&v–â×F÷£Gƒ¶6öÆ÷#¢3†V†&c¶föçB×6—¦S£'ƒ·v†—FR×76S¦æ÷w&¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖ÷fW&fÆ÷s¦VÆÆ—6—7×Ð¢çÆ–W"×FööÇ7·¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦fÆW‚ÖVæC¶v£ƒ¶Ö–â×v–GFƒ£#ƒ‡×Òæ6÷’×&÷w·¶F—7Æ“¦fÆWƒ¶v£·v–GFƒ¦Ö–âƒCC‚ÃR—×Òæ6÷’×&÷r–çWG·¶fÆWƒ£¶Ö–â×v–GFƒ£·FF–æs£'‚Gƒ¶&÷&FW"×&F—W3£'‚'ƒ¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âãb“¶&÷&FW"×&–v‡C£¶&6¶w&÷VæC¢3ƒ¶6öÆ÷#¢6VFcffg×Òæ6÷’×&÷r'WGFöç··FF–æs£‡ƒ¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âãb“¶&6¶w&÷VæC¢33#c6#¶6öÆ÷#¢6VFcffc¶&÷&FW"×&F—W3£'‚'‚¶7W'6÷#§ö–çFW#·v†—FR×76S¦æ÷w&×Ð¢ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ô”ädõôDõtäÄôEõc3Cb¢òçÆ–W"ÖF÷væÆöBÖ'WGFöç·¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶Ö–âÖ†V–v‡C£CGƒ·FF–æs£#Gƒ¶&÷&FW"×&F—W3£'ƒ¶&6¶w&÷VæC¢6fc##¶6öÆ÷#¢6ffc·FW‡BÖFV6÷&F–öã¦æöæS¶föçB×vV–v‡C£ƒS¶&÷&FW#£‚6öÆ–B6fcFF¶7W'6÷#§ö–çFW#·W6W"×6VÆV7C¦æöæW×Ð¢ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô4$Eôu$”Eõ5D$ÄUõc##B¢òæ'&÷w6W"×æVÇ··FF–æs£gƒ·÷6—F–öã§7F–6·“·F÷£‡ƒ¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷ƒ¶†V–v‡C§f"‚Ò×vF6‚×æVÂÖ†V–v‡BÆWFò“¶Ö‚Ö†V–v‡C§f"‚Ò×vF6‚×æVÂÖ†V–v‡BÆæöæR“¶÷fW&fÆ÷s¦†–FFVã¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖç×Òæ'&÷w6W"Ö†VG··FW‡BÖÆ–vã¦6VçFW#·FF–æs£'‚'‡×Òæ'&÷w6W"Ö†VB7G&öæw·¶F—7Æ“¦–æÆ–æRÖ&Æö6³¶föçB×6—¦S£#wƒ¶ÆWGFW"×76–æs¢ã#VÓ¶föçB×vV–v‡C£ƒ×Òæ'&÷w6W"Ö†VC£¦gFW'·¶6öçFVçC¢"#¶F—7Æ“¦&Æö6³·v–GFƒ£s'ƒ¶†V–v‡C£7ƒ¶Ö&v–ã£‚WFò¶&÷&FW"×&F—W3£““—ƒ¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒ“FVrÂ6fcFSFRÂ6fc&S&R—×Ð¢æ6FVv÷'’Ö6†—×&÷w·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ2ÆÖ–æÖ‚ƒÃg"’“¶v£ƒ¶Ö&v–ã£‚‡‡×Òæ6FVv÷'’Ö6†—·¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âã‚“¶&6¶w&÷VæC¢3#s¶6öÆ÷#¢6VFcVfC¶&÷&FW"×&F—W3£Gƒ·FF–æs£‚'ƒ¶föçB×vV–v‡C£s¶7W'6÷#§ö–çFW#·G&ç6—F–öã¢ãW2V6S·FW‡BÖÆ–vã¦6VçFW'×Òæ6FVv÷'’Ö6†—¦†÷fW'·¶&÷&FW"Ö6öÆ÷#¢3CCSƒs#¶&6¶w&÷VæC¢3#ƒ#7×Òò¢5E$TÔdõ$tUôäôDUõtD4…õDTÕÄDUôdõ$ÔEõc###r¢òò¢5E$TÔdõ$tUôäôDUô4DTtõ%•ôtÄõuôd•…õc###¢òæ6FVv÷'’Ö6†—æ7F—fW·¶&6¶w&÷VæC¦Æ–æV"Öw&F–VçBƒƒFVrÂ6fc#S#RÂ6F#SR“¶&÷&FW"Ö6öÆ÷#¢6fcFF¶6öÆ÷#¢6ffc¶&÷‚×6†F÷s¦æöæW×Ð¢ò¢5E$TÔdõ$tUôäôDUô4„ääTÅô4$Eõ$U5ôå4•dUõc##2¢òò¢5E$TÔdõ$tUôäôDUô4„ääTÅõäTÅô„T”t…Eõc##‚¢òæ6†ææVÂÖw&–G·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒBÆÖ–æÖ‚ƒÃg"’“¶v£ƒ¶Æ–vâÖ—FV×3§7F'C¶÷fW&fÆ÷r×“¦WFó¶Ö–âÖ†V–v‡C£·FF–ær×&–v‡C£'‡×Òæ6†ææVÂÖ6&G·¶F—7Æ“¦&Æö6³¶Ö–â×v–GFƒ£·v–GFƒ¦WFó·FW‡BÖFV6÷&F–öã¦æöæS¶6öÆ÷#¢6cVffg×Òæ6†ææVÂÖ6&E¶†–FFVå×·¶F—7Æ“¦æöæR–×÷'FçG×Òæ6†ææVÂ×F–ÆW·¶F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶Ö–â×v–GFƒ£¶†V–v‡C¦WFó¶&÷&FW#£‚6öÆ–B&v&ƒ#ÃS"Ã“Âãb“¶&÷&FW"×&F—W3£gƒ¶&6¶w&÷VæC¢3#s·FF–æs£‡ƒ·G&ç6—F–öã¢ãW2V6W×Òæ6†ææVÂÖ6&C¦†÷fW"æ6†ææVÂ×F–ÆW·¶&6¶w&÷VæC¢3s##¶&÷&FW"Ö6öÆ÷#¢3CCSƒs#·G&ç6f÷&Ó§G&ç6ÆFU’‚Ó‚—×Òæ6†ææVÂÖ6&Bæ7F—fRæ6†ææVÂ×F–ÆW·¶&÷&FW"Ö6öÆ÷#¢6fcCsCs¶&÷‚×6†F÷s£‚&v&ƒ#SRÃsÃsÂã‚’ÃG‚3‚&v&ƒ#SRÃCÃCÂã—×Ð¢æ6†ææVÂ×F‡VÖ"Âæ6†ææVÂ×F‡VÖ"ÖfÆÆ&6···v–GFƒ£S¶7V7B×&F–ó£ó¶&÷&FW"×&F—W3£Gƒ¶&6¶w&÷VæC¢6cFcfcƒ¶&÷&FW#£‚6öÆ–B&v&ƒÃÃÂãR“¶F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶÷fW&fÆ÷s¦†–FFVç×Òæ6†ææVÂ×F‡VÖ"–Öw·¶F—7Æ“¦&Æö6³·v–GFƒ£S¶†V–v‡C£S¶ö&¦V7BÖf—C¦6öçF–ã¶&6¶w&÷VæC¢6ffg×Òæ6†ææVÂ×F‡VÖ"ÖfÆÆ&6··¶&6¶w&÷VæC¢3s3C¶6öÆ÷#¢3†f#vfc¶föçB×6—¦S£‡ƒ¶föçB×vV–v‡C£ƒ¶&÷&FW"Ö6öÆ÷#§&v&ƒ#ÃS"Ã“Âãb—×Òò¢5E$TÔdõ$tUôäôDUô4„ääTÅôäÔUô4TåDU%õc##b¢òæ6†ææVÂÖæÖW·¶Ö&v–â×F÷£wƒ¶föçB×6—¦S£'ƒ¶föçB×vV–v‡C£ƒ¶Æ–æRÖ†V–v‡C£ã#S¶Ö–âÖ†V–v‡C£¶÷fW&fÆ÷r×w&¦ç—v†W&S¶F—7Æ“¢×vV&¶—BÖ&÷ƒ²×vV&¶—BÖÆ–æRÖ6Æ×£#²×vV&¶—BÖ&÷‚Ö÷&–VçC§fW'F–6Ã¶÷fW&fÆ÷s¦†–FFVã·FW‡BÖÆ–vã¦6VçFW#·v–GFƒ£W×Òæ6†ææVÂÖÖWF·¶Ö&v–â×F÷£Gƒ¶föçB×6—¦S£ƒ¶6öÆ÷#¢3†V†&c¶Æ–æRÖ†V–v‡C£ã#S¶Ö–âÖ†V–v‡C£#‡ƒ¶F—7Æ“¢×vV&¶—BÖ&÷ƒ²×vV&¶—BÖÆ–æRÖ6Æ×£#²×vV&¶—BÖ&÷‚Ö÷&–VçC§fW'F–6Ã¶÷fW&fÆ÷s¦†–FFVç×Ð¢çvF6‚ÖV×G—··FF–æs£‡ƒ¶&÷&FW#£‚F6†VB&v&ƒ#ÃS"Ã“Âã‚“¶&÷&FW"×&F—W3£gƒ¶6öÆ÷#¢3†V†&c·FW‡BÖÆ–vã¦6VçFW'×Òç7FGW7·¶F—7Æ“¦–æÆ–æRÖfÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶§W7F–g’Ö6öçFVçC¦6VçFW#¶&÷&FW"×&F—W3£““—ƒ·FF–æs£w‚'ƒ¶föçB×6—¦S£'ƒ¶föçB×vV–v‡C£ƒ¶ÆWGFW"×76–æs¢ã&VÓ·FW‡B×G&ç6f÷&Ó§WW&66S·v†—FR×76S¦æ÷w&×Òç7FGW2ç7F'F–æw·¶&6¶w&÷VæC§&v&ƒ2ÃcÃ#SRÂãB“¶6öÆ÷#¢3“63VfG×Òç7FGW2æö··¶&6¶w&÷VæC§&v&ƒbÃƒRÃ#’ÂãR“¶6öÆ÷#¢3fVSv#w×Òç7FGW2æ&G·¶&6¶w&÷VæC§&v&ƒ#3’Ãc‚Ãc‚ÂãB“¶6öÆ÷#¢6f6VW×Ð¤ÖVF–†Ö‚×v–GFƒ£#C‚—··×ÔÖVF–†Ö‚×v–GFƒ£ƒ‚—·²çvF6‚Öw&–G·¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"’3C‡×××ÔÖVF–†Ö‚×v–GFƒ£“ƒ‚—·²çvF6‚Öw&–G·¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'×Òæ'&÷w6W"×æVÇ··÷6—F–öã§7FF–3¶Ö‚Ö†V–v‡C¦æöæS¶÷fW&fÆ÷s§f—6–&ÆW××f–FV÷·¶Ö–âÖ†V–v‡C£¶Ö‚Ö†V–v‡C¦æöæW×××ÔÖVF–†Ö‚×v–GFƒ£sc‚—·²çw&··FF–æs£‡×ÒçÆ–W"×æVÂÂæ'&÷w6W"×æVÇ··FF–æs£Gƒ¶&÷&FW"×&F—W3£‡‡×ÒçÆ–W"×F÷·¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶Æ–vâÖ—FV×3¦fÆW‚×7F'G×Òææ÷rÖ6&G·¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶Æ–vâÖ—FV×3§7G&WF6‡×Òææ÷rÖÖWF··v–GFƒ£W×Òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôÔô$”ÄUô”ädõôÄ”õUEõc3C’¢òææ÷rÖ7F–öç7·¶Ö&v–âÖÆVgC£¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3¦Ö–æÖ‚ƒÃg"“·v–GFƒ£S¶§W7F–g’Ö6öçFVçC§7G&WF6‡×Òçf–WvW"Ö–æf÷·¶F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’“·v–GFƒ£S·FW‡BÖÆ–vã¦ÆVgC¶v£‡ƒ¶fÆWƒ¦æöæW×Òçf–WvW"Ö–æfòÖw&÷WÂçf–WvW"Ö–æfòÖw&÷Wæ6öææV7F–öç·¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷ƒ·v–GFƒ£S¶Ö–â×v–GFƒ£¶Ö‚×v–GFƒ£W×Òçf–WvW"Ö–æfòÖw&÷WF—g·¶v£‡ƒ¶Ö–â×v–GFƒ£×Òçf–WvW"Ö–æfòÖw&÷W'·¶Ö–â×v–GFƒ£×ÒçÆ–W"ÖF÷væÆöBÖ'WGFöç·¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷ƒ·v–GFƒ£S¶Ö–âÖ†V–v‡C£CGƒ¶fÆWƒ¦æöæW×ÒçÆ–W"×FööÇ7·¶Ö–â×v–GFƒ£¶§W7F–g’Ö6öçFVçC§7G&WF6‡×Òæ6÷’×&÷w··v–GFƒ£W×Òæ6FVv÷'’Ö6†—×&÷w·¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVBƒ"ÆÖ–æÖ‚ƒÃg"’—×××ÔÖVF–†Ö‚×v–GFƒ£S#‚—·²æ'&÷w6W"Ö†VB7G&öæw·¶föçB×6—¦S£#'ƒ¶ÆWGFW"×76–æs¢ãfV××ÒçÆ–W"×F÷ƒ·¶föçB×6—¦S£#‡×××ÔÖVF–†Ö‚×v–GFƒ£3ƒ‚—·²çf–WvW"Ö–æf÷·¶w&–B×FV×ÆFRÖ6öÇVÖç3£g'×××Ð£§&ö÷G·²Ò×6b×vS§·F†VÖU÷vWÓ²Ò×6b×æVÃ§·F†VÖU÷æVÇÓ²Ò×6bÖ66VçC§·F†VÖUö66VçGÓ²Ò×6b×FW‡C§·F†VÖU÷FW‡G××Ð¢ò¢5E$TÔdõ$tUôäôDUõtD4…õD„TÔUô4õ$Uõc###’¢ð¢ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô$4´u$õTäEõ5”ä5õc##3B¢ð¦‡FÖÇ·¶&6¶w&÷VæC¢3–×÷'FçG×Ð¦&öG—·¶&6¶w&÷VæC§f"‚Ò×6b×vR’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçC¶Ö–âÖ†V–v‡C£f‡×Ð¢ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô”ädõõD„TÔUõ5”ä5õc##S"¢ð¢çÆ–W"×æVÂÂæ'&÷w6W"×æVÂÂææ÷rÖ6&BÂæ6†ææVÂ×F–ÆRÂçf–WvW"Ö–æfòÖw&÷W·¶&6¶w&÷VæC§f"‚Ò×6b×æVÂ’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçC¶&÷&FW"Ö6öÆ÷#¦6öÆ÷"ÖÖ—‚†–â7&v"Çf"‚Ò×6b×FW‡B’RRÇG&ç7&VçB’–×÷'FçG×Ð¢çÆ–W"×F÷ƒÂçÆ–W"Ö&6²Âæ'&÷w6W"Ö†VB7G&öærÂææ÷rÖ6÷’7G&öærÂææ÷rÖ6÷’7âÂæ6†ææVÂÖæÖRÂçf–WvW"Ö–æfòÂçf–WvW"Ö–æfòÆ&VÂÂçf–WvW"Ö–æfò"Âçf–WvW"Ö–æfòÖw&÷WÂçf–WvW"Ö–æfòÖw&÷WÆ&VÂÂçf–WvW"Ö–æfòÖw&÷W"ÂçvF6‚ÖV×G—·¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçG×Ð¢ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô5D•dUô4DTtõ%•ô4Ä$•E•õc##3r¢ð¢æ6FVv÷'’Ö6†—æ7F—fW·¶&6¶w&÷VæC§f"‚Ò×6bÖ66VçB’–×÷'FçC¶&÷&FW#£'‚6öÆ–Bf"‚Ò×6bÖ66VçB’–×÷'FçC¶&÷‚×6†F÷s¦–ç6WB‚7W'&VçD6öÆ÷"–×÷'FçC¶föçB×vV–v‡C£“–×÷'FçC·G&ç6f÷&Ó§G&ç6ÆFU’‚Ó‚—×Ð¢æ6FVv÷'’Ö6†—æ7F—fS£¦&Vf÷&W·¶6öçFVçC¢.)É2#¶F—7Æ“¦–æÆ–æRÖ&Æö6³¶Ö&v–â×&–v‡C£wƒ¶föçB×vV–v‡C£×Ð¢æ6†ææVÂÖ6&Bæ7F—fRæ6†ææVÂ×F–ÆRÂæ6†ææVÂÖ6&C¦†÷fW"æ6†ææVÂ×F–ÆW·¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6bÖ66VçB’–×÷'FçG×Ð¢æ'&÷w6W"Ö†VC£¦gFW'·¶&6¶w&÷VæC§f"‚Ò×6bÖ66VçB’–×÷'FçG×Ð¢çÆ–W"ÖF÷væÆöBÖ'WGFöç·¶&6¶w&÷VæC§f"‚Ò×6bÖ66VçB’–×÷'FçC¶&÷&FW"Ö6öÆ÷#§f"‚Ò×6bÖ66VçB’–×÷'FçC¶6öÆ÷#§f"‚Ò×6b×FW‡B’–×÷'FçG×Ð£Â÷7G–ÆSãÂö†VCãÆ&öG“à£ÆÖ–â6Æ73Ò'w&#ãÇ6V7F–öâ6Æ73Ò'vF6‚Öw&–B#ãÇ6V7F–öâ6Æ73Ò'Æ–W"×æVÂ#ãÆF—b6Æ73Ò'Æ–W"×F÷#ãÆF—cãÆ6Æ73Ò'Æ–W"Ö&6²"‡&VcÒ'¶6æöæ–6Å÷Æ–W%÷W&ÇÒ#î(iÆÂ6†ææVÇ3ÂöãÆƒ–CÒ&7W'&VçBÖ6†ææVÂ×F—FÆR#ç·F—FÆWÓÂöƒãÂöF—cãÇ7â–CÒ'7FFR"6Æ73Ò'7FGW27F'F–ær#æÆöF–æsÂ÷7ããÂöF—cãÆF—b6Æ73Ò'vF6‚Ög&ÖR#ãÇf–FVò–CÒ'f–FVò"6öçG&öÇ2WF÷Æ’Æ—6–æÆ–æW·÷7FW%öGG'ÓãÂ÷f–FVóãÂöF—cãÆF—b6Æ73Ò&æ÷rÖ6&B#ãÆF—b6Æ73Ò&æ÷rÖÖWF"–CÒ&7W'&VçBÖæ÷rÖÖWF#ç¶æ÷u÷f—7VÇÓÆF—b6Æ73Ò&æ÷rÖ6÷’#ãÇ7G&öær–CÒ&7W'&VçBÖæ÷rÖæÖR#ç·F—FÆWÓÂ÷7G&öæsãÇ7ãäæ÷r7G&VÖ–æsÂ÷7ããÂöF—cãÂöF—cç·Æ–W%ö–æfõö7F–öç7ÓÂöF—cãÂ÷6V7F–öããÆ6–FR6Æ73Ò&'&÷w6W"×æVÂ#ãÆF—b6Æ73Ò&'&÷w6W"Ö†VB#ãÇ7G&öæsäÄ•dR5E$TÕ3Â÷7G&öæsãÂöF—cãÆF—b–CÒ&6FVv÷'’Ö6†—2"6Æ73Ò&6FVv÷'’Ö6†—×&÷r#ãÂöF—cãÆF—b–CÒ&6†ææVÂÖw&–B"6Æ73Ò&6†ææVÂÖw&–B#ç¶6&G5ö‡FÖÇÓÂöF—cãÆF—b–CÒ&6†ææVÂÖV×G’"6Æ73Ò'vF6‚ÖV×G’"†–FFVãäæò6†ææVÇ2–âF†—26FVv÷'’ãÂöF—cãÂö6–FSãÂ÷6V7F–öããÂöÖ–ãà£Ç67&—Câ‚‚“Óç·²ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô4äôä”4ÅõtT%Ä”U%õU$Åõc##C’¢ö6öç7B6æöæ–6ÅÆ–W%W&Ã×¶6æöæ–6Å÷Æ–W%÷W&Åö§6öçÓ¶–b‡v–æF÷ræÆö6F–öâçF†æÖRÓÖ6æöæ–6ÅÆ–W%W&Â—·¶†—7F÷'’ç&WÆ6U7FFR†çVÆÂÂrrÆ6æöæ–6ÅÆ–W%W&Â“·×Ö6öç7Bf–FVóÖFö7VÖVçBævWDVÆVÖVçD'”–B‚'f–FVò"’Ç7FFSÖFö7VÖVçBævWDVÆVÖVçD'”–B‚'7FFR"“¶ÆWBW&Ã×·7G&VÕ÷W&Åö§6öçÒÇ–÷WGV&TÆ÷tÆFVæ7“×·–÷WGV&UöÆ÷uöÆFVæ7•ö§6öçÒÆƒÖçVÆÂÆfÆÆ&6³ÖfÇ6RÆfFÃÓÆÆ7E&öw&W74CÔFFRææ÷r‚’ÆÆ7DÖVF–F–ÖSÓÆÆ7D†Ç4FFCÔFFRææ÷r‚’ÆÆ7E6ögE&V6÷fW'”CÓÆVæv–æTÆöF–æsÖfÇ6RÆVæv–æU&WG'“ÖçVÆÂÇ7v—F6„vVæW&F–öãÓ²ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õ$ôu$U55õtD4„Dôuõcc2¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôUd”DTä4Uô$4TEõ5DÄÅõ$T4õdU%•õccr¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôäôåôDU5E%T5D•dUõ5DÄÅõ$T4õdU%•õccr¢ögVæ7F–öâæ÷FU&öw&W72†f÷&6SÖfÇ6R—·¶6öç7BCÔçVÖ&W"‡f–FVòæ7W'&VçEF–ÖWÇÃ“¶–b†f÷&6WÇÄÖF‚æ'2‡BÖÆ7DÖVF–F–ÖR“ãÓãR—·¶Æ7DÖVF–F–ÖS×C¶Æ7E&öw&W74CÔFFRææ÷r‚“¶Æ7D†Ç4FFCÔFFRææ÷r‚—×××ÖgVæ7F–öâæ÷FT†Ç4FF‚—·¶Æ7D†Ç4FFCÔFFRææ÷r‚—×ÖgVæ7F–öâ'VffW&VD†VB‚—·¶6öç7BCÔçVÖ&W"‡f–FVòæ7W'&VçEF–ÖWÇÃ“·G'—·¶f÷"†ÆWB“Ó¶“Çf–FVòæ'VffW&VBæÆVæwFƒ¶’³Ó—·¶6öç7B×f–FVòæ'VffW&VBç7F'B†’’Æ#×f–FVòæ'VffW&VBæVæB†’“¶–b‡B³ããÖbgCÃÖ"³ã—&WGW&âÖF‚æÖ‚ƒÆ"×B—×××Ö6F6‚…ò—··××&WGW&â×ÖgVæ7F–öâ6ögE&V6÷fW"†ÖW76vSÒt'VffW&–ærÆ—fR7G&VÞ(
br—·¶–b‡f–FVòçW6VGÇÆFö7VÖVçBæ†–FFVâ—&WGW&ã¶6öç7Bæ÷sÔFFRææ÷r‚“¶–b†æ÷rÖÆ7E6ögE&V6÷fW'”CÃƒ—&WGW&ã¶Æ7E6ögE&V6÷fW'”CÖæ÷s·6WE7FFR‚v'VffW&–ærrÂw7F'F–ærr“·6WD×6r†ÖW76vR“¶–b†‚—··G'—·¶‚ç7F'DÆöB‚Ó—×Ö6F6‚…ò—··××××Æ”WF÷Æ•6fR‚’æ6F6‚‚‚“Óç··×Ò—×Òò¢5E$TÔdõ$tUôäôDUõtT%õÄ”U%ôäõõd”DTõôõdU$Ä•õc##’¢ð¢ò¢5E$TÔdõ$tUôäôDUõtT%õÄ”U%ô4ÄTåô”ädõõc##¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õ5D$ÄUôTDtUô%TddU%õcsr¢òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôDTdTÅEôTD”õó3õc"¢÷f–FVòæFVfVÇD×WFVCÖfÇ6S·f–FVòæ×WFVCÖfÇ6S·f–FVòçföÇVÖSÓã3²ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ôUDõÄ•ôÕUDTEôdÄÄ$4µõc¢öÆWBWF÷Æ”×WFVDfÆÆ&6³ÖfÇ6RÆWF÷Æ•&W7F÷&T&ÖVCÖfÇ6S¶gVæ7F–öâ&ÔWF÷Æ”VF–õ&W7F÷&R‚—·¶–b†WF÷Æ•&W7F÷&T&ÖVB—&WGW&ã¶WF÷Æ•&W7F÷&T&ÖVC×G'VS¶6öç7B&W7F÷&SÒ‚“Óç·¶–b‚WF÷Æ”×WFVDfÆÆ&6²—&WGW&ã¶WF÷Æ”×WFVDfÆÆ&6³ÖfÇ6S·f–FVòæFVfVÇD×WFVCÖfÇ6S·f–FVòæ×WFVCÖfÇ6S¶–b‚çVÖ&W"æ—4f–æ—FR‡f–FVòçföÇVÖR—ÇÇf–FVòçföÇVÖSÃÓ—f–FVòçföÇVÖSÓã3·f–FVòçÆ’‚’æ6F6‚‚‚“Óç··×Ò—×Ó·v–æF÷ræFDWfVçDÆ—7FVæW"‚wö–çFW&F÷vârÇ&W7F÷&RÇ·¶öæ6S§G'VRÆ6GW&S§G'VW×Ò“·v–æF÷ræFDWfVçDÆ—7FVæW"‚v¶W–F÷vârÇ&W7F÷&RÇ·¶öæ6S§G'VRÆ6GW&S§G'VW×Ò“·v–æF÷ræFDWfVçDÆ—7FVæW"‚wF÷V6‡7F'BrÇ&W7F÷&RÇ·¶öæ6S§G'VRÆ6GW&S§G'VRÇ76—fS§G'VW×Ò—×Ö7–æ2gVæ7F–öâÆ”WF÷Æ•6fR‚—··G'—·¶v—Bf–FVòçÆ’‚“·&WGW&âG'VW×Ö6F6‚†W'&÷"—·¶–b†W'&÷#òææÖRÓÒtæ÷DÆÆ÷vVDW'&÷"r—F‡&÷rW'&÷#·f–FVòæFVfVÇD×WFVC×G'VS·f–FVòæ×WFVC×G'VS·G'—·¶v—Bf–FVòçÆ’‚“¶WF÷Æ”×WFVDfÆÆ&6³×G'VS¶&ÔWF÷Æ”VF–õ&W7F÷&R‚“·&WGW&âG'VW×Ö6F6‚…ò—··&WGW&âfÇ6W×××××Ö6öç7B6WE7FFSÒ‡BÆ2“Óç··7FFRçFW‡D6öçFVçC×C·7FFRæ6Æ74æÖSÒw7FGW2r¶7×ÒÇ6WD×6s×CÓç··7FFRçF—FÆS×GÇÂrw×Ó²ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õ”õUET$UôTDtUõcr¢òò¢5E$TÔdõ$tUôäôDUõ”õUET$Uô´U”e$ÔUõ4dUõÄ”U%õc#¢òò¢5E$TÔdõ$tUôäôDUõ”õUET$Uõ4TEõÄ”U%ô%TddU%õc#¢ögVæ7F–öâ6fr‡7F&ÆSÖfÇ6R—·¶–b‡7F&ÆR—&WGW&â·¶Væ&ÆUv÷&¶W#§G'VRÆÆ÷tÆFVæ7”ÖöFS¦fÇ6RÆÆ—fU7–æ4GW&F–öä6÷VçC£"ÆÆ—fTÖ„ÆFVæ7”GW&F–öä6÷VçC£bÆ&6´'VffW$ÆVæwFƒ£#ÆÖ„'VffW$ÆVæwFƒ£RÆÖ„Ö„'VffW$ÆVæwFƒ£#RÆÖæ–fW7DÆöF–æuF–ÖT÷WC£ÆÖæ–fW7DÆöF–ætÖ…&WG'“£2ÆÆWfVÄÆöF–æuF–ÖT÷WC£ÆÆWfVÄÆöF–ætÖ…&WG'“£2Æg&tÆöF–æuF–ÖT÷WC£#Æg&tÆöF–ætÖ…&WG'“£W×Ó¶–b‡–÷WGV&TÆ÷tÆFVæ7’—&WGW&â·¶Væ&ÆUv÷&¶W#§G'VRÆÆ÷tÆFVæ7”ÖöFS¦fÇ6RÆÆ—fU7–æ4GW&F–öä6÷VçC£"ÆÆ—fTÖ„ÆFVæ7”GW&F–öä6÷VçC£RÆÖ„Æ—fU7–æ5Æ–&6µ&FS£ã‚Ç7F'Dg&u&VfWF6ƒ§G'VRÆ&6´'VffW$ÆVæwFƒ£#ÆÖ„'VffW$ÆVæwFƒ£RÆÖ„Ö„'VffW$ÆVæwFƒ£#RÆÖæ–fW7DÆöF–æuF–ÖT÷WC£#ÆÖæ–fW7DÆöF–ætÖ…&WG'“£RÆÆWfVÄÆöF–æuF–ÖT÷WC£#ÆÆWfVÄÆöF–ætÖ…&WG'“£RÆg&tÆöF–æuF–ÖT÷WC£#SÆg&tÆöF–ætÖ…&WG'“£‡×Ó²ò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õ4dUõ$TdUD4…õ5D%Eõc#b¢÷&WGW&â·¶Væ&ÆUv÷&¶W#§G'VRÆÆ÷tÆFVæ7”ÖöFS¦fÇ6RÆÆ—fU7–æ4GW&F–öä6÷VçC£"ÆÆ—fTÖ„ÆFVæ7”GW&F–öä6÷VçC£RÇ7F'Dg&u&VfWF6ƒ§G'VRÆ&6´'VffW$ÆVæwFƒ£#ÆÖ„'VffW$ÆVæwFƒ£"ÆÖ„Ö„'VffW$ÆVæwFƒ£#ÆÖæ–fW7DÆöF–æuF–ÖT÷WC£#ÆÖæ–fW7DÆöF–ætÖ…&WG'“£BÆÆWfVÄÆöF–æuF–ÖT÷WC£#ÆÆWfVÄÆöF–ætÖ…&WG'“£BÆg&tÆöF–æuF–ÖT÷WC£#Æg&tÆöF–ætÖ…&WG'“£g×××Òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%õ5t•D4…ôtTäU$D”ôåõc#r¢ð¦gVæ7F–öâ¶–ÆÂ‚—··7v—F6„vVæW&F–öâ³Ó¶6öç7B&Wf–÷W3Öƒ¶ƒÖçVÆÃ¶–b‡&Wf–÷W2—··G'—··&Wf–÷W2æFW7G&÷’‚—×Ö6F6‚…ò—··×××××Ð¦gVæ7F–öâæF—fU7F'B‚—·¶¶–ÆÂ‚“¶6öç7B'Vã×7v—F6„vVæW&F–öâÆ7W'&VçEW&Ã×W&Ã·f–FVòç7&3Ö7W'&VçEW&Ã·f–FVòæÆöB‚“·f–FVòæFDWfVçDÆ—7FVæW"‚vÆöFVFFFrÂ‚“Óç·¶–b‡'VãÓÓ×7v—F6„vVæW&F–öâ—6WD×6r‚rr—×ÒÇ·¶öæ6S§G'VW×Ò“·Æ”WF÷Æ•6fR‚’çF†Vâ‡7F'FVCÓç·¶–b‡'VâÓ×7v—F6„vVæW&F–öâ—&WGW&ã¶–b‡7F'FVB—··6WE7FFR‚vÆ—fRrÂvö²r“·6WD×6r‚rr—×ÖVÇ6W··6WE7FFR‚w&VG’rÂw7F'F–ærr“·6WD×6r‚uFÆ’Fò7F'BF†R7G&VÒâr—×××Ò’æ6F6‚‚‚“Óç·¶–b‡'VâÓ×7v—F6„vVæW&F–öâ—&WGW&ã·6WE7FFR‚w&VG’rÂw7F'F–ærr“·6WD×6r‚uFÆ’Fò7F'BF†R7G&VÒâr—×Ò—×Ð¦gVæ7F–öâÆöB‡7F&ÆSÖfÇ6R—·¶¶–ÆÂ‚“¶6öç7B'Vã×7v—F6„vVæW&F–öâÆ7W'&VçEW&Ã×W&ÂÆ–ç7Fæ6SÖæWr†Ç2†6fr‡7F&ÆR’’ÆÆ—fSÒ‚“Óç'VãÓÓ×7v—F6„vVæW&F–öâbfƒÓÓÖ–ç7Fæ6S¶ƒÖ–ç7Fæ6S·6WE7FFR‚v6öææV7F–ærrÂw7F'F–ærr“·6WD×6r‡7F&ÆSòu&V6öææV7F–ær–â7F&ÆRÖöF^(
bs¢u&W&–ærÆ—fR7G&VÞ(
br“¶–ç7Fæ6RæGF6„ÖVF–‡f–FVò“¶–ç7Fæ6Ræöâ„†Ç2äWfVçG2äÔTD”ôED4„TBÂ‚“Óç·¶–b†Æ—fR‚’––ç7Fæ6RæÆöE6÷W&6R†7W'&VçEW&Â—×Ò“¶–ç7Fæ6Ræöâ„†Ç2äWfVçG2äe$uôÄôDTBÂ‚“Óç·¶–b‚Æ—fR‚’—&WGW&ã¶æ÷FT†Ç4FF‚“¶fFÃÓ×Ò“¶–ç7Fæ6Ræöâ„†Ç2äWfVçG2äÄUdTÅõUDDTBÂ‚“Óç·¶–b†Æ—fR‚’–æ÷FT†Ç4FF‚—×Ò“¶–ç7Fæ6Ræöâ„†Ç2äWfVçG2äÔä”dU5Eõ%4TBÂ‚“Óç·¶–b‚Æ—fR‚’—&WGW&ã¶æ÷FT†Ç4FF‚“·Æ”WF÷Æ•6fR‚’çF†Vâ‡7F'FVCÓç·¶–b‚Æ—fR‚’—&WGW&ã¶–b‡7F'FVB—··6WE7FFR‚vÆ—fRrÂvö²r“·6WD×6r‚rr—×ÖVÇ6W··6WE7FFR‚w&VG’rÂw7F'F–ærr“·6WD×6r‚uFÆ’Fò7F'BF†R7G&VÒâr—×××Ò’æ6F6‚‚‚“Óç·¶–b‚Æ—fR‚’—&WGW&ã·6WE7FFR‚w&VG’rÂw7F'F–ærr“·6WD×6r‚uFÆ’Fò7F'BF†R7G&VÒâr—×Ò—×Ò“¶–ç7Fæ6Ræöâ„†Ç2äWfVçG2äU%$õ"Â…öRÆFF“Óç·¶–b‚Æ—fR‚—ÇÂFFòæfFÂ—&WGW&ã¶fFÂ³Ó¶–b†FFçG—SÓÓÔ†Ç2äW'&÷%G—W2ääUEtõ$µôU%$õ"bffFÃÃÓb—·¶6öç7B6öFSÔçVÖ&W"†FFòç&W7öç6Sòæ6öFWÇÃ“·6WE7FFR‚v'VffW&–ærrÂw7F'F–ærr“·6WD×6r†6öFSö…EEG·¶6öFW×Ó²&W7VÖ–ærF†RÆ—fR7G&VÞ(
f¢tæWGv÷&²†–67WFWFV7FVBâ&W7VÖ–ærF†RÆ—fR7G&VÞ(
br“·G'—·¶–b†6öFSÓÓÓCB––ç7Fæ6Rç7F÷ÆöB‚“¶–ç7Fæ6Rç7F'DÆöB‚Ó—×Ö6F6‚…ò—··6ögE&V6÷fW"‚tæWGv÷&²&V6÷fW'’—2–â&öw&W7>(
br—×Ó·&WGW&ç×Ö–b†FFçG—SÓÓÔ†Ç2äW'&÷%G—W2äÔTD”ôU%$õ"bffFÃÃÓB—··6WE7FFR‚w&V6÷fW&–ærrÂw7F'F–ærr“·6WD×6r‚u&V6÷fW&–ærF†RÆ—fRÖVF–—VÆ–æ^(
br“·G'—·¶–ç7Fæ6Rç&V6÷fW$ÖVF–W'&÷"‚—×Ö6F6‚…ò—··6ögE&V6÷fW"‚tÖVF–&V6÷fW'’—2–â&öw&W7>(
br—×Ó·&WGW&ç×Ö–b†FFòç&W7öç6Sòæ6öFR—··6WD×6r†…EEG·¶FFç&W7öç6Ræ6öFW×Ö—×Ö–b‚fÆÆ&6²—·¶fÆÆ&6³×G'VS·6WD×6r‚u7v—F6†–ærFòF†R7F&ÆRÆ—fR'VffW.(
br“¶ÆöB‡G'VR“·&WGW&ç××&WG'•6ööâ‚u&WVFVBfFÂ„Å2W'&÷'2FWFV7FVBâ&V6öææV7F–ærWFöÖF–6ÆÇž(
br—×Ò—×Òò¢5E$TÔdõ$tUôäôDUõtT%Ä”U%ô„Å4¥5ôÄô4Åô4DåôdÄÄ$4µõcc2¢ögVæ7F–öâVç7W&TVæv–æR‚—·¶–b‡f–FVòæ6åÆ•G—R‚vÆ–6F–öâ÷fæBæÆRæ×VwW&Âr’—·¶æF—fU7F'B‚“·&WGW&ç×Ö–b‡v–æF÷rä†Ç2bd†Ç2æ—57W÷'FVB‚’—·¶ÆöB†fÆÆ&6²“·&WGW&ç×Ö–b†Væv–æTÆöF–ær—&WGW&ã¶Væv–æTÆöF–æs×G'VS¶–b†Væv–æU&WG'’ÓÖçVÆÂ—·¶6ÆV%F–ÖV÷WB†Væv–æU&WG'’“¶Væv–æU&WG'“ÖçVÆÇ×Ö6öç7B6÷W&6W3Õ·¶†Ç5ö§5÷W&Åö§6öçÒÂv‡GG3¢òö6Fâæ§6FVÆ—g"ææWBöçÒö†Ç2æ§4ãrãöF—7Bö†Ç2æÖ–âæ§2rÂv‡GG3¢ò÷Vç¶ræ6öÒö†Ç2æ§4ãrãöF—7Bö†Ç2æÖ–âæ§2uÓ¶6öç7BGFV×CÖ“Óç·¶–b‡v–æF÷rä†Ç2bd†Ç2æ—57W÷'FVB‚’—·¶Væv–æTÆöF–æsÖfÇ6S¶ÆöB†fÆÆ&6²“·&WGW&ç×Ö–b†“ã×6÷W&6W2æÆVæwF‚—·¶Væv–æTÆöF–æsÖfÇ6S·6WE7FFR‚w&V6öææV7F–ærrÂw7F'F–ærr“·6WD×6r‚uÆ–W"Væv–æR—2FV×÷&&–Ç’Væf–Æ&ÆRâ&WG'––ærWFöÖF–6ÆÇž(
br“¶Væv–æU&WG'“×6WEF–ÖV÷WB‚‚“Óç·¶Væv–æU&WG'“ÖçVÆÃ¶Vç7W&TVæv–æR‚—×ÒÃ3“·&WGW&ç×Ö6öç7B63ÖFö7VÖVçBæ7&VFTVÆVÖVçB‚w67&—Br“·62ç7&3×6÷W&6W5¶•Ó·62æ7–æ3×G'VS·62æöæÆöCÒ‚“Óç·¶–b‡v–æF÷rä†Ç2bd†Ç2æ—57W÷'FVB‚’—·¶Væv–æTÆöF–æsÖfÇ6S¶ÆöB†fÆÆ&6²—×ÖVÇ6W··62ç&VÖ÷fR‚“¶GFV×B†’³—×××Ó·62æöæW'&÷#Ò‚“Óç··62ç&VÖ÷fR‚“¶GFV×B†’³—×Ó¶Fö7VÖVçBæ†VBæVæD6†–ÆB‡62—×Ó¶GFV×Bƒ—×ÖgVæ7F–öâ7F'B‚—·¶–b‡f–FVòæ6åÆ•G—R‚vÆ–6F–öâ÷fæBæÆRæ×VwW&Âr’—·¶æF—fU7F'B‚“·&WGW&ç×Ö–b‡v–æF÷rä†Ç2bd†Ç2æ—57W÷'FVB‚’—·¶ÆöB†fÇ6R“·&WGW&ç×ÖVç7W&TVæv–æR‚—×Òò¢5E$TÔdõ$tUõtT%Ä”U%õU%4•5DTåEôUDõõ$T4ôääT5Eõc3SR¢öÆWBWFõ&WG'“ÖçVÆÂÇ7FÆÆVE&WG'“ÖçVÆÃ¶gVæ7F–öâ6æ6VÅ&WG'’‚—·¶–b†WFõ&WG'’ÓÖçVÆÂ—·¶6ÆV%F–ÖV÷WB†WFõ&WG'’“¶WFõ&WG'“ÖçVÆÇ×Ö–b‡7FÆÆVE&WG'’ÓÖçVÆÂ—·¶6ÆV%F–ÖV÷WB‡7FÆÆVE&WG'’“·7FÆÆVE&WG'“ÖçVÆÇ×××ÖgVæ7F–öâ&WG'•6ööâ†ÖW76vSÒtÆ—fR7G&VÒ—2öffÆ–æRâWFöÖF–2&V6öææV7B—27F—fRâr—·¶–b†WFõ&WG'’ÓÖçVÆÂ—&WGW&ã¶¶–ÆÂ‚“·6WE7FFR‚w&V6öææV7F–ærrÂw7F'F–ærr“·6WD×6r†ÖW76vR“¶WFõ&WG'“×6WEF–ÖV÷WB‚‚“Óç·¶WFõ&WG'“ÖçVÆÃ¶fÆÆ&6³ÖfÇ6S¶fFÃÓ·7F'B‚—×ÒÃ3—××f–FVòæFDWfVçDÆ—7FVæW"‚wÆ––ærrÂ‚“Óç·¶æ÷FU&öw&W72‡G'VR“¶6æ6VÅ&WG'’‚“¶fFÃÓ¶fÆÆ&6³ÖfÇ6S·6WE7FFR‚vÆ—fRrÂvö²r“·6WD×6r‚rr—×Ò“·f–FVòæFDWfVçDÆ—7FVæW"‚wF–ÖWWFFRrÂ‚“Óææ÷FU&öw&W72†fÇ6R’“·f–FVòæFDWfVçDÆ—7FVæW"‚w&öw&W72rÂ‚“Óç·¶–b†'VffW&VD†VB‚“ãã#R–æ÷FT†Ç4FF‚—×Ò“·f–FVòæFDWfVçDÆ—7FVæW"‚vW'&÷"rÂ‚“Óç·¶–b†‚—6ögE&V6÷fW"‚uÆ–&6²—VÆ–æRW6VBâ&W7VÖ–ærv—F†÷WB&V6öææV7F–æ~(
br“¶VÇ6R&WG'•6ööâ‚uÆ–&6²7F÷VBâv—F–ærf÷"F†R6†ææVÂFò&WGW&î(
br—×Ò“·f–FVòæFDWfVçDÆ—7FVæW"‚wv—F–ærrÂ‚“Óç·¶–b‚f–FVòçW6VB–æ÷FU&öw&W72†fÇ6R—×Ò“·f–FVòæFDWfVçDÆ—7FVæW"‚w7FÆÆVBrÂ‚“Óç·¶–b‡7FÆÆVE&WG'’ÓÖçVÆÂ–6ÆV%F–ÖV÷WB‡7FÆÆVE&WG'’“¶–b‡f–FVòçW6VB—&WGW&ã·7FÆÆVE&WG'“×6WEF–ÖV÷WB‚‚“Óç··7FÆÆVE&WG'“ÖçVÆÃ¶6öç7Bæ÷sÔFFRææ÷r‚’ÆÖVF–V–WCÖæ÷rÖÆ7E&öw&W74CãÓSÆ†Ç5V–WCÖæ÷rÖÆ7D†Ç4FFCãÓ#¶–b‚f–FVòçW6VBbfÖVF–V–WBbf†Ç5V–WBbf'VffW&VD†VB‚“Ãã#Rbgf–FVòç&VG•7FFSÄ…DÔÄÖVF–VÆVÖVçBä„dUôeUEU$UôDD—6ögE&V6÷fW"‚uv—F–ærf÷"g&W6‚„Å2FF²¶VW–ærF†R7W'&VçBÆ–W"6W76–öî(
br—×ÒÃ#—×Ò“·6WD–çFW'fÂ‚‚“Óç·¶–b…7G&–ær‡7FFRçFW‡D6öçFVçGÇÂrr’çFôÆ÷vW$66R‚“ÓÓÒvW'&÷"r—&WG'•6ööâ‚“¶–b†Fö7VÖVçBæ†–FFVçÇÇf–FVòçW6VGÇÇf–FVòç6VV¶–æwÇÇf–FVòæVæFVB—&WGW&ã¶æ÷FU&öw&W72†fÇ6R“¶6öç7Bæ÷sÔFFRææ÷r‚’ÆÖVF–V–WCÖæ÷rÖÆ7E&öw&W74CãÓƒÆ†Ç5V–WCÖæ÷rÖÆ7D†Ç4FFCãÓ#¶–b†ÖVF–V–WBbf†Ç5V–WBbf'VffW&VD†VB‚“Ãã#RbfWFõ&WG'“ÓÓÖçVÆÂ—6ögE&V6÷fW"‚uÆ–&6²W6VBBF†RÆ—fRVFvS²&W7VÖ–ærv—F†÷WB&V'V–ÆF–ærF†RÆ–W.(
br—×ÒÃ#S“¶Fö7VÖVçBæFDWfVçDÆ—7FVæW"‚wf—6–&–Æ—G–6†ævRrÂ‚“Óç·¶–b‚Fö7VÖVçBæ†–FFVâ–æ÷FU&öw&W72‡G'VR—×Ò“¶6öç7B6&G3Ô'&’æg&öÒ†Fö7VÖVçBçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖ6†ææVÂÖ6&EÒr’’Æ6†ææVÅF—FÆSÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v7W'&VçBÖ6†ææVÂ×F—FÆRr’Ææ÷tæÖSÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v7W'&VçBÖæ÷rÖæÖRr’Ææ÷tÖWFÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v7W'&VçBÖæ÷rÖÖWFr“°¢ò¢5E$TÔdõ$tUôäôDUõÄ”U%ô”åtUô4„ääTÅõ5t•D4…õc##C¢ð¦gVæ7F–öâWFFTæ÷tÆövò†ÆövõW&Â—·¶–b‚æ÷tÖWF—&WGW&ã¶6öç7BöÆDÆövóÖæ÷tÖWFçVW'•6VÆV7F÷"‚rææ÷rÖÆövòÂææ÷rÖÆövòÖfÆÆ&6²r“¶–b‚öÆDÆövò—&WGW&ã¶–b†ÆövõW&Â—·¶6öç7B7ãÖFö7VÖVçBæ7&VFTVÆVÖVçB‚w7âr“·7âæ6Æ74æÖSÒvæ÷rÖÆövòs¶6öç7B–ÖsÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v–Örr“¶–Örç7&3ÖÆövõW&Ã¶–ÖræÇCÒrs¶–ÖræÆöF–æsÒvÆ§’s·7âæVæD6†–ÆB†–Ör“¶öÆDÆövòç&WÆ6Uv—F‚‡7â“·f–FVòç÷7FW#ÖÆövõW&Ç×ÖVÇ6W·¶6öç7B7ãÖFö7VÖVçBæ7&VFTVÆVÖVçB‚w7âr“·7âæ6Æ74æÖSÒvæ÷rÖÆövòÖfÆÆ&6²s·7âçFW‡D6öçFVçCÒ~)kbs¶öÆDÆövòç&WÆ6Uv—F‚‡7â“·f–FVòç&VÖ÷fTGG&–'WFR‚w÷7FW"r—×××Ð¦gVæ7F–öâ7v—F6„6†ææVÂ†6&B—·¶6öç7BæW‡CÖ6&BæFF6WBç7G&VÕW&ÇÇÂrs¶–b‚æW‡GÇÆ6&Bæ6Æ74Æ—7Bæ6öçF–ç2‚v7F—fRr’—&WGW&ã·W&ÃÖæW‡C·–÷WGV&TÆ÷tÆFVæ7“Ö6&BæFF6WBç–÷WGV&U6÷W&6SÓÓÒss¶fÆÆ&6³ÖfÇ6S¶fFÃÓ¶6æ6VÅ&WG'’‚“¶¶–ÆÂ‚“¶Æ7E&öw&W74CÔFFRææ÷r‚“¶Æ7D†Ç4FFCÖÆ7E&öw&W74C¶Æ7DÖVF–F–ÖSÓ·G'—··f–FVòçW6R‚—×Ö6F6‚…ò—··××f–FVòç&VÖ÷fTGG&–'WFR‚w7&2r“·f–FVòæÆöB‚“¶6öç7BæÖSÖ6&BæFF6WBæ6†ææVÄæÖWÇÂrs¶–b†6†ææVÅF—FÆR–6†ææVÅF—FÆRçFW‡D6öçFVçCÖæÖS¶–b†æ÷tæÖR–æ÷tæÖRçFW‡D6öçFVçCÖæÖS·WFFTæ÷tÆövò†6&BæFF6WBæÆövõW&ÇÇÂrr“¶6&G2æf÷$V6‚†—FVÓÓæ—FVÒæ6Æ74Æ—7BçFövvÆR‚v7F—fRrÆ—FVÓÓÓÖ6&B’“²ò¢5E$TÔdõ$tUôäôDUõÄ”U%ôD•$T5Eõ5t•D4…ôäõõU$Åõc##Cb¢öFö7VÖVçBçF—FÆSÖæÖWÇÆFö7VÖVçBçF—FÆS·7F'B‚—×Ð¦6&G2æf÷$V6‚†6&CÓæ6&BæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÆWfVçCÓç·¶–b†WfVçBæÖWF¶W—ÇÆWfVçBæ7G&Ä¶W—ÇÆWfVçBç6†–gD¶W—ÇÆWfVçBæÇD¶W—ÇÆWfVçBæ'WGFöâÓÓ—&WGW&ã¶WfVçBç&WfVçDFVfVÇB‚“·7v—F6„6†ææVÂ†6&B—×Ò’“°¦6öç7B6†—w&ÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v6FVv÷'’Ö6†—2r“¶6öç7BV×G“ÖFö7VÖVçBævWDVÆVÖVçD'”–B‚v6†ææVÂÖV×G’r“¶6öç7BVæ—VSÖæWr6WB‚“¶6&G2æf÷$V6‚†6&CÓç·²†6&BæFF6WBæ6FVv÷&–W7ÇÂrr’ç7Æ—B‚wÂr’æÖ†æÖSÓææÖRçG&–Ò‚’’æf–ÇFW"„&ööÆVâ’æf÷$V6‚†æÖSÓçVæ—VRæFB†æÖR’“·×Ò“¶6öç7B6FVv÷&–W3Õ²tÆÂrÂââä'&’æg&öÒ‡Væ—VR•Ó¶gVæ7F–öâÇ”6FVv÷'’†æÖR—·¶ÆWB6†÷vãÓ¶6&G2æf÷$V6‚†6&CÓç·¶6öç7BæÖW3Ò†6&BæFF6WBæ6FVv÷&–W7ÇÂrr’ç7Æ—B‚wÂr’æÖ†—FVÓÓæ—FVÒçG&–Ò‚’’æf–ÇFW"„&ööÆVâ“¶6öç7B6†÷sÖæÖSÓÓÒtÆÂwÇÆæÖW2æ–æ6ÇVFW2†æÖR“¶6&Bæ†–FFVãÒ6†÷s¶6&Bç7G–ÆRæw&–D6öÇVÖã×6†÷sòw7â2s¢rs¶–b‡6†÷r—6†÷vâ³Ó·×Ò“²ò¢5E$TÔdõ$tUôäôDUôäõ$ÔÅôÄ5Eõ$õuõc##‚¢ö6&G2æf÷$V6‚†6&CÓç·¶–b‚6&Bæ†–FFVâ–6&Bç7G–ÆRæw&–D6öÇVÖãÒrs·×Ò“¶V×G’æ†–FFVã×6†÷vâÓÓ¶6†—w&çVW'•6VÆV7F÷$ÆÂ‚u¶FFÖ6†—Òr’æf÷$V6‚†6†—Óæ6†—æ6Æ74Æ—7BçFövvÆR‚v7F—fRrÆ6†—æFF6WBæ6†—ÓÓÖæÖR’“·×Ö6FVv÷&–W2æf÷$V6‚†æÖSÓç·¶6öç7B'FãÖFö7VÖVçBæ7&VFTVÆVÖVçB‚v'WGFöâr“¶'FâçG—SÒv'WGFöâs¶'Fâæ6Æ74æÖSÒv6FVv÷'’Ö6†—s¶'FâçFW‡D6öçFVçCÖæÖS¶'FâæFF6WBæ6†—ÖæÖS¶'FâæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚“ÓæÇ”6FVv÷'’†æÖR’“¶6†—w&æVæD6†–ÆB†'Fâ“·×Ò“¶Ç”6FVv÷'’‚tÆÂr“²ò¢5E$TÔdõ$tUôäôDUõÄ”U%ôÄ•dUôôäÄ”äUõc##Cb¢ö6öç7BöæÆ–æUW6TVÃÖFö7VÖVçBævWDVÆVÖVçD'”–B‚wÆ–W"ÖöæÆ–æR×W6Rr’ÆöæÆ–æUW6TVæGö–çC×¶öæÆ–æU÷W6UöVæGö–çEö§6öçÓ¶ÆWBöæÆ–æUW6T'W7“ÖfÇ6S¶7–æ2gVæ7F–öâ&Vg&W6„öæÆ–æUW6R‚—·¶–b‚öæÆ–æUW6TVÇÇÆöæÆ–æUW6T'W7’—&WGW&ã¶öæÆ–æUW6T'W7“×G'VS·G'—·¶6öç7B#Öv—BfWF6‚†öæÆ–æUW6TVæGö–çBÇ·¶66†S¢væò×7F÷&RrÆ7&VFVçF–Ç3¢w6ÖRÖ÷&–v–ârÆ†VFW'3§·²t66WBs¢vÆ–6F–öâö§6öâw×××Ò“¶–b‡"æö²—·¶6öç7BFFÖv—B"æ§6öâ‚“¶–b„çVÖ&W"æ—4f–æ—FR„çVÖ&W"†FFæöæÆ–æU÷W6R’’–öæÆ–æUW6TVÂçFW‡D6öçFVçCÕ7G&–ær„ÖF‚æÖ‚ƒÄçVÖ&W"†FFæöæÆ–æU÷W6R’’—×××Ö6F6‚…ò—··×Öf–æÆÇ—·¶öæÆ–æUW6T'W7“ÖfÇ6W×××Ö–b†öæÆ–æUW6TVÂ—··&Vg&W6„öæÆ–æUW6R‚“·6WD–çFW'fÂ‡&Vg&W6„öæÆ–æUW6RÃ#—×Òò¢5E$TÔdõ$tUôäôDUõäTÅô„T”t…Eõ5”ä5õc##‚¢ö6öç7BÆ–W%æVÃÖFö7VÖVçBçVW'•6VÆV7F÷"‚rçÆ–W"×æVÂr’Æ'&÷w6W%æVÃÖFö7VÖVçBçVW'•6VÆV7F÷"‚ræ'&÷w6W"×æVÂr“¶6öç7B7–æ5æVÄ†V–v‡CÒ‚“Óç·¶–b‚Æ–W%æVÇÇÂ'&÷w6W%æVÂ—&WGW&ã¶–b‡v–æF÷ræÖF6„ÖVF–‚r†Ö‚×v–GFƒ¢c‚’r’æÖF6†W2—·¶'&÷w6W%æVÂç7G–ÆRç&VÖ÷fU&÷W'G’‚rÒ×vF6‚×æVÂÖ†V–v‡Br“·&WGW&ç×Ö'&÷w6W%æVÂç7G–ÆRç6WE&÷W'G’‚rÒ×vF6‚×æVÂÖ†V–v‡BrÆG·´ÖF‚æ6V–Â‡Æ–W%æVÂævWD&÷VæF–æt6Æ–VçE&V7B‚’æ†V–v‡B—××†—×Ó·7–æ5æVÄ†V–v‡B‚“·v–æF÷ræFDWfVçDÆ—7FVæW"‚w&W6—¦RrÇ7–æ5æVÄ†V–v‡B“¶–b‡v–æF÷rå&W6—¦Tö'6W'fW"bgÆ–W%æVÂ–æWr&W6—¦Tö'6W'fW"‡7–æ5æVÄ†V–v‡B’æö'6W'fR‡Æ–W%æVÂ“¶Vç7W&TVæv–æR‚“·×Ò’‚“³Â÷67&—CãÂö&öG“ãÂö‡FÖÃâ"""æf÷&ÖB€¢ff–6öãÕöæöFU÷vV%öff–6öåöÆ–æ²‡&Vf—‚Â&WVW7B’À¢F—FÆSÖ‡FÖÂæW66R†6†ææVÂææÖR²"+r"²öæöFU÷vV'Æ–W%ö'&æEöæÖR‡&WVW7B’’À¢&Vf—ƒÖ‡FÖÂæW66R‡&Vf—‚’À¢÷7FW%öGG#Ò†br÷7FW#Ò'¶‡FÖÂæW66R†7W'&VçEöÆövòÂV÷FSÕG'VR—Ò"r–b7W'&VçEöÆövòVÇ6Rrr’À¢æ÷u÷f—7VÃÖæ÷u÷f—7VÂÀ¢Æ–W%ö–æfõö7F–öç3×Æ–W%ö–æfõö7F–öç2À¢F†VÖU÷vSÖ‡FÖÂæW66R‡F†VÖU÷6æ6†÷E²'vR%Ò’À¢F†VÖU÷æVÃÖ‡FÖÂæW66R‡F†VÖU÷6æ6†÷E²'æVÂ%Ò’À¢F†VÖUö66VçCÖ‡FÖÂæW66R‡F†VÖU÷6æ6†÷E²&66VçB%Ò’À¢F†VÖU÷FW‡CÖ‡FÖÂæW66R‡F†VÖU÷6æ6†÷E²'FW‡B%Ò’À¢6&G5ö‡FÖÃÖ6&G5ö‡FÖÂÀ¢7G&VÕ÷W&Åö§6öãÖ§6öâæGV×2‡7G&VÕ÷W&Â’À¢†Ç5ö§5÷W&Åö§6öãÖ§6öâæGV×2†b'·&Vf—‡Ò÷vV"×Æ–W"ö†Ç2æÖ–âæ§3÷c×µdU%4”ôçÒ"’À¢–÷WGV&UöÆ÷uöÆFVæ7•ö§6öãÖ§6öâæGV×2…öæöFUö6†ææVÅö—5÷–÷WGV&U÷6÷W&6R†6†ææVÂ’’À¢öæÆ–æU÷W6UöVæGö–çEö§6öãÖ§6öâæGV×2†b'·&Vf—‡Ò÷vV"×Æ–W"ööæÆ–æR×W6R"’À¢6æöæ–6Å÷Æ–W%÷W&Åö§6öãÖ§6öâæGV×2…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’’À¢6æöæ–6Å÷Æ–W%÷W&ÃÖ‡FÖÂæW66R…öæöFU÷vV%ö†öÖU÷W&Â‡&WVW7B’ÂV÷FSÕG'VR’À¢¢–bÖævW"æ†–FU÷æVÅö†÷fW%÷W&Ç3 ¢vRÒvRç&WÆ6R‚#Âö&öG“â"ÂöæöFU÷vV%ö†–FUö†÷fW%÷67&—B‚’²#Âö&öG“â"¢&WGW&â…DÔÅ&W7öç6R‡vRÂ†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R'Ò  ¥ôäôDUô4Ä”TåEôÄôt”åôDTEUUôÄô4²ÒF‡&VF–ærå$Æö6²‚¥ôäôDUô4Ä”TåEôÄôt”åôDTEUS¢F–7E·7G"ÂfÆöEÒÒ·Ð  ¦FVböæöFUö6Æ–VçEöÆöv–åöÆöuööæ6R†¶–æC¢7G"ÂW6W%÷Fö¶Vã¢7G"Â6–C¢7G"ÂGFÃ¢–çBÒ’Óâ&ööÃ ¢""$7&÷72×v÷&¶W"FVGWRf÷"öæR7V66W76gVÂ&÷rW"Æöv–6Â6Æ–VçB6W76–öââ"" ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõ$uõ4”EôDTEUUõcsP¢25E$TÔdõ$tUôäôDUô4Ä”TåEõ4U54”ôåôDTEUUõ$U4UEõcƒ¢¶–æB—2–çFVçF–öæÆÇ¢2W†6ÇVFVBg&öÒF†R¶W’6òÆöv–â²Æ–Æ—7B&WVW7G26†&RöæRÆöv–6Â&÷rà¢2F†Rö'6W'fW"&Vg&W6†W2F†—2¶W’v†–ÆRÆ–&6²—27F—fS²gFW"F†P¢26öæf–wW&VBöffÆ–æRv—BW‡—&W2æBF†RæW‡B&WVW7B7&VFW2æWr&÷rà¢GFÂÒÖævW"æ6Æ–VçE÷6W76–öå÷&W6WE÷6V6öæG2‚¢F–vW7BÒ†6†Æ–"ç6†#Sb†b'·W6W%÷Fö¶Vç×Ç·6–GÒ"æVæ6öFR‚'WFbÓ‚"ÂW'&÷'3Ò&–væ÷&R"’’æ†W†F–vW7B‚•³£CÐ¢–bæöFU÷&VF—2—2æ÷BæöæRæBæöFU÷&VF—2æf–Æ&ÆRæBvWFGG"†æöFU÷&VF—2Âv6Æ–VçBrÂæöæR’—2æ÷BæöæS ¢G'“ ¢¶W’ÒæöFU÷&VF—2åö²†b&6Æ–VçBÖÆör×6W76–öã§¶F–vW7GÒ"¢&WGW&â&ööÂ†æöFU÷&VF—2æ6Æ–VçBç6WB†¶W’Â#"ÂçƒÕG'VRÂWƒ×GFÂ’¢W†6WBW†6WF–öã ¢70¢æ÷rÒF–ÖRæÖöæ÷Föæ–2‚¢v—F‚ôäôDUô4Ä”TåEôÄôt”åôDTEUUôÄô4³ ¢7FÆRÒ¶¶W’f÷"¶W’ÂVçF–Â–âôäôDUô4Ä”TåEôÄôt”åôDTEURæ—FV×2‚’–bVçF–ÂÃÒæ÷uÐ¢f÷"¶W’–â7FÆU³£#SeÓ ¢ôäôDUô4Ä”TåEôÄôt”åôDTEURç÷†¶W’ÂæöæR¢–bôäôDUô4Ä”TåEôÄôt”åôDTEURævWB†F–vW7BÂã’âæ÷s ¢&WGW&âfÇ6P¢ôäôDUô4Ä”TåEôÄôt”åôDTEUU¶F–vW7EÒÒæ÷r²GFÀ¢&WGW&âG'VP  ¤ævWB‚"÷Æ–W%ö’ç‡"¦FVbæöFU÷Æ–W%ö’‡&WVW7C¢&WVW7BÂW6W&æÖS¢7G"Â77v÷&C¢7G"Â7F–öã¢7G"Ò""Â6FVv÷'•ö–C¢7G"ÂæöæRÒæöæR“ ¢W6W"ÒöæöFU÷‡G&VÕ÷W6W"‡W6W&æÖRÂ77v÷&B¢7F–öâÒ7G"†7F–öâ÷"""’ç7G&—‚’æÆ÷vW"‚¢–bæ÷BW6W# ¢ÖævW"æÆör€¢%‡G&VÒ6Æ–VçBWF†VçF–6F–öâf–ÆVB"Â66÷SÒ&6Æ–VçB"ÂÆWfVÃÒ'v&æ–ær"ÂW6W#×W6W&æÖR÷"$wVW7B"À¢FWF–Ç3Öb'W6W#×·W6W&æÖR÷"twVW7BwÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²7F–öã×¶7F–öâ÷"vÆöv–âwÒ"À¢¢–bæ÷B7F–öâæBæ÷BW6W# ¢&WGW&â°¢'W6W%ö–æfò#¢°¢'W6W&æÖR#¢W6W&æÖRÂ'77v÷&B#¢77v÷&BÂ&ÖW76vR#¢$–çfÆ–B7&VFVçF–Ç2"À¢&WF‚#¢Â'7FGW2#¢$F—6&ÆVB"Â&W‡öFFR#¢æöæRÂ&—5÷G&–Â#¢#"À¢&7F—fUö6öç2#¢#"Â&7&VFVEöB#¢#"Â&Ö…ö6öææV7F–öç2#¢#"À¢&ÆÆ÷vVEö÷WGWEöf÷&ÖG2#¢²&Ó7S‚%ÒÀ¢ÒÀ¢'6W'fW%ö–æfò#¢öæöFU÷6W'fW%ö–æfò‡&WVW7B’À¢Ð¢–bæ÷BW6W# ¢&WGW&âµÐ¢6FVv÷&–W2ÒöæöFUö6FVv÷'•öÖ‡W6W"¢–bæ÷B7F–öã ¢25E$TÔdõ$tUôäôDUõ…E$TÕôÔåTÅôÄôt”åôÄôuõcCS¢7V66W76gVÂæòÖ7F–öà¢2Æ–W%ö’ç‡7&VFVçF–Â6†V6²—2F†R‡G&VÒöÖçVÂÆ–Æ—7BÆöv–âà¢6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢–böæöFUö6Æ–VçEöÆöv–åöÆöuööæ6R‚'‡G&VÒÖÆöv–â"ÂW6W"çFö¶VâÂ6W76–öåö–B“ ¢ÖævW"æÆör€¢%‡G&VÒ6Æ–VçBÆöv–â"Â66÷SÒ&6Æ–VçB"ÂW6W#×W6W"çW6W&æÖRÀ¢FWF–Ç3Ò†b'W6W#×·W6W"çW6W&æÖWÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó² ¢b&6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²ÖöFSÖÖçVÃ²6W76–öåö–C×·6W76–öåö–GÒ"’À¢¢æ÷rÒF–ÖRæÖöæ÷Föæ–2‚¢7F—fU÷6W76–öç2Ò°¢¶W•³5Òf÷"¶W’Â6W76–öâ–âÖævW"çf–WvW%÷6W76–öç2æ—FV×2‚¢–b¶W•³ÒÓÒW6W"çFö¶VâæBæ÷rÒfÆöB‡6W76–öâævWB‚&Æ7E÷6VVâ"Âã’’ÃÒd”UtU%õEDÀ¢Ð¢W‡öFFRÒ" ¢–bW6W"æW‡—&W5öC ¢G'“ ¢g&öÒFFWF–ÖR–×÷'BFFWF–ÖP¢W‡öFFRÒ7G"†–çB†FFWF–ÖRæg&öÖ—6öf÷&ÖB‡W6W"æW‡—&W5öBç&WÆ6R‚%¢"Â"³£"’’çF–ÖW7F×‚’’¢W†6WBfÇVTW'&÷# ¢W‡öFFRÒ" ¢&WGW&â°¢'W6W%ö–æfò#¢°¢'W6W&æÖR#¢W6W"çW6W&æÖRÂ'77v÷&B#¢77v÷&BÂ&ÖW76vR#¢""Â&WF‚#¢À¢'7FGW2#¢$7F—fR"Â&W‡öFFR#¢W‡öFFR÷"æöæRÂ&—5÷G&–Â#¢#"À¢&7F—fUö6öç2#¢7G"†ÆVâ†7F—fU÷6W76–öç2’’Â&7&VFVEöB#¢#"À¢&Ö…ö6öææV7F–öç2#¢7G"†Ö‚ƒÂ–çB‡W6W"æÖ…ö6öææV7F–öç2÷"’’’À¢&ÆÆ÷vVEö÷WGWEöf÷&ÖG2#¢…²&Ó7S‚"Â'G2%Ò–bW6W"çW6W%÷G—RÓÒ'&W7G&VÒ"VÇ6R²&Ó7S‚%Ò’À¢ÒÀ¢'6W'fW%ö–æfò#¢öæöFU÷6W'fW%ö–æfò‡&WVW7B’À¢Ð¢–b7F–öâÓÒ&vWEöÆ—fUö6FVv÷&–W2# ¢&WGW&â°¢²&6FVv÷'•ö–B#¢6FVv÷'•ö–E÷fÇVRÂ&6FVv÷'•öæÖR#¢æÖRÂ'&VçEö–B#¢Ð¢f÷"æÖRÂ6FVv÷'•ö–E÷fÇVR–â6FVv÷&–W2æ—FV×2‚¢Ð¢–b7F–öâÓÒ&vWEöÆ—fU÷7G&V×2# ¢&÷w2ÒµÐ¢Æ–&6µ÷6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢f÷"–æFW‚Â6†ææVÂ–âVçVÖW&FR…ööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"’Â“ ¢6FVv÷'•öæÖW2ÒöæöFUö6†ææVÅö6FVv÷&–W2†6†ææVÂ¢6FVv÷'•öæÖRÒ6FVv÷'•öæÖW5³Ð¢6FVv÷'•ö–G2Ò¶6FVv÷&–W2ævWB†æÖRÂ#"’f÷"æÖR–â6FVv÷'•öæÖW5Ð¢6Eö–BÒ6FVv÷'•ö–G5³Ò–b6FVv÷'•ö–G2VÇ6R# ¢–b6FVv÷'•ö–Bæ÷B–â„æöæRÂ""Â#"’æB7G"†6FVv÷'•ö–B’æ÷B–â6FVv÷'•ö–G3 ¢6öçF–çVP¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢&÷w2æVæB‡°¢&çVÒ#¢–æFW‚Â&æÖR#¢6†ææVÂææÖRÂ'7G&VÕ÷G—R#¢&Æ—fR"À¢'7G&VÕö–B#¢öæöFU÷7G&VÕö–B†6†ææVÂ’Â'7G&VÕö–6öâ#¢öæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†6†ææVÂæÆövõ÷W&ÂÂ&WVW7B’À¢&Wuö6†ææVÅö–B#¢6†ææVÂç6ÇVr÷"6†ææVÂæ¶W’Â&FFVB#¢#"À¢&6FVv÷'•ö–B#¢6Eö–BÂ&6FVv÷'•ö–G2#¢6FVv÷'•ö–G2Â&7W7FöÕ÷6–B#¢Æ–&6µ÷6W76–öåö–BÂ'Geö&6†—fR#¢À¢&F—&V7E÷6÷W&6R#¢b'¶&6WÒöæöFR×Æ’÷¶—77VUöæöFU÷Æ–&6µö¶W’‡W6W"Â6†ææVÂÂ&WVW7BÂÆ–&6µ÷6W76–öåö–B—Ò÷µöæöFU÷7G&VÕö–B†6†ææVÂ—ÒöÖ7FW"æÓ7S‚"À¢'Geö&6†—fUöGW&F–öâ#¢Â&6öçF–æW%öW‡FVç6–öâ#¢&Ó7S‚"À¢Ò¢&WGW&â&÷w0¢25E$TÔdõ$tUôäôDUõ…E$TÕôTÕE•õTå5Uõ%DTEô4DÄôuõcC ¢2•Eb6Æ–VçG27V6‚26Ö'FW'2&ö&RdôBõ6W&–W27F–öç2v†–ÆRFF–ærWfVâÆ—fRÖöæÇ’66÷VçBà¢2C&W7öç6R—26öÖÖöæÇ’7W&f6VB2$–çfÆ–BW6W"FWF–Ç2"Â6òÆ—fRÖöæÇ’æöFW2×W7Bç7vW ¢2fÆ–BV×G’‡G&VÒ–ÆöG2–ç7FVBöbG&VF–ærVç7W÷'FVB6FÆöwVR7F–öç22â…EEW'&÷"à¢–b7F–öâ–â°¢&vWE÷föEö6FVv÷&–W2"Â&vWE÷föE÷7G&V×2"Â&vWE÷6W&–W5ö6FVv÷&–W2"Â&vWE÷6W&–W2"À¢&vWE÷6†÷'EöWr"Â&vWE÷6–×ÆUöFF÷F&ÆR"À¢Ó ¢&WGW&âµÐ¢–b7F–öâ–â²&vWE÷föEö–æfò"Â&vWE÷6W&–W5ö–æfò'Ó ¢&WGW&â·Ð¢&WGW&âµÐ  ¤ævWB‚"övWBç‡"Â&W7öç6Uö6Æ73ÕÆ–åFW‡E&W7öç6R¦FVbæöFU÷‡G&VÕöÓ7R‡&WVW7C¢&WVW7BÂW6W&æÖS¢7G"Â77v÷&C¢7G"ÂG—S¢7G"Ò&Ó7U÷ÇW2"Â÷WGWC¢7G"Ò&Ó7S‚"“ ¢W6W"ÒöæöFU÷‡G&VÕ÷W6W"‡W6W&æÖRÂ77v÷&B¢–bæ÷BW6W# ¢ÖævW"æÆör‚%‡G&VÒÆ–Æ—7BWF†VçF–6F–öâf–ÆVB"Â66÷SÒ&6Æ–VçB"ÂÆWfVÃÒ'v&æ–ær"ÂW6W#×W6W&æÖR÷"$wVW7B"ÂFWF–Ç3Öb'W6W#×·W6W&æÖR÷"twVW7BwÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ò"¢&—6R…EEW†6WF–öâƒC2Â$–çfÆ–BW6W&æÖR÷"77v÷&B"¢Æ–&6µ÷6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢–böæöFUö6Æ–VçEöÆöv–åöÆöuööæ6R‚'Æ–Æ—7B×‡G&VÒ"ÂW6W"çFö¶VâÂÆ–&6µ÷6W76–öåö–B“ ¢ÖævW"æÆör‚$6Æ–VçBÆ–Æ—7B&WVW7FVB"Â66÷SÒ&6Æ–VçB"ÂFWF–Ç3Öb'W6W#×·W6W"çW6W&æÖWÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²f÷&ÖC×‡G&VÒÖÓ7S²6W76–öåö–C×·Æ–&6µ÷6W76–öåö–GÒ"ÂW6W#×W6W"çW6W&æÖR’25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõÄ”Ä•5Eô4ôåDU…Eõcs ¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢Æ–æW2Ò²"4U…DÓ5R%Ð¢f÷"6†ææVÂ–âööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"“ ¢ÆövòÒöæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†6†ææVÂæÆövõ÷W&ÂÂ&WVW7B’ç&WÆ6R‚r"rÂrS#"r’ç&WÆ6R‚%Æâ"Â""¢ÆövõöGG"ÒbrGfrÖÆövóÒ'¶Æöv÷Ò"r–bÆövòVÇ6R" ¢w&÷WÒ†6†ææVÂæ6FVv÷'’÷"%Væ6FVv÷&—¦VB"’ç&WÆ6R‚r"rÂ"r"’ç&WÆ6R‚%Æâ"Â""¢Æ–æW2æVæB†br4U…D”äc¢ÓGfrÖ–CÒ'¶6†ææVÂç6ÇVwÒ'¶ÆövõöGG'Òw&÷W×F—FÆSÒ'¶w&÷WÒ"Ç¶6†ææVÂææÖWÒr¢Æ–&6µö¶W’Ò—77VUöæöFU÷Æ–&6µö¶W’‡W6W"Â6†ææVÂÂ&WVW7BÂÆ–&6µ÷6W76–öåö–B¢Æ–æW2æVæB†b'¶&6WÒöæöFR×Æ’÷·Æ–&6µö¶W—Ò÷µöæöFU÷7G&VÕö–B†6†ææVÂ—ÒöÖ7FW"æÓ7S‚"¢F÷væÆöEöæÖRÒ&Rç7V"‡"%µäÕ¦×£Ó’åòÕÒ²"Â"Ò"ÂW6W"çW6W&æÖR’ç7G&—‚"Òåò"•³£ƒÒ÷"'Æ–Æ—7B ¢&WGW&âÆ–åFW‡E&W7öç6R‚%Æâ"æ¦ö–â†Æ–æW2’²%Æâ"ÂÖVF–÷G—SÒ&VF–ò÷‚Ö×VwW&Â"Â†VFW'3×°¢$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFR"À¢$6öçFVçBÔF—7÷6—F–öâ#¢bvGF6†ÖVçC²f–ÆVæÖSÒ'¶F÷væÆöEöæÖWÒæÓ7R"rÀ¢Ò  ¤ævWB‚"öÆ—fR÷·W6W&æÖWÒ÷·77v÷&GÒ÷·7G&VÕö–GÒç¶W‡FVç6–öçÒ"¦FVbæöFU÷‡G&VÕöÆ—fR€¢W6W&æÖS¢7G"Â77v÷&C¢7G"Â7G&VÕö–C¢–çBÂW‡FVç6–öã¢7G"Â&WVW7C¢&WVW7BÀ¢6–C¢7G"Ò""ÂFWf–6Uö–C¢7G"Ò""À¢“ ¢–bW‡FVç6–öâæÆ÷vW"‚’æ÷B–â²&Ó7S‚"Â'G2'Ó ¢&—6R…EEW†6WF–öâƒCB¢W6W"ÒöæöFU÷‡G&VÕ÷W6W"‡W6W&æÖRÂ77v÷&B¢–bæ÷BW6W# ¢ÖævW"æÆör‚%‡G&VÒ7G&VÒWF†VçF–6F–öâf–ÆVB"Â66÷SÒ&6Æ–VçB"ÂÆWfVÃÒ'v&æ–ær"ÂW6W#×W6W&æÖR÷"$wVW7B"ÂFWF–Ç3Öb'W6W#×·W6W&æÖR÷"twVW7BwÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ò"¢&—6R…EEW†6WF–öâƒC2Â$–çfÆ–BW6W&æÖR÷"77v÷&B"¢–bW‡FVç6–öâæÆ÷vW"‚’ÓÒ'G2"æBW6W"çW6W%÷G—RÒ'&W7G&VÒ# ¢&—6R…EEW†6WF–öâƒC2Â$æ÷&ÖÂW6W'26ææ÷BW6RE2÷&W7G&VÒ÷WGWB"¢6†ææVÂÒöæöFUö6†ææVÅöf÷%÷7G&VÕö–B‡W6W"Â7G&VÕö–B¢–bæ÷B6†ææVÃ ¢&—6R…EEW†6WF–öâƒCBÂ%7G&VÒæ÷B76–væVBFòF†—2W6W""¢6–BÒÖævW"ç&WVW7E÷6W76–öåö–B‡&WVW7BÂ6–B÷"FWf–6Uö–B¢&WV—&U÷æVÅöæE÷W6W"‡W6W"çFö¶VâÂ&WVW7BÂVæf÷&6Uö6öææV7F–öãÕG'VRÂ6W76–öåö–C×6–BÂÆ–&6µ÷7F'CÕG'VR¢ÖævW"çF÷V6…÷f–WvW"‡W6W"çFö¶VâÂ6†ææVÂæ¶W’Â&WVW7BÂ6–B¢Æ–&6µö¶W’Ò—77VUöæöFU÷Æ–&6µö¶W’‡W6W"Â6†ææVÂÂ&WVW7BÂ6–B¢&WGW&â&VF—&V7E&W7öç6R†b"öæöFR×Æ’÷·Æ–&6µö¶W—Ò÷µöæöFU÷7G&VÕö–B†6†ææVÂ—ÒöÖ7FW"æÓ7Sƒ÷6–C×·W&ÆÆ–"ç'6RçV÷FR‡6–B—Ò"Â7FGW5ö6öFSÓ3"  ¢25E$TÔdõ$tUôäôDUôÄTt5•õ…E$TÕôD•$T5EôTäEô”åEõc#s ¢26ö×F–&–Æ—G’f÷"6Æ–VçG2F†BW6R÷W6W&æÖR÷77v÷&B÷7G&VÕö–BæÓ7S‡ÇG2à¢2&WW6RF†R6æöæ–6ÂöÆ—fR–×ÆVÖVçFF–öâ6ò7&VFVçF–ÂÂ6öææV7F–öâÖÆ–Ö—BÀ¢26†ææVÂÖ76–væÖVçBæBÆ–&6²Ö¶W’Væf÷&6VÖVçB7F’–FVçF–6Âà¤ævWB‚"÷·W6W&æÖWÒ÷·77v÷&GÒ÷·7G&VÕö–GÒç¶W‡FVç6–öçÒ"¦FVbæöFU÷‡G&VÕöÆVv7•öF—&V7B€¢W6W&æÖS¢7G"Â77v÷&C¢7G"Â7G&VÕö–C¢–çBÂW‡FVç6–öã¢7G"Â&WVW7C¢&WVW7BÀ¢“ ¢&WGW&âæöFU÷‡G&VÕöÆ—fR‡W6W&æÖRÂ77v÷&BÂ7G&VÕö–BÂW‡FVç6–öâÂ&WVW7B  ¤ævWB‚"÷Æ–Æ—7B÷·Fö¶VçÒæÓ7R"Â&W7öç6Uö6Æ73ÕÆ–åFW‡E&W7öç6R¦FVbF—&V7EöæöFU÷Æ–Æ—7B‡Fö¶Vã¢7G"Â&WVW7C¢&WVW7B“ ¢G'“ ¢W6W"Ò&WV—&U÷æVÅöæE÷W6W"‡Fö¶VâÂ&WVW7B¢W†6WB…EEW†6WF–öâ2W†3 ¢ÖævW"æÆör‚$6Æ–VçBÆ–Æ—7B66W72FVæ–VB"Â66÷SÒ&6Æ–VçB"ÂÆWfVÃÒ'v&æ–ær"ÂW6W#Ò$wVW7B"ÂFWF–Ç3Öb'W6W#ÔwVW7C²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²&V6öã×¶W†2æFWF–ÇÒ"¢&—6P¢Æ–&6µ÷6W76–öåö–BÒÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B¢–böæöFUö6Æ–VçEöÆöv–åöÆöuööæ6R‚'Æ–Æ—7B×Fö¶Vâ"ÂW6W"çFö¶VâÂÆ–&6µ÷6W76–öåö–B“ ¢ÖævW"æÆör‚$6Æ–VçBÆ–Æ—7B&WVW7FVB"Â66÷SÒ&6Æ–VçB"ÂFWF–Ç3Öb'W6W#×·W6W"çW6W&æÖWÓ²—×¶ÖævW"åö6Æ–VçEö—‡&WVW7B—Ó²6Æ–VçC×·&WVW7Bæ†VFW'2ævWB‚wW6W"ÖvVçBrÂrr•³£3×Ó²f÷&ÖC×Fö¶VâÖÓ7S²6W76–öåö–C×·Æ–&6µ÷6W76–öåö–GÒ"ÂW6W#×W6W"çW6W&æÖR’25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuõÄ”Ä•5Eô4ôåDU…Eõcs ¢†VFW"Ò"4U…DÓ5R ¢–bW6W"çÆ–Æ—7EöæÖS ¢†VFW"³Òbr‚×Æ–Æ—7BÖæÖSÒ'·W6W"çÆ–Æ—7EöæÖRç&WÆ6R†6‡"ƒ3B’Â6‡"ƒ3’’—Ò"p¢–bW6W"çÆ–Æ—7EöÆövõ÷W&Ã ¢†VFW"³Òbr‚×Æ–Æ—7BÖÆövóÒ'·W6W"çÆ–Æ—7EöÆövõ÷W&Âç&WÆ6R†6‡"ƒ3B’Â"S#""—Ò"p¢Æ–æW2Ò¶†VFW%Ð¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢f÷"6†ææVÂ–âööæÆ–æUöVffV7F—fU÷W6W%ö6†ææVÇ2‡W6W"“ ¢ÆövòÒöæöFUö6†ææVÅöÆövõ÷V&Æ–5÷W&Â†6†ææVÂæÆövõ÷W&ÂÂ&WVW7B’ç&WÆ6R‚r"rÂrS#"r’ç&WÆ6R‚%Æâ"Â""¢ÆövõöGG"ÒbrGfrÖÆövóÒ'¶Æöv÷Ò"r–bÆövòVÇ6R" ¢w&÷WÒ6†ææVÂæ6FVv÷'’ç&WÆ6R‚r"rÂ"r"’ç&WÆ6R‚%Æâ"Â""¢Æ–æW2æVæB†br4U…D”äc¢ÓGfrÖ–CÒ'¶6†ææVÂç6ÇVwÒ'¶ÆövõöGG'Òw&÷W×F—FÆSÒ'¶w&÷WÒ"Ç¶6†ææVÂææÖWÒr¢Æ–&6µö¶W’Ò—77VUöæöFU÷Æ–&6µö¶W’‡W6W"Â6†ææVÂÂ&WVW7BÂÆ–&6µ÷6W76–öåö–B¢Æ–æW2æVæB†b'¶&6WÒöæöFR×Æ’÷·Æ–&6µö¶W—Ò÷µöæöFU÷7G&VÕö–B†6†ææVÂ—ÒöÖ7FW"æÓ7S‚"¢&WGW&âÆ–åFW‡E&W7öç6R‚%Æâ"æ¦ö–â†Æ–æW2’²%Æâ"ÂÖVF–÷G—SÒ&VF–ò÷‚Ö×VwW&Â"Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFR'Ò  ¢25E$TÔdõ$tUôäôDUõ4”täTEôÔTD”ôd5ED…õcc3¢Ö–âcbã2Ö’†æBÖVF–¢26VvÖVçG2F—&V7FÇ’FòâWFFVB&VÖ÷FRæöFRâF†R6†÷'BÖÆ—fVB„Ô2—26–væV@¢2v—F‚F†RW†—7F–æræöFR’Fö¶VâÂ6òæòV&Æ–2æöFR7&VFVçF–Â—2W‡÷6VBæ@¢2öÆBæöFW26–×Ç’&VÖ–âöâF†RÆVv7’Ö–â&÷‡’F‚à¦FVb÷6–væVEöÖ–åöÖVF–÷6–væGW&R†W‡—&W3¢–çBÂ¶W“¢7G"Âf–ÆVæÖS¢7G"’Óâ7G# ¢–ÆöBÒb'6fÖVF–×cÇ¶–çB†W‡—&W2—×Ç¶¶W—×Ç¶f–ÆVæÖWÒ"æVæ6öFR‚'WFbÓ‚"¢&WGW&â†Ö2ææWr…Dô´TâæVæ6öFR‚'WFbÓ‚"’Â–ÆöBÂ†6†Æ–"ç6†#Sb’æ†W†F–vW7B‚  ¤ævWB‚"õ÷6bÖÖVF–÷¶W‡—&W7Ò÷·6–væGW&WÒ÷¶¶W—Ò÷¶f–ÆVæÖWÒ"¦FVb6–væVEöÖ–åöÖVF–÷6VvÖVçB†W‡—&W3¢–çBÂ6–væGW&S¢7G"Â¶W“¢7G"Âf–ÆVæÖS¢7G"Â&WVW7C¢&WVW7B“ ¢–bæ÷BDô´Tã ¢&—6R…EEW†6WF–öâƒCB¢æ÷rÒ–çB‡F–ÖRçF–ÖR‚’¢2F–ç’6Æö6²×6¶WrÆÆ÷væ6R¶VW2ÆVv—F–ÖFR6Æ–VçG2v÷&¶–ærÂv†–ÆRF†P¢2WW"&÷VæB&WfVçG2f÷&vVBf"ÖgWGW&RF–ÖW7F×g&öÒ&V6öÖ–ærW6VgVÂà¢–b–çB†W‡—&W2’Âæ÷rÒ2÷"–çB†W‡—&W2’âæ÷r²# ¢&—6R…EEW†6WF–öâƒC2Â$ÖVF–w&çBW‡—&VB"¢G'“ ¢6fUö¶W’ÒÖævW"ç6fUö¶W’†¶W’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCB’g&öÒW†0¢6fRÒF‚†f–ÆVæÖR’ææÖP¢–b6fRÒf–ÆVæÖR÷"æ÷B6fRæVæG7v—F‚‚‚"çG2"Â"æÓG2"Â"æ2"Â"æ×2"Â"æ¶W’"’“ ¢&—6R…EEW†6WF–öâƒCB¢W‡V7FVBÒ÷6–væVEöÖ–åöÖVF–÷6–væGW&R†–çB†W‡—&W2’Â6fUö¶W’Â6fR¢–bæ÷B†Ö2æ6ö×&UöF–vW7B†W‡V7FVBÂ7G"‡6–væGW&R÷"""’“ ¢&—6R…EEW†6WF–öâƒC2Â$–çfÆ–BÖVF–w&çB"¢F‚Ò„Å5õ$ôõBò6fUö¶W’ò6fP¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–Ò²"çG2#¢'f–FVòö×'B"Â"æÓG2#¢'f–FVòö—6òç6VvÖVçB"Â"æ2#¢&VF–òö2"Â"æ×2#¢&VF–òö×Vr"Â"æ¶W’#¢&Æ–6F–öâöö7FWB×7G&VÒ'Ð¢2&ö÷Bõ54‚Ö–ç7FÆÆVBcbã2ÖævVBÕDÅ2æv–ç‚GfW'F—6W2F†—2&—fFP¢26&–Æ—G’†VFW"â’ÖöæÇ’æöFRWw&FW2¶VWf–ÆU&W7öç6RfÆÆ&6²Â6ð¢2Væ&Æ–ærF†R6–væVBÖ–â†æFöfbæWfW"FWVæG2öâ&ö÷B6öæf–r6†ævRà¢–b&WVW7Bæ†VFW'2ævWB‚%‚Õ7G&VÔf÷&vRÔæöFRÔÖVF–Õ„66VÂ"’ÓÒ## ¢&WGW&â&W7öç6R€¢7FGW5ö6öFSÓ#À¢†VFW'3×°¢%‚Ô66VÂÕ&VF—&V7B#¢b"õ÷7G&VÖf÷&vUöæöFUö†Ç2÷·W&ÆÆ–"ç'6RçV÷FR‡6fUö¶W’Â6fSÒrr—Ò÷·W&ÆÆ–"ç'6RçV÷FR‡6fRÂ6fSÒrr—Ò"À¢$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"À¢$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢"À¢$66WBÕ&ævW2#¢&'—FW2"À¢%‚Õ7G&VÔf÷&vRÔÖVF–ÕF‚#¢'&VÖ÷FRÖæöFRÖæv–ç‚×‚Ö66VÂ"À¢ÒÀ¢¢&WGW&âf–ÆU&W7öç6R€¢F‚À¢ÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’À¢†VFW'3×°¢$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"À¢$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢"À¢$66WBÕ&ævW2#¢&'—FW2"À¢%‚Õ7G&VÔf÷&vRÔÖVF–ÕF‚#¢'&VÖ÷FRÖæöFRÖF—&V7B"À¢ÒÀ¢  ¦FVböæöFU÷V&Æ–5ö÷&–v–â‡&WVW7C¢&WVW7B’Óâ7G# ¢&÷FòÒ‡&WVW7Bæ†VFW'2ævWB‚'‚Öf÷'v&FVB×&÷Fò"’÷"&WVW7BçW&Âç66†VÖR÷"&‡GG"’ç7Æ—B‚"Â"Â•³Òç7G&—‚¢†÷7BÒ‡&WVW7Bæ†VFW'2ævWB‚'‚Öf÷'v&FVBÖ†÷7B"’÷"&WVW7Bæ†VFW'2ævWB‚&†÷7B"’÷"&WVW7BçW&ÂææWFÆö2’ç7Æ—B‚"Â"Â•³Òç7G&—‚¢&WGW&âb'·&÷F÷Ó¢ò÷¶†÷7GÒ"ç'7G&—‚"ò"  ¤ævWB‚"õö–çFW&æÂöæöFRÖÖVF–ÖWF‚"¦FVbæöFUö–çFW&æÅöÖVF–öWF‚‡&WVW7C¢&WVW7B“ ¢25E$TÔdõ$tUôäôDUôät”å…ôÔTD”ôUD…õccS¢æv–ç‚66†W2F†—2÷6—F—fP¢2WF†÷&—¦F–öâ'&–VfÇ’Â6ò„Å2–æFW‚÷6VvÖVçB'—FW2æWfW"G&fW'6R—F†öâà¢–bæ÷Bö–çFW&æÅöÖVF–öWF…÷66÷R‡&WVW7Bç66÷R“ ¢&—6R…EEW†6WF–öâƒCB¢Æ–&6µö¶W’Ò7G"‡&WVW7Bæ†VFW'2ævWB‚'‚×7G&VÖf÷&vR×Æ–&6²Ö¶W’"’÷"""’ç7G&—‚¢6–BÒ&Rç7V"‡"%µäÕ¦×£Ó’å÷âÕÒ²"Â""Â7G"‡&WVW7Bæ†VFW'2ævWB‚'‚×7G&VÖf÷&vR×6W76–öâ"’÷"""’ç7G&—‚’•³£“eÐ¢6†ææVÅ÷&VbÒ7G"‡&WVW7Bæ†VFW'2ævWB‚'‚×7G&VÖf÷&vRÖ6†ææVÂ×&Vb"’÷"""’ç7G&—‚•³£“eÐ¢†Ç5ö¶W’Ò7G"‡&WVW7Bæ†VFW'2ævWB‚'‚×7G&VÖf÷&vRÖ†Ç2Ö¶W’"’÷"""’ç7G&—‚•³£“eÐ¢–bæ÷BÆ–&6µö¶W’÷"æ÷B6–B÷"æ÷B6†ææVÅ÷&Vb÷"æ÷B†Ç5ö¶W“ ¢&—6R…EEW†6WF–öâƒC2¢W6W"Âw&çE÷6–BÂ6†ææVÅö¶W’Ò&W6öÇfUöæöFU÷Æ–&6µö¶W’‡Æ–&6µö¶W’Â6†ææVÅ÷&VbÂ&WVW7B¢–bw&çE÷6–BæBæ÷B†Ö2æ6ö×&UöF–vW7B‡7G"†w&çE÷6–B’Â6–B“ ¢&—6R…EEW†6WF–öâƒC2Â%Æ–&6²6W76–öâÖ—6ÖF6‚"¢–bÖævW"ç6fUö¶W’†6†ææVÅö¶W’’Ò†Ç5ö¶W“ ¢&—6R…EEW†6WF–öâƒC2Â%Æ–&6²F‚Ö—6ÖF6‚"¢–bæ÷Bö6†ææVÅ÷Æ–&6µ÷&VG’†6†ææVÅö¶W’“ ¢&—6R…EEW†6WF–öâƒCB¢25E$TÔdõ$tUôäôDUôÄ•dUõÄ”Ä•5Eõ$TõTåõ$U4U%dD”ôåõc#3 ¢2Æ—fR×Æ–Æ—7B†V'F&VB—2âWF†VçF–6FVB6öçF–çVF–öâæBÖ’&V÷Và¢2—G24”BgFW"6†÷'Båd”UtU%õEDÂæWGv÷&²ô„Å2vâ66†VBÖVF–×6VvÖVç@¢2WF‚&VÖ–ç2æöâ×7F'F–ærÂ6òG&–Æ–ær6VvÖVçG26ææ÷B&W7W'&V7B6Æ÷Bà¢†V'F&VE÷&WVW7BÒ7G"‡&WVW7Bæ†VFW'2ævWB‚'‚×7G&VÖf÷&vR×f–WvW"Ö†V'F&VB"’÷"""’ç7G&—‚’ÓÒ# ¢&WV—&U÷æVÅöæE÷W6W"€¢W6W"çFö¶VâÂ&WVW7BÂVæf÷&6Uö6öææV7F–öãÕG'VRÂ6W76–öåö–C×6–BÀ¢Æ–&6µ÷7F'CÖ†V'F&VE÷&WVW7BÀ¢¢ÖævW"çF÷V6…÷f–WvW"‡W6W"çFö¶VâÂ6†ææVÅö¶W’Â&WVW7BÂ6–B¢25E$TÔdõ$tUôäôDUôÄ•dUõ4U54”ôåô„T%D$TEõc“ ¢25E$TÔdõ$tUôäôDUôÄ•dUõ4U54”ôåõ5D$ÄUõEDÅõc“#3¢d”UtU%õEDÂ—2æ÷p¢2&VÆöFVBg&öÒ6†&VB66W72æ§6öâ–âWfW'’ÆöærÖÆ—fVBV&Æ–2v÷&¶W"à¢26VvÖVçBWF†÷&—¦F–öâ¶VW2F†RÆöær†–v‚Ö6öæ7W'&Væ7’66†RâÆ—fP¢2Æ–Æ—7G2W6R6W&FR66†R¶W’æB6†÷'FW"G–æÖ–266†RW&–öBÀ¢2&Vg&W6†–ær&VF—2Æ7B×6VVâögFVâVæ÷Vv‚f÷"F†R6öæf–wW&VBöæÆ–æR6W76–öà¢2F–ÖV÷WBFòF¶RVffV7B&ö×FÇ’v—F†÷WB6VæF–ærÖVF–'—FW2F‡&÷Vv‚—F†öâà¢25E$TÔdõ$tUôäôDUôÄ5Eô5D•d•E•ó%5õc“¢F†RÆ—fR6W76–öç2vR&Vg&W6†W2WfW'’'2à¢2¶VWF†RÆ—fR×Æ–Æ—7B†V'F&VB66†RB'22vVÆÂÂ–æFWVæFVçBöbÆ&vW ¢2öæÆ–æR6W76–öâF–ÖV÷WBÂ6òÆ7B7F—f—G’æòÆöævW"Gfæ6W2–âW2&F6†W2à¢66†U÷6V6öæG2Ò"–b†V'F&VE÷&WVW7BVÇ6RäôDUôÔTD”ôUD…ô44„Uõ4T4ôäE0¢&WGW&â&W7öç6R€¢7FGW5ö6öFSÓ#À¢†VFW'3×°¢$66†RÔ6öçG&öÂ#¢b'V&Æ–2ÂÖ‚ÖvS×¶66†U÷6V6öæG7Ò"À¢%‚Õ7G&VÔf÷&vRÔÖVF–ÔWF†÷&—¦VB#¢#"À¢%‚Õ7G&VÔf÷&vRÔÖVF–ÔWF‚Ô66†R#¢7G"†66†U÷6V6öæG2’À¢%‚Õ7G&VÔf÷&vRÕf–WvW"Ô†V'F&VB#¢#"–b†V'F&VE÷&WVW7BVÇ6R#"À¢ÒÀ¢  ¤ævWB‚"öæöFR×Æ’÷·Fö¶VçÒ÷¶¶W—ÒöÖ7FW"æÓ7S‚"Â&W7öç6Uö6Æ73ÕÆ–åFW‡E&W7öç6R¦FVbæöFU÷W6W%öÖ7FW"‡Fö¶Vã¢7G"Â¶W“¢7G"Â&WVW7C¢&WVW7BÂ6–C¢7G"Ò""“ ¢W6W"Âw&çE÷6–BÂ6†ææVÅö¶W’Ò&W6öÇfUöæöFU÷Æ–&6µö¶W’‡Fö¶VâÂ¶W’Â&WVW7B¢–bæ÷Bö6†ææVÅ÷Æ–&6µ÷&VG’†6†ææVÅö¶W’“ ¢&—6R…EEW†6WF–öâƒCB¢6–BÒÖævW"ç&WVW7E÷6W76–öåö–B‡&WVW7BÂ6–B÷"w&çE÷6–B÷"ÖævW"æ6FÆöu÷6W76–öåö–B‡W6W"Â&WVW7B’¢&WV—&U÷æVÅöæE÷W6W"‡W6W"çFö¶VâÂ&WVW7BÂVæf÷&6Uö6öææV7F–öãÕG'VRÂ6W76–öåö–C×6–BÂÆ–&6µ÷7F'CÕG'VR¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢–b&WVW7Bæ†VFW'2ævWB‚%‚Õ7G&VÔf÷&vRÔæöFRÔÖVF–Ôf7EF‚"’ÓÒ## ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%õEtõõ5DtUôät”å…ôÔTD”õc##ƒ ¢2¶VWF†RÖ7FW"Æ–Æ—7B7FF–2ÂÆ–¶RÖ–ââ„Å2æ§2fWF6†W2—Böæ6RÀ¢2F†Vâ&VÆöG2–æFW‚æÓ7S‚F‡&÷Vv‚æv–ç‚w266†VBWF‚öF—&V7BÖf–ÆRF‚à¢2&WGW&æ–ærÆ—fRÖVF–†W&RÖFRWfW'’öæR×6V6öæBÆ–Æ—7B&Vg&W6‚&WV@¢2—F†öâõ&VF—2WF†÷&—¦F–öâæB&öGV6VB–çFW&Ö—GFVçB×VÇF’×6V6öæBEDd"à¢6fUö6†ææVÅö¶W’ÒÖævW"ç6fUö¶W’†6†ææVÅö¶W’¢ÖVF–÷W&ÂÒ€¢b"õ÷6bÖæöFRÖÖVF–ò ¢b'·W&ÆÆ–"ç'6RçV÷FR‡Fö¶VâÂ6fSÒrr—Ò÷·W&ÆÆ–"ç'6RçV÷FR‡6–BÂ6fSÒrr—Òò ¢b'·W&ÆÆ–"ç'6RçV÷FR†¶W’Â6fSÒrr—Ò÷·W&ÆÆ–"ç'6RçV÷FR‡6fUö6†ææVÅö¶W’Â6fSÒrr—Òö–æFW‚æÓ7S‚ ¢¢&W7öç6RÒÆ–åFW‡E&W7öç6R€¢b"4U…DÓ5UÆâ4U…BÕ‚ÕdU%4”ôã£5Æâ4U…BÕ‚Õ5E$TÒÔ”äc¤$äEt”EDƒÓ3Æç¶ÖVF–÷W&ÇÕÆâ"À¢ÖVF–÷G—SÒ&Æ–6F–öâ÷fæBæÆRæ×VwW&Â"À¢†VFW'3×°¢$66†RÔ6öçG&öÂ#¢&æò×7F÷&RÂæòÖ66†RÂ×W7B×&WfÆ–FFRÂÖ‚ÖvSÓ"À¢$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢"À¢%‚Õ7G&VÔf÷&vRÔÖVF–ÕF‚#¢&æöFR×Gvò×7FvRÖæv–ç‚×c##‚"À¢ÒÀ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô5”ä5õd”UtU%õDõT4…õc##S ¢26öææV7F–öâÆ–Ö—G2&R&W6W'fVB7–æ6‡&öæ÷W6Ç’&÷fS²F†RÆ&vW ¢2f–WvW"ÖWFFFG&ç67F–öâFöW2æ÷BæVVBFòFVÆ’f—'7Bf–FVòà¢&6¶w&÷VæCÔ&6¶w&÷VæEF6²€¢ÖævW"çF÷V6…÷f–WvW"ÂW6W"çFö¶VâÂ6†ææVÅö¶W’Â&WVW7BÂ6–@¢’À¢¢&WGW&â&W7öç6P¢ÖævW"çF÷V6…÷f–WvW"‡W6W"çFö¶VâÂ6†ææVÅö¶W’Â&WVW7BÂ6–B¢&WGW&âÆ–åFW‡E&W7öç6R€¢b"4U…DÓ5UÆâ4U…BÕ‚ÕdU%4”ôã£5Æâ4U…BÕ‚Õ5E$TÒÔ”äc¤$äEt”EDƒÓ3Æâ ¢b'¶&6WÒöæöFR×Æ’÷·Fö¶VçÒ÷·W&ÆÆ–"ç'6RçV÷FR†¶W’—Òö–æFW‚æÓ7Sƒ÷6–C×·W&ÆÆ–"ç'6RçV÷FR‡6–B—ÕÆâ"À¢ÖVF–÷G—SÒ&Æ–6F–öâ÷fæBæÆRæ×VwW&Â"À¢†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'ÒÀ¢  ¤ævWB‚"öæöFR×Æ’÷·Fö¶VçÒ÷¶¶W—Òö–æFW‚æÓ7S‚"Â&W7öç6Uö6Æ73ÕÆ–åFW‡E&W7öç6R¦FVbæöFU÷W6W%÷Æ–Æ—7B‡Fö¶Vã¢7G"Â¶W“¢7G"Â&WVW7C¢&WVW7BÂ6–C¢7G"Ò""“ ¢W6W"Âw&çE÷6–BÂ6†ææVÅö¶W’Ò&W6öÇfUöæöFU÷Æ–&6µö¶W’‡Fö¶VâÂ¶W’Â&WVW7B¢–bæ÷Bö6†ææVÅ÷Æ–&6µ÷&VG’†6†ææVÅö¶W’“ ¢&—6R…EEW†6WF–öâƒCB¢6–BÒÖævW"ç&WVW7E÷6W76–öåö–B‡&WVW7BÂ6–B÷"w&çE÷6–B¢25E$TÔdõ$tUôäôDUôÄ•dUõÄ”Ä•5Eõ$TõTåõ$U4U%dD”ôåõc#3¢F—&V7B—F†öà¢2Æ–Æ—7BFVÆ—fW'’†2F†R6ÖR&V6÷fW'’6VÖçF–722æv–ç‚Æ—fRWF‚à¢&WV—&U÷æVÅöæE÷W6W"€¢W6W"çFö¶VâÂ&WVW7BÂVæf÷&6Uö6öææV7F–öãÕG'VRÂ6W76–öåö–C×6–BÂÆ–&6µ÷7F'CÕG'VP¢¢ÖævW"çF÷V6…÷f–WvW"‡W6W"çFö¶VâÂ6†ææVÅö¶W’Â&WVW7BÂ6–B¢F‚Ò„Å5õ$ôõBòÖævW"ç6fUö¶W’†6†ææVÅö¶W’’ò&–æFW‚æÓ7S‚ ¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒS2Â%7G&VÒ—2æ÷B&VG’"¢&6RÒöæöFU÷V&Æ–5ö&6R‡&WVW7B¢Æ–æW2ÒµÐ¢f÷"Æ–æR–âF‚ç&VE÷FW‡B†W'&÷'3Ò'&WÆ6R"’ç7Æ—FÆ–æW2‚“ ¢Æ–æW2æVæB†b'¶&6WÒöæöFR×Æ’÷·Fö¶VçÒ÷·W&ÆÆ–"ç'6RçV÷FR†¶W’—Ò÷µF‚†Æ–æR’ææÖWÓ÷6–C×·W&ÆÆ–"ç'6RçV÷FR‡6–B—Ò"–bÆ–æRæBæ÷BÆ–æRç7F'G7v—F‚‚"2"’VÇ6RÆ–æR¢&WGW&âÆ–åFW‡E&W7öç6R‚%Æâ"æ¦ö–â†Æ–æW2’²%Æâ"ÂÖVF–÷G—SÒ&Æ–6F–öâ÷fæBæÆRæ×VwW&Â"Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'Ò  ¤ævWB‚"öæöFR×Æ’÷·Fö¶VçÒ÷¶¶W—Ò÷¶f–ÆVæÖWÒ"¦FVbæöFU÷W6W%÷6VvÖVçB‡Fö¶Vã¢7G"Â¶W“¢7G"Âf–ÆVæÖS¢7G"Â&WVW7C¢&WVW7BÂ6–C¢7G"Ò""“ ¢W6W"Âw&çE÷6–BÂ6†ææVÅö¶W’Ò&W6öÇfUöæöFU÷Æ–&6µö¶W’‡Fö¶VâÂ¶W’Â&WVW7B¢6–BÒÖævW"ç&WVW7E÷6W76–öåö–B‡&WVW7BÂ6–B÷"w&çE÷6–B¢&WV—&U÷æVÅöæE÷W6W"‡W6W"çFö¶VâÂ&WVW7BÂVæf÷&6Uö6öææV7F–öãÕG'VRÂ6W76–öåö–C×6–B¢ÖævW"çF÷V6…÷f–WvW"‡W6W"çFö¶VâÂ6†ææVÅö¶W’Â&WVW7BÂ6–B¢6fRÒF‚†f–ÆVæÖR’ææÖP¢–b6fRÒf–ÆVæÖR÷"æ÷B6fRæVæG7v—F‚‚‚"çG2"Â"æÓG2"Â"æ2"Â"æ×2"Â"æ¶W’"’“ ¢&—6R…EEW†6WF–öâƒCB¢F‚Ò„Å5õ$ôõBòÖævW"ç6fUö¶W’†6†ææVÅö¶W’’ò6fP¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–Ò²"çG2#¢'f–FVòö×'B"Â"æÓG2#¢'f–FVòö—6òç6VvÖVçB"Â"æ2#¢&VF–òö2"Â"æ×2#¢&VF–òö×Vr"Â"æ¶W’#¢&Æ–6F–öâöö7FWB×7G&VÒ'Ð¢&WGW&âf–ÆU&W7öç6R‡F‚ÂÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'Ò   ¤ç÷7B‚"ö’÷c÷f–WvW'2ö¶–ÆÂ"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbf–WvW%ö¶–ÆÂ‡–ÆöC¢¶–ÆÅf–WvW%–ÆöB“ ¢&WGW&â²&ö²#¢G'VRÂ&¶–ÆÆVB#¢ÖævW"æ¶–ÆÅ÷f–WvW%÷6W76–öâ‡–ÆöBç6W76–öåö–B—Ð  ¢25E$TÔdõ$tUôäôDUôÄ•dUõ4U54”ôå5ôôåôDTÔäEõcƒ“ ¢2FWF–ÆVBf–WvW"&÷w2&R–çFVçF–öæÆÇ’W‡÷6VBF‡&÷Vv‚FVF–6FVB6öçG&öÀ¢2VæGö–çB–ç7FVBöb&ÆöF–ærö†VÇF‚âÖ–âfWF6†W2F†—2öæÇ’v†–ÆRF†RÆ—fP¢26W76–öç2vR—2÷Vã²÷&F–æ'’æöFR6&G2¶VWW6–ær6†V†V'F&VB6÷VçG2à¤ævWB‚"ö’÷c÷f–WvW'2÷6W76–öç2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbf–WvW%÷6W76–öç5÷6æ6†÷B‚“ ¢7FG2ÒÖævW"çf–WvW%÷7FG2†–æ6ÇVFUövVóÔfÇ6R¢&WGW&â°¢&ö²#¢G'VRÀ¢'F÷FÅ÷W6W'2#¢–çB‡7FG2ævWB‚'F÷FÅ÷W6W'2"’÷"’À¢'Væ—VUö66÷VçG2#¢–çB‡7FG2ævWB‚'Væ—VUö66÷VçG2"’÷"’À¢&F—&V7E÷F÷FÅ÷W6W'2#¢–çB‡7FG2ævWB‚&F—&V7E÷F÷FÅ÷W6W'2"’÷"’À¢&F—&V7Eö6†ææVÇ2#¢F–7B‡7FG2ævWB‚&F—&V7Eö6†ææVÇ2"’÷"·Ò’À¢&F—&V7E÷6W76–öç2#¢Æ—7B‡7FG2ævWB‚&F—&V7E÷6W76–öç2"’÷"µÒ’À¢Ð  ¢25E$TÔdõ$tUôäôDUôÄôuõ4T$4…õc3S ¢2¶VWF†RæöFR&–ærÖ'VffW"6V&6†&ÆRv—F†÷WB6†—–ærWfW'’&÷rFòÖ–âà¢2FW&×2&RäFVB7&÷72F†R6ÖRf—6–&ÆRf–VÆG2W6VB'’F†RÖ–âÆöw2F&ÆRà¦FVböæöFUöÆöuöÖF6†W5÷6V&6‚†—FVÓ¢F–7E·7G"Âç•ÒÂ¢7G"’Óâ&ööÃ ¢æ÷&ÖÆ—¦VBÒ""æ¦ö–â‡7G"‡÷"""’ç7G&—‚’ç7Æ—B‚’•³£#CÒæ66VföÆB‚¢FW&×2Ò·FW&Òf÷"FW&Ò–âæ÷&ÖÆ—¦VBç7Æ—B‚""’–bFW&ÕÕ³£%Ð¢–bæ÷BFW&×3 ¢&WGW&âG'VP¢†—7F6²Ò%Æâ"æ¦ö–â…°¢7G"†ÖævW"ææöFUöæÖR÷"""’À¢7G"†—FVÒævWB‚'66÷R"’÷"""’À¢7G"†—FVÒævWB‚&ÆWfVÂ"’÷"""’À¢7G"†—FVÒævWB‚&6†ææVÂ"’÷"""’À¢7G"†—FVÒævWB‚'W6W""’÷"""’À¢7G"†—FVÒævWB‚&ÖW76vR"’÷"""’À¢7G"†—FVÒævWB‚&FWF–Ç2"’÷"""’À¢Ò’æ66VföÆB‚¢&WGW&âÆÂ‡FW&Ò–â†—7F6²f÷"FW&Ò–âFW&×2  ¤ævWB‚"ö’÷cöÆöw2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVçEöÆöw2†Æ–Ö—C¢–çBÒ3ÂÆWfVÃ¢7G"Ò""Â66÷S¢7G"Ò""Â¢7G"Ò""“ ¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuô”äÄ”äUõ4U54”ôåôtUõcc¢&VÖ÷FRÖ–âÆöw2æ@¢2F†R7FæFÆöæRæöFRæVÂ&V6V—fRF†R6ÖRföÆFVB6Æ–VçBÖÆör&W6VçFF–öâà¢—FV×2ÒöæöFUöföÆE÷Æ–&6µ÷6W76–öå÷&÷w2†ÖævW"æWfVçEöÆöw5÷6æ6†÷B‚’¢–bÆWfVÃ ¢—FV×2Ò¶—FVÒf÷"—FVÒ–â—FV×2–b—FVÒævWB‚&ÆWfVÂ"’ÓÒÆWfVÅÐ¢–b66÷S ¢—FV×2Ò¶—FVÒf÷"—FVÒ–â—FV×2–b—FVÒævWB‚'66÷R"’ÓÒ66÷UÐ¢–b7G"‡÷"""’ç7G&—‚“ ¢—FV×2Ò¶—FVÒf÷"—FVÒ–â—FV×2–böæöFUöÆöuöÖF6†W5÷6V&6‚†—FVÒÂ•Ð¢6fUöÆ–Ö—BÒ–çB†Æ–Ö—B÷"¢6VÆV7FVEö—FV×2Ò—FV×2–b6fUöÆ–Ö—BÃÒVÇ6R—FV×5³¦Ö‚ƒÂÖ–âƒSÂ6fUöÆ–Ö—B’•Ð¢25E$TÔdõ$tUôäôDUô4Ä”TåEôÄôuôEU$D”ôåô•õcCS¢Ö–â&V6V—fW2F†R6ÖP¢26W76–öâvR2F†R7FæFÆöæRæöFR6Æ–VçBÆörâcãCb¶VW2F†Rf–æÂvP¢2gFW"F†Rf–WvW"vöW2öffÆ–æR–ç7FVBöbG&÷–ærF†R6öÇVÖâ&6²FòVÒF6‚à¢6VÆV7FVEö—FV×2Ò¶F–7B†—FVÒ’f÷"—FVÒ–â6VÆV7FVEö—FV×5Ð¢6Æ–VçE÷&÷w2Ò¶—FVÒf÷"—FVÒ–â6VÆV7FVEö—FV×2–b7G"†—FVÒævWB‚'66÷R"’÷"""’ç7G&—‚’æÆ÷vW"‚’ÓÒ&6Æ–VçB%Ð¢GW&F–öå÷7FFW2ÒöæöFUö6Æ–VçEöÆöuöGW&F–öå÷7FFW2†6Æ–VçE÷&÷w2’–b6Æ–VçE÷&÷w2VÇ6RµÐ¢f÷"—FVÒÂ7FFR–â¦—†6Æ–VçE÷&÷w2ÂGW&F–öå÷7FFW2“ ¢Æ—fU÷6W76–öåö–BÒöæöFUö6Æ–VçEöÆöu÷6W76–öåö–B†—FVÒ¢–bÆ—fU÷6W76–öåö–C ¢—FVÕ²'6W76–öåö–B%ÒÒÆ—fU÷6W76–öåö–@¢–b7FFS ¢—FVÕ²&GW&F–öå÷6V6öæG2%ÒÒ–çB‡7FFRævWB‚&GW&F–öå÷6V6öæG2"’÷"¢—FVÕ²'6W76–öåööæÆ–æR%ÒÒ&ööÂ‡7FFRævWB‚&öæÆ–æR"’¢&WGW&â²&ö²#¢G'VRÂ'F÷FÂ#¢ÆVâ†—FV×2’Â&—FV×2#¢6VÆV7FVEö—FV×7Ð  ¤ævWB‚"ö’÷cö6†ææVÇ2÷¶¶W—ÒöÆöw2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVçEö6†ææVÅöÆöw2†¶W“¢7G"ÂÆ–Ö—C¢–çBÒ“ ¢6fUö¶W’ÒÖævW"ç6fUö¶W’†¶W’¢—FV×2Ò°¢—FVÒf÷"—FVÒ–âÖævW"æWfVçEöÆöw5÷6æ6†÷B‚¢–b7G"†—FVÒævWB‚&6†ææVÂ"’÷"""’ç7G&—‚’ÓÒ6fUö¶W¢Ð¢&WGW&â²&ö²#¢G'VRÂ&—FV×2#¢—FV×5³¦Ö‚ƒÂÖ–âƒÂ–çB†Æ–Ö—B÷"’’•×Ð  ¤ç÷7B‚"ö’÷cö6†ææVÇ2÷¶¶W—ÒöÆöw2ö6ÆV""ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVçEö6†ææVÅöÆöw5ö6ÆV"†¶W“¢7G"“ ¢6fUö¶W’ÒÖævW"ç6fUö¶W’†¶W’¢ÖævW"ç7–æ5öWfVçEöÆöw5ög&öÕöf–ÆR‚¢v—F‚ÖævW"æÆö6³ ¢&Wf–÷W2ÒÆ—7B†ÖævW"æWfVçEöÆöw2¢&VÖ–æ–ærÒ¶—FVÒf÷"—FVÒ–â&Wf–÷W2–b7G"†—FVÒævWB‚&6†ææVÂ"’÷"""’ç7G&—‚’Ò6fUö¶W•Ð¢6ÆV&VBÒÆVâ‡&Wf–÷W2’ÒÆVâ‡&VÖ–æ–ær¢ÖævW"æWfVçEöÆöw2ÒFWVR‡&VÖ–æ–ærÂÖ†ÆVãÔäôDUôUdTåEôÄôuôÔ‚¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB‡6fUö¶W’¢–b'VçF–ÖS ¢'VçF–ÖRæÆ7EöW'&÷"Ò" ¢÷&Ww&—FUöæöFUöWfVçEöÆöuöf–ÆR‚¢&WGW&â²&ö²#¢G'VRÂ&6ÆV&VB#¢6ÆV&VGÐ  ¤ç÷7B‚"ö’÷cöÆöw2ö6ÆV""ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVçEöÆöw5ö6ÆV"†Æöu÷G—S¢7G"Ò""ÂÆWfVÃ¢7G"Ò""Â66÷S¢7G"Ò""Â¢7G"Ò""“ ¢25E$TÔdõ$tUôäôDUõ44õTEôÄôuô4ÄT%õccc ¢ÖævW"ç7–æ5öWfVçEöÆöw5ög&öÕöf–ÆR‚¢26ÆV"öæÇ’F†R6VÆV7FVBÖ–âÆöw2F"öf–ÇFW"–ç7FVBöbv—–ærF†P¢2VçF—&RæöFR&–ær'VffW"âv—F‚æòf–ÇFW'2F†—2&VÖ–ç2&6·v&BÖ6ö×F–&ÆRà¢6VÆV7FVBÒ7G"†Æöu÷G—R÷"""’ç7G&—‚’æÆ÷vW"‚¢7—7FVÕ÷66÷W2Ò²'7—7FVÒ"Â&æöFR"Â'WFFR"Â'6WGF–æw2'Ð¢W†6ÇVFVEö7F—f—G’Ò²&WF‚"Â&6Æ–VçB"Â§7—7FVÕ÷66÷W7Ð ¢FVbÖF6†W2†—FVÒ“ ¢—FVÕ÷66÷RÒ7G"†—FVÒævWB‚'66÷R"’÷"'7—7FVÒ"¢—FVÕöÆWfVÂÒ7G"†—FVÒævWB‚&ÆWfVÂ"’÷"&–æfò"¢–b6VÆV7FVBÓÒ&66W72"æB—FVÕ÷66÷RÒ&WF‚# ¢&WGW&âfÇ6P¢–b6VÆV7FVBÓÒ&6Æ–VçB"æB—FVÕ÷66÷RÒ&6Æ–VçB# ¢&WGW&âfÇ6P¢–b6VÆV7FVBÓÒ'7—7FVÒ"æB—FVÕ÷66÷Ræ÷B–â7—7FVÕ÷66÷W3 ¢&WGW&âfÇ6P¢–b6VÆV7FVBÓÒ&7F—f—G’"æB—FVÕ÷66÷R–âW†6ÇVFVEö7F—f—G“ ¢&WGW&âfÇ6P¢–b66÷RæB—FVÕ÷66÷RÒ66÷S ¢&WGW&âfÇ6P¢–bÆWfVÂæB—FVÕöÆWfVÂÒÆWfVÃ ¢&WGW&âfÇ6P¢–b7G"‡÷"""’ç7G&—‚’æBæ÷BöæöFUöÆöuöÖF6†W5÷6V&6‚†—FVÒÂ“ ¢&WGW&âfÇ6P¢&WGW&âG'VP ¢v—F‚ÖævW"æÆö6³ ¢&Wf–÷W2ÒÆ—7B†ÖævW"æWfVçEöÆöw2¢–bæ÷B6VÆV7FVBæBæ÷B66÷RæBæ÷BÆWfVÂæBæ÷B7G"‡÷"""’ç7G&—‚“ ¢&VÖ–æ–ærÒµÐ¢VÇ6S ¢&VÖ–æ–ærÒ¶—FVÒf÷"—FVÒ–â&Wf–÷W2–bæ÷BÖF6†W2†—FVÒ•Ð¢6ÆV&VBÒÆVâ‡&Wf–÷W2’ÒÆVâ‡&VÖ–æ–ær¢ÖævW"æWfVçEöÆöw2ÒFWVR‡&VÖ–æ–ærÂÖ†ÆVãÔäôDUôUdTåEôÄôuôÔ‚¢÷&Ww&—FUöæöFUöWfVçEöÆöuöf–ÆR‚¢ÖævW"æÆör‚$æöFRÆöw26ÆV&VB"Â66÷SÒ'7—7FVÒ"ÂFWF–Ç3×²&6ÆV&VB#¢6ÆV&VBÂ&Æöu÷G—R#¢6VÆV7FVBÂ'66÷R#¢66÷RÂ&ÆWfVÂ#¢ÆWfVÂÂ'#¢7G"‡÷"""•³£#C×Ò¢&WGW&â²&ö²#¢G'VRÂ&6ÆV&VB#¢6ÆV&VGÐ ¤ævWB‚"ö’÷cövVò÷6WGF–æw2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVõ÷6WGF–æw5÷7FGW2‚“ ¢&WGW&âÖævW"ævVõ÷6WGF–æw5÷7FGW2‚  ¤ç÷7B‚"ö’÷cövVò÷6WGF–æw2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVõ÷6WGF–æw5÷6fR‡–ÆöC¢vVõ6WGF–æw5–ÆöB“ ¢G'“ ¢&WGW&âÖævW"ç6fUövVõ÷6WGF–æw2‡–ÆöB¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cövVò÷WFFR"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbvVõ÷WFFUöæ÷r‚“ ¢G'“ ¢&WGW&âÖævW"ç'VåövVõ÷WFFR‚¢W†6WB'VçF–ÖTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒSÂ7G"†W†2’’g&öÒW†0  ¤ævWB‚"ö’÷cö6â÷7FGW2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb6å÷7FGW2‚“ ¢&WGW&âÖævW"æ6å÷7FGW2‚  ¤ævWB‚"ö’÷cö6âöÆöö·W"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb6åöÆöö·W†—¢7G"“ ¢G'“ ¢æ÷&ÖÆ—¦VBÒ7G"†—FG&W72æ—öFG&W72†—ç7G&—‚’’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ$fÆ–B•cB÷"•cbFG&W72—2&WV—&VB"’g&öÒW†0¢7FGW2ÒÖævW"æ6å÷7FGW2‚¢&W7VÇBÒÖævW"åöÆöö·WövVò†æ÷&ÖÆ—¦VB¢&W7VÇBçWFFR‡²&ö²#¢&ööÂ‡7FGW2ævWB‚&ÆöFVB"’÷"7FGW2ævWB‚&—–æfõö6öæf–wW&VB"’’Â'7FGW2#¢7FGW7Ò¢&WGW&â&W7VÇ@  ¥ô„TÅD…ô4$”Ä•E•ô44„S¢F–7E·7G"Âç•ÒÒ²&6†V6¶VEöB#¢ãÂ'fÇVW2#¢·×Ð¥ô„TÅD…ô4$”Ä•E•ôÄô4²ÒF‡&VF–ærå$Æö6²‚  ¦FVbö†VÇF…ö6&–Æ—F–W2‚’ÓâF–7E·7G"Â7G%Ó ¢æ÷rÒF–ÖRæÖöæ÷Föæ–2‚¢v—F‚ô„TÅD…ô4$”Ä•E•ôÄô4³ ¢66†VBÒF–7B…ô„TÅD…ô4$”Ä•E•ô44„RævWB‚'fÇVW2"’÷"·Ò¢–b66†VBæBæ÷rÒfÆöB…ô„TÅD…ô4$”Ä•E•ô44„RævWB‚&6†V6¶VEöB"’÷"ã’Â3 ¢&WGW&â66†V@¢fÇVW3¢F–7E·7G"Â7G%ÒÒ·Ð¢f÷"fÖ–Ç’–â‚&ƒ#cB"Â&ƒ#cR"“ ¢G'“ ¢fÇVW5¶fÖ–Ç•ÒÒÖævW"æWFõöVæ6öFW"†fÖ–Ç’¢W†6WBW†6WF–öâ2W†3 ¢fÇVW5¶fÖ–Ç•ÒÒb'Væf–Æ&ÆS¢¶W†7Ò ¢ô„TÅD…ô4$”Ä•E•ô44„U²&6†V6¶VEöB%ÒÒæ÷p¢ô„TÅD…ô4$”Ä•E•ô44„U²'fÇVW2%ÒÒfÇVW0¢&WGW&âF–7B‡fÇVW2  ¢25E$TÔdõ$tUôäôDUô4$Eõ5E$”5EôDTÄ•dU%•ô4õTåE5õcCS ¢25E$TÔdõ$tUôäôDUô4$Eõt•D”äuô5ôDõtåõcCc¢Ö–âföÆG2F†—2FWF–ÆVBv—F–ær6÷VçB–çFòF÷vâöâ7VÖÖ'’6&G2à¦FVböæöFUö6&EöFVÆ—fW'•ö6÷VçG2†÷væW#¢7G"ÂæöæRÒæöæR’ÓâF–7E·7G"Â–çEÓ ¢""$6÷VçBæöFR6†ææVÇ2'’Æ–&ÆRFVÆ—fW'’&F†W"F†âdf×VrÆ—fVæW72â"" ¢vçFVEö÷væW"Ò7G"†÷væW"÷"""’ç7G&—‚’æÆ÷vW"‚¢v—F‚ÖævW"æÆö6³ ¢6öæf–w2Ò°¢†¶W’Â'VçF–ÖRæ6öæf–r¢f÷"¶W’Â'VçF–ÖR–âÖævW"æ6†ææVÇ2æ—FV×2‚¢–bæ÷BvçFVEö÷væW"÷"7G"†vWFGG"‡'VçF–ÖRæ6öæf–rÂ&6FÆöuö÷væW""Â&Ö–â"’÷"&Ö–â"’ç7G&—‚’æÆ÷vW"‚’ÓÒvçFVEö÷væW ¢Ð¢6÷VçG2Ò²'F÷FÂ#¢ÆVâ†6öæf–w2’Â'W#¢Â'v—F–ær#¢Â&F÷vâ#¢Â&7F—fR#¢Ð¢f÷"¶W’Â6öæf–r–â6öæf–w3 ¢7FGW2ÒÖævW"ç7FGW2†¶W’¢–b&ööÂ‡7FGW2ævWB‚&Æ—fR"’“ ¢6÷VçG5²&7F—fR%Ò³Ò¢FVÆ—fW'’ÒöæöFUö6†ææVÅöFVÆ—fW'•÷7FGW2†6öæf–rÂ7FGW2¢6÷VçG5¶FVÆ—fW'’–bFVÆ—fW'’–â²'W"Â'v—F–ær"Â&F÷vâ'ÒVÇ6R&F÷vâ%Ò³Ò¢&WGW&â6÷VçG0  ¤ævWB‚"ö’÷c÷7FGW2÷V–6²"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbV–6µ÷7FGW2‚“ ¢""%&WGW&âF†RæöFRÖ6&B–ÆöBv—F†÷WBW‡Vç6—fR†VÇF‚6–FRVffV7G2â"" ¢FVÆ—fW'•ö6÷VçG2ÒöæöFUö6&EöFVÆ—fW'•ö6÷VçG2‚¢7F—fUö6†ææVÇ2Ò–çB†FVÆ—fW'•ö6÷VçG5²&7F—fR%Ò¢7F—fUö6öææV7F–öç2Ò–çB†ÖævW"çF÷FÅö7F—fUö6öææV7F–öç2‚’¢&WGW&â°¢&ö²#¢G'VRÀ¢'7FGW2#¢&öæÆ–æR"À¢'fW'6–öâ#¢dU%4”ôâÀ¢&†÷7FæÖR#¢÷2çVæÖR‚’ææöFVæÖRÀ¢&ÖWG&–72#¢ÖævW"æÖWG&–72‚’À¢&7F—fUö6†ææVÇ2#¢7F—fUö6†ææVÇ2À¢'Wö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²'W%Ò’À¢'v—F–æuö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²'v—F–ær%Ò’À¢&F÷våö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²&F÷vâ%Ò’À¢'f–WvW%÷7FG2#¢²'F÷FÅ÷W6W'2#¢7F—fUö6öææV7F–öç7ÒÀ¢&7F—fUö6öææV7F–öç2#¢7F—fUö6öææV7F–öç2À¢'F÷FÅöÖ…ö6öææV7F–öç2#¢–çB†ÖævW"çF÷FÅöÖ…ö6öææV7F–öç2’À¢'æVÅ÷W&Ç2#¢Æ—7B†ÖævW"çæVÅ÷W&Ç2’À¢'7G&VÕ÷W&Ç2#¢Æ—7B†ÖævW"ç7G&VÕ÷W&Ç2’À¢'7G&VÕ÷÷'B#¢–çB†ÖævW"ç7G&VÕ÷÷'B’À¢&6öçG&öÅ÷÷'B#¢–çB„4ôåE$ôÅõõ%B’À¢&æöFUöÖöFR#¢äôDUôÔôDRÀ¢&W‡FW&æÅ÷&÷‡•öÖöFR#¢&ööÂ„U…DU$äÅõ$õ…•ôÔôDR’À¢25E$TÔdõ$tUôäôDUôDå3õ5DEU5õT”4µõcC¢ÆWG2Ö–â6†÷rF†RöæR×F–ÖP¢24äÔR–ç7G'V7F–öâWfVâ&Vf÷&R…EE2—2f–Æ&ÆRöâF†—2æöFRà¢&ÖævVE÷FÇ2#¢ÖævW"çFÇ5÷7FGW2‚’À¢Ð  ¢25E$TÔdõ$tUôäôDUôDå3ô4ôåE$ôÅõDU5Eõcƒ#¢WF†VçF–6FVBÖ–â6öçG&öÂ×ÆæP¢2FW7BVæGö–çBâ—BFVÆ–&W&FVÇ’&WW6W2F†RW†7BæöFR6WGF–æw2&W6öÇfW"Æöv–0¢26òÖ–âæBæöFR6âæWfW"F—6w&VR&V6W6RF†W’FW7FVBg&öÒF–ffW&VçB†÷7G2à¤ç÷7B‚"ö’÷c÷FÇ2öFç3÷FW7B"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦7–æ2FVbFÇ5öFç3÷FW7B‡&WVW7C¢&WVW7B“ ¢G'“ ¢–ÆöBÒv—B&WVW7Bæ§6öâ‚¢W†6WBW†6WF–öã ¢–ÆöBÒ·Ð¢†÷7BÒ7G"‡–ÆöBævWB‚&†÷7B"’÷"""’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚"â"’–b—6–ç7Fæ6R‡–ÆöBÂF–7B’VÇ6R" ¢&W7VÇBÒv—B7–æ6–òçFõ÷F‡&VB…öæöFU÷6WGF–æw5öFç3÷FW7E÷7–æ2Â†÷7B¢–b&W7VÇBævWB‚&ö²"’æBæ÷B&W7VÇBævWB‚&6W'F–f–6FU÷&VG’"’æBæ÷BU…DU$äÅõ$õ…•ôÔôDS ¢ÖævW"ç&WVW7E÷FÇ5÷&V6öæ6–ÆR††÷7BÂf÷&6U÷&WG'“ÕG'VR¢&W7VÇBÒF–7B‡&W7VÇB¢&W7VÇE²&—77Væ6U÷VWVVB%ÒÒG'VP¢&W7VÇE²&ÖW76vR%ÒÒ$4äÔR—26÷'&V7BæBV&Æ–6Ç’f—6–&ÆRâ6W'F–f–6FR—77Væ6RVWVVBWFöÖF–6ÆÇ’â ¢&WGW&â¥4ôå&W7öç6R‡&W7VÇB  ¢25E$TÔdõ$tUôäôDUõDÅ5õ$T4ôä4”ÄUô•õcC#¢Ö–âôæöFRæVÂ6â&WVW7Bà¢2–ÖÖVF–FR&ö÷BDÅ2&V6öæ6–Æ–F–öâv—F†÷WB&Æö6¶–ærF†—2wVæ–6÷&âv÷&¶W"à¢25E$TÔdõ$tUôäôDUôDå3õ$UE%•õdÄ”DDUõcƒ#¢v†VâÖ–â7WÆ–W2†÷7FæÖRÀ¢2fÆ–FFR—Bv–ç7BF†RæöFRw2÷vâDÅ2†÷7BÆ—7B&F†W"F†âÖ–âw266†V@¢27FGW2âöÆFW"6ÆÆW'2Ö’öÖ—BF†R&öG’æB7F–ÆÂ&V6öæ6–ÆRÆÂ6öæf–wW&VB†÷7G2à¤ç÷7B‚"ö’÷c÷FÇ2÷&V6öæ6–ÆR"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦7–æ2FVbFÇ5÷&V6öæ6–ÆR‡&WVW7C¢&WVW7B“ ¢G'“ ¢–ÆöBÒv—B&WVW7Bæ§6öâ‚¢W†6WBW†6WF–öã ¢–ÆöBÒ·Ð¢†÷7BÒ7G"‡–ÆöBævWB‚&†÷7B"’÷"""’ç7G&—‚’æÆ÷vW"‚’ç'7G&—‚"â"’–b—6–ç7Fæ6R‡–ÆöBÂF–7B’VÇ6R" ¢–b†÷7BæB†÷7Bæ÷B–âöæöFU÷6WGF–æw5÷FÇ5ö†÷7G2‚“ ¢&—6R…EEW†6WF–öâƒCÂ$†÷7FæÖR—2æ÷B6öæf–wW&VBöâF†—2æöFR"¢–bU…DU$äÅõ$õ…•ôÔôDS ¢&WGW&â²&ö²#¢G'VRÂ'VWVVB#¢fÇ6RÂ&ÖW76vR#¢$W‡FW&æÂ&÷‡’ÖöFR÷vç2DÅ2'Ð¢ÖævW"ç&WVW7E÷FÇ5÷&V6öæ6–ÆR††÷7BÂf÷&6U÷&WG'“Ö&ööÂ††÷7B’¢&WGW&â°¢&ö²#¢G'VRÀ¢'VWVVB#¢G'VRÀ¢&ÖW76vR#¢$æöFR6W'F–f–6FR&WG'’VWVVB"²‚#²&Wf–÷W24ÔR&6¶öfb6ÆV&VB"–b†÷7BVÇ6R""’À¢&†÷7B#¢†÷7BÀ¢&ÖævVE÷FÇ2#¢ÖævW"çFÇ5÷7FGW2‚’À¢Ð  ¤ævWB‚"ö’÷cö†VÇF‚"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb†VÇF‚‚“ ¢6&–Æ—F–W2Òö†VÇF…ö6&–Æ—F–W2‚¢ƒ#cBÒ6&–Æ—F–W2ævWB‚&ƒ#cB"Â'Væ¶æ÷vâ"¢ƒ#cRÒ6&–Æ—F–W2ævWB‚&ƒ#cR"Â'Væ¶æ÷vâ"¢FVÆ—fW'•ö6÷VçG2ÒöæöFUö6&EöFVÆ—fW'•ö6÷VçG2‚¢7F—fUö6†ææVÇ2Ò–çB†FVÆ—fW'•ö6÷VçG5²&7F—fR%Ò¢7F—fUö6öææV7F–öç2Ò–çB†ÖævW"çF÷FÅö7F—fUö6öææV7F–öç2‚’¢&WGW&â°¢&ö²#¢G'VRÂ'7FGW2#¢&öæÆ–æR"Â'fW'6–öâ#¢dU%4”ôâÂ&†÷7FæÖR#¢÷2çVæÖR‚’ææöFVæÖRÀ¢&ÖWG&–72#¢ÖævW"æÖWG&–72‚’Â&6&–Æ—F–W2#¢²&ƒ#cB#¢ƒ#cBÂ&ƒ#cR#¢ƒ#cWÒÀ¢&7F—fUö6†ææVÇ2#¢7F—fUö6†ææVÇ2À¢'Wö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²'W%Ò’À¢'v—F–æuö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²'v—F–ær%Ò’À¢&F÷våö6†ææVÇ2#¢–çB†FVÆ—fW'•ö6÷VçG5²&F÷vâ%Ò’À¢'f–WvW%÷7FG2#¢²'F÷FÅ÷W6W'2#¢7F—fUö6öææV7F–öç2Â'6†&VE÷'VçF–ÖR#¢'&VF—2"–bæöFU÷&VF—2—2æ÷BæöæRæBæöFU÷&VF—2æf–Æ&ÆRVÇ6R&Æö6Â'ÒÀ¢'æVÅö6öææV7FVB#¢ÖævW"çæVÅö6öææV7FVB‚’À¢&–æFWVæFVçEöÖöFR#¢&ööÂ†ÖævW"æ–æFWVæFVçEöÖöFR’À¢&Æö6Åö6†ææVÅöÆ–Ö—B#¢–çB†ÖævW"æÆö6Åö6†ææVÅöÆ–Ö—B’À¢&Æö6Åö6†ææVÅö6÷VçB#¢öÆö6Åö6†ææVÅö6÷VçB‚’À¢'F÷FÅöÖ…ö6öææV7F–öç2#¢–çB†ÖævW"çF÷FÅöÖ…ö6öææV7F–öç2’À¢&7F—fUö6öææV7F–öç2#¢7F—fUö6öææV7F–öç2À¢&F—&V7E÷W6W'2#¢ÆVâ†ÖævW"çW6W'2’À¢'æVÅ÷W6W'2#¢ÆVâ†ÖævW"çæVÅ÷W6W'2’À¢&æöFU÷æVÅ÷W&Â#¢"÷æVÂ"À¢&Fç5ööæÇ’#¢G'VRÂ&ÆÆ÷vVEö†÷7B#¢ÖævW"çæVÅö†÷7B÷"æöæRÀ¢'æVÅöFç5ööæÇ’#¢G'VRÂ'æVÅö†÷7B#¢ÖævW"çæVÅö†÷7B÷"æöæRÀ¢'æVÅ÷W&Ç2#¢Æ—7B†ÖævW"çæVÅ÷W&Ç2’Â&66W75÷6ÇVr#¢ÖævW"æ66W75÷6ÇVrÀ¢'7G&VÕöFç5ööæÇ’#¢G'VRÂ'7G&VÕö†÷7B#¢ÖævW"ç7G&VÕö†÷7B÷"æöæRÀ¢'7G&VÕ÷W&Ç2#¢Æ—7B†ÖævW"ç7G&VÕ÷W&Ç2’Â'7G&VÕ÷6ÇVr#¢ÖævW"ç7G&VÕ÷6ÇVrÀ¢'7G&VÕ÷÷'B#¢–çB†ÖævW"ç7G&VÕ÷÷'B’Â&6öçG&öÅ÷÷'B#¢–çB„4ôåE$ôÅõõ%B’Â&6öçG&öÅö&6¶VæE÷÷'B#¢–çB„4ôåE$ôÅô$4´TäEõõ%B’Â&æöFUöÖöFR#¢äôDUôÔôDRÀ¢&W‡FW&æÅ÷&÷‡•öÖöFR#¢&ööÂ„U…DU$äÅõ$õ…•ôÔôDR’À¢'V&Æ–5övFWv’#¢ÖævW"çV&Æ–5övFWv•÷7FGW2‚’À¢'&VF—5÷'VçF–ÖR#¢²&Væ&ÆVB#¢äôDUõ$TD•5ôTä$ÄTBÂ&öæÆ–æR#¢&ööÂ†æöFU÷&VF—2—2æ÷BæöæRæBæöFU÷&VF—2æf–Æ&ÆR’Â'W&Â#¢äôDUõ$TD•5õU$Âç'7Æ—B‚$"Â•²Ó×ÒÀ¢&ÖVF–öf7E÷F‚#¢²&WF…ö66†U÷6V6öæG2#¢äôDUôÔTD”ôUD…ô44„Uõ4T4ôäE2Â&†V'F&VE÷GFÅ÷6V6öæG2#¢äôDUôÔTD”ô„T%D$TEõEDÇÒÀ¢'æVÅövFWv—2#¢ÖævW"çæVÅövFWv•÷7FGW2‚’À¢&ÖævVE÷FÇ2#¢ÖævW"çFÇ5÷7FGW2‚’À¢&6â#¢ÖævW"æ6å÷7FGW2‚’À¢Ð  ¤ævWB‚"ö’÷cöÖWG&–72"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbÖWG&–72‚“ ¢&WGW&âÖævW"æÖWG&–72‚  ¤ævWB‚"ö’÷c÷V&Æ–2ÖvFWv’÷7FGW2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbV&Æ–5övFWv•÷7FGW2‚“ ¢&WGW&âÖævW"çV&Æ–5övFWv•÷7FGW2‚  ¦FVb&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W“¢7G"Â¢ÂÆÆ÷uöÖ—76–æs¢&ööÂÒG'VR’ÓâæöæS ¢6fRÒÖævW"ç6fUö¶W’†¶W’¢v—F‚ÖævW"æÆö6³ ¢'VçF–ÖRÒÖævW"æ6†ææVÇ2ævWB‡6fR¢–b'VçF–ÖRæB'VçF–ÖRæ6öæf–ræ6FÆöuö÷væW"ÓÒ&Æö6Â# ¢&—6R…EEW†6WF–öâƒC’Â$æöFRÖÆö6Â6†ææVÂÇ&VG’W6W2F†—2¶W’"¢–bæ÷B'VçF–ÖRæBæ÷BÆÆ÷uöÖ—76–æs ¢&—6R…EEW†6WF–öâƒCBÂ$Ö–â6W'fW"6†ææVÂæ÷Bf÷VæB"  ¤ç÷7B‚"ö’÷cöÖöFR÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7–æ5öæöFUöÖöFR‡–ÆöC¢ÖöFU7–æ5–ÆöB“ ¢G'“ ¢&WGW&âÖævW"ç7–æ5öÖöFR‡–ÆöB¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cö6†ææVÂÖÆöv÷2÷¶f–ÆVæÖWÒ÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7–æ5ö6†ææVÅöÆövõöf–ÆR†f–ÆVæÖS¢7G"Â–ÆöC¢6†ææVÄÆövõ7–æ5–ÆöB“ ¢6fRÒF‚†f–ÆVæÖR’ææÖP¢W‡FVç6–öâÒF‚‡6fR’ç7Vff—‚æÆ÷vW"‚¢7FVÒÒF‚‡6fR’ç7FVÐ¢–b6fRÒf–ÆVæÖR÷"W‡FVç6–öâæ÷B–â²"çær"Â"æ§r"Â"æ§Vr"Â"çvV'"Â"æv–b'Ò÷"öÆö6Å÷6ÇVr‡7FVÒ’Ò7FVÓ ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–B6†ææVÂÆövòf–ÆVæÖR"¢G'“ ¢FFÒ&6ScBæ#cFFV6öFR‡–ÆöBæ6öçFVçEö&6ScBÂfÆ–FFSÕG'VR¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–B6†ææVÂÆövòFF"’g&öÒW†0¢–bæ÷BFF÷"ÆVâ†FF’â"¢#B¢#C ¢&—6R…EEW†6WF–öâƒCÂ$6†ææVÂÆövò×W7B&R&WGvVVâ'—FRæB"Ô""¢fÆ–BÒ€¢†W‡FVç6–öâÓÒ"çær"æBFFç7F'G7v—F‚†"%Çƒƒ•äuÇ%ÆåÇƒÆâ"’¢÷"†W‡FVç6–öâ–â²"æ§r"Â"æ§Vr'ÒæBFFç7F'G7v—F‚†"%Ç†feÇ†C…Ç†fb"’¢÷"†W‡FVç6–öâÓÒ"çvV'"æBÆVâ†FF’ãÒ"æBFF³£EÒÓÒ"%$”db"æBFF³ƒ£%ÒÓÒ"%tT%"¢÷"†W‡FVç6–öâÓÒ"æv–b"æBFFç7F'G7v—F‚‚†"$t”cƒv"Â"$t”cƒ–"’’¢¢–bæ÷BfÆ–C ¢&—6R…EEW†6WF–öâƒCÂ$6†ææVÂÆövò6öçFVçBFöW2æ÷BÖF6‚—G2W‡FVç6–öâ"¢4„ääTÅôÄôtõõ$ôõBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢F&vWBÒ4„ääTÅôÄôtõõ$ôõBò6fP¢FV×÷&'’Ò4„ääTÅôÄôtõõ$ôõBòb"ç·6fWÒç·6V7&WG2çFö¶Våö†W‚ƒB—ÒçF× ¢FV×÷&'’çw&—FUö'—FW2†FF¢÷2æ6†ÖöB‡FV×÷&'’ÂócCB¢FV×÷&'’ç&WÆ6R‡F&vWB¢f÷"÷F†W%öW‡FVç6–öâ–â‚"çær"Â"æ§r"Â"æ§Vr"Â"çvV'"Â"æv–b"“ ¢÷F†W"Ò4„ääTÅôÄôtõõ$ôõBòb'·7FV××¶÷F†W%öW‡FVç6–öçÒ ¢–b÷F†W"ÒF&vWC ¢÷F†W"çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&WGW&â²&ö²#¢G'VRÂ&Æövõ÷W&Â#¢b"ö6†ææVÂÖÆöv÷2÷·6fWÒ"Â&'—FW2#¢ÆVâ†FF—Ð  ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ô%$äEô54UEõ5”ä5õcSƒ ¦FVbö'&æE÷7–æ5÷F&vWB†'&æEö–C¢7G"Â¶–æC¢7G"Âf–ÆVæÖS¢7G"Ò""’ÓâFƒ ¢6fUö–BÒ&Rç7V"‡"%µæ×£Ó•òÕÒ²"Â""Â7G"†'&æEö–B÷"""’ç7G&—‚’æÆ÷vW"‚’•³£CÐ¢–bæ÷B6fUö–C ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–BvV"Æ–W"'&æB–B"¢tT%Ä”U%ô%$äEõ$ôõBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢–b¶–æBÓÒ&F÷væÆöB# ¢&WGW&âtT%Ä”U%ô%$äEõ$ôõBòb'·6fUö–GÒÖF÷væÆöBæ&–â ¢7Vff—‚ÒF‚†f–ÆVæÖR’ç7Vff—‚æÆ÷vW"‚¢ÆÆ÷vVBÒ²&Æövò#¢²"çær"Â"æ§r"Â"æ§Vr"Â"çvV'"Â"æv–b'ÒÂ&ff–6öâ#¢²"æ–6ò"Â"çær"Â"æ§r"Â"æ§Vr"Â"çvV'"Â"æv–b'×Ð¢–b7Vff—‚æ÷B–âÆÆ÷vVBævWB†¶–æBÂ6WB‚’“ ¢&—6R…EEW†6WF–öâƒCÂb$–çfÆ–BvV"Æ–W"'&æB¶¶–æGÒf–ÆR"¢&WGW&âtT%Ä”U%ô%$äEõ$ôõBòb'·6fUö–GÒ×¶¶–æG×·7Vff—‡Ò   ¤ç÷7B‚"ö’÷c÷vV'Æ–W"Ö'&æB÷¶'&æEö–GÒ÷¶¶–æGÒ÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦7–æ2FVb7–æ5÷vV'Æ–W%ö'&æEö76WB†'&æEö–C¢7G"Â¶–æC¢7G"Â&WVW7C¢&WVW7BÂ…÷7G&VÖf÷&vUöf–ÆVæÖS¢7G"Ò†VFW"†FVfVÇCÒ""’“ ¢–b¶–æBæ÷B–â²&Æövò"Â&ff–6öâ"Â&F÷væÆöB'Ó ¢&—6R…EEW†6WF–öâƒCB¢F—7Æ•öæÖRÒF‚‡W&ÆÆ–"ç'6RçVçV÷FR‡7G"‡…÷7G&VÖf÷&vUöf–ÆVæÖR÷"""’’ç&WÆ6R‚%ÅÂ"Â"ò"’’ææÖRç7G&—‚¢FFÒv—B&WVW7Bæ&öG’‚¢Æ–Ö—BÒ¢#B¢#B–b¶–æBÓÒ&F÷væÆöB"VÇ6R"¢#B¢#@¢–bæ÷BFF÷"ÆVâ†FF’âÆ–Ö—C ¢&—6R…EEW†6WF–öâƒCÂb%vV"Æ–W"'&æB¶¶–æGÒf–ÆR6—¦R—2–çfÆ–B"¢F&vWBÒö'&æE÷7–æ5÷F&vWB†'&æEö–BÂ¶–æBÂF—7Æ•öæÖR¢–b¶–æBÒ&F÷væÆöB# ¢W‡BÒF&vWBç7Vff—‚æÆ÷vW"‚¢fÆ–BÒ€¢†W‡BÓÒ"æ–6ò"æBFFç7F'G7v—F‚†"%ÇƒÇƒÇƒÇƒ"’¢÷"†W‡BÓÒ"çær"æBFFç7F'G7v—F‚†"%Çƒƒ•äuÇ%ÆåÇƒÆâ"’¢÷"†W‡B–â²"æ§r"Â"æ§Vr'ÒæBFFç7F'G7v—F‚†"%Ç†feÇ†C…Ç†fb"’¢÷"†W‡BÓÒ"çvV'"æBÆVâ†FF’ãÒ"æBFF³£EÒÓÒ"%$”db"æBFF³ƒ£%ÒÓÒ"%tT%"¢÷"†W‡BÓÒ"æv–b"æBFFç7F'G7v—F‚‚†"$t”cƒv"Â"$t”cƒ–"’’¢¢–bæ÷BfÆ–C ¢&—6R…EEW†6WF–öâƒCÂb%vV"Æ–W"'&æB¶¶–æGÒ6öçFVçBFöW2æ÷BÖF6‚—G2W‡FVç6–öâ"¢&Vf—‚Òb'·&Rç7V"‡"uµæ×£Ó•òÕÒ²rÂrrÂ'&æEö–BæÆ÷vW"‚’•³£C×Ò×¶¶–æGÒ ¢f÷"÷F†W"–âtT%Ä”U%ô%$äEõ$ôõBævÆö"‡&Vf—‚²"â¢"“ ¢–b÷F†W"ÒF&vWC ¢÷F†W"çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢FV×÷&'’ÒF&vWBçv—F…öæÖR†b"ç·F&vWBææÖWÒç·6V7&WG2çFö¶Våö†W‚ƒB—ÒçF×"¢FV×÷&'’çw&—FUö'—FW2†FF¢÷2æ6†ÖöB‡FV×÷&'’ÂócCB¢FV×÷&'’ç&WÆ6R‡F&vWB¢&WGW&â²&ö²#¢G'VRÂ&f–ÆVæÖR#¢F—7Æ•öæÖR÷"F&vWBææÖRÂ&'—FW2#¢ÆVâ†FF’Â&76WB#¢F&vWBææÖWÐ  ¤æFVÆWFR‚"ö’÷c÷vV'Æ–W"Ö'&æB÷¶'&æEö–GÒ÷¶¶–æGÒ÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb&VÖ÷fU÷vV'Æ–W%ö'&æEö76WB†'&æEö–C¢7G"Â¶–æC¢7G"“ ¢6fUö–BÒ&Rç7V"‡"%µæ×£Ó•òÕÒ²"Â""Â7G"†'&æEö–B÷"""’ç7G&—‚’æÆ÷vW"‚’•³£CÐ¢–bæ÷B6fUö–B÷"¶–æBæ÷B–â²&Æövò"Â&ff–6öâ"Â&F÷væÆöB'Ó ¢&—6R…EEW†6WF–öâƒCB¢–b¶–æBÓÒ&F÷væÆöB# ¢…tT%Ä”U%ô%$äEõ$ôõBòb'·6fUö–GÒÖF÷væÆöBæ&–â"’çVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢VÇ6S ¢f÷"F&vWB–âtT%Ä”U%ô%$äEõ$ôõBævÆö"†b'·6fUö–GÒ×¶¶–æGÒâ¢"“ ¢F&vWBçVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢&WGW&â²&ö²#¢G'VRÂ'&VÖ÷fVB#¢G'VWÐ  ¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôDõtäÄôEõ5”ä5õc3CS ¤ç÷7B‚"ö’÷c÷vV'Æ–W"ÖF÷væÆöB÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦7–æ2FVb7–æ5÷vV'Æ–W%öF÷væÆöEöf–ÆR€¢&WVW7C¢&WVW7BÀ¢…÷7G&VÖf÷&vUöf–ÆVæÖS¢7G"Ò†VFW"†FVfVÇCÒ""’À¢“ ¢F—7Æ•öæÖRÒF‚‡W&ÆÆ–"ç'6RçVçV÷FR‡7G"‡…÷7G&VÖf÷&vUöf–ÆVæÖR÷"""’’ç&WÆ6R‚%ÅÂ"Â"ò"’’ææÖRç7G&—‚¢–bæ÷BF—7Æ•öæÖR÷"F—7Æ•öæÖR–â²"â"Â"ââ'Ò÷"ÆVâ†F—7Æ•öæÖR’âƒ ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–BvV"Æ–W"F÷væÆöBf–ÆVæÖR"¢FFÒv—B&WVW7Bæ&öG’‚¢–bæ÷BFF÷"ÆVâ†FF’â¢#B¢#C ¢&—6R…EEW†6WF–öâƒCÂ%vV"Æ–W"F÷væÆöB×W7B&R&WGvVVâ'—FRæBÔ""¢tT%Ä”U%ôDõtäÄôEôd”ÄRç&VçBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢FV×÷&'’ÒtT%Ä”U%ôDõtäÄôEôd”ÄRçv—F…öæÖR†b"çµtT%Ä”U%ôDõtäÄôEôd”ÄRææÖWÒç·6V7&WG2çFö¶Våö†W‚ƒB—ÒçF×"¢FV×÷&'’çw&—FUö'—FW2†FF¢÷2æ6†ÖöB‡FV×÷&'’ÂócCB¢FV×÷&'’ç&WÆ6R…tT%Ä”U%ôDõtäÄôEôd”ÄR¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôDõtäÄôEô44U55õD4…õc#s ¢2Ö–â6ÆÇ2F†—2–ÖÖVF–FVÇ’gFW"WF†÷&—FF—fR66W72ö'&æB7–æ2âæ÷F†W ¢26öçG&öÂv÷&¶W"6â&V6V—fRF†—2&WVW7Bv—F‚7FÆRÆ–6W2ö'&æG2Â6òF6€¢2öæÇ’F†R÷væVBF÷væÆöBf–VÆB–ç7FVBöbgVÆÂ×6f–ær—G27FÆR6æ6†÷Bà¢ÖævW"çF6…ö66W75öf–ÆR‡²'vV'Æ–W%öF÷væÆöEöæÖR#¢F—7Æ•öæÖWÒ¢ÖævW"æÆöEö66W72‚¢&WGW&â²&ö²#¢G'VRÂ&f–ÆVæÖR#¢F—7Æ•öæÖRÂ&'—FW2#¢ÆVâ†FF—Ð  ¤æFVÆWFR‚"ö’÷c÷vV'Æ–W"ÖF÷væÆöB÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb&VÖ÷fU÷vV'Æ–W%öF÷væÆöEöf–ÆR‚“ ¢tT%Ä”U%ôDõtäÄôEôd”ÄRçVæÆ–æ²†Ö—76–æuöö³ÕG'VR¢25E$TÔdõ$tUôäôDUõtT%Ä”U%ôDõtäÄôEô44U55õD4…õc#s ¢ÖævW"çF6…ö66W75öf–ÆR‡²'vV'Æ–W%öF÷væÆöEöæÖR#¢"'Ò¢ÖævW"æÆöEö66W72‚¢&WGW&â²&ö²#¢G'VRÂ'&VÖ÷fVB#¢G'VWÐ  ¤ç÷7B‚"ö’÷cöæöFRÖÆövò÷¶f–ÆVæÖWÒ÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7–æ5öæöFUöÆövõöf–ÆR†f–ÆVæÖS¢7G"Â–ÆöC¢6†ææVÄÆövõ7–æ5–ÆöB“ ¢6fRÒF‚†f–ÆVæÖR’ææÖP¢W‡FVç6–öâÒF‚‡6fR’ç7Vff—‚æÆ÷vW"‚¢–b6fRÒf–ÆVæÖR÷"W‡FVç6–öâæ÷B–â²"çær"Â"æ§r"Â"æ§Vr"Â"çvV'"Â"æv–b'Ó ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–BæöFRÆövòf–ÆVæÖR"¢G'“ ¢FFÒ&6ScBæ#cFFV6öFR‡–ÆöBæ6öçFVçEö&6ScBÂfÆ–FFSÕG'VR¢W†6WB…fÇVTW'&÷"ÂG—TW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ$–çfÆ–BæöFRÆövòFF"’g&öÒW†0¢–bæ÷BFF÷"ÆVâ†FF’â"¢#B¢#C ¢&—6R…EEW†6WF–öâƒCÂ$æöFRÆövò×W7B&R&WGvVVâ'—FRæB"Ô""¢fÆ–BÒ€¢†W‡FVç6–öâÓÒ"çær"æBFFç7F'G7v—F‚†"%Çƒƒ•äuÇ%ÆåÇƒÆâ"’¢÷"†W‡FVç6–öâ–â²"æ§r"Â"æ§Vr'ÒæBFFç7F'G7v—F‚†"%Ç†feÇ†C…Ç†fb"’¢÷"†W‡FVç6–öâÓÒ"çvV'"æBÆVâ†FF’ãÒ"æBFF³£EÒÓÒ"%$”db"æBFF³ƒ£%ÒÓÒ"%tT%"¢÷"†W‡FVç6–öâÓÒ"æv–b"æBFFç7F'G7v—F‚‚†"$t”cƒv"Â"$t”cƒ–"’’¢¢–bæ÷BfÆ–C ¢&—6R…EEW†6WF–öâƒCÂ$æöFRÆövò6öçFVçBFöW2æ÷BÖF6‚—G2W‡FVç6–öâ"¢äôDUôÄôtõõ$ôõBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢F&vWBÒäôDUôÄôtõõ$ôõBò6fP¢FV×÷&'’ÒäôDUôÄôtõõ$ôõBòb"ç·6fWÒç·6V7&WG2çFö¶Våö†W‚ƒB—ÒçF× ¢FV×÷&'’çw&—FUö'—FW2†FF¢÷2æ6†ÖöB‡FV×÷&'’ÂócCB¢FV×÷&'’ç&WÆ6R‡F&vWB¢&WGW&â²&ö²#¢G'VRÂ&Æövõ÷W&Â#¢b"öæöFRÖÆöv÷2÷·6fWÒ"Â&'—FW2#¢ÆVâ†FF—Ð  ¢25E$TÔdõ$tUôäôDUô%TÄµô4ôäd”uô•õc#s ¤ç÷7B‚"ö’÷cö6†ææVÇ2ö'VÆ²×7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7–æ5ö6†ææVÅö6öæf–w5ö'VÆ²‡–ÆöC¢'VÆ´6†ææVÅ7–æ5–ÆöB“ ¢6öæf–w3¢Æ—7E´6†ææVÄ6öæf–uÒÒµÐ¢f÷"6öæf–r–â–ÆöBæ6†ææVÇ3 ¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†6öæf–ræ¶W’¢6öæf–w2æVæB†6öæf–ræÖöFVÅö6÷’‡WFFS×²&6FÆöuö÷væW"#¢&Ö–â'Ò’¢G'“ ¢&WGW&âÖævW"ç7–æ5ö6†ææVÇ5ö'VÆ²†6öæf–w2¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò÷7–æ2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7–æ5ö6†ææVÅö6öæf–r†¶W“¢7G"Â6öæf–s¢6†ææVÄ6öæf–rÂ&W7F'E÷'Vææ–æs¢&ööÂÒfÇ6R“ ¢25E$TÔdõ$tUôäôDUô%TÄµõ$ôd”ÄUõ%Tää”äuõ$U5D%Eõc#S ¢2Ö–âw2æ÷&ÖÂ6FÆöwVRö6öæf–r7–æ27F—2&W7F'BÖg&VRâ'VÆ²Væ6öF–æp¢2&öf–ÆRWFFW2Ö’&WVW7B&W7F'Böb&ö6W72F†B—2Ç&VG¢2'Vææ–æs²ÖævW"ç7–æ5ö6†ææVÂ&W6W'fW27F÷VBFW6—&VB×7FFRà¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W’¢6öæf–rÒ6öæf–ræÖöFVÅö6÷’‡WFFS×²&6FÆöuö÷væW"#¢&Ö–â'Ò¢G'“ ¢&WGW&âÖævW"ç7–æ5ö6†ææVÂ†¶W’Â6öæf–rÂ&W7F'E÷'Vææ–æsÖ&ööÂ‡&W7F'E÷'Vææ–ær’¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò÷7F'B"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7F'Eö6†ææVÂ†¶W“¢7G"Â6öæf–s¢6†ææVÄ6öæf–r“ ¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W’¢6öæf–rÒ6öæf–ræÖöFVÅö6÷’‡WFFS×²&6FÆöuö÷væW"#¢&Ö–â'Ò¢G'“ ¢&WGW&âÖævW"ç7F'B†¶W’Â6öæf–r¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò÷7F÷"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb7F÷ö6†ææVÂ†¶W“¢7G"“ ¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W’ÂÆÆ÷uöÖ—76–æsÔfÇ6R¢G'“ ¢&WGW&âÖævW"ç7F÷†¶W’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò÷&W7F'B"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb&W7F'Eö6†ææVÂ†¶W“¢7G"Â6öæf–s¢6†ææVÄ6öæf–r“ ¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W’¢6öæf–rÒ6öæf–ræÖöFVÅö6÷’‡WFFS×²&6FÆöuö÷væW"#¢&Ö–â'Ò¢G'“ ¢&WGW&âÖævW"ç&W7F'B†¶W’Â6öæf–r¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ævWB‚"ö’÷cö6†ææVÂ×7FGW6W2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb6†ææVÅ÷7FGW6W2‚“ ¢ÖævW"ç&VÆöEö6†ææVÅö6FÆöuö–eö6†ævVB‚¢v—F‚ÖævW"æÆö6³ ¢¶W—2ÒÆ—7B†ÖævW"æ6†ææVÇ2¢W'&÷%öÖÒöæöFU÷&V6VçEö6†ææVÅöW'&÷'2‚¢6†ææVÇ3¢F–7E·7G"ÂF–7E·7G"Âç•ÕÒÒ·Ð¢7WW'f—6÷%÷&÷w3¢F–7E·7G"ÂF–7E·7G"Âç•ÕÒÒ·Ð¢–bæ÷BäôDUô4„ääTÅôõtäU"æBäôDUôDTD”4DTEô4„ääTÅõ5UU%d•4õ# ¢G'“ ¢7WW'f—6÷%÷&÷w2ÒÖævW"å÷7WW'f—6÷%÷7FGW6W2†f÷&6SÕG'VR¢W†6WB'VçF–ÖTW'&÷# ¢7WW'f—6÷%÷&÷w2Ò·Ð¢f÷"¶W’–â¶W—3 ¢—FVÒÒF–7B‡7WW'f—6÷%÷&÷w2ævWB†¶W’’÷"ÖævW"ç7FGW2†¶W’’¢&V6VçBÒW'&÷%öÖævWB†¶W’ÂµÒ¢—FVÕ²&W'&÷%ö6÷VçB%ÒÒÆVâ‡&V6VçB¢—FVÕ²'&W7F'Eö6÷VçB%ÒÒ7VÒ€¢f÷"WfVçB–â&V6Vç@¢–b'&W7F'B"–â7G"†WfVçBævWB‚&ÖW76vR"’÷"""’æÆ÷vW"‚¢÷"&ff×VrW†—FVB"–â7G"†WfVçBævWB‚&ÖW76vR"’÷"""’æÆ÷vW"‚¢¢6†ææVÇ5¶¶W•ÒÒ—FVÐ¢&WGW&â²&6†ææVÇ2#¢6†ææVÇ7Ð  ¤ævWB‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò÷7FGW2"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb6†ææVÅ÷7FGW2†¶W“¢7G"“ ¢G'“ ¢–ÆöBÒF–7B†ÖævW"ç7FGW2†¶W’’¢25E$TÔdõ$tUõ$UÄ”4ôäôDUô4„ääTÅõUD”ÔUõcc5#s ¢2Ö–âw2öâÖFVÖæB7G&VÒ–æfòÆ—fR6†V6²6â6†÷ræöFR†÷7BWF–ÖRæ@¢2F†—26†ææVÂw2df×VrWF–ÖRg&öÒöæRWF†VçF–6FVB&WVW7Bà¢G'“ ¢–ÆöE²&æöFU÷WF–ÖU÷6V6öæG2%ÒÒ–çB†fÆöB…F‚‚"÷&ö2÷WF–ÖR"’ç&VE÷FW‡B‚’ç7Æ—B‚•³Ò’¢W†6WB„õ4W'&÷"ÂfÇVTW'&÷"Â–æFW„W'&÷"“ ¢–ÆöE²&æöFU÷WF–ÖU÷6V6öæG2%ÒÒ–çB†ÖævW"æÖWG&–72‚’ævWB‚'WF–ÖU÷6V6öæG2"’÷"¢&WGW&â–Æö@¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤æFVÆWFR‚"ö’÷cö6†ææVÇ2÷¶¶W—Ò"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVbFVÆWFUö6†ææVÂ†¶W“¢7G"“ ¢&÷FV7EöÆö6Åö6†ææVÅög&öÕöÖ–åö’†¶W’ÂÆÆ÷uöÖ—76–æsÕG'VR¢G'“ ¢ÖævW"æFVÆWFR†¶W’¢&WGW&â²&ö²#¢G'VWÐ¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ævWB‚"ö’÷cö†Ç2÷¶¶W—Ò÷¶f–ÆVæÖWÒ"ÂFWVæFVæ6–W3Õ´FWVæG2†WF†÷&—¦VB•Ò¦FVb†Ç5öf–ÆR†¶W“¢7G"Âf–ÆVæÖS¢7G"“ ¢G'“ ¢¶W’ÒÖævW"ç6fUö¶W’†¶W’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCB’g&öÒW†0¢6fRÒF‚†f–ÆVæÖR’ææÖP¢–b6fRÒf–ÆVæÖR÷"æ÷B6fRæVæG7v—F‚‚‚"æÓ7S‚"Â"çG2"Â"æÓG2"Â"æ2"Â"æ×2"Â"æ¶W’"’“ ¢&—6R…EEW†6WF–öâƒCB¢F‚Ò„Å5õ$ôõBò¶W’ò6fP¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&—6R…EEW†6WF–öâƒCB¢ÖVF–Ò²"æÓ7S‚#¢&Æ–6F–öâ÷fæBæÆRæ×VwW&Â"Â"çG2#¢'f–FVòö×'B"Â"æÓG2#¢'f–FVòö—6òç6VvÖVçB"Â"æ2#¢&VF–òö2"Â"æ×2#¢&VF–òö×Vr"Â"æ¶W’#¢&Æ–6F–öâöö7FWB×7G&VÒ'Ð¢&WGW&âf–ÆU&W7öç6R‡F‚ÂÖVF–÷G—SÖÖVF–ævWB‡F‚ç7Vff—‚æÆ÷vW"‚’Â&Æ–6F–öâöö7FWB×7G&VÒ"’Â†VFW'3×²$66†RÔ6öçG&öÂ#¢&æò×7F÷&R"Â$66W72Ô6öçG&öÂÔÆÆ÷rÔ÷&–v–â#¢"¢'Ò ¢2cããC‚Væ–f–VB'VçF–ÖRv&æ–æröÆör6÷W&6S¢7FÆRÆ7EöW'&÷"—2–væ÷&VBgFW"'VçF–ÖR&V6÷fW'’à ¢2cããC’W'6—7FVçB6†ææVÂ†—7F÷'’66W73¢7G&VÒÆöw26â÷Vâ–æFWVæFVçFÇ’öbv&æ–ær7FFRà ¢2cããƒ"æF—fR÷'BÓƒæVÂô’FVfVÇ@¢2cããƒ2Væ–f–VBæVÂô’²Æ–Æ—7BôÆ—7FVæW"æBF—&V7BæVÂU$À ¢2cãã“S¢W‡÷'BW&R4t’w&W"÷WG6–FR7F&ÆWGFRw2W†6WF–öâæ@¢2&6T…EEÖ–FFÆWv&R7F6²âF†—2&W6W'fW2Wf–6÷&âw2&÷VæB6VæB6ÆÆ&ÆR6òà¢2–çfÆ–BV&Æ–2æöFRWF†÷&—G’6â&R6Æ÷6VBv—F†÷WBVÖ—GF–ærâ…EE7FGW2à¦Ò6–ÆVçD–çfÆ–DæöFT66W74Ö–FFÆWv&R†  ¢25E$TÔdõ$tUôäôDUôDTD”4DTEô4„ääTÅõ5UU%d•4õ%ôTåE%•ô”åEõcP¦–bõöæÖUõòÓÒ%õöÖ–åõò"æB"ÒÖ6†ææVÂ×7WW'f—6÷""–â7—2æ&wc ¢&—6R7—7FVÔW†—B…÷'Våö6†ææVÅ÷7WW'f—6÷"‚’ 