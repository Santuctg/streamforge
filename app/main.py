from __future__ import annotations

import asyncio
import hashlib
import base64
import ipaddress
import json
import math
import hmac
import os
import re
import urllib.error
import urllib.request
import urllib.parse
from urllib.parse import quote_plus, urlencode, urlsplit
import secrets
import subprocess
import threading
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import and_, case, delete, func, or_, select, text
from sqlalchemy.orm import Session, object_session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from .auth import current_admin, hash_password, verify_password
from .config import settings
from .db import Base, SessionLocal, engine, ensure_runtime_schema, get_db
from .ffmpeg import stream_manager
from .node_manager import NodeError, ensure_local_node, node_controller, normalize_node_url, effective_node_url, channel_input_urls, channel_source_program_ids, set_node_maintenance
from .load_balancer import NodeSelectionError, choose_playback_node, normalize_prefixes, user_allowed_nodes, playback_candidate_nodes, validate_routed_node, pinned_node_for_ip, node_ip_whitelist_allows, node_playback_access_allows
from .importer import ImportItem, detect_and_parse
from .models import AdminUser, AppSetting, Channel, ChannelCategory, Node, Role, StreamUser, LogEntry, PlaylistProfile, channel_nodes, channel_category_links, playlist_profile_channels as playlist_profile_channel_links, user_channels as user_channel_links
from .permissions import (
    ALL_PERMISSION_KEYS,
    PERMISSION_GROUPS,
    SUPERUSER_PERMISSION,
    decode_permissions,
    encode_permissions,
    has_permission,
    role_permission_set,
)
from .stream_info import probe_stream
from .system_metrics import available_network_interfaces, system_metrics
from .metrics_history import MetricsHistory
from .backup_manager import load_targets as load_backup_targets, add_target as add_backup_target, delete_target as delete_backup_target, run_target as run_backup_target, run_due_targets as run_due_backup_targets, google_drive_status as backup_google_drive_status, test_google_drive_connection as test_backup_google_drive_connection, save_google_oauth_credentials, load_google_oauth_credentials, save_google_drive_token, test_destination as test_backup_destination, update_target as update_backup_target, list_local_backup_archives, resolve_local_backup_archive, list_target_backup_archives, fetch_target_backup_archive, delete_target_backup_archive, local_backup_settings, save_local_backup_settings, run_local_backup
from .ssh_installer import SSHInstallError, install_node_over_ssh, uninstall_node_over_ssh, run_node_admin_command_over_ssh
from .viewer_tracking import viewer_tracker
from .redis_state import redis_state
from .secrets_store import encrypt_secret, decrypt_secret
from .audit_log import log_event, clear_logs, prune_logs
from .access_control import evaluate_access, normalize_ip_rules, normalize_asn_rules, ip_matches, client_ip, asn_database_status, geo_details, clear_geo_cache
from .playback_keys import issue_playback_key, issue_playback_keys, verify_playback_key
from .geoip_config import load_geoip_settings, save_geoip_settings, run_maxmind_update
from .playlist_ordering import (
    channel_category_key, category_position_map, ordered_channels as apply_playlist_order,
    parse_category_order, parse_channel_order,
)
from .sync_queue import (
    clear_node_sync_pending,
    mark_node_channel_logo_pending,
    mark_node_channel_sync_pending,
    mark_node_sync_pending,
    node_channel_logo_pending_ids,
    node_channel_sync_pending_ids,
    node_sync_pending_reason,
)
from .source_resolver import youtube_cookie_configured, youtube_cookie_path

BASE_DIR = Path(__file__).resolve().parent
APP_ROOT = BASE_DIR.parent
APP_VERSION = (APP_ROOT / "VERSION").read_text(encoding="utf-8").strip() if (APP_ROOT / "VERSION").exists() else "dev"
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# STREAMFORGE_RESTORED_ASSET_CACHE_BUST_V3028:
# A browser can retain a 404 for a logo URL requested before backup restore.
# Version only StreamForge-owned asset URLs so restored bytes are fetched
# immediately; remote operator-provided logo URLs remain untouched.
def versioned_asset_url(value: object) -> str:
    raw = str(value or "").strip()
    if raw.startswith(("/channel-logos/", "/node-logos/", "/branding-assets/")):
        separator = "&" if "?" in raw else "?"
        return f"{raw}{separator}sfv={APP_VERSION}"
    return raw


TEMPLATES.env.filters["versioned_asset"] = versioned_asset_url

try:
    DISPLAY_TZ = ZoneInfo(settings.timezone_name)
except Exception:
    DISPLAY_TZ = timezone.utc

_NODE_UPDATE_LOCK = threading.RLock()
_NODE_UPDATE_JOBS: dict[int, dict[str, object]] = {}
# STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118:
# A channel form save must commit the requested configuration and return to
# the browser immediately. Remote Node/config/logo/direct-user propagation is
# bounded network work and is deliberately serialized outside the request path.
# Reading the channel again by id inside the worker means queued jobs always
# propagate committed database state instead of holding request-scoped objects.
_CHANNEL_SAVE_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sf-channel-save-sync")
_BULK_NODE_ASSIGN_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sf-bulk-node-sync")
# STREAMFORGE_AUTO_INSTALL_LIVE_WORKFLOW_V306: the synchronous installer
# publishes real SSH/package/install phases while the browser keeps the form
# request open and polls a separate authenticated status endpoint.
_NODE_INSTALL_LOCK = threading.RLock()
_NODE_INSTALL_JOBS: dict[str, dict[str, object]] = {}

# Main/Local configured-host enforcement cache. The policy is intentionally
# short-lived so an administrator can recover immediately after changing the
# URL aliases, while avoiding a SQLite read for every playlist segment.
_MAIN_HOST_POLICY_LOCK = threading.RLock()
_MAIN_HOST_POLICY_CACHE: dict[str, object] = {
    "loaded_at": 0.0,
    "enabled": False,
    "aliases": tuple(),
    "panel_ip_whitelist": "",
    "panel_ip_blacklist": "",
    "panel_asn_whitelist": "",
    "panel_asn_blacklist": "",
    "ip_whitelist": "",
    "ip_blacklist": "",
    "asn_whitelist": "",
    "asn_blacklist": "",
}
_MAIN_HOST_POLICY_TTL_SECONDS = 30.0  # STREAMFORGE_MAIN_ACCESS_CACHE_V34

# v1.11.94: Remote Node resource metrics are cached from authenticated
# heartbeats and lightweight status replies. This lets the Nodes page render
# useful CPU/memory/network values before browser polling starts and provides
# a fallback when a public alias request is temporarily unavailable.
_NODE_METRICS_CACHE: dict[int, tuple[float, dict[str, float]]] = {}
_NODE_METRICS_LOCK = threading.RLock()
_NODE_METRICS_TTL_SECONDS = 90.0

# STREAMFORGE_MAIN_STATUS_HOT_CACHE_V34:
# v11.14 supersedes the historical-log hot cache by removing that scan from
# the polling endpoint entirely; the compatibility marker is retained.

# STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6:
# Keep remote Node card delivery counts and active connection totals alongside
# heartbeat metrics.  The Nodes page can then render true per-Node state without
# turning every browser refresh into another Remote Node HTTP probe.
_NODE_LIVE_SUMMARY_CACHE: dict[int, tuple[float, dict[str, int]]] = {}
_NODE_LIVE_SUMMARY_LOCK = threading.RLock()
_NODE_LIVE_SUMMARY_TTL_SECONDS = 90.0

def _finite_metric(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, number)


def normalize_node_card_metrics(payload: object) -> dict[str, float]:
    """Normalize Main and Node Agent metric schemas for card rendering."""
    source = payload if isinstance(payload, dict) else {}
    cpu = source.get("cpu") if isinstance(source.get("cpu"), dict) else {}
    memory = source.get("memory") if isinstance(source.get("memory"), dict) else {}
    network = source.get("network") if isinstance(source.get("network"), dict) else {}

    cpu_percent = _finite_metric(source.get("cpu_percent"))
    if cpu_percent is None:
        cpu_percent = _finite_metric(cpu.get("percent"))
    memory_percent = _finite_metric(source.get("memory_percent"))
    if memory_percent is None:
        memory_percent = _finite_metric(memory.get("percent"))

    download_mbps = _finite_metric(source.get("download_mbps"))
    if download_mbps is None:
        download_mbps = _finite_metric(source.get("network_download_mbps"))
    if download_mbps is None:
        rx_bits = _finite_metric(network.get("rx_bits_per_second"))
        if rx_bits is not None:
            download_mbps = rx_bits / 1_000_000.0
    upload_mbps = _finite_metric(source.get("upload_mbps"))
    if upload_mbps is None:
        upload_mbps = _finite_metric(source.get("network_upload_mbps"))
    if upload_mbps is None:
        tx_bits = _finite_metric(network.get("tx_bits_per_second"))
        if tx_bits is not None:
            upload_mbps = tx_bits / 1_000_000.0
    uptime_seconds = _finite_metric(source.get("uptime_seconds"))

    result: dict[str, float] = {}
    if cpu_percent is not None:
        result["cpu_percent"] = min(100.0, cpu_percent)
    if memory_percent is not None:
        result["memory_percent"] = min(100.0, memory_percent)
    if download_mbps is not None:
        result["download_mbps"] = download_mbps
    if upload_mbps is not None:
        result["upload_mbps"] = upload_mbps
    if uptime_seconds is not None:
        result["uptime_seconds"] = float(int(uptime_seconds))
    return result


def cache_node_metrics(node_id: int, payload: object) -> dict[str, float]:
    normalized = normalize_node_card_metrics(payload)
    if normalized:
        with _NODE_METRICS_LOCK:
            _NODE_METRICS_CACHE[int(node_id)] = (time.monotonic(), dict(normalized))
    return normalized


def cached_node_metrics(node_id: int) -> dict[str, float]:
    with _NODE_METRICS_LOCK:
        cached = _NODE_METRICS_CACHE.get(int(node_id))
        if not cached:
            return {}
        if time.monotonic() - cached[0] > _NODE_METRICS_TTL_SECONDS:
            _NODE_METRICS_CACHE.pop(int(node_id), None)
            return {}
        return dict(cached[1])


def cache_node_live_summary(node_id: int, payload: object) -> dict[str, int]:
    source = payload if isinstance(payload, dict) else {}
    result: dict[str, int] = {}
    for key in ("up", "waiting", "down", "active_connections"):
        value = source.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            result[key] = max(0, int(float(value)))
        except (TypeError, ValueError):
            continue
    if result:
        with _NODE_LIVE_SUMMARY_LOCK:
            _NODE_LIVE_SUMMARY_CACHE[int(node_id)] = (time.monotonic(), dict(result))
    return result


def cached_node_live_summary(node_id: int) -> dict[str, int]:
    with _NODE_LIVE_SUMMARY_LOCK:
        cached = _NODE_LIVE_SUMMARY_CACHE.get(int(node_id))
        if not cached:
            return {}
        if time.monotonic() - cached[0] > _NODE_LIVE_SUMMARY_TTL_SECONDS:
            _NODE_LIVE_SUMMARY_CACHE.pop(int(node_id), None)
            return {}
        return dict(cached[1])


def source_endpoint(value: object) -> str:
    """Return the primary source hostname without credentials or port."""
    raw = str(value or "").strip().splitlines()[0].strip() if str(value or "").strip() else ""
    if not raw:
        return "Source unavailable"
    raw = raw.split("|", 1)[0].strip()
    candidate = raw if "://" in raw else f"//{raw}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
        host = parsed.hostname
    except ValueError:
        host = None
    if host:
        return host
    cleaned = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    cleaned = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    if cleaned.startswith("[") and "]" in cleaned:
        return cleaned[1:cleaned.index("]")]
    if cleaned.count(":") == 1:
        cleaned = cleaned.split(":", 1)[0]
    return cleaned or "Source"


# STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87:
def channel_active_source_endpoint(channel: Channel, runtime: dict[str, object] | None = None) -> str:
    """Return the endpoint of the input the Main FFmpeg runtime is actually using."""
    values = channel_input_urls(channel)
    raw_index = (runtime or {}).get("active_input_index", getattr(channel, "active_input_index", 0))
    try:
        index = int(raw_index or 0)
    except (TypeError, ValueError):
        index = int(getattr(channel, "active_input_index", 0) or 0)
    if values:
        index = max(0, min(index, len(values) - 1))
        return source_endpoint(values[index])
    return source_endpoint(getattr(channel, "input_url", ""))


def channel_log_action(message: object, level: object = "info") -> tuple[str, str]:
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


def format_local_time(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text_value = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)



def channel_log_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except Exception:
            dt = datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def channel_log_details(value: object) -> tuple[str, object]:
    if value in (None, ""):
        return "", {}
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), value
    raw = str(value).strip()
    if not raw:
        return "", {}
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str), parsed
    except Exception:
        return raw, {}


def collapse_aggregate_channel_log_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep Main Panel fallback events only when no server event confirms them.

    Remote Node logs can be temporarily unavailable.  In that case the Main
    Panel aggregate start/stop result remains useful and must not disappear.
    When a concrete Main Server/Remote Node event exists within 15 seconds,
    suppress only the redundant aggregate success row.
    """
    concrete = [item for item in rows if not item.get("_aggregate_success")]
    result: list[dict[str, object]] = []
    for row in rows:
        if not row.get("_aggregate_success"):
            result.append(row)
            continue
        stamp = row.get("_sort_time")
        action = str(row.get("action") or "")
        matched = False
        if isinstance(stamp, datetime):
            for other in concrete:
                other_stamp = other.get("_sort_time")
                if not isinstance(other_stamp, datetime):
                    continue
                if str(other.get("action") or "") != action:
                    continue
                if str(other.get("server_name") or "") == "Main Panel":
                    continue
                if abs((other_stamp - stamp).total_seconds()) <= 15:
                    matched = True
                    break
        if not matched:
            result.append(row)
    return result


def dedupe_channel_log_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(
        rows,
        key=lambda item: item.get("_sort_time") or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )
    seen: set[tuple[object, ...]] = set()
    result: list[dict[str, object]] = []
    for row in ordered:
        stamp = row.get("_sort_time")
        second = int(stamp.timestamp()) if isinstance(stamp, datetime) else 0
        key = (
            str(row.get("server_name") or "").strip().lower(),
            str(row.get("action") or "").strip().lower(),
            re.sub(r"\s+", " ", str(row.get("message") or "").strip().lower()),
            re.sub(r"\s+", " ", str(row.get("details") or "").strip().lower()),
            second,
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned = dict(row)
        cleaned.pop("_sort_time", None)
        cleaned.pop("_aggregate_success", None)
        result.append(cleaned)
    return result

def human_duration(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    prefix = f"{days}d " if days else ""
    return f"{prefix}{hours}h {minutes}m {seconds}s"


class AuthenticationRequired(Exception):
    pass


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(permission)


def permission_required(permission: str):
    def dependency(request: Request, db: Session = Depends(get_db)) -> AdminUser:
        admin = current_admin(request, db)
        if not admin:
            raise AuthenticationRequired()
        if not has_permission(admin, permission):
            raise PermissionDenied(permission)
        return admin

    return dependency


def enforce_permission(request: Request, db: Session, permission: str) -> AdminUser:
    admin = current_admin(request, db)
    if not admin:
        raise AuthenticationRequired()
    if not has_permission(admin, permission):
        raise PermissionDenied(permission)
    return admin


def enforce_node_editor_permission(request: Request, db: Session, node: Node) -> AdminUser:
    admin = current_admin(request, db)
    if not admin:
        raise AuthenticationRequired()
    if node.node_type == "local":
        if not has_permission(admin, "main_access.edit"):
            raise PermissionDenied("main_access.edit")
    elif not has_permission(admin, "nodes.edit"):
        raise PermissionDenied("nodes.edit")
    return admin


def first_allowed_path(admin: AdminUser) -> str:
    candidates = [
        ("dashboard.view", "/"),
        ("channels.view", "/channels"),
        ("nodes.view", "/nodes"),
        ("categories.view", "/categories"),
        ("imports.view", "/channels/import"),
        ("stream_users.view", "/users"),
        ("panel_users.view", "/admin-users"),
        ("roles.view", "/roles"),
        ("logs.view", "/logs"),
    ]
    for permission, path in candidates:
        if has_permission(admin, permission):
            return path
    return "/no-access"


def ensure_default_roles(db: Session) -> Role:
    super_role = db.scalar(select(Role).where(func.lower(Role.name) == "super admin"))
    if not super_role:
        super_role = Role(
            name="Super Admin",
            description="Built-in unrestricted role. This role cannot be edited or deleted.",
            permissions=encode_permissions([SUPERUSER_PERMISSION], allow_wildcard=True),
            is_system=True,
        )
        db.add(super_role)
        db.flush()
    else:
        super_role.permissions = encode_permissions([SUPERUSER_PERMISSION], allow_wildcard=True)
        super_role.is_system = True

    # STREAMFORGE_ALL_GRANULAR_PERMISSION_MIGRATION_V2258:
    # Migrate every remaining historical broad *.manage permission into
    # explicit action permissions before normalization removes unsupported keys.
    for role in db.scalars(select(Role).where(Role.is_system.is_(False))).all():
        try:
            legacy_permissions = set(json.loads(role.permissions or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            legacy_permissions = set()

        migration_map = {
            "main_access.manage": {
                "main_access.view", "main_access.edit",
            },
            "main_system.manage": {
                "settings.view", "settings.edit",
                "backups.view", "backups.add", "backups.edit", "backups.run",
                "backups.download", "backups.restore", "backups.delete",
                "main_system.service_restart", "main_system.reboot",
            },
            "nodes.manage": {
                "nodes.view", "nodes.create", "nodes.edit", "nodes.delete",
                "nodes.service_restart", "nodes.reboot",
            },
            "categories.manage": {
                "categories.view", "categories.create", "categories.edit",
                "categories.reorder", "categories.delete",
            },
            "logs.manage": {
                "logs.view", "logs.clear",
            },
        }
        changed = False
        for legacy_key, replacements in migration_map.items():
            if legacy_key in legacy_permissions:
                legacy_permissions.discard(legacy_key)
                legacy_permissions.update(replacements)
                changed = True
        if changed:
            role.permissions = encode_permissions(legacy_permissions)

    # STREAMFORGE_RBAC_SPLIT_MIGRATION_V2257:
    # Split legacy broad Panel Administration permissions without granting
    # custom roles the newly Super-Admin-only role creation capability.
    for role in db.scalars(select(Role).where(Role.is_system.is_(False))).all():
        try:
            raw_permissions = set(json.loads(role.permissions or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_permissions = set()
        if "panel_users.manage" in raw_permissions:
            raw_permissions.discard("panel_users.manage")
            raw_permissions.update({"panel_users.view", "panel_users.create", "panel_users.edit", "panel_users.delete"})
        if "roles.manage" in raw_permissions:
            raw_permissions.discard("roles.manage")
            raw_permissions.update({"roles.view", "roles.edit", "roles.delete"})
        role.permissions = encode_permissions(raw_permissions)

    # Before RBAC existed every panel account had unrestricted access. Assigning
    # legacy accounts to Super Admin preserves that behaviour during upgrade.
    legacy_admins = db.scalars(select(AdminUser).where(AdminUser.role_id.is_(None))).all()
    for admin in legacy_admins:
        admin.role = super_role
    return super_role


class ConnectionTracker:
    """Playback connection limiter with Redis-backed shared state.

    STREAMFORGE_REDIS_CONNECTION_TRACKER_V61: Redis makes per-user and global
    connection limits atomic across future Public workers. The original local
    tracker stays warm and is used immediately if Redis is unavailable.
    """

    _ALLOW_LUA = r"""
local user_key = KEYS[1]
local global_key = KEYS[2]
local sid = ARGV[1]
local global_member = ARGV[2]
local now = tonumber(ARGV[3])
local cutoff = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local playback_start = tonumber(ARGV[6])
local user_limit = tonumber(ARGV[7])
local total_limit = tonumber(ARGV[8])
redis.call('ZREMRANGEBYSCORE', user_key, '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', global_key, '-inf', cutoff)
if redis.call('ZSCORE', user_key, sid) then
  redis.call('ZADD', user_key, now, sid)
  redis.call('ZADD', global_key, now, global_member)
  redis.call('EXPIRE', user_key, ttl + 10)
  redis.call('EXPIRE', global_key, math.max(60, ttl * 3))
  return 1
end
if playback_start ~= 1 then return 0 end
if user_limit > 0 and redis.call('ZCARD', user_key) >= user_limit then return 0 end
if total_limit > 0 and redis.call('ZCARD', global_key) >= total_limit then return 0 end
redis.call('ZADD', user_key, now, sid)
redis.call('ZADD', global_key, now, global_member)
redis.call('EXPIRE', user_key, ttl + 10)
redis.call('EXPIRE', global_key, math.max(60, ttl * 3))
return 1
"""

    def __init__(self, ttl_seconds: int = 5) -> None:
        # STREAMFORGE_MAIN_HLS_CONNECTION_GRACE_V1054:
        # HLS.js can legitimately pause media-playlist polling for more than the
        # 5-second viewer-presence window while buffering/recovering. Keep the
        # connection reservation alive for at least 15 seconds so authenticated
        # child requests can resume the SAME SID without being allowed to create
        # a new slot. Viewer/live-session presence still uses its configured TTL.
        self.ttl_seconds = max(15, int(ttl_seconds))
        self.total_limit = 0
        self._sessions: dict[int, dict[str, float]] = {}
        self._active_total=0
        self._next_prune = 0.0
        self._redis_touch_after: dict[tuple[int, str], float] = {}
        self._lock = threading.RLock()

    def set_total_limit(self, value: int) -> None:
        with self._lock:
            self.total_limit = max(0, min(1000000, int(value or 0)))

    def set_ttl(self, value: int) -> None:
        with self._lock:
            # STREAMFORGE_MAIN_HLS_CONNECTION_GRACE_V1054
            self.ttl_seconds = max(15, min(3600, int(value or 5)))

    def _prune_locked(self, now: float, force: bool = False) -> None:
        if not force and now < self._next_prune:
            return
        removed = 0
        for uid, sessions in list(self._sessions.items()):
            for sid in [sid for sid, seen in sessions.items() if now - seen > self.ttl_seconds]:
                if sessions.pop(sid, None) is not None:
                    removed += 1
                    self._redis_touch_after.pop((int(uid), sid), None)
            if not sessions:
                self._sessions.pop(uid, None)
        self._active_total = max(0, self._active_total - removed)
        self._next_prune = now + 2.0

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        return forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    def session_id(self, request: Request, explicit: str = "") -> str:
        value = (
            explicit
            or request.query_params.get("sid", "")
            or request.headers.get("x-streamforge-session", "")
            or request.headers.get("x-playback-session", "")
            or request.headers.get("x-device-id", "")
        ).strip()
        value = re.sub(r"[^A-Za-z0-9._~-]+", "", value)[:96]
        if value:
            return value
        fingerprint = "|".join(
            [
                self._client_ip(request),
                request.headers.get("user-agent", ""),
                request.headers.get("accept", ""),
                request.headers.get("origin", ""),
            ]
        )
        return "fp-" + hashlib.sha256(fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]

    def _redis_touch_interval(self) -> float:
        return max(0.25, float(settings.redis_touch_interval_ms) / 1000.0)

    def _allow_local(self, user: StreamUser, sid: str, *, playback_start: bool) -> bool:
        now = time.monotonic()
        uid = int(user.id)
        with self._lock:
            self._prune_locked(now)
            sessions = self._sessions.get(uid)
            if sessions is not None and sid in sessions:
                sessions[sid] = now
                self._redis_touch_after.setdefault((uid, sid), now + self._redis_touch_interval())
                return True
            if not playback_start:
                return False
            if sessions is None:
                sessions = {}
                self._sessions[uid] = sessions
            # STREAMFORGE_USER_ZERO_UNLIMITED_V2245: zero means unlimited.
            limit = max(0, int(user.max_connections or 0))
            # STREAMFORGE_STRICT_MAX_CONNECTIONS: a distinct active session
            # owns its slot until TTL expiry or explicit kill.
            if limit > 0 and len(sessions) >= limit:
                return False
            if self.total_limit > 0 and self._active_total >= self.total_limit:
                if not sessions:
                    self._sessions.pop(uid, None)
                return False
            sessions[sid] = now
            self._active_total += 1
            self._redis_touch_after[(uid, sid)] = now + self._redis_touch_interval()
            return True

    def _local_touch_state(self, uid: int, sid: str) -> tuple[bool, bool]:
        """Return (present, Redis heartbeat due) and refresh local activity."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            sessions = self._sessions.get(uid)
            if not sessions or sid not in sessions:
                return False, False
            sessions[sid] = now
            key = (uid, sid)
            due = now >= self._redis_touch_after.get(key, 0.0)
            if due:
                self._redis_touch_after[key] = now + self._redis_touch_interval()
            return True, due

    def _mirror_local_touch(self, uid: int, sid: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            sessions = self._sessions.setdefault(uid, {})
            if sid not in sessions:
                self._active_total += 1
            sessions[sid] = now
            self._redis_touch_after[(uid, sid)] = now + self._redis_touch_interval()

    def allow(self, user: StreamUser, request: Request, *, session_id: str = "", playback_start: bool = False) -> bool:
        sid = self.session_id(request, session_id)
        uid = int(user.id)

        # STREAMFORGE_REDIS_HOT_PATH_THROTTLE_V61: once this worker knows a
        # live session, segment requests stay in-process and Redis is refreshed
        # only on a short heartbeat interval. A different worker/local miss
        # still checks the shared state immediately.
        local_present, heartbeat_due = self._local_touch_state(uid, sid)
        if local_present and not heartbeat_due:
            return True

        ttl = max(5, int(self.ttl_seconds))
        now_epoch = time.time()
        user_limit = max(0, int(user.max_connections or 0))
        total_limit = max(0, int(self.total_limit or 0))
        user_key = redis_state.key("connections", "user", uid)
        global_key = redis_state.key("connections", "all")
        global_member = f"{uid}:{sid}"
        # A locally-known session is allowed to repopulate Redis after a Redis
        # restart. New/local-miss non-start requests still cannot create slots.
        shared_start = bool(playback_start or local_present)
        ok, allowed = redis_state.call(
            lambda client: client.eval(
                self._ALLOW_LUA,
                2,
                user_key,
                global_key,
                sid,
                global_member,
                now_epoch,
                now_epoch - ttl,
                ttl,
                1 if shared_start else 0,
                user_limit,
                total_limit,
            )
        )
        if ok:
            if int(allowed or 0) == 1:
                self._mirror_local_touch(uid, sid)
                return True
            if local_present:
                self._kill_local(uid, sid)
            return False
        if local_present:
            return True
        return self._allow_local(user, sid, playback_start=playback_start)

    def total_active_count(self) -> int:
        ttl = max(5, int(self.ttl_seconds))
        now_epoch = time.time()
        global_key = redis_state.key("connections", "all")
        ok, count = redis_state.call(
            lambda client: (
                client.zremrangebyscore(global_key, "-inf", now_epoch - ttl),
                client.zcard(global_key),
            )[1]
        )
        if ok:
            return int(count or 0)
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now, True)
            return self._active_total

    def _kill_local(self, user_id: int, sid: str) -> int:
        with self._lock:
            sessions = self._sessions.get(int(user_id))
            if not sessions or sessions.pop(sid, None) is None:
                return 0
            self._active_total = max(0, self._active_total - 1)
            self._redis_touch_after.pop((int(user_id), sid), None)
            if not sessions:
                self._sessions.pop(int(user_id), None)
            return 1

    def kill(self, user_id: int, session_id: str) -> int:
        sid = re.sub(r"[^A-Za-z0-9._~-]+", "", str(session_id or "").strip())[:96]
        if not sid:
            return 0
        uid = int(user_id)
        user_key = redis_state.key("connections", "user", uid)
        global_key = redis_state.key("connections", "all")
        global_member = f"{uid}:{sid}"
        ok, removed = redis_state.call(
            lambda client: client.eval(
                "local a=redis.call('ZREM',KEYS[1],ARGV[1]); redis.call('ZREM',KEYS[2],ARGV[2]); return a",
                2,
                user_key,
                global_key,
                sid,
                global_member,
            )
        )
        local_removed = self._kill_local(uid, sid)
        return max(int(removed or 0), local_removed) if ok else local_removed

    def active_count(self, user_id: int) -> int:
        uid = int(user_id)
        ttl = max(5, int(self.ttl_seconds))
        now_epoch = time.time()
        user_key = redis_state.key("connections", "user", uid)
        ok, count = redis_state.call(
            lambda client: (
                client.zremrangebyscore(user_key, "-inf", now_epoch - ttl),
                client.zcard(user_key),
            )[1]
        )
        if ok:
            return int(count or 0)
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            return len(self._sessions.get(uid, {}))


connection_tracker = ConnectionTracker()

MAIN_TOTAL_CONNECTIONS_KEY = "main_total_max_connections"
METRICS_RETENTION_DAYS_KEY = "metrics_retention_days"
LOG_RETENTION_DAYS_KEY = "log_retention_days"
METRICS_DEFAULT_SPAN_HOURS_KEY = "metrics_default_span_hours"
METRICS_SAMPLE_INTERVAL_SECONDS_KEY = "metrics_sample_interval_seconds"
METRICS_NETWORK_INTERFACES_KEY = "metrics_network_interfaces"
# STREAMFORGE_SESSION_HOVER_SETTINGS_V2254:
VIEWER_SESSION_TIMEOUT_KEY = "viewer_session_timeout_seconds"
# STREAMFORGE_CLIENT_SESSION_RESET_OFFLINE_V1081: stable device SID, configurable logical-session boundary.
CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY = "client_session_reset_offline_minutes"
HIDE_PANEL_HOVER_URLS_KEY = "hide_panel_hover_urls"

def app_setting_int(db: Session, key: str, default: int = 0) -> int:
    row = db.get(AppSetting, key)
    try:
        return int(row.value) if row else int(default)
    except (TypeError, ValueError):
        return int(default)

def app_setting_str(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return str(row.value if row else default).strip()

def selected_network_interfaces(db: Session) -> list[str]:
    return sorted({item.strip() for item in app_setting_str(db, METRICS_NETWORK_INTERFACES_KEY).split(",") if item.strip() and item.strip() != "lo"})

def save_app_setting(db: Session, key: str, value: object) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=str(value))
        db.add(row)
    else:
        row.value = str(value)


# STREAMFORGE_DELEGATED_DNS01_PANEL_STATUS_V41
MAIN_TLS_STATUS_FILE = Path("/var/lib/streamforge/tls/status.json")

def main_managed_tls_status() -> dict[str, object]:
    try:
        data = json.loads(MAIN_TLS_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def managed_tls_status_for_node(node: Node) -> dict[str, object]:
    if node.node_type == "local":
        return main_managed_tls_status()
    try:
        return node_controller.managed_tls_status(node)
    except NodeError as exc:
        return {"mode": "delegated-dns01", "errors": {"status": str(exc)}, "delegations": {}}


# STREAMFORGE_DNS01_TEST_API_V42: the Panel can verify the one-time public
# CNAME without invoking Certbot or touching a DNS-provider API.  The expected
# target is read only from StreamForge's restricted acme-dns registration.
def dns01_cname_test_for_node(node: Node, requested_host: str) -> dict[str, object]:
    host = str(requested_host or "").strip().lower().rstrip(".")
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise HTTPException(400, "Invalid DNS hostname")

    # STREAMFORGE_MAIN_NODE_DNS01_REMOTE_ACTIONS_V82: Remote Node Settings is
    # authoritative for delegated DNS-01. Ask the Node to run the exact same
    # resolver test first; v8.1 and older Nodes fall back to Main-side testing.
    if node.node_type != "local":
        try:
            remote = node_controller.dns01_cname_test(node, host)
            if isinstance(remote, dict):
                return dict(remote)
        except NodeError:
            pass

    tls = managed_tls_status_for_node(node)
    delegations = tls.get("delegations", {}) if isinstance(tls, dict) else {}
    if not isinstance(delegations, dict) or host not in delegations:
        # A normal DNS/registration state must not return HTTP 404 here: strict
        # Main Nginx intentionally converts upstream 404 into silent 444, which
        # browsers surface only as the misleading TypeError: Failed to fetch.
        return {
            "ok": False,
            "state": "registration_missing",
            "host": host,
            "certificate_ready": False,
            "message": "DNS-01 registration is not available from this Node yet. Refresh the Node status or retry after the Node is online.",
        }
    item = delegations.get(host) or {}
    if not isinstance(item, dict):
        item = {}
    cname_name = str(item.get("cname_name") or f"_acme-challenge.{host}").strip().rstrip(".")
    expected = str(item.get("cname_target") or "").strip().lower().rstrip(".")
    certificate_ready = bool(item.get("certificate_ready"))
    if not expected:
        return {
            "ok": False, "state": "registration_pending", "host": host,
            "name": cname_name, "expected": "", "found": "",
            "certificate_ready": certificate_ready,
            "message": "Delegation registration is still pending",
        }
    try:
        completed = subprocess.run(
            ["dig", "+time=3", "+tries=1", "+short", "CNAME", cname_name],
            text=True, capture_output=True, timeout=7, check=False,
        )
    except FileNotFoundError:
        return {
            "ok": False, "state": "tester_unavailable", "host": host,
            "name": cname_name, "expected": expected + ".", "found": "",
            "certificate_ready": certificate_ready,
            "message": "DNS tester is unavailable (dnsutils/dig is not installed)",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "state": "timeout", "host": host,
            "name": cname_name, "expected": expected + ".", "found": "",
            "certificate_ready": certificate_ready,
            "message": "DNS lookup timed out; try again after propagation",
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "dig failed").strip()[-500:]
        return {
            "ok": False, "state": "lookup_failed", "host": host,
            "name": cname_name, "expected": expected + ".", "found": "",
            "certificate_ready": certificate_ready, "message": detail,
        }
    answers = [line.strip().lower().rstrip(".") for line in completed.stdout.splitlines() if line.strip()]
    found = answers[0] if answers else ""
    if expected in answers:
        message = "CNAME is correct and publicly visible"
        if certificate_ready:
            message += " · certificate active"
        return {
            "ok": True, "state": "ready", "host": host, "name": cname_name,
            "expected": expected + ".", "found": expected + ".",
            "certificate_ready": certificate_ready, "message": message,
        }
    if not found:
        message = "CNAME not found yet"
        state = "missing"
    else:
        message = f"Wrong CNAME target: {found}. Expected {expected}."
        state = "wrong_target"
    return {
        "ok": False, "state": state, "host": host, "name": cname_name,
        "expected": expected + ".", "found": (found + ".") if found else "",
        "certificate_ready": certificate_ready, "message": message,
    }


BRANDING_NAME_KEY = "branding_name"
BRANDING_SUBTITLE_KEY = "branding_subtitle"
BRANDING_LOGO_KEY = "branding_logo"
BRANDING_FAVICON_KEY = "branding_favicon"
BRANDING_LOGO_PREFIX = "/branding-assets/"

# STREAMFORGE_WEBPLAYER_MANAGE_V2219:
# Web Player presentation is stored per server in AppSetting so the feature
# needs no database schema migration. Remote settings are pushed to that node.
WEBPLAYER_DEFAULTS = {
    "show_user_info": "1",
    "show_connection_info": "1",
    "login_mode": "manual",
    "auto_user_id": "0",
    "auto_user_token": "",
    "page_color": "#04080d", "page_alpha": "100",
    "panel_color": "#071019", "panel_alpha": "100",
    "accent_color": "#ff2020", "accent_alpha": "100",
    "text_color": "#f6fbff", "text_alpha": "100",
    "download_name": "",
    "download_stored_name": "",
    # STREAMFORGE_ANDROID_UPDATE_MANIFEST_V3061:
    # Version/description are managed beside the Web Player APK download.
    "android_version_name": "",
    "android_description": "",
}


# STREAMFORGE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041:
def _webplayer_login_mode(value: object) -> str:
    mode = str(value or "manual").strip().lower()
    return mode if mode in {"manual", "auto", "quick"} else "manual"

def _webplayer_setting_key(node_id: int, name: str) -> str:
    return f"webplayer_{int(node_id)}_{name}"


# STREAMFORGE_WEBPLAYER_MULTI_BRAND_V1155:
# Optional host-based Web Player brand profiles live in one JSON AppSetting per
# server. The existing single Web Player presentation remains the fallback, so
# upgrades and fresh installs keep exactly the old behaviour until a profile is
# explicitly added.
def _webplayer_brands_setting_key(node_id: int) -> str:
    return _webplayer_setting_key(node_id, "brands_json")


def _webplayer_brand_domain(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw if "://" in raw else "//" + raw)
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
    except Exception:
        host = ""
    if not host or len(host) > 253:
        return ""
    return host


def _webplayer_brand_access_url(value: object) -> str:
    """Normalize one brand authority to a root Playlist/App URL.

    Full http/https URLs keep their scheme/port. A bare domain/IP intentionally
    defaults to HTTP so adding a brand never fabricates a TLS certificate.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(candidate)
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return ""
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    safe_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = safe_host if port in {None, default_port} else f"{safe_host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")


def _webplayer_brand_url(value: object) -> str:
    raw = str(value or "").strip()[:1000]
    if not raw:
        return ""
    # Uploaded brand assets are stored in StreamForge's existing protected
    # branding tree and served through the host-specific /web-player/* routes.
    if raw.startswith(BRANDING_LOGO_PREFIX):
        filename = Path(raw[len(BRANDING_LOGO_PREFIX):]).name
        return raw if filename and raw == f"{BRANDING_LOGO_PREFIX}{filename}" else ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return ""
    return raw if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def _normalize_webplayer_brand(raw: object, fallback: dict[str, object] | None = None) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    fallback = fallback or WEBPLAYER_DEFAULTS
    brand_id = re.sub(r"[^a-z0-9_-]+", "", str(raw.get("id") or "").strip().lower())[:40]
    if not brand_id:
        brand_id = secrets.token_hex(6)
    name = str(raw.get("name") or "").strip()[:120]
    domains_raw = raw.get("domains") or raw.get("access_urls") or []
    if isinstance(domains_raw, str):
        domains_raw = re.split(r"[\s,;]+", domains_raw)
    access_raw = raw.get("access_urls") or domains_raw
    if isinstance(access_raw, str):
        access_raw = re.split(r"[\s,;]+", access_raw)
    access_urls: list[str] = []
    for value in list(access_raw or []):
        url = _webplayer_brand_access_url(value)
        if url and url not in access_urls:
            access_urls.append(url)
    domains: list[str] = []
    for item in list(domains_raw or []) + access_urls:
        host = _webplayer_brand_domain(item)
        if host and host not in domains:
            domains.append(host)
    if not name or not domains:
        return None
    if not access_urls:
        access_urls = [url for host in domains if (url := _webplayer_brand_access_url(host))]
    # STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_PARITY_V1159:
    # Every host brand owns the same viewer/login/theme controls as the server-
    # level Web Player. Existing v11.55-v11.58 profiles are normalized with the
    # historical defaults, so no schema migration or destructive rewrite is
    # required before the operator saves the expanded profile.
    item: dict[str, object] = {
        "id": brand_id,
        "name": name,
        "domains": domains[:32],
        "access_urls": access_urls[:32],
        "logo_url": _webplayer_brand_url(raw.get("logo_url")),
        "favicon_url": _webplayer_brand_url(raw.get("favicon_url")),
        "show_user_info": bool(raw.get("show_user_info", str(fallback.get("show_user_info", WEBPLAYER_DEFAULTS["show_user_info"])).lower() not in {"0", "false", "off", "no"})),
        "show_connection_info": bool(raw.get("show_connection_info", str(fallback.get("show_connection_info", WEBPLAYER_DEFAULTS["show_connection_info"])).lower() not in {"0", "false", "off", "no"})),
        "login_mode": _webplayer_login_mode(raw.get("login_mode") or fallback.get("login_mode") or WEBPLAYER_DEFAULTS["login_mode"]),
        "auto_user_id": max(0, int(raw.get("auto_user_id") or fallback.get("auto_user_id") or 0)),
        "auto_user_token": str(raw.get("auto_user_token") or fallback.get("auto_user_token") or "").strip()[:256],
        "download_name": Path(str(raw.get("download_name") or "")).name,
        "download_stored_name": Path(str(raw.get("download_stored_name") or "")).name,
        "android_version_name": str(raw.get("android_version_name") or "").strip()[:64],
        "android_description": str(raw.get("android_description") or "").strip()[:2000],
    }
    for key, default in (
        ("page_color", str(fallback.get("page_color") or WEBPLAYER_DEFAULTS["page_color"])),
        ("panel_color", str(fallback.get("panel_color") or WEBPLAYER_DEFAULTS["panel_color"])),
        ("accent_color", str(fallback.get("accent_color") or WEBPLAYER_DEFAULTS["accent_color"])),
        ("text_color", str(fallback.get("text_color") or WEBPLAYER_DEFAULTS["text_color"])),
    ):
        item[key] = _webplayer_color(raw.get(key), default)
    for key, default in (
        ("page_alpha", fallback.get("page_alpha", WEBPLAYER_DEFAULTS["page_alpha"])),
        ("panel_alpha", fallback.get("panel_alpha", WEBPLAYER_DEFAULTS["panel_alpha"])),
        ("accent_alpha", fallback.get("accent_alpha", WEBPLAYER_DEFAULTS["accent_alpha"])),
        ("text_alpha", fallback.get("text_alpha", WEBPLAYER_DEFAULTS["text_alpha"])),
    ):
        item[key] = _webplayer_alpha(raw.get(key, default), int(default or 100))
    item.update({
        "page_rgba": _webplayer_rgba(str(item["page_color"]), int(item["page_alpha"])),
        "panel_rgba": _webplayer_rgba(str(item["panel_color"]), int(item["panel_alpha"])),
        "accent_rgba": _webplayer_rgba(str(item["accent_color"]), int(item["accent_alpha"])),
        "text_rgba": _webplayer_rgba(str(item["text_color"]), int(item["text_alpha"])),
    })
    return item


def webplayer_brands_for_node(db: Session, node_id: int) -> list[dict[str, object]]:
    row = db.get(AppSetting, _webplayer_brands_setting_key(node_id))
    if not row or not str(row.value or "").strip():
        return []
    try:
        loaded = json.loads(str(row.value))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    items: list[dict[str, object]] = []
    seen_hosts: set[str] = set()
    for raw in loaded[:16]:
        item = _normalize_webplayer_brand(raw)
        if not item:
            continue
        domains = [host for host in list(item.get("domains") or []) if host not in seen_hosts]
        if not domains:
            continue
        item["domains"] = domains
        seen_hosts.update(domains)
        items.append(item)
    return items


def save_webplayer_brands(db: Session, node_id: int, brands: list[dict[str, object]]) -> None:
    normalized: list[dict[str, object]] = []
    seen_hosts: set[str] = set()
    for raw in brands[:16]:
        item = _normalize_webplayer_brand(raw)
        if not item:
            continue
        domains = [host for host in list(item.get("domains") or []) if host not in seen_hosts]
        if not domains:
            continue
        item["domains"] = domains
        seen_hosts.update(domains)
        normalized.append(item)
    save_app_setting(db, _webplayer_brands_setting_key(node_id), json.dumps(normalized, separators=(",", ":")))


def _webplayer_brand_for_request(db: Session, node_id: int, request: Request) -> dict[str, object] | None:
    host = _webplayer_brand_domain(request.url.hostname or request.headers.get("host", ""))
    if not host:
        return None
    for item in webplayer_brands_for_node(db, node_id):
        if host in list(item.get("domains") or []):
            return item
    return None


def _webplayer_effective_settings(db: Session, node_id: int, request: Request) -> dict[str, object]:
    values = webplayer_settings_for_node(db, node_id)
    brand = _webplayer_brand_for_request(db, node_id, request)
    if brand:
        # STREAMFORGE_WEBPLAYER_BRAND_FULL_RUNTIME_PARITY_V1159:
        # A selected host profile is an independent Web Player configuration,
        # not merely a color/name skin.
        values["show_user_info"] = bool(brand.get("show_user_info", values.get("show_user_info", True)))
        values["show_connection_info"] = bool(brand.get("show_connection_info", values.get("show_connection_info", True)))
        values["login_mode"] = _webplayer_login_mode(brand.get("login_mode") or values.get("login_mode"))
        values["auto_user_id"] = max(0, int(brand.get("auto_user_id") or 0))
        values["auto_user_token"] = str(brand.get("auto_user_token") or "").strip()
        for key in ("page_color", "panel_color", "accent_color", "text_color"):
            values[key] = _webplayer_color(brand.get(key), str(values.get(key) or WEBPLAYER_DEFAULTS[key]))
        for key in ("page_alpha", "panel_alpha", "accent_alpha", "text_alpha"):
            values[key] = _webplayer_alpha(brand.get(key), int(values.get(key) or 100))
        # STREAMFORGE_WEBPLAYER_BRAND_ASSET_ISOLATION_V1213:
        # A matched brand is an isolated identity. Missing per-brand downloads
        # intentionally stay unavailable instead of inheriting the Main player.
        values["download_name"] = ""
        values["download_stored_name"] = ""
        values["android_version_name"] = ""
        values["android_description"] = ""
        if str(brand.get("download_stored_name") or ""):
            values["download_name"] = Path(str(brand.get("download_name") or "")).name
            values["download_stored_name"] = Path(str(brand.get("download_stored_name") or "")).name
            values["android_version_name"] = str(brand.get("android_version_name") or "")[:64]
            values["android_description"] = str(brand.get("android_description") or "")[:2000]
        values.update({
            "page_rgba": _webplayer_rgba(str(values["page_color"]), int(values.get("page_alpha") or 100)),
            "panel_rgba": _webplayer_rgba(str(values["panel_color"]), int(values.get("panel_alpha") or 100)),
            "accent_rgba": _webplayer_rgba(str(values["accent_color"]), int(values.get("accent_alpha") or 100)),
            "text_rgba": _webplayer_rgba(str(values["text_color"]), int(values.get("text_alpha") or 100)),
            "brand_id": str(brand.get("id") or ""),
        })
    stored_name = Path(str(values.get("download_stored_name") or "")).name
    values["download_available"] = bool(stored_name and (settings.webplayer_download_root / stored_name).is_file())
    return values


def _webplayer_branding_for_request(db: Session, node_id: int, request: Request) -> dict[str, str]:
    values = dict(branding_settings(db))
    brand = _webplayer_brand_for_request(db, node_id, request)
    if brand:
        # STREAMFORGE_WEBPLAYER_BRAND_ASSET_ISOLATION_V1213:
        # Empty brand assets must not expose the Main server identity.
        values["name"] = str(brand.get("name") or values.get("name") or "StreamForge")
        values["logo_url"] = str(brand.get("logo_url") or "")
        values["favicon_url"] = str(brand.get("favicon_url") or "")
    return values


# STREAMFORGE_NODE_VIEWER_TTL_SETTING_V2256:
def _node_viewer_ttl_setting_key(node_id: int) -> str:
    return f"node_{int(node_id)}_viewer_session_timeout_seconds"

# STREAMFORGE_NODE_HIDE_HOVER_SETTING_V2293:
def _node_hide_hover_setting_key(node_id: int) -> str:
    return f"node_{int(node_id)}_hide_panel_hover_urls"


def _webplayer_color(value: object, default: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", cleaned) else default

def _webplayer_alpha(value: object, default: int = 100) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = int(default)
    return max(0, min(100, parsed))

def _webplayer_rgba(hex_color: str, alpha: int) -> str:
    color = _webplayer_color(hex_color, "#000000")
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return f"rgba({r},{g},{b},{_webplayer_alpha(alpha)/100.0:.3f})"

def webplayer_settings_for_node(db: Session, node_id: int) -> dict[str, object]:
    # STREAMFORGE_MAIN_WEBPLAYER_BATCH_SETTINGS_V1112:
    # The public Web Player used to issue ~20 individual AppSetting primary-key
    # lookups on every page render.  Fetch the complete setting set in one SQL
    # query so catalogue/watch latency does not scale with the number of theme
    # and viewer options.
    node_key_names = (
        "show_user_info", "show_connection_info", "login_mode", "auto_user_id",
        "auto_user_token", "download_name", "download_stored_name",
        "android_version_name", "android_description", "page_color", "page_alpha",
        "panel_color", "panel_alpha", "accent_color", "accent_alpha",
        "text_color", "text_alpha",
    )
    node_keys = {_webplayer_setting_key(node_id, name) for name in node_key_names}
    shared_keys = {
        VIEWER_SESSION_TIMEOUT_KEY, CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY,
        HIDE_PANEL_HOVER_URLS_KEY, _node_viewer_ttl_setting_key(node_id),
        _node_hide_hover_setting_key(node_id),
    }
    setting_rows = db.scalars(
        select(AppSetting).where(AppSetting.key.in_(node_keys | shared_keys))
    ).all()
    setting_map = {str(row.key): str(row.value or "").strip() for row in setting_rows}

    def value(name: str, default: object = "") -> str:
        return setting_map.get(_webplayer_setting_key(node_id, name), str(default or "")).strip()

    def shared(key: str, default: object = "") -> str:
        return setting_map.get(key, str(default or "")).strip()

    def integer(raw: object, default: int) -> int:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return int(default)

    node = db.get(Node, int(node_id))
    global_hover = shared(HIDE_PANEL_HOVER_URLS_KEY, "1")
    hover_value = (
        global_hover
        if str(getattr(node, "node_type", "") or "").strip().lower() == "local"
        else shared(_node_hide_hover_setting_key(node_id), global_hover)
    )
    viewer_ttl_default = integer(shared(VIEWER_SESSION_TIMEOUT_KEY, "5"), 5)
    values = {
        "show_user_info": value("show_user_info", WEBPLAYER_DEFAULTS["show_user_info"]) not in {"0", "false", "off", "no"},
        # STREAMFORGE_WEBPLAYER_CONNECTION_INFO_SETTING_V2238:
        "show_connection_info": value("show_connection_info", WEBPLAYER_DEFAULTS["show_connection_info"]) not in {"0", "false", "off", "no"},
        # STREAMFORGE_WEBPLAYER_LOGIN_MODE_V2222:
        "login_mode": _webplayer_login_mode(value("login_mode", WEBPLAYER_DEFAULTS["login_mode"])),
        "auto_user_id": integer(value("auto_user_id", WEBPLAYER_DEFAULTS["auto_user_id"]), 0),
        "auto_user_token": value("auto_user_token", WEBPLAYER_DEFAULTS["auto_user_token"]),
        # STREAMFORGE_WEBPLAYER_MANAGED_DOWNLOAD_V3045:
        "download_name": Path(value("download_name", WEBPLAYER_DEFAULTS["download_name"])).name,
        "download_stored_name": Path(value("download_stored_name", WEBPLAYER_DEFAULTS["download_stored_name"])).name,
        "android_version_name": value("android_version_name", WEBPLAYER_DEFAULTS["android_version_name"])[:64],
        "android_description": value("android_description", WEBPLAYER_DEFAULTS["android_description"])[:2000],
        "page_color": _webplayer_color(value("page_color", WEBPLAYER_DEFAULTS["page_color"]), WEBPLAYER_DEFAULTS["page_color"]),
        "page_alpha": _webplayer_alpha(value("page_alpha", WEBPLAYER_DEFAULTS["page_alpha"])),
        "panel_color": _webplayer_color(value("panel_color", WEBPLAYER_DEFAULTS["panel_color"]), WEBPLAYER_DEFAULTS["panel_color"]),
        "panel_alpha": _webplayer_alpha(value("panel_alpha", WEBPLAYER_DEFAULTS["panel_alpha"])),
        "accent_color": _webplayer_color(value("accent_color", WEBPLAYER_DEFAULTS["accent_color"]), WEBPLAYER_DEFAULTS["accent_color"]),
        "accent_alpha": _webplayer_alpha(value("accent_alpha", WEBPLAYER_DEFAULTS["accent_alpha"])),
        "text_color": _webplayer_color(value("text_color", WEBPLAYER_DEFAULTS["text_color"]), WEBPLAYER_DEFAULTS["text_color"]),
        "text_alpha": _webplayer_alpha(value("text_alpha", WEBPLAYER_DEFAULTS["text_alpha"])),
        "viewer_ttl_seconds": max(5, min(3600, integer(shared(_node_viewer_ttl_setting_key(node_id), str(viewer_ttl_default)), viewer_ttl_default))),
        "client_session_reset_offline_minutes": max(1, min(10080, integer(shared(CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY, "60"), 60))),
        # STREAMFORGE_MAIN_WEBPLAYER_GLOBAL_HOVER_SETTING_V99R18:
        "hide_hover_urls": hover_value.lower() not in {"0", "false", "off", "no"},
    }
    values.update({
        "page_rgba": _webplayer_rgba(values["page_color"], values["page_alpha"]),
        "panel_rgba": _webplayer_rgba(values["panel_color"], values["panel_alpha"]),
        "accent_rgba": _webplayer_rgba(values["accent_color"], values["accent_alpha"]),
        "text_rgba": _webplayer_rgba(values["text_color"], values["text_alpha"]),
    })
    stored_name = str(values.get("download_stored_name") or "")
    values["download_available"] = bool(
        stored_name and (settings.webplayer_download_root / stored_name).is_file()
    )
    values["brands"] = webplayer_brands_for_node(db, node_id)
    return values

def save_webplayer_settings(db: Session, node_id: int, values: dict[str, object]) -> None:
    save_app_setting(db, _webplayer_setting_key(node_id, "show_user_info"), "1" if values.get("show_user_info") else "0")
    save_app_setting(db, _webplayer_setting_key(node_id, "show_connection_info"), "1" if values.get("show_connection_info") else "0")
    save_app_setting(db, _webplayer_setting_key(node_id, "login_mode"), _webplayer_login_mode(values.get("login_mode")))
    save_app_setting(db, _webplayer_setting_key(node_id, "auto_user_id"), str(int(values.get("auto_user_id") or 0)))
    save_app_setting(db, _webplayer_setting_key(node_id, "auto_user_token"), str(values.get("auto_user_token") or "").strip())
    save_app_setting(db, _webplayer_setting_key(node_id, "download_name"), Path(str(values.get("download_name") or "")).name)
    save_app_setting(db, _webplayer_setting_key(node_id, "download_stored_name"), Path(str(values.get("download_stored_name") or "")).name)
    save_app_setting(db, _webplayer_setting_key(node_id, "android_version_name"), str(values.get("android_version_name") or "").strip()[:64])
    save_app_setting(db, _webplayer_setting_key(node_id, "android_description"), str(values.get("android_description") or "").strip()[:2000])
    for name in ("page_color", "panel_color", "accent_color", "text_color"):
        save_app_setting(db, _webplayer_setting_key(node_id, name), values[name])
    for name in ("page_alpha", "panel_alpha", "accent_alpha", "text_alpha"):
        save_app_setting(db, _webplayer_setting_key(node_id, name), str(_webplayer_alpha(values.get(name), 100)))



def branding_settings(db: Session) -> dict[str, str]:
    values = {
        row.key: str(row.value or "")
        for row in db.scalars(
            select(AppSetting).where(
                AppSetting.key.in_((BRANDING_NAME_KEY, BRANDING_SUBTITLE_KEY, BRANDING_LOGO_KEY, BRANDING_FAVICON_KEY))
            )
        ).all()
    }
    logo_url = values.get(BRANDING_LOGO_KEY, "").strip()
    favicon_url = values.get(BRANDING_FAVICON_KEY, "").strip()
    # STREAMFORGE_MAIN_BRANDING_HASH_CACHE_V1116:
    # Branding filenames are content-addressed. If the operator uploaded the
    # exact same bytes as logo + favicon (the captured 1.04 MB/1.04 MB case),
    # reuse one URL so the browser downloads/decodes one object, not two.
    if logo_url.startswith(BRANDING_LOGO_PREFIX) and favicon_url.startswith(BRANDING_LOGO_PREFIX):
        logo_name = Path(logo_url[len(BRANDING_LOGO_PREFIX):]).name
        favicon_name = Path(favicon_url[len(BRANDING_LOGO_PREFIX):]).name
        if logo_name and favicon_name == f"favicon-{logo_name}":
            favicon_url = logo_url
    return {
        "name": values.get(BRANDING_NAME_KEY, "").strip() or "StreamForge",
        # Preserve an explicitly saved blank subtitle. Only use the historical
        # default when no subtitle setting exists yet (fresh/older installs).
        "subtitle": (
            values[BRANDING_SUBTITLE_KEY].strip()
            if BRANDING_SUBTITLE_KEY in values
            else "Encoding Control"
        ),
        "logo_url": logo_url,
        "favicon_url": favicon_url,
    }


def catalog_session_id(user: StreamUser, request: Request) -> str:
    """Return a stable device session for catalogue/M3U refreshes.

    Older builds generated a new random SID every time get_live_streams or
    get.php was refreshed. With max_connections=1 that made a normal channel
    change look like a second device. Explicit device IDs still win; otherwise
    a stable client fingerprint is reused across channel changes.
    """
    explicit = (
        request.query_params.get("device_id", "")
        or request.query_params.get("sid", "")
        or request.headers.get("x-device-id", "")
        or request.headers.get("x-streamforge-device", "")
        or request.cookies.get("sf_device", "")
    ).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._~-]+", "", explicit)[:96]
    if cleaned:
        seed = f"{user.id}|{cleaned}"
    else:
        seed = "|".join([
            str(user.id),
            client_ip(request),
            request.headers.get("user-agent", ""),
            request.headers.get("accept-language", ""),
            request.headers.get("x-requested-with", ""),
        ])
    return "dev-" + hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:32]



def normalize_node_public_url(value: str | None) -> str | None:
    cleaned = (value or "").strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Playlist/App public URL must start with http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Playlist/App public URL cannot contain credentials, query or fragment")
    return cleaned


def normalize_node_access_slug(value: str | None) -> str:
    raw = str(value or "").strip().strip("/")
    if not raw:
        return ""
    cleaned = slugify(raw)[:120]
    if not cleaned:
        raise ValueError("Access slug is invalid")
    return cleaned


def collapse_repeated_url_suffix(value: str) -> str:
    """Repair accidental ``:port/slug:port/slug`` duplication from older forms."""
    cleaned = str(value or "").strip().rstrip("/")
    pattern = re.compile(
        r"^(https?://(?:\[[^\]]+\]|[^/:?#]+)):(\d{1,5})(/[A-Za-z0-9_-]+):\2\3$",
        re.IGNORECASE,
    )
    match = pattern.fullmatch(cleaned)
    if match:
        return f"{match.group(1)}:{match.group(2)}{match.group(3)}"
    return cleaned


def normalize_node_access_urls(
    value: str | None,
    label: str,
    legacy_slug: str | None = "",
    fallback_port: int | None = None,
) -> tuple[list[str], str]:
    """Normalize URL aliases while preserving each alias's own path slug.

    The first URL remains canonical and its path is returned through the legacy
    single-slug field for compatibility.  Every additional URL may use a
    different one-segment path (for example ``/admin``, ``/panel2`` or no path).
    ``legacy_slug`` is applied only when all supplied URLs are pathless, which
    safely upgrades older records without overwriting explicit new aliases.
    """
    parsed_rows: list[tuple[urllib.parse.SplitResult, str]] = []
    for raw in str(value or "").replace("\r", "").split("\n"):
        item = collapse_repeated_url_suffix(raw)
        if not item:
            continue
        parsed = urlsplit(item)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{label} must contain one http:// or https:// URL per line")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{label} URLs cannot contain credentials, query or fragment")
        raw_path = parsed.path.strip("/")
        if "/" in raw_path:
            raise ValueError(f"Each {label} URL may use only one access-path segment")
        path_slug = normalize_node_access_slug(raw_path) if raw_path else ""
        parsed_rows.append((parsed, path_slug))

    legacy = normalize_node_access_slug(legacy_slug)
    apply_legacy = bool(legacy and parsed_rows and not any(path_slug for _parsed, path_slug in parsed_rows))
    urls: list[str] = []
    preserved_port = max(0, min(65535, int(fallback_port or 0)))
    for parsed, explicit_slug in parsed_rows:
        path_slug = legacy if apply_legacy else explicit_slug
        path = f"/{path_slug}" if path_slug else ""
        # STREAMFORGE_PUBLIC_URL_SCHEME_PORT_NORMALIZATION_V36:
        # A public URL without an explicit port belongs to the scheme default
        # (HTTP=80, HTTPS=443).  Never inherit the Node's internal listener
        # port into that public URL: v3.5 could turn https://host into
        # https://host:80 when the Agent listener was 80, causing browsers to
        # send TLS to a plaintext HTTP socket (SSL_ERROR_RX_RECORD_TOO_LONG).
        # Also repair the two obvious v3.5 cross-scheme default-port artifacts.
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        explicit_port = parsed.port
        if (parsed.scheme == "https" and explicit_port == 80) or (parsed.scheme == "http" and explicit_port == 443):
            explicit_port = None
        default_port = 443 if parsed.scheme == "https" else 80
        netloc = host if explicit_port in {None, default_port} else f"{host}:{explicit_port}"
        normalized = urllib.parse.urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")
        if normalized not in urls:
            urls.append(normalized)

    primary_slug = ""
    if urls:
        primary_slug = normalize_node_access_slug(urlsplit(urls[0]).path.strip("/"))
    return urls, primary_slug


MAIN_ACCESS_RESERVED_SLUGS = {
    "static", "channel-logos", "node-logos", "branding-assets", "login", "logout", "health", "version",
    "nodes", "channels", "categories", "users", "playlists", "roles", "admin-users",
    "logs", "viewer-sessions", "player", "watch", "playlist", "play", "live", "relay",
    "ek", "api", "player_api.php", "get.php", "system-metrics.json", "status.json",
}


def normalize_main_access_urls(
    value: str | None,
    label: str,
    legacy_slug: str | None = "",
    fallback_port: int | None = None,
) -> tuple[list[str], str]:
    urls, slug = normalize_node_access_urls(value, label, legacy_slug, fallback_port)
    for item in urls:
        item_slug = normalize_node_access_slug(urlsplit(item).path.strip("/"))
        if item_slug and item_slug.lower() in MAIN_ACCESS_RESERVED_SLUGS:
            raise ValueError(f"{label} access path /{item_slug} conflicts with a built-in Main Server route")
    return urls, slug


def normalize_http_host(value: object) -> str:
    """Return a lowercase hostname/IP from an HTTP Host value or URL."""
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    except ValueError:
        return ""
    return str(parsed.hostname or "").strip().lower().rstrip(".")


def configured_main_access_aliases(local: Node | None) -> tuple[tuple[str, str, int, str, str], ...]:
    """Return normalized (host, port, slug, role) Main access aliases."""
    if local is None:
        return tuple()
    groups = (
        ("panel", str(getattr(local, "api_urls", None) or getattr(local, "api_url", None) or "")),
        ("playlist", str(getattr(local, "playlist_urls", None) or getattr(local, "playlist_url", None) or "")),
    )
    aliases: list[tuple[str, str, int, str, str]] = []
    for role, raw_values in groups:
        for raw in raw_values.replace("\r", "").splitlines():
            item = raw.strip().rstrip("/")
            if not item:
                continue
            try:
                parsed = urlsplit(item)
                host = str(parsed.hostname or "").strip().lower().rstrip(".")
                port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
            except (TypeError, ValueError):
                continue
            if parsed.scheme not in {"http", "https"} or not host:
                continue
            slug = normalize_node_access_slug(parsed.path.strip("/"))
            alias = (parsed.scheme, host, port, slug, role)
            if alias not in aliases:
                aliases.append(alias)
    return tuple(aliases)


def configured_main_recovery_aliases() -> tuple[tuple[str, str, int, str, str], ...]:
    """Return the root-owned environment URL as a last-resort Main alias."""
    # STREAMFORGE_MAIN_ENV_RECOVERY_ALIAS_V3013: restore writes the exact
    # destination browser URL into STREAMFORGE_PUBLIC_BASE_URL before restart.
    # Trust it alongside DB aliases so a stale/replayed SQLite policy cannot
    # lock the administrator out after Nginx has already accepted the request.
    raw = str(settings.public_base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    except (TypeError, ValueError):
        return tuple()
    if parsed.scheme not in {"http", "https"} or not host:
        return tuple()
    slug = normalize_node_access_slug(parsed.path.strip("/"))
    return (
        (parsed.scheme, host, port, slug, "panel"),
        (parsed.scheme, host, port, slug, "playlist"),
    )


def effective_main_access_aliases(local: Node | None) -> tuple[tuple[str, str, int, str, str], ...]:
    """Prefer saved role-specific aliases; use recovery only when none exist."""
    # STREAMFORGE_MAIN_RECOVERY_ALIAS_NO_ROLE_WIDEN_V3031: the environment URL
    # is an emergency alias, not a permanent extra Panel+Playlist alias. If a
    # saved Panel URL is /12 and the saved Playlist URL is root, appending a
    # root recovery alias with both roles exposes the Panel at root. Recovery
    # is therefore used only when the database has no usable access aliases.
    aliases = list(configured_main_access_aliases(local))
    if not aliases:
        aliases.extend(configured_main_recovery_aliases())
    return tuple(aliases)


def invalidate_main_host_policy_cache() -> None:
    with _MAIN_HOST_POLICY_LOCK:
        _MAIN_HOST_POLICY_CACHE.update({
            "loaded_at": 0.0,
            "enabled": False,
            "aliases": tuple(),
            "panel_ip_whitelist": "",
            "panel_ip_blacklist": "",
            "panel_asn_whitelist": "",
            "panel_asn_blacklist": "",
            "ip_whitelist": "",
            "ip_blacklist": "",
            "asn_whitelist": "",
            "asn_blacklist": "",
        })


def main_access_policy() -> tuple[bool, tuple[tuple[str, str, int, str, str], ...]]:
    """Load the exact Main authority/path policy without holding DB writes."""
    now = time.monotonic()
    with _MAIN_HOST_POLICY_LOCK:
        loaded_at = float(_MAIN_HOST_POLICY_CACHE.get("loaded_at") or 0.0)
        if now - loaded_at < _MAIN_HOST_POLICY_TTL_SECONDS:
            return (
                bool(_MAIN_HOST_POLICY_CACHE.get("enabled")),
                tuple(_MAIN_HOST_POLICY_CACHE.get("aliases") or ()),
            )
    try:
        with SessionLocal() as db:
            local = db.scalar(
                select(Node)
                .where(Node.node_type == "local")
                .order_by(Node.id)
                .limit(1)
            )
            aliases = effective_main_access_aliases(local)
            enabled = bool(local is not None and local.dns_only and aliases)
    except Exception:
        with _MAIN_HOST_POLICY_LOCK:
            if float(_MAIN_HOST_POLICY_CACHE.get("loaded_at") or 0.0) > 0:
                return (
                    bool(_MAIN_HOST_POLICY_CACHE.get("enabled")),
                    tuple(_MAIN_HOST_POLICY_CACHE.get("aliases") or ()),
                )
        return False, tuple()
    with _MAIN_HOST_POLICY_LOCK:
        _MAIN_HOST_POLICY_CACHE.update({
            "loaded_at": now,
            "enabled": enabled,
            "aliases": aliases,
            "panel_ip_whitelist": str(getattr(local, "panel_ip_whitelist", None) or "").strip() if local is not None else "",
            "panel_ip_blacklist": str(getattr(local, "panel_ip_blacklist", None) or "").strip() if local is not None else "",
            "panel_asn_whitelist": str(getattr(local, "panel_asn_whitelist", None) or "").strip() if local is not None else "",
            "panel_asn_blacklist": str(getattr(local, "panel_asn_blacklist", None) or "").strip() if local is not None else "",
            "ip_whitelist": str(getattr(local, "ip_whitelist", None) or "").strip() if local is not None else "",
            "ip_blacklist": str(getattr(local, "ip_blacklist", None) or "").strip() if local is not None else "",
            "asn_whitelist": str(getattr(local, "asn_whitelist", None) or "").strip() if local is not None else "",
            "asn_blacklist": str(getattr(local, "asn_blacklist", None) or "").strip() if local is not None else "",
        })
    return enabled, aliases


# STREAMFORGE_MAIN_LOCAL_ACCESS_POLICY_RUNTIME_V100R3:
def main_local_access_rules() -> dict[str, str]:
    """Return cached Local/Main Panel and playback access policies."""
    main_access_policy()
    keys = (
        "panel_ip_whitelist", "panel_ip_blacklist",
        "panel_asn_whitelist", "panel_asn_blacklist",
        "ip_whitelist", "ip_blacklist", "asn_whitelist", "asn_blacklist",
    )
    with _MAIN_HOST_POLICY_LOCK:
        return {key: str(_MAIN_HOST_POLICY_CACHE.get(key) or "").strip() for key in keys}


def _main_public_access_decision(request: Request, role: str):
    rules = main_local_access_rules()
    if role == "panel":
        return evaluate_access(
            request,
            ip_whitelist=rules.get("panel_ip_whitelist"),
            ip_blacklist=rules.get("panel_ip_blacklist"),
            asn_whitelist=rules.get("panel_asn_whitelist"),
            asn_blacklist=rules.get("panel_asn_blacklist"),
        )
    return evaluate_access(
        request,
        ip_whitelist=rules.get("ip_whitelist"),
        ip_blacklist=rules.get("ip_blacklist"),
        asn_whitelist=rules.get("asn_whitelist"),
        asn_blacklist=rules.get("asn_blacklist"),
    )


def _main_access_restricted_response(request: Request, role: str, reason: str) -> Response:
    path = str(request.scope.get("path") or request.url.path or "/").lower()
    accept = str(request.headers.get("accept") or "").lower()
    if role == "panel":
        _log_panel_access_denied(request, "Main panel access denied", reason=reason)
        if "application/json" in accept or path.endswith(".json"):
            return JSONResponse({"detail": "Access denied"}, status_code=403)
        title = "Panel access restricted"
        message = "This panel is unavailable from your current network."
    else:
        if (
            "/web-player" in path or path.endswith("/get.php") or path.endswith("/player_api.php")
            or "/playlist/" in path or "/player/" in path or "/watch/" in path
            or path.endswith("/master.m3u8")
        ):
            _log_client_denied(request, "Main playback/catalog access denied", reason=reason)
        if path.endswith("/player_api.php") or "application/json" in accept:
            return JSONResponse({"detail": "Access denied"}, status_code=403)
        if not ("text/html" in accept or "/web-player" in path or "/player/" in path or "/watch/" in path):
            return PlainTextResponse("Access denied", status_code=403)
        title = "Service unavailable"
        message = "This service is unavailable from your current network."
    # STREAMFORGE_MAIN_RESTRICTED_PAGE_RESPONSIVE_V101:
    page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{title}</title>
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;width:100%;height:100%;min-height:100%;overflow:hidden}}
body{{min-height:100vh;min-height:100dvh;display:grid;place-items:center;padding:clamp(10px,2.4vw,18px);background:#05090e;color:#edf4fb;font-family:system-ui,-apple-system,Segoe UI,sans-serif}}
.sf-restricted-card{{width:min(380px,calc(100vw - 24px));max-width:100%;padding:clamp(20px,4vw,28px) clamp(18px,4.2vw,26px);border:1px solid #233244;border-radius:clamp(12px,2.5vw,16px);background:#0b131d;box-shadow:0 16px 48px #0007;text-align:center}}
.sf-restricted-card h2{{margin:0 0 clamp(7px,1.5vw,10px);font-size:clamp(18px,4.6vw,22px);line-height:1.2}}
.sf-restricted-card p{{margin:0;color:#a9b9c8;line-height:1.4;white-space:nowrap;font-size:clamp(9px,2.8vw,14px)}}
@media(max-width:360px){{.sf-restricted-card{{width:calc(100vw - 18px);padding:18px 12px}}.sf-restricted-card p{{font-size:clamp(8px,2.7vw,10px)}}}}
@media(max-height:480px){{body{{padding:8px}}.sf-restricted-card{{padding:15px 16px}}.sf-restricted-card h2{{font-size:17px;margin-bottom:6px}}}}
</style></head><body>
<div class="sf-restricted-card"><h2>{title}</h2><p>{message}</p></div>
</body></html>'''
    return HTMLResponse(page, status_code=403, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-StreamForge-Version": APP_VERSION,
        "X-StreamForge-Route": "main-access-policy-denied",
    })


def request_main_authority(request: Request) -> tuple[str, str, int]:
    """Return the public request host and port preserved by the managed proxy."""
    proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",", 1)[0].strip().lower()
    host_value = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    try:
        parsed = urlsplit(f"//{host_value}")
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        explicit_port = parsed.port
    except ValueError:
        return proto, "", 0
    forwarded_port = str(request.headers.get("x-forwarded-port") or "").split(",", 1)[0].strip()
    try:
        port = int(explicit_port or forwarded_port or (443 if proto == "https" else 80))
    except (TypeError, ValueError):
        port = 443 if proto == "https" else 80
    return proto, host, max(1, min(65535, port))


def loopback_host_request(request: Request, request_host: str) -> bool:
    """Allow local health/service calls without creating a public IP bypass."""
    if request_host == "localhost":
        host_is_loopback = True
    else:
        try:
            host_is_loopback = ipaddress.ip_address(request_host).is_loopback
        except ValueError:
            host_is_loopback = False
    if not host_is_loopback:
        return False
    try:
        return ipaddress.ip_address(str(request.client.host if request.client else "")).is_loopback
    except ValueError:
        return False


MAIN_PLAYLIST_ROUTE_PREFIXES = (
    "/get.php", "/player_api.php", "/live/", "/play/", "/relay/",
    "/playlist/", "/player/", "/watch/", "/web-player", "/ek/", "/update.json",
)
MAIN_SHARED_ASSET_PREFIXES = ("/static/", "/channel-logos/", "/node-logos/", "/branding-assets/")


def main_route_role(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    # STREAMFORGE_ANDROID_UPDATE_PUBLIC_APK_ROLE_V3061:
    # One-segment APK filenames belong to the Playlist/App authority so the
    # update_url emitted by /update.json can be downloaded without Panel access.
    if normalized.count("/") == 1 and normalized.lower().endswith(".apk"):
        return "playlist"
    # STREAMFORGE_MAIN_WEB_PLAYER_ROLE_V2192:
    # Playlist route constants contain both exact files (/get.php) and path
    # roots (/web-player, /live/, /play/). Match both the exact route and its
    # descendants. The previous "prefix.endswith('/')" filter skipped
    # /web-player entirely, so /stream -> internal /web-player was incorrectly
    # treated as a panel route and redirected to /admin.
    for prefix in MAIN_PLAYLIST_ROUTE_PREFIXES:
        base = prefix.rstrip("/")
        if normalized == base or normalized.startswith(base + "/"):
            return "playlist"
    if any(normalized.startswith(prefix) for prefix in MAIN_SHARED_ASSET_PREFIXES):
        return "shared"
    return "panel"


# STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49:
# Main panel navigation uses full document reloads while keeping the public
# Panel/API root in the browser address bar.  The selected internal GET route
# is stored in a non-sensitive browser cookie and rewritten server-side only
# when the configured Panel/API root itself is requested. Permissions and
# route dependencies still execute normally after the rewrite.
STREAMFORGE_PANEL_ROUTE_COOKIE = "streamforge_panel_route"
STREAMFORGE_HIDDEN_PANEL_PREFIXES = (
    "/channels", "/nodes", "/categories", "/users", "/playlists", "/logs",
    "/admin-users", "/roles", "/system", "/viewer-sessions", "/account",
    "/no-access",
)


def normalize_hidden_panel_target(raw: str | None) -> str:
    value = urllib.parse.unquote(str(raw or "").strip())
    if not value or len(value) > 3500 or not value.startswith("/") or value.startswith("//"):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme or parts.netloc or parts.fragment:
        return ""
    path = parts.path or "/"
    if path != "/" and not any(path == prefix or path.startswith(prefix + "/") for prefix in STREAMFORGE_HIDDEN_PANEL_PREFIXES):
        return ""
    if main_route_role(path) != "panel":
        return ""
    target = path
    if parts.query:
        target += "?" + parts.query
    return target


def match_main_access_alias(
    request: Request,
    aliases: tuple[tuple[str, str, int, str, str], ...],
) -> tuple[str, frozenset[str]] | None:
    scheme, host, port = request_main_authority(request)
    if not host:
        return None
    path = str(request.scope.get("path") or "/")
    authority_aliases = [item for item in aliases if item[0] == scheme and item[1] == host and item[2] == port]
    if not authority_aliases:
        return None
    matching_slugs = sorted(
        {
            slug for _scheme, _host, _port, slug, _role in authority_aliases
            if slug and (path == f"/{slug}" or path.startswith(f"/{slug}/"))
        },
        key=len,
        reverse=True,
    )
    chosen_slug = matching_slugs[0] if matching_slugs else ""
    roles = frozenset(
        role for _scheme, _host, _port, slug, role in authority_aliases if slug == chosen_slug
    )
    if not roles:
        return None
    return chosen_slug, roles


# STREAMFORGE_CANONICAL_PROTOCOL_REDIRECT_V35:
def main_canonical_protocol_redirect_target(
    request: Request,
    aliases: tuple[tuple[str, str, int, str, str], ...],
) -> str:
    """Return the canonical configured-scheme URL for a wrong-protocol request.

    Each saved Main Panel/API or Playlist/App URL owns its protocol.  A request
    for the same hostname/path/role over the opposite protocol is redirected
    to the configured URL while preserving the public path and query string.
    Exact current-protocol aliases always win, so two different hostnames may
    intentionally use HTTP and HTTPS at the same time.
    """
    request_scheme, request_host, request_port = request_main_authority(request)
    if request_scheme not in {"http", "https"} or not request_host:
        return ""
    path = str(request.scope.get("path") or "/")
    candidates: list[tuple[int, int, str, int]] = []
    for index, (scheme, host, port, slug, role) in enumerate(aliases):
        if host != request_host:
            continue
        prefix = f"/{slug}" if slug else ""
        if prefix:
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            stripped = path[len(prefix):] or "/"
        else:
            stripped = path
        required_role = main_route_role(stripped)
        serves = bool(
            required_role == "shared"
            or required_role == role
            or (stripped == "/" and role in {"panel", "playlist"})
        )
        if not serves:
            continue
        if scheme == request_scheme and int(port) == int(request_port):
            return ""
        if scheme != request_scheme:
            candidates.append((len(prefix), -index, scheme, int(port)))
    if not candidates:
        return ""
    _prefix_len, _order, target_scheme, target_port = max(candidates, key=lambda item: (item[0], item[1]))
    safe_host = f"[{request_host}]" if ":" in request_host and not request_host.startswith("[") else request_host
    default_port = 443 if target_scheme == "https" else 80
    authority = safe_host if target_port == default_port else f"{safe_host}:{target_port}"
    query = bytes(request.scope.get("query_string") or b"").decode("latin-1")
    return urllib.parse.urlunsplit((target_scheme, authority, path, query, ""))


MAIN_ACCESS_RUNTIME_DIR = settings.main_access_runtime_dir
MAIN_ACCESS_REQUEST_FILE = MAIN_ACCESS_RUNTIME_DIR / "request.json"
MAIN_ACCESS_RESULT_FILE = MAIN_ACCESS_RUNTIME_DIR / "result.json"
MAIN_SYSTEM_RUNTIME_DIR = settings.main_access_runtime_dir.parent / "main-system-runtime"
MAIN_SYSTEM_REQUEST_FILE = MAIN_SYSTEM_RUNTIME_DIR / "request.json"
MAIN_RESTORE_INBOX = settings.main_access_runtime_dir.parent / "restore-inbox"
MAIN_RESTORE_ALLOWED_FILES = frozenset({
    "BACKUP-MANIFEST.txt",
    "streamforge-assets/ASSET-MANIFEST.json",
    "var/lib/streamforge/streamforge.db",
    "var/lib/streamforge/streamforge.db-wal",
    "var/lib/streamforge/streamforge.db-shm",
    "etc/streamforge.env",
})
MAIN_RESTORE_REQUIRED_FILES = frozenset({
    "BACKUP-MANIFEST.txt",
    "var/lib/streamforge/streamforge.db",
})


# STREAMFORGE_SHARED_RESTORE_ARCHIVE_POLICY_V3020: all three web restore
# entry points must use the same archive-member policy as the privileged root
# helper. Keeping one validator prevents uploaded, local and remote restores
# from drifting apart when a new managed asset tree is added.
def _validate_main_restore_archive(archive: Path) -> set[str]:
    found: set[str] = set()
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar.getmembers():
                name = str(member.name or "").lstrip("./")
                if not name:
                    continue
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Restore archive contains an unsafe path")
                allowed_logo = name == "opt/streamforge/logo" or name.startswith("opt/streamforge/logo/")
                allowed_assets = (
                    name in {"streamforge-assets/main-logo", "streamforge-assets/node-logo"}
                    or name.startswith(("streamforge-assets/main-logo/", "streamforge-assets/node-logo/"))
                )
                if name not in MAIN_RESTORE_ALLOWED_FILES and not allowed_logo and not allowed_assets:
                    raise ValueError(f"Unexpected file in restore archive: {name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError("Restore archive contains unsupported links or device files")
                if member.isfile():
                    found.add(name)
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"Restore archive could not be read: {exc}") from exc
    missing = sorted(MAIN_RESTORE_REQUIRED_FILES - found)
    if missing:
        raise ValueError("Restore archive is missing required Main Server backup files")
    return found


def _restore_recovery_access_url(request: Request) -> str:
    """Capture the exact browser-proven Main authority/prefix for restore."""
    scheme, host, port = request_main_authority(request)
    if scheme not in {"http", "https"} or not host:
        raise ValueError("Could not determine the active Main access URL")
    safe_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if scheme == "https" else 80
    authority = safe_host if int(port) == default_port else f"{safe_host}:{int(port)}"
    prefix = str(request.scope.get("root_path") or request.scope.get("state", {}).get("main_access_prefix") or "").strip()
    path = "/" + prefix.strip("/") if prefix.strip("/") else ""
    return urllib.parse.urlunsplit((scheme, authority, path, "", "")).rstrip("/")


def _write_main_system_request(
    action: str,
    admin: AdminUser,
    restore_path: str = "",
    recovery_url: str = "",
) -> str:
    """Queue a validated privileged Main service/server action via systemd."""
    if action not in {"restart_service", "reboot_server", "restore_backup"}:
        raise ValueError("Unsupported Main Server action")
    request_id = f"{int(time.time() * 1000)}-{secrets.token_hex(12)}"
    MAIN_SYSTEM_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": request_id,
        "action": action,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_by": admin.username,
        "app_version": APP_VERSION,
    }
    if action == "restore_backup":
        restore_file = Path(str(restore_path or "")).resolve()
        restore_root = MAIN_RESTORE_INBOX.resolve()
        if restore_file.parent != restore_root or not restore_file.is_file():
            raise ValueError("Invalid staged restore archive")
        payload["restore_path"] = str(restore_file)
        # STREAMFORGE_RESTORE_ACTIVE_URL_PAYLOAD_V3010: this URL was proven by
        # the authenticated restore request and is independently revalidated
        # by the privileged helper before it reaches SQLite/Nginx.
        parsed_recovery = urlsplit(str(recovery_url or "").strip())
        if (
            parsed_recovery.scheme not in {"http", "https"}
            or not parsed_recovery.hostname
            or parsed_recovery.username
            or parsed_recovery.password
            or parsed_recovery.query
            or parsed_recovery.fragment
        ):
            raise ValueError("Invalid active Main recovery URL")
        payload["recovery_url"] = str(recovery_url).strip().rstrip("/")
    temporary = MAIN_SYSTEM_RUNTIME_DIR / f".request-{request_id}.tmp"
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o640)
        temporary.replace(MAIN_SYSTEM_REQUEST_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    return request_id


def _write_main_access_request(request_id: str) -> None:
    """Atomically notify the root-owned systemd path helper without privilege escalation."""
    MAIN_ACCESS_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": request_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
    }
    temporary = MAIN_ACCESS_RUNTIME_DIR / f".request-{request_id}.tmp"
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o640)
        temporary.replace(MAIN_ACCESS_REQUEST_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def apply_main_access_runtime() -> str:
    """Apply Main listeners through a root systemd path/service handoff.

    The web process deliberately runs with ``NoNewPrivileges=true``.  It must
    never call sudo or a setuid helper.  Instead it atomically writes a request
    owned by the unprivileged StreamForge account; a root-owned systemd path
    unit activates the validated Nginx helper and publishes a matching result.
    """
    request_id = f"{int(time.time() * 1000)}-{secrets.token_hex(12)}"
    try:
        _write_main_access_request(request_id)
    except OSError as exc:
        raise RuntimeError(f"Could not queue Main listener update: {exc}") from exc

    deadline = time.monotonic() + 35.0
    last_detail = ""
    while time.monotonic() < deadline:
        try:
            result = json.loads(MAIN_ACCESS_RESULT_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            result = None
        except (OSError, ValueError, TypeError) as exc:
            last_detail = str(exc)
            result = None
        if isinstance(result, dict) and str(result.get("request_id") or "") == request_id:
            message = str(result.get("message") or "Main listener apply finished").strip()
            if bool(result.get("ok")):
                return message
            raise RuntimeError(message[-1200:] or "Main listener apply failed")
        time.sleep(0.10)

    suffix = f" Last result error: {last_detail}" if last_detail else ""
    raise RuntimeError(
        "Timed out waiting for the root-owned Main listener service. "
        "Check streamforge-main-access.path and streamforge-main-access.service." + suffix
    )


def normalized_native_control_port(saved_port: object, reported_port: object = 0) -> int:
    """Return the desired native Node listener port.

    Port 80 is authoritative once saved. A health response reached through the
    legacy 8810 listener must not silently undo the requested port-80 migration.
    """
    try:
        saved = max(0, min(65535, int(saved_port or 0)))
    except (TypeError, ValueError):
        saved = 0
    if saved:
        return saved
    try:
        reported = max(0, min(65535, int(reported_port or 0)))
    except (TypeError, ValueError):
        reported = 0
    return reported or 80


def urls_on_connected_listener(values: list[str], connected_base: str) -> list[str]:
    """Move broken default-web-port aliases onto the proven native listener."""
    try:
        connected = urlsplit(str(connected_base or "").strip())
        connected_port = int(connected.port or 0)
    except (TypeError, ValueError):
        return list(values)
    if not connected.hostname or not connected_port or connected_port in {80, 443}:
        return list(values)
    result: list[str] = []
    for value in values:
        try:
            parsed = urlsplit(value)
            effective_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        except (TypeError, ValueError):
            result.append(value)
            continue
        if effective_port not in {80, 443}:
            result.append(value)
            continue
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        rewritten = urllib.parse.urlunsplit((
            connected.scheme or "http",
            f"{host}:{connected_port}",
            parsed.path,
            "",
            "",
        )).rstrip("/")
        result.append(rewritten)
    return result


def node_public_base(node: Node) -> str:
    public = (getattr(node, "playlist_url", None) or "").strip().rstrip("/")
    return public or effective_node_url(node).strip().rstrip("/")


# STREAMFORGE_CLIENT_LOG_UNAUTHORIZED_ATTEMPTS_V99R17:
def _client_log_identity(request: Request, supplied: str = "") -> str:
    value = str(supplied or request.query_params.get("username") or "").strip()
    return value[:120] or "Guest"


def _log_client_denied(request: Request, message: str, *, username: str = "", reason: str = "", db: Session | None = None) -> None:
    details = {
        "ip": client_ip(request),
        "client": request.headers.get("user-agent", "")[:300],
        "path": str(request.url.path or "")[:300],
    }
    if reason:
        details["reason"] = str(reason)[:300]
    log_event(message, scope="client", level="warning", actor=_client_log_identity(request, username), details=details, db=db)


# STREAMFORGE_PANEL_POLICY_ACCESS_LOG_V103:
# Browser Panel authorization/policy failures belong in Access log (scope=auth),
# not Client log. Playback/WebPlayer/Xtream denials remain Client events.
def _log_panel_access_denied(request: Request, message: str, *, username: str = "", reason: str = "", level: str = "warning", db: Session | None = None) -> None:
    details = {
        "ip": client_ip(request),
        "client": request.headers.get("user-agent", "")[:300],
        "path": str(request.url.path or "")[:300],
    }
    if reason:
        details["reason"] = str(reason)[:500]
    log_event(message, scope="auth", level=level, actor=_client_log_identity(request, username), details=details, db=db)


def enforce_node_access_policy(request: Request, node: Node) -> None:
    decision = evaluate_access(
        request,
        ip_whitelist=node.ip_whitelist,
        ip_blacklist=node.ip_blacklist,
        asn_whitelist=node.asn_whitelist,
        asn_blacklist=node.asn_blacklist,
    )
    if not decision.allowed:
        path = str(request.url.path or "").lower()
        if ("/web-player" in path or path.endswith("/get.php") or path.endswith("/player_api.php") or "/playlist/" in path):
            # Use a separate audit transaction because HTTPException rolls back the request session.
            _log_client_denied(request, f"Client access denied — {decision.reason}", reason=decision.reason)
        raise HTTPException(403, decision.reason)


def enforce_stream_user_source(user: StreamUser, request: Request) -> None:
    if user.user_type != "restream":
        return
    source_ip = client_ip(request)
    if not (user.restream_allowed_ips or "").strip() or not ip_matches(user.restream_allowed_ips, source_ip):
        raise HTTPException(403, "This IP is not allowed for the restream account")


# STREAMFORGE_MAIN_VIEWER_GEO_BACKGROUND_V1126:
# Live Sessions must stay non-blocking even when IPinfo is selected.  Reuse a
# small local cache and resolve missing GeoIP records on a bounded executor; the
# browser request gets cached data immediately and the next AJAX refresh fills
# Business/ASN/Country without waiting on the network.
_VIEWER_GEO_LOCK = threading.RLock()
_VIEWER_GEO_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_VIEWER_GEO_PENDING: set[str] = set()
_VIEWER_GEO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sf-viewer-geo")


def _resolve_viewer_geo_background(ip_text: str) -> None:
    try:
        value = geo_details(ip_text)
        payload = dict(value) if isinstance(value, dict) else {}
    except Exception as exc:
        payload = {"ipinfo_error": str(exc)}
    with _VIEWER_GEO_LOCK:
        _VIEWER_GEO_CACHE[ip_text] = (time.monotonic(), payload)
        _VIEWER_GEO_PENDING.discard(ip_text)


def viewer_geo_cached(ip_text: str) -> dict[str, object]:
    raw = str(ip_text or "").strip()
    try:
        normalized = str(ipaddress.ip_address(raw))
    except ValueError:
        return {}
    now = time.monotonic()
    with _VIEWER_GEO_LOCK:
        cached = _VIEWER_GEO_CACHE.get(normalized)
        if cached:
            age = now - float(cached[0])
            value = dict(cached[1])
            # Successful GeoIP records are stable for six hours. Failed/empty
            # records retry quickly so a newly-saved token/database appears.
            ttl = 30.0 if value.get("ipinfo_error") or not any(value.get(k) for k in ("asn", "business_name", "country_name", "country_code")) else 21600.0
            if age < ttl:
                return value
        if normalized not in _VIEWER_GEO_PENDING:
            _VIEWER_GEO_PENDING.add(normalized)
            _VIEWER_GEO_EXECUTOR.submit(_resolve_viewer_geo_background, normalized)
        # Keep stale useful data visible while it refreshes.
        return dict(cached[1]) if cached else {}


def central_viewer_session_details(
    db: Session,
    *,
    node_id: int | None = None,
    channel_id: int | None = None,
    include_geo: bool = True,
) -> list[dict[str, object]]:
    snapshot = viewer_tracker.snapshot()
    sessions = [
        item for item in snapshot.sessions
        if (node_id is None or item.node_id == node_id)
        and (channel_id is None or item.channel_id == channel_id)
    ]
    if not sessions:
        return []
    user_ids = {item.user_id for item in sessions}
    channel_ids = {item.channel_id for item in sessions}
    node_ids = {item.node_id for item in sessions}
    users = {
        item.id: item
        for item in db.scalars(select(StreamUser).where(StreamUser.id.in_(user_ids))).all()
    }
    channels = {
        item.id: item
        for item in db.scalars(select(Channel).where(Channel.id.in_(channel_ids))).all()
    }
    nodes = {
        item.id: item
        for item in db.scalars(select(Node).where(Node.id.in_(node_ids))).all()
    }
    # STREAMFORGE_MAIN_STATUS_NO_GEO_NETWORK_V34:
    # Hot status/count callers do not need ASN/country. In auto/IPinfo mode a
    # cache miss can otherwise perform external HTTP lookups while the panel is
    # polling, consuming worker threads and making the Main web appear frozen.
    unique_ips = {item.client_ip for item in sessions if str(item.client_ip or "").strip()}
    geo_by_ip = (
        {ip: geo_details(ip) for ip in unique_ips}
        if include_geo else {ip: viewer_geo_cached(ip) for ip in unique_ips}
    )
    now_utc = datetime.now(timezone.utc)
    details: list[dict[str, object]] = []
    for item in sessions:
        idle_seconds = max(0, int(snapshot.captured_monotonic - item.last_seen_monotonic))
        duration_seconds = max(0, int(snapshot.captured_monotonic - item.first_seen_monotonic))
        user = users.get(item.user_id)
        channel = channels.get(item.channel_id)
        node = nodes.get(item.node_id)
        geo = geo_by_ip.get(item.client_ip, {})
        details.append({
            "session_id": item.session_id,
            "source": "Main Panel proxy",
            "source_key": "main_proxy",
            "user_id": item.user_id,
            "user_name": user.name if user else f"User #{item.user_id}",
            "client_ip": item.client_ip,
            "asn": geo.get("asn"),
            "business_name": geo.get("business_name") or "",
            "country_name": geo.get("country_name") or "",
            "country_code": geo.get("country_code") or "",
            "node_id": item.node_id,
            "node_name": node.name if node else f"Node #{item.node_id}",
            "channel_id": item.channel_id,
            "channel_name": channel.name if channel else f"Channel #{item.channel_id}",
            "channel_key": node_controller._channel_key(channel) if channel else "",
            "started_at": (now_utc - timedelta(seconds=duration_seconds)).isoformat(),
            "last_seen_at": (now_utc - timedelta(seconds=idle_seconds)).isoformat(),
            "duration_seconds": duration_seconds,
            "idle_seconds": idle_seconds,
            "user_agent": item.user_agent,
        })
    return sorted(details, key=lambda item: (int(item.get("idle_seconds") or 0), str(item.get("user_name") or "")))


# STREAMFORGE_MAIN_ONLINE_USERS_BACKGROUND_CACHE_V1123:
# Live Sessions is a panel observation page.  Never make the browser request
# wait on Remote Node control HTTP or external GeoIP.  Detailed Remote rows are
# refreshed in one throttled daemon job while HTML/JSON requests consume only
# recent cache plus the existing heartbeat count fallback.
_REMOTE_VIEWER_DETAIL_LOCK = threading.RLock()
_REMOTE_VIEWER_DETAIL_CACHE: dict[int, tuple[float, dict[str, object]]] = {}
_REMOTE_VIEWER_DETAIL_REFRESHING = False
_REMOTE_VIEWER_DETAIL_LAST_START = 0.0
_REMOTE_VIEWER_DETAIL_TTL_SECONDS = 20.0
_REMOTE_VIEWER_DETAIL_REFRESH_MIN_SECONDS = 2.0


def cached_remote_viewer_payload(node_id: int) -> dict[str, object] | None:
    now = time.monotonic()
    with _REMOTE_VIEWER_DETAIL_LOCK:
        cached = _REMOTE_VIEWER_DETAIL_CACHE.get(int(node_id))
        if not cached:
            return None
        if now - float(cached[0]) > _REMOTE_VIEWER_DETAIL_TTL_SECONDS:
            return None
        return dict(cached[1])


def _cache_empty_remote_viewer_payload(node_id: int) -> None:
    with _REMOTE_VIEWER_DETAIL_LOCK:
        _REMOTE_VIEWER_DETAIL_CACHE[int(node_id)] = (time.monotonic(), {
            "ok": True, "total_users": 0,
            "direct_total_users": 0, "direct_sessions": [],
        })


# STREAMFORGE_MAIN_VIEWER_HEARTBEAT_DETAIL_PREFETCH_V1126:
# A Node heartbeat already tells Main whether direct viewers exist.  When it
# reports viewers, refresh only that Node's detailed rows in the background so
# clicking Online Users does not show a count-only placeholder first.
_REMOTE_VIEWER_NODE_REFRESHING: set[int] = set()
_REMOTE_VIEWER_NODE_LAST_START: dict[int, float] = {}
_REMOTE_VIEWER_NODE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sf-viewer-node-cache")


def _refresh_remote_viewer_node_worker(node_id: int) -> None:
    try:
        with SessionLocal() as session:
            node = session.get(Node, int(node_id))
            if not node or not node.enabled or node.node_type != "remote":
                _cache_empty_remote_viewer_payload(int(node_id))
                return
            session.expunge(node)
        payload = node_controller.viewer_sessions(node, timeout=3.0)
        if not isinstance(payload, dict):
            return
        try:
            fresh_total = max(0, int(payload.get("direct_total_users") or payload.get("total_users") or 0))
        except (TypeError, ValueError):
            fresh_total = 0
        with _REMOTE_VIEWER_DETAIL_LOCK:
            _REMOTE_VIEWER_DETAIL_CACHE[int(node_id)] = (time.monotonic(), dict(payload))
        live_summary = cached_node_live_summary(int(node_id))
        live_summary["active_connections"] = fresh_total
        cache_node_live_summary(int(node_id), live_summary)
    except Exception:
        # Keep the last successful snapshot until its short TTL expires.
        pass
    finally:
        with _REMOTE_VIEWER_DETAIL_LOCK:
            _REMOTE_VIEWER_NODE_REFRESHING.discard(int(node_id))


def request_remote_viewer_node_refresh(node_id: int, *, active_hint: int | None = None) -> None:
    node_key = int(node_id)
    if active_hint is not None and int(active_hint) <= 0:
        _cache_empty_remote_viewer_payload(node_key)
        return
    now = time.monotonic()
    with _REMOTE_VIEWER_DETAIL_LOCK:
        if node_key in _REMOTE_VIEWER_NODE_REFRESHING:
            return
        if now - float(_REMOTE_VIEWER_NODE_LAST_START.get(node_key, 0.0)) < 2.0:
            return
        _REMOTE_VIEWER_NODE_REFRESHING.add(node_key)
        _REMOTE_VIEWER_NODE_LAST_START[node_key] = now
    _REMOTE_VIEWER_NODE_EXECUTOR.submit(_refresh_remote_viewer_node_worker, node_key)


def _refresh_remote_viewer_details_worker() -> None:
    global _REMOTE_VIEWER_DETAIL_REFRESHING
    try:
        with SessionLocal() as session:
            node_ids = list(session.execute(
                select(Node.id).where(Node.enabled.is_(True), Node.node_type == "remote").order_by(Node.id)
            ).scalars().all())
        for node_id in node_ids:
            summary = cached_node_live_summary(int(node_id))
            active_hint = int(summary.get("active_connections") or 0) if summary else None
            request_remote_viewer_node_refresh(int(node_id), active_hint=active_hint)
    finally:
        with _REMOTE_VIEWER_DETAIL_LOCK:
            _REMOTE_VIEWER_DETAIL_REFRESHING = False


def request_remote_viewer_details_refresh() -> None:
    global _REMOTE_VIEWER_DETAIL_REFRESHING, _REMOTE_VIEWER_DETAIL_LAST_START
    now = time.monotonic()
    with _REMOTE_VIEWER_DETAIL_LOCK:
        if _REMOTE_VIEWER_DETAIL_REFRESHING:
            return
        if now - float(_REMOTE_VIEWER_DETAIL_LAST_START) < _REMOTE_VIEWER_DETAIL_REFRESH_MIN_SECONDS:
            return
        _REMOTE_VIEWER_DETAIL_REFRESHING = True
        _REMOTE_VIEWER_DETAIL_LAST_START = now
    threading.Thread(
        target=_refresh_remote_viewer_details_worker,
        name="streamforge-viewer-detail-cache",
        daemon=True,
    ).start()


def collect_viewer_stats(
    db: Session,
    *,
    allow_remote_fetch: bool = False,
    include_geo: bool = True,
) -> dict[str, object]:
    """Merge central and direct-node playback sessions by session ID.

    Counts intentionally represent active sessions/devices, not just unique
    account names. Two devices using the same account, IP and channel therefore
    appear as two sessions when they have different playback session IDs.
    """
    session_details = central_viewer_session_details(db, include_geo=include_geo)
    # Loading every Node's assigned channels lazily creates one extra SQL query
    # per Node.  Keep cached/non-blocking callers cheap even with a large Node
    # cluster by fetching the relationship in one bounded query.
    nodes = db.scalars(
        select(Node)
        .options(selectinload(Node.channels))
        .where(Node.enabled.is_(True))
        .order_by(Node.id)
    ).all()
    # STREAMFORGE_VIEWER_STATS_RELEASE_DB_V32:
    # Remote control calls are network I/O and may hit their timeout. The local
    # viewer rows and Node/channel relationships are fully loaded now, so return
    # the connection to the pool before contacting Remote Nodes.
    # STREAMFORGE_MAIN_ONLINE_USERS_NO_CACHE_COMMIT_V1123: cache-only browser
    # requests do not perform Remote I/O, so do not expire every eager-loaded ORM
    # relationship with commit() and trigger lazy reloads while formatting rows.
    if allow_remote_fetch:
        db.commit()

    # STREAMFORGE_MAIN_REMOTE_LIVE_SESSIONS_V89:
    # /health intentionally remains count-only. Detailed Remote Node viewer rows
    # are fetched from the dedicated endpoint only when a caller explicitly
    # allows remote I/O (Live Sessions page/live-data). Fetch Nodes concurrently
    # so one unavailable Node cannot serially stall the entire session view.
    remote_nodes = [node for node in nodes if node.node_type == "remote"]
    remote_payloads: dict[int, dict[str, object]] = {}
    # STREAMFORGE_MAIN_CACHED_REMOTE_VIEWER_COUNTS_V90:
    # Hot pages (Dashboard/Channels/Nodes) must stay non-blocking, but their
    # online totals still need the most recent direct-Node count from cached
    # health/heartbeat data. Detailed rows remain on-demand only.
    count_only_by_node: dict[int, int] = {}
    if allow_remote_fetch and remote_nodes:
        workers = max(1, min(8, len(remote_nodes)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sf-viewer-node") as executor:
            futures = {executor.submit(node_controller.viewer_sessions, node): node for node in remote_nodes}
            for future in as_completed(futures):
                node = futures[future]
                try:
                    payload = future.result()
                except (NodeError, Exception):
                    continue
                if isinstance(payload, dict):
                    remote_payloads[int(node.id)] = payload
                    # STREAMFORGE_MAIN_LIVE_SESSION_DASHBOARD_SYNC_V90R2:
                    # A detailed Live Sessions fetch is fresher than health/heartbeat.
                    # Feed its direct count into the same non-blocking summary cache
                    # used by Dashboard/Channels so both views agree immediately.
                    try:
                        fresh_total = max(0, int(payload.get("direct_total_users") or payload.get("total_users") or 0))
                    except (TypeError, ValueError):
                        fresh_total = 0
                    live_summary = cached_node_live_summary(int(node.id))
                    live_summary["active_connections"] = fresh_total
                    cache_node_live_summary(int(node.id), live_summary)
    else:
        # STREAMFORGE_MAIN_ONLINE_USERS_CACHED_DETAILS_V1123:
        # Panel requests use recent detailed rows produced by the background
        # refresher.  If the cache is cold/stale, fall back to count-only
        # heartbeat/health state without performing network I/O here.
        for node in remote_nodes:
            cached_detail = cached_remote_viewer_payload(int(node.id))
            if isinstance(cached_detail, dict):
                remote_payloads[int(node.id)] = cached_detail
                continue
            try:
                health = node_controller.cached_health(node, max_age=30.0)
            except NodeError:
                health = None
            if isinstance(health, dict):
                health_viewers = health.get("viewer_stats") or {}
                cached_total = int(health_viewers.get("total_users") or health.get("active_connections") or 0)
                remote_payloads[int(node.id)] = {
                    "direct_sessions": list(health_viewers.get("direct_sessions") or []),
                    "direct_total_users": max(0, cached_total),
                }
                continue
            # Heartbeat summary is fresher than the optional health cache and is
            # already in Main memory; use it without any Remote Node I/O.
            live_summary = cached_node_live_summary(int(node.id))
            if live_summary:
                remote_payloads[int(node.id)] = {
                    "direct_sessions": [],
                    "direct_total_users": max(0, int(live_summary.get("active_connections") or 0)),
                }

    for node in remote_nodes:
        payload = remote_payloads.get(int(node.id)) or {}
        direct_sessions = payload.get("direct_sessions") or []
        if not allow_remote_fetch and not direct_sessions:
            count_only_by_node[int(node.id)] = max(0, int(payload.get("direct_total_users") or 0))
        channel_by_key = {node_controller._channel_key(channel): channel for channel in node.channels}
        for raw in direct_sessions:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("channel_key") or "")
            channel = channel_by_key.get(key)
            item = dict(raw)
            # STREAMFORGE_MAIN_REMOTE_SESSION_TIME_NORMALIZE_V1049: prefer the
            # Node's numeric ages, but reconstruct them from ISO timestamps when
            # an older/rolling-update Node omitted the numeric fields.
            now_remote = datetime.now(timezone.utc)
            if item.get("duration_seconds") in (None, ""):
                try:
                    started = datetime.fromisoformat(str(item.get("started_at") or "").replace("Z", "+00:00"))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    item["duration_seconds"] = max(0, int((now_remote - started).total_seconds()))
                except (TypeError, ValueError):
                    item["duration_seconds"] = 0
            if item.get("idle_seconds") in (None, ""):
                try:
                    last_seen = datetime.fromisoformat(str(item.get("last_seen_at") or "").replace("Z", "+00:00"))
                    if last_seen.tzinfo is None:
                        last_seen = last_seen.replace(tzinfo=timezone.utc)
                    item["idle_seconds"] = max(0, int((now_remote - last_seen).total_seconds()))
                except (TypeError, ValueError):
                    item["idle_seconds"] = 0
            item.update({
                "source": "Direct node playlist",
                "source_key": "direct_node",
                "node_id": node.id,
                "node_name": node.name,
                "channel_id": channel.id if channel else None,
                "channel_name": channel.name if channel else str(raw.get("channel_name") or key or "Unknown"),
            })
            geo_fields = ("asn", "business_name", "country_name", "country_code")
            if item.get("client_ip") and any(item.get(field) in (None, "") for field in geo_fields):
                resolved_geo = (
                    geo_details(str(item.get("client_ip")))
                    if include_geo else viewer_geo_cached(str(item.get("client_ip")))
                )
                for field in geo_fields:
                    if item.get(field) in (None, ""):
                        item[field] = resolved_geo.get(field)
            session_details.append(item)

    deduped_sessions: list[dict[str, object]] = []
    seen_sessions: set[str] = set()
    by_node_sets: dict[int, set[str]] = {}
    by_channel_sets: dict[int, set[str]] = {}
    source_sessions: dict[str, set[str]] = {"main_proxy": set(), "direct_node": set()}
    account_ids: set[str] = set()
    for item in session_details:
        session_id = str(item.get("session_id") or "").strip()
        if not session_id:
            session_id = hashlib.sha256(
                "|".join([
                    str(item.get("user_id") or item.get("user_name") or "unknown"),
                    str(item.get("client_ip") or ""),
                    str(item.get("node_id") or ""),
                    str(item.get("channel_id") or item.get("channel_key") or ""),
                    str(item.get("user_agent") or ""),
                ]).encode("utf-8", errors="ignore")
            ).hexdigest()[:24]
            item["session_id"] = session_id
        if session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        deduped_sessions.append(item)
        source_key = str(item.get("source_key") or "")
        source_sessions.setdefault(source_key, set()).add(session_id)
        user_id = item.get("user_id")
        account_ids.add(f"user:{user_id}" if user_id not in (None, "", 0, "0") else f"name:{item.get('user_name') or 'unknown'}")
        node_id = int(item.get("node_id")) if item.get("node_id") not in (None, "") else None
        channel_id = int(item.get("channel_id")) if item.get("channel_id") not in (None, "") else None
        if node_id is not None:
            by_node_sets.setdefault(node_id, set()).add(session_id)
        if channel_id is not None:
            by_channel_sets.setdefault(channel_id, set()).add(session_id)

    deduped_sessions.sort(key=lambda item: (int(item.get("idle_seconds") or 0), str(item.get("user_name") or "")))
    detailed_by_node = {key: len(value) for key, value in by_node_sets.items()}
    if not allow_remote_fetch:
        for node_key, cached_count in count_only_by_node.items():
            # Cached Node health is direct-node-only in v9.0. Central proxy
            # sessions are already represented by viewer_tracker, so adding the
            # cached count cannot double-count Main-proxy delivery.
            detailed_by_node[node_key] = int(detailed_by_node.get(node_key, 0)) + int(cached_count)
    cached_direct_total = sum(int(value) for value in count_only_by_node.values()) if not allow_remote_fetch else 0
    return {
        "total_users": len(deduped_sessions) + cached_direct_total,
        "unique_accounts": len(account_ids),
        "central_users": len(source_sessions.get("main_proxy", set())),
        "direct_node_users": len(source_sessions.get("direct_node", set())) + cached_direct_total,
        "by_node": detailed_by_node,
        "by_channel": {key: len(value) for key, value in by_channel_sets.items()},
        "sessions": deduped_sessions,
    }


# STREAMFORGE_MAIN_HOT_VIEWER_COUNTS_V114:
_FAST_VIEWER_COUNTS_CACHE_LOCK = threading.RLock()
_FAST_VIEWER_COUNTS_CACHE: dict[str, object] = {
    "loaded_at": 0.0,
    "value": {"total_users": 0, "by_node": {}, "by_channel": {}},
}


def collect_viewer_counts_fast(db: Session) -> dict[str, object]:
    """Return hot-page viewer counters without session-detail DB expansion.

    Channels/status polling needs only totals. Resolving every active session to
    User/Channel/Node ORM rows and committing a read transaction every 3 seconds
    made panel navigation unnecessarily expensive. This path consumes the
    in-memory Main tracker plus already-cached Node health only; it performs no
    Remote Node request and no GeoIP lookup.

    STREAMFORGE_MAIN_VIEWER_COUNT_MICROCACHE_V116: Multiple panel widgets can
    request the same aggregate within a second. Reuse a short immutable snapshot
    instead of re-expanding Node/channel mappings for each near-simultaneous
    request. This never affects connection enforcement or Live Sessions detail.
    """
    now = time.monotonic()
    with _FAST_VIEWER_COUNTS_CACHE_LOCK:
        loaded_at = float(_FAST_VIEWER_COUNTS_CACHE.get("loaded_at") or 0.0)
        cached = _FAST_VIEWER_COUNTS_CACHE.get("value")
        if isinstance(cached, dict) and now - loaded_at < 1.5:
            return {
                "total_users": int(cached.get("total_users") or 0),
                "by_node": dict(cached.get("by_node") or {}),
                "by_channel": dict(cached.get("by_channel") or {}),
            }

    snapshot = viewer_tracker.snapshot()
    seen: set[str] = set()
    by_channel_sets: dict[int, set[str]] = {}
    by_node_sets: dict[int, set[str]] = {}

    for item in snapshot.sessions:
        sid = str(getattr(item, "session_id", "") or "").strip()
        if not sid:
            sid = f"main:{getattr(item, 'user_id', 0)}:{getattr(item, 'client_ip', '')}:{getattr(item, 'node_id', 0)}:{getattr(item, 'channel_id', 0)}:{getattr(item, 'user_agent', '')}"
        if sid in seen:
            continue
        seen.add(sid)
        try:
            node_id = int(getattr(item, "node_id", 0) or 0)
            channel_id = int(getattr(item, "channel_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if node_id > 0:
            by_node_sets.setdefault(node_id, set()).add(sid)
        if channel_id > 0:
            by_channel_sets.setdefault(channel_id, set()).add(sid)

    # STREAMFORGE_MAIN_VIEWER_COUNTS_NO_NODE_RELATIONSHIPS_V1114:
    # Hot panel counters must never eager-load Node.channels.  Cached Node
    # health already carries direct-session channel keys in the form
    # ``sf-<channel_id>-<slug>``; parse the id directly and keep the aggregate
    # request to one lightweight Node row query with no relationship expansion.
    cached_count_only = 0
    remote_nodes = db.scalars(
        select(Node)
        .where(Node.enabled.is_(True), Node.node_type == "remote")
        .order_by(Node.id)
    ).all()
    for node in remote_nodes:
        try:
            health = node_controller.cached_health(node, max_age=30.0)
        except NodeError:
            health = None
        if not isinstance(health, dict):
            live_summary = cached_node_live_summary(int(node.id))
            if live_summary:
                cached_count_only += max(0, int(live_summary.get("active_connections") or 0))
            continue
        health_viewers = health.get("viewer_stats") or {}
        direct_sessions = list(health_viewers.get("direct_sessions") or [])
        if not direct_sessions:
            cached_count_only += max(0, int(health_viewers.get("total_users") or health.get("active_connections") or 0))
            continue
        for raw in direct_sessions:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("session_id") or "").strip()
            if not sid:
                sid = f"node:{node.id}:{raw.get('user_id') or raw.get('user_name') or ''}:{raw.get('client_ip') or ''}:{raw.get('channel_key') or ''}:{raw.get('user_agent') or ''}"
            if sid in seen:
                continue
            seen.add(sid)
            by_node_sets.setdefault(int(node.id), set()).add(sid)
            channel_key = str(raw.get("channel_key") or "").strip()
            match = re.match(r"^sf-(\d+)-", channel_key)
            if match:
                try:
                    channel_id = int(match.group(1))
                except (TypeError, ValueError):
                    channel_id = 0
                if channel_id > 0:
                    by_channel_sets.setdefault(channel_id, set()).add(sid)

    result = {
        "total_users": len(seen) + cached_count_only,
        "by_node": {key: len(value) for key, value in by_node_sets.items()},
        "by_channel": {key: len(value) for key, value in by_channel_sets.items()},
    }
    with _FAST_VIEWER_COUNTS_CACHE_LOCK:
        _FAST_VIEWER_COUNTS_CACHE["loaded_at"] = time.monotonic()
        _FAST_VIEWER_COUNTS_CACHE["value"] = {
            "total_users": int(result["total_users"]),
            "by_node": dict(result["by_node"]),
            "by_channel": dict(result["by_channel"]),
        }
    return result


def _metrics_retention_days() -> int:
    with SessionLocal() as db:
        return max(1, min(3650, app_setting_int(db, METRICS_RETENTION_DAYS_KEY, 30)))


def _metrics_online_users() -> int:
    # STREAMFORGE_MAIN_METRICS_CACHE_ONLY_VIEWERS_V1114:
    # History sampling is background telemetry; it must not resolve full viewer
    # detail or perform Remote Node requests. Reuse the same cache-only aggregate
    # as the hot panel status path.
    with SessionLocal() as db:
        return int(collect_viewer_counts_fast(db).get("total_users") or 0)


def _metrics_sample_interval_seconds() -> int:
    with SessionLocal() as db:
        return max(5, min(3600, app_setting_int(db, METRICS_SAMPLE_INTERVAL_SECONDS_KEY, 60)))


# STREAMFORGE_MAIN_METRICS_PLAYBACK_READY_CHANNELS_V1024:
def _metrics_channel_counts() -> dict[str, int]:
    """Count Main-local playable channels without Remote Node I/O.

    STREAMFORGE_MAIN_METRICS_LOCAL_CACHE_ONLY_CHANNELS_V1114: metrics history is
    not a control-plane health check.  The former remote-fetching batch
    could hold background workers on every Remote Node timeout and compete with
    Panel requests.  Count Main FFmpeg state plus the existing asynchronous HLS
    readiness cache only; Node heartbeat/status paths maintain Remote summaries.
    """
    with SessionLocal() as db:
        total = int(db.scalar(select(func.count(Channel.id))) or 0)
        channels = list(db.scalars(
            select(Channel)
            .options(selectinload(Channel.nodes), selectinload(Channel.node))
            .where(Channel.enabled.is_(True), Channel.output_type == "hls")
            # STREAMFORGE_DASHBOARD_LIVE_CHANNEL_ID_ORDER_V65R10:
        # Keep Dashboard Live rows stable by database Channel ID.
        .order_by(Channel.id)
        ).all())
        online = 0
        for channel in channels:
            runtime = main_channel_runtime(channel)
            if bool(runtime.get("alive")) and bool(runtime.get("hls_ready")):
                online += 1
    return {"total": total, "online": int(online)}


metrics_history = MetricsHistory(
    Path("/var/lib/streamforge/metrics-history.jsonl"),
    system_metrics.snapshot,
    _metrics_online_users,
    _metrics_retention_days,
    _metrics_sample_interval_seconds,
    _metrics_channel_counts,
)


def _backup_scheduler_loop() -> None:
    while True:
        try:
            run_due_backup_targets()
        except Exception:
            pass
        time.sleep(60)


def _sync_main_base(request_or_url: Request | str) -> str:
    if isinstance(request_or_url, Request):
        with SessionLocal() as db:
            return main_panel_public_base(db, request_or_url)
    return str(request_or_url or "").strip().rstrip("/")


# STREAMFORGE_RESTORE_REMOTE_LOGO_RESYNC_V3017:
# The privileged restore helper writes this marker only after local logo files
# pass checksum verification. Once Main is healthy, push restored Node
# branding/favicons and channel logos back to every enabled Remote Node.
def _resync_restored_logo_assets(*, startup_delay: float = 5.0) -> None:
    marker = Path("/var/lib/streamforge/restore-logo-sync.pending")
    if not marker.is_file():
        return
    if startup_delay > 0:
        time.sleep(startup_delay)
    errors: list[str] = []
    with SessionLocal() as db:
        local = db.scalar(select(Node).where(Node.node_type == "local").order_by(Node.id))
        panel_url = (
            _first_access_url((local.api_urls or local.api_url) if local else "")
            or settings.public_base_url.rstrip("/")
        )
        remote_ids = [
            int(item.id) for item in db.scalars(
                select(Node).where(Node.enabled.is_(True), Node.node_type == "remote").order_by(Node.name)
            ).all()
        ]
        channel_ids = [
            int(item.id) for item in db.scalars(
                select(Channel).where(Channel.logo_url.like("/channel-logos/%")).order_by(Channel.id)
            ).all()
        ]
    for node_id in remote_ids:
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            if node is None:
                continue
            try:
                node_controller.sync_panel_users(node, panel_url)
            except NodeError as exc:
                errors.append(f"{node.name} branding: {exc}")
    for channel_id in channel_ids:
        errors.extend(node_controller.sync_channel_logo_to_all_nodes(channel_id))
    if errors:
        log_event(
            "Restored logo resync incomplete; it will retry after the next Main restart",
            scope="backup",
            level="warning",
            details="; ".join(errors[:20]),
        )
        return
    marker.unlink(missing_ok=True)
    log_event(
        "Restored logo assets verified and synchronized",
        scope="backup",
        details=f"remote_nodes={len(remote_ids)}; channel_logos={len(channel_ids)}",
    )


def _run_channel_save_sync(
    channel_id: int,
    remote_node_ids: set[int],
    removed_node_ids: set[int],
    panel_url: str,
    *,
    sync_logo: bool,
) -> None:
    """Propagate one committed channel edit without delaying its HTTP response."""
    errors: list[str] = []
    for removed_node_id in sorted({int(item) for item in removed_node_ids}):
        try:
            node_controller.forget_on_node(int(channel_id), int(removed_node_id))
        except Exception as exc:
            errors.append(f"Remove old node {removed_node_id}: {exc}")

    try:
        errors.extend(
            node_controller.sync_channel_to_nodes(
                int(channel_id),
                restart_running=False,
                node_ids={int(item) for item in remote_node_ids},
                sync_logo=bool(sync_logo),
            )
        )
    except Exception as exc:
        errors.append(f"Node configuration sync: {exc}")

    if remote_node_ids:
        try:
            with SessionLocal() as sync_db:
                errors.extend(
                    sync_direct_users_for_node_ids(
                        {int(item) for item in remote_node_ids},
                        panel_url,
                        sync_db,
                    )
                )
        except Exception as exc:
            errors.append(f"Direct playlist user sync: {exc}")

    if errors:
        try:
            log_event(
                "Channel background sync incomplete",
                scope="channel",
                level="warning",
                channel_id=int(channel_id),
                details="; ".join(str(item) for item in errors if item)[-4000:],
            )
        except Exception:
            pass


def queue_channel_save_sync(
    channel_id: int,
    remote_node_ids: set[int],
    panel_url: str,
    *,
    removed_node_ids: set[int] | None = None,
    sync_logo: bool = False,
) -> None:
    # One worker preserves save ordering. Each job re-reads current DB state,
    # so a slow/offline Node cannot hold the browser POST open.
    try:
        _CHANNEL_SAVE_SYNC_EXECUTOR.submit(
            _run_channel_save_sync,
            int(channel_id),
            {int(item) for item in remote_node_ids},
            {int(item) for item in (removed_node_ids or set())},
            str(panel_url or "").strip().rstrip("/"),
            sync_logo=bool(sync_logo),
        )
    except RuntimeError as exc:
        log_event(
            "Channel background sync could not be queued",
            scope="channel",
            level="warning",
            channel_id=int(channel_id),
            details=str(exc),
        )


# STREAMFORGE_BULK_PROFILE_ASYNC_APPLY_V1119:
# Bulk Encoding Profile changes are database-first, just like single-channel
# saves.  Restarting Local FFmpeg and reconciling Remote Nodes can involve
# bounded network waits and process shutdown/startup, so none of that work may
# keep the browser POST open.  The worker re-reads committed channel state via
# node_controller.sync_channel_to_nodes().
def _run_bulk_profile_sync(
    targets: list[tuple[int, str, set[int] | None]],
) -> None:
    errors: list[str] = []
    applied = 0
    for channel_id, channel_name, node_scope in targets:
        try:
            applied += 1
            # STREAMFORGE_OFFLINE_BULK_PROFILE_QUEUE_V3045:
            # STREAMFORGE_BULK_PROFILE_APPLY_RUNNING_V1025:
            # STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027:
            # STREAMFORGE_BULK_PROFILE_ASYNC_TARGETED_RESTART_COMPAT_V1120:
            # Historical bulk-profile guarantees remain intact after the v11.19
            # non-blocking refactor: only the selected node scope is reconciled,
            # and replicas that were already running are restarted in background.
            errors.extend(
                node_controller.sync_channel_to_nodes(
                    int(channel_id),
                    restart_running=True,
                    node_ids=node_scope,
                )
            )
        except Exception as exc:
            errors.append(f"{channel_name}: {exc}")
    try:
        log_event(
            "Bulk encoding profile background apply completed" if not errors else "Bulk encoding profile background apply completed with errors",
            scope="channel",
            level="info" if not errors else "warning",
            details={
                "channels": int(applied),
                "errors": [str(item) for item in errors[:50]],
            },
        )
    except Exception:
        pass


def queue_bulk_profile_sync(
    targets: list[tuple[int, str, set[int] | None]],
) -> bool:
    if not targets:
        return True
    isolated = [
        (
            int(channel_id),
            str(channel_name),
            None if node_scope is None else {int(item) for item in node_scope},
        )
        for channel_id, channel_name, node_scope in targets
    ]
    try:
        _CHANNEL_SAVE_SYNC_EXECUTOR.submit(_run_bulk_profile_sync, isolated)
        return True
    except RuntimeError as exc:
        try:
            log_event(
                "Bulk encoding profile background apply could not be queued",
                scope="channel",
                level="warning",
                details={"channels": len(isolated), "error": str(exc)},
            )
        except Exception:
            pass
        return False


# STREAMFORGE_BULK_NODE_ASSIGNMENT_ASYNC_V1124:
# Bulk Channel -> Node assignment is a local database transaction. Remote
# DELETE/config/logo requests are reconciliation work and must never keep the
# browser POST open. The worker receives primitive IDs only, re-opens fresh DB
# sessions through node_manager helpers, and limits synchronization to nodes
# whose membership actually changed.
def _run_bulk_node_assignment_sync(
    targets: list[tuple[int, str, set[int], set[int]]],
) -> None:
    # STREAMFORGE_BULK_NODE_TRUE_BATCH_V1127:
    # Group newly-added channels by target Node and send one bulk config request
    # per Node. Configs land first so channels appear immediately; optional logo
    # copies run afterwards and cannot delay catalogue availability.
    errors: list[str] = []
    applied = 0
    added_by_node: dict[int, list[tuple[int, str]]] = {}
    removed_work: list[tuple[int, str, int]] = []

    for channel_id, channel_name, removed_ids, added_ids in targets:
        applied += 1
        for node_id in sorted({int(item) for item in removed_ids}):
            removed_work.append((int(channel_id), str(channel_name), int(node_id)))
        for node_id in sorted({int(item) for item in added_ids}):
            added_by_node.setdefault(int(node_id), []).append((int(channel_id), str(channel_name)))

    # New assignments are the latency-sensitive path: one HTTP request per Node.
    for node_id, rows in sorted(added_by_node.items()):
        try:
            errors.extend(
                node_controller.sync_channel_batch_to_node(
                    [int(channel_id) for channel_id, _channel_name in rows],
                    int(node_id),
                )
            )
        except Exception as exc:
            errors.append(f"Node {node_id} bulk config sync: {exc}")

    # Deletions stay targeted. They are independent of the add fast-path above.
    for channel_id, channel_name, node_id in removed_work:
        try:
            node_controller.forget_on_node(int(channel_id), int(node_id))
        except Exception as exc:
            errors.append(f"{channel_name}, removed node {node_id}: {exc}")

    # Logo bytes are best-effort background work. Do them only after the channel
    # configs have already been delivered in bulk.
    for node_id, rows in sorted(added_by_node.items()):
        try:
            with SessionLocal() as db:
                node = db.get(Node, int(node_id))
                if not node or node.node_type != "remote":
                    continue
                if node_controller.is_effectively_offline(node):
                    continue
                for channel_id, channel_name in rows:
                    current = db.get(Channel, int(channel_id))
                    if not current or not str(current.logo_url or "").startswith("/channel-logos/"):
                        continue
                    try:
                        node_controller.sync_channel_logo(current, node)
                    except Exception as exc:
                        errors.append(f"{channel_name} / {node.name} logo: {exc}")
        except Exception as exc:
            errors.append(f"Node {node_id} logo sync: {exc}")

    try:
        log_event(
            "Bulk Node assignment background sync completed" if not errors else "Bulk Node assignment background sync completed with errors",
            scope="channel",
            level="info" if not errors else "warning",
            details={
                "channels": int(applied),
                "target_nodes": int(len(added_by_node)),
                "errors": [str(item) for item in errors[:50]],
            },
        )
    except Exception:
        pass

def queue_bulk_node_assignment_sync(
    targets: list[tuple[int, str, set[int], set[int]]],
) -> bool:
    if not targets:
        return True
    isolated = [
        (
            int(channel_id),
            str(channel_name),
            {int(item) for item in removed_ids},
            {int(item) for item in added_ids},
        )
        for channel_id, channel_name, removed_ids, added_ids in targets
    ]
    try:
        _BULK_NODE_ASSIGN_SYNC_EXECUTOR.submit(_run_bulk_node_assignment_sync, isolated)
        return True
    except RuntimeError as exc:
        try:
            log_event(
                "Bulk Node assignment background sync could not be queued",
                scope="channel",
                level="warning",
                details={"channels": len(isolated), "error": str(exc)},
            )
        except Exception:
            pass
        return False


# STREAMFORGE_BULK_NODE_ASSIGNMENT_DIRECT_SQL_V1125:
# Bulk node membership is a small association-table edit, not an ORM graph
# rewrite.  Updating Channel.nodes for 50-500 rows makes SQLAlchemy diff every
# collection and can hold the browser POST open for seconds.  Read the current
# edges once, calculate the desired sets in memory, then execute bounded bulk
# DELETE/INSERT/UPDATE statements in one transaction.  Existing per-node
# encoding/input overrides are preserved for edges that remain assigned.
def _bulk_node_assignment_db_fast(
    db: Session,
    action: str,
    selected_ids: set[int],
    chosen_nodes: list[Node],
) -> list[dict[str, object]]:
    channel_ids = sorted({int(item) for item in selected_ids if int(item) > 0})
    if not channel_ids:
        return []

    channel_rows = db.execute(
        select(Channel.id, Channel.name, Channel.node_id, Channel.logo_url)
        .where(Channel.id.in_(channel_ids))
        .order_by(Channel.id)
    ).all()
    if not channel_rows:
        return []

    # Pull node metadata once.  The local row should always exist on an
    # installed Main Server; only fall back to the historical repair helper on
    # a damaged/fresh database where it is genuinely absent.
    node_rows = db.execute(
        select(Node.id, Node.node_type, Node.name, Node.dns_name, Node.api_url, Node.enabled).order_by(Node.id)
    ).all()
    node_meta: dict[int, dict[str, object]] = {
        int(row.id): {
            "id": int(row.id),
            "node_type": str(row.node_type or "remote"),
            "name": str(row.name or f"Node {row.id}"),
            "dns_name": str(row.dns_name or ""),
            "api_url": str(row.api_url or ""),
            "enabled": bool(row.enabled),
        }
        for row in node_rows
    }
    local_id = next(
        (node_id for node_id, meta in node_meta.items() if meta["node_type"] == "local" and bool(meta["enabled"])),
        0,
    )
    if not local_id:
        local = ensure_local_node(db)
        local_id = int(local.id)
        node_meta[local_id] = {
            "id": local_id,
            "node_type": "local",
            "name": str(local.name or "Main Server"),
            "dns_name": str(local.dns_name or ""),
            "api_url": str(local.api_url or ""),
            "enabled": True,
        }

    existing_rows = db.execute(
        select(channel_nodes.c.channel_id, channel_nodes.c.node_id).where(channel_nodes.c.channel_id.in_(channel_ids))
    ).all()
    existing_all: dict[int, set[int]] = {channel_id: set() for channel_id in channel_ids}
    for channel_id, node_id in existing_rows:
        existing_all.setdefault(int(channel_id), set()).add(int(node_id))

    enabled_node_ids = {node_id for node_id, meta in node_meta.items() if bool(meta.get("enabled"))}
    chosen_ids = {int(item.id) for item in chosen_nodes if int(item.id) in enabled_node_ids}
    channel_by_id = {int(row.id): row for row in channel_rows}
    deletes: list[dict[str, int]] = []
    inserts: list[dict[str, object]] = []
    primaries: list[dict[str, int]] = []
    results: list[dict[str, object]] = []

    def node_sort_key(node_id: int) -> tuple[int, str, int]:
        meta = node_meta.get(int(node_id), {})
        return (
            0 if str(meta.get("node_type") or "remote") == "local" else 1,
            str(meta.get("name") or "").lower(),
            int(node_id),
        )

    for channel_id in channel_ids:
        row = channel_by_id.get(channel_id)
        if row is None:
            continue
        all_old_ids = set(existing_all.get(channel_id, set()))
        old_ids = {node_id for node_id in all_old_ids if node_id in enabled_node_ids}
        legacy_id = int(row.node_id or 0)
        if not old_ids and legacy_id in enabled_node_ids:
            old_ids.add(legacy_id)

        if action == "nodes_add":
            new_ids = set(old_ids) | set(chosen_ids)
        elif action == "nodes_remove":
            new_ids = set(old_ids) - set(chosen_ids)
            if not new_ids:
                new_ids = {int(local_id)}
        else:  # nodes_set
            new_ids = set(chosen_ids) or {int(local_id)}

        ordered_new_ids = sorted(new_ids, key=node_sort_key)
        primary_id = int(ordered_new_ids[0])
        for node_id in sorted(all_old_ids - new_ids):
            deletes.append({"channel_id": int(channel_id), "node_id": int(node_id)})
        for node_id in sorted(new_ids - all_old_ids):
            inserts.append({
                "channel_id": int(channel_id),
                "node_id": int(node_id),
                "priority": 100,
                "input_mode": "source",
                "video_codec": None,
                "video_bitrate": None,
                "resolution": None,
                "audio_codec": None,
                "audio_bitrate": None,
                "hls_segment_time": None,
            })
        if legacy_id != primary_id:
            primaries.append({"channel_id": int(channel_id), "node_id": primary_id})

        assigned_nodes = []
        for node_id in ordered_new_ids:
            meta = node_meta.get(node_id, {})
            assigned_nodes.append({
                "id": int(node_id),
                "name": "Main Server" if str(meta.get("node_type")) == "local" else str(meta.get("name") or f"Node {node_id}"),
                "node_type": str(meta.get("node_type") or "remote"),
                "detail": str(meta.get("dns_name") or meta.get("api_url") or ("Main Server" if str(meta.get("node_type")) == "local" else "Remote server")),
            })
        results.append({
            "channel_id": int(channel_id),
            "channel_name": str(row.name or f"Channel {channel_id}"),
            "removed_ids": {int(item) for item in (old_ids - new_ids)},
            "added_ids": {int(item) for item in (new_ids - old_ids)},
            "node_ids": ordered_new_ids,
            "nodes": assigned_nodes,
        })

    # STREAMFORGE_BULK_NODE_ASSIGNMENT_DURABLE_RETURN_V1137:
    # Added replicas are durable DB queue work before this transaction commits.
    # The browser request must never hand off remote config/logo work to an
    # executor before returning; the authenticated Node heartbeat drains these
    # exact keys through the true-batch targeted retry path.  This makes Add as
    # fast/reliable as Remove and survives Main restarts without losing delivery.
    pending_upserts: list[dict[str, str]] = []
    for item in results:
        channel_id = int(item["channel_id"])
        channel_name = str(item["channel_name"])
        channel_row = channel_by_id.get(channel_id)
        logo_url = str(getattr(channel_row, "logo_url", "") or "") if channel_row is not None else ""
        for node_id in sorted({int(value) for value in item["added_ids"]}):
            meta = node_meta.get(node_id, {})
            if str(meta.get("node_type") or "remote") != "remote":
                continue
            pending_upserts.append({
                "key": f"node_{node_id}_channel_{channel_id}_sync_pending",
                "value": f"Bulk Node assignment config queued: {channel_name}"[-2000:],
            })
            if logo_url.startswith("/channel-logos/"):
                pending_upserts.append({
                    "key": f"node_{node_id}_channel_{channel_id}_logo_pending",
                    "value": f"Bulk Node assignment logo queued: {channel_name}"[-2000:],
                })
    if pending_upserts:
        db.execute(
            text(
                "INSERT INTO app_settings(key,value,updated_at) "
                "VALUES (:key,:value,CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=CURRENT_TIMESTAMP"
            ),
            pending_upserts,
        )

    if deletes:
        db.execute(
            text("DELETE FROM channel_nodes WHERE channel_id=:channel_id AND node_id=:node_id"),
            deletes,
        )
    if inserts:
        db.execute(channel_nodes.insert(), inserts)
    if primaries:
        db.execute(
            text("UPDATE channels SET node_id=:node_id, updated_at=CURRENT_TIMESTAMP WHERE id=:channel_id"),
            primaries,
        )
    db.commit()
    return results


def sync_stream_user_registry(node: Node, request_or_url: Request | str) -> tuple[bool, str]:
    if node.node_type == "local":
        return False, "Direct node playlists require a remote node"
    try:
        result = node_controller.sync_stream_users(node, _sync_main_base(request_or_url))
        return True, f"Synced {int(result.get('users') or 0)} direct playlist user(s)"
    except NodeError as exc:
        return False, str(exc)


def sync_panel_user_registry(node: Node, request_or_url: Request | str) -> tuple[bool, str]:
    if node.node_type == "local":
        return False, "Local panel users are stored on the Main Panel"
    try:
        result = node_controller.sync_panel_users(node, _sync_main_base(request_or_url))
        return True, f"Synced {int(result.get('users') or 0)} panel user(s)"
    except NodeError as exc:
        return False, str(exc)


# Independent node access/GeoIP settings preserved locally through mandatory Node-to-Main back-sync.
def sync_node_registries(
    node: Node,
    request_or_url: Request | str,
    access_connect_urls: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[str], list[str]]:
    messages: list[str] = []
    errors: list[str] = []
    transport_blocked = False
    if node.node_type == "remote":
        # STREAMFORGE_WEBPLAYER_THEME_ALWAYS_SYNC_V2229:
        # Web Player appearance/login settings are Main-owned. Push the exact
        # saved settings during every access/registry sync so a Node update,
        # reinstall, test-sync, or URL change cannot fall back to Node defaults.
        with SessionLocal() as theme_db:
            webplayer_sync_settings = webplayer_settings_for_node(theme_db, int(node.id))
        try:
            access = node_controller.sync_access_settings(
                node,
                _sync_main_base(request_or_url),
                connect_urls=access_connect_urls,
                webplayer_settings=webplayer_sync_settings,
            )
            messages.append(
                "Synced Node Panel/API and Playlist/App URLs"
                + (f" · {len(access.get('panel_urls') or [])} panel alias(es)" if access.get("panel_urls") else "")
            )
        except NodeError as exc:
            errors.append(f"access URL sync: {exc}")
            transport_blocked = node_controller.is_transport_error(exc) or "retry backoff active" in str(exc).lower()
        if not transport_blocked:
            try:
                node_controller.sync_webplayer_download(node, webplayer_sync_settings)
                if webplayer_sync_settings.get("download_available"):
                    messages.append("Synced managed Web Player download")
                else:
                    messages.append("Confirmed no managed Web Player download")
                brand_assets = node_controller.sync_webplayer_brand_assets(node, webplayer_sync_settings)
                messages.append(f"Synced {int(brand_assets.get('assets') or 0)} Web Player brand asset(s)")
            except NodeError as exc:
                errors.append(f"Web Player download/brand asset sync: {exc}")
                transport_blocked = node_controller.is_transport_error(exc) or "retry backoff active" in str(exc).lower()
        if not transport_blocked:
            try:
                mode = node_controller.sync_node_mode(node)
                mode_name = "Independent" if bool(mode.get("independent_mode")) else "Shared"
                detail = f"Node switched to {mode_name} mode" if mode.get("changed") else f"Node confirmed in {mode_name} mode"
                restored = int(mode.get("restored_channels") or 0)
                if restored:
                    detail += f" · restored {restored} saved channel(s)"
                messages.append(detail)
            except NodeError as exc:
                errors.append(f"mode sync: {exc}")
                transport_blocked = node_controller.is_transport_error(exc) or "retry backoff active" in str(exc).lower()

    # STREAMFORGE_NODE_RECONCILE_TRANSPORT_SHORTCIRCUIT_V1064:
    # If Main cannot reach the Node control plane, do not continue through user
    # registries and every assigned channel.  The pending flag below preserves
    # the full reconcile for a later heartbeat retry.
    if not transport_blocked:
        for sync_fn in (sync_stream_user_registry, sync_panel_user_registry):
            ok, detail = sync_fn(node, request_or_url)
            (messages if ok else errors).append(detail)
            if not ok and "retry backoff active" in str(detail).lower():
                transport_blocked = True
                break
    if not transport_blocked:
        channel_errors = node_controller.sync_node_channels(node)
        if channel_errors:
            errors.extend(f"channel sync: {item}" for item in channel_errors)
        elif node.node_type == "remote" and bool(getattr(node, "sync_main_users", False)):
            messages.append("Independent node local catalogue preserved; assigned Main channels synchronized read-only")
        elif node.node_type == "remote":
            messages.append(f"Shared catalogue reconciled with {len(node_controller.configured_channels(node))} Main Panel channel(s)")
    elif node.node_type == "remote":
        errors.append("remaining Node registry/catalogue synchronization deferred by transport backoff")

    if node.node_type == "remote":
        # STREAMFORGE_NODE_RECONNECT_AUTO_SYNC_V3045:
        # The pending flag is claimed by heartbeat/Test and is cleared only
        # after the complete settings/users/channels reconcile succeeds.
        with SessionLocal() as queue_db:
            current = queue_db.get(Node, int(node.id))
            if current:
                if errors:
                    mark_node_sync_pending(queue_db, current, "Full reconcile retry: " + "; ".join(errors))
                else:
                    clear_node_sync_pending(queue_db, current.id)
                queue_db.commit()
    return messages, errors


# STREAMFORGE_NODE_HEARTBEAT_RECONCILE_BACKOFF_V1064:
# A Node can still heartbeat to Main while the reverse Main->Node API path is
# broken.  Without a single-flight/backoff guard, every heartbeat immediately
# starts another expensive full settings/users/channel reconcile.
_NODE_RECONCILE_LOCK = threading.RLock()
_NODE_RECONCILE_INFLIGHT: set[int] = set()
_NODE_RECONCILE_FAILURES: dict[int, int] = {}
_NODE_RECONCILE_RETRY_AT: dict[int, float] = {}


def _claim_node_reconcile(node_id: int) -> bool:
    key = int(node_id)
    now = time.monotonic()
    with _NODE_RECONCILE_LOCK:
        if key in _NODE_RECONCILE_INFLIGHT:
            return False
        if now < float(_NODE_RECONCILE_RETRY_AT.get(key, 0.0) or 0.0):
            return False
        _NODE_RECONCILE_INFLIGHT.add(key)
        return True


def _finish_node_reconcile(node_id: int, success: bool) -> None:
    key = int(node_id)
    now = time.monotonic()
    with _NODE_RECONCILE_LOCK:
        _NODE_RECONCILE_INFLIGHT.discard(key)
        if success:
            _NODE_RECONCILE_FAILURES.pop(key, None)
            _NODE_RECONCILE_RETRY_AT.pop(key, None)
            return
        failures = min(6, int(_NODE_RECONCILE_FAILURES.get(key, 0)) + 1)
        delay = min(300.0, 15.0 * (2 ** (failures - 1)))
        _NODE_RECONCILE_FAILURES[key] = failures
        _NODE_RECONCILE_RETRY_AT[key] = now + delay


def _reset_node_reconcile_backoff(node_id: int) -> None:
    key = int(node_id)
    with _NODE_RECONCILE_LOCK:
        _NODE_RECONCILE_INFLIGHT.discard(key)
        _NODE_RECONCILE_FAILURES.pop(key, None)
        _NODE_RECONCILE_RETRY_AT.pop(key, None)


# STREAMFORGE_TARGETED_PENDING_SEPARATE_SCHEDULER_V1133:
# Targeted config/logo delivery is persistent DB queue work, not a full Node
# reconcile.  Keep its single-flight/backoff separate so an old full-reconcile
# failure cannot suppress exact per-channel retries, and so the targeted worker
# can make one real wire attempt when its own retry slot becomes due.
_NODE_TARGETED_RETRY_LOCK = threading.RLock()
_NODE_TARGETED_RETRY_INFLIGHT: set[int] = set()
_NODE_TARGETED_RETRY_FAILURES: dict[int, int] = {}
_NODE_TARGETED_RETRY_AT: dict[int, float] = {}


def _claim_node_targeted_retry(node_id: int) -> bool:
    key = int(node_id)
    now = time.monotonic()
    with _NODE_TARGETED_RETRY_LOCK:
        if key in _NODE_TARGETED_RETRY_INFLIGHT:
            return False
        if now < float(_NODE_TARGETED_RETRY_AT.get(key, 0.0) or 0.0):
            return False
        _NODE_TARGETED_RETRY_INFLIGHT.add(key)
        return True


def _finish_node_targeted_retry(node_id: int, success: bool) -> None:
    key = int(node_id)
    now = time.monotonic()
    with _NODE_TARGETED_RETRY_LOCK:
        _NODE_TARGETED_RETRY_INFLIGHT.discard(key)
        if success:
            _NODE_TARGETED_RETRY_FAILURES.pop(key, None)
            _NODE_TARGETED_RETRY_AT.pop(key, None)
            return
        failures = min(5, int(_NODE_TARGETED_RETRY_FAILURES.get(key, 0)) + 1)
        delay = min(180.0, 15.0 * (2 ** (failures - 1)))
        _NODE_TARGETED_RETRY_FAILURES[key] = failures
        _NODE_TARGETED_RETRY_AT[key] = now + delay


def sync_node_registries_background(
    node_id: int,
    main_base_url: str,
    access_connect_urls: list[str] | tuple[str, ...] | None = None,
) -> None:
    success = False
    try:
        with SessionLocal() as background_db:
            node = background_db.get(Node, int(node_id))
            if not node or node.node_type != "remote":
                success = True
                return
            messages, errors = sync_node_registries(
                node,
                main_base_url,
                access_connect_urls=access_connect_urls,
            )
            success = not errors
            log_event(
                "Node background synchronization completed" if not errors else "Node background synchronization completed with errors",
                scope="node", level="info" if not errors else "warning", node_id=node.id,
                details={"messages": messages, "errors": errors},
            )
    except Exception as exc:
        log_event("Node background synchronization failed", scope="node", level="error", node_id=int(node_id), details={"error": str(exc)})
    finally:
        _finish_node_reconcile(int(node_id), success)



# STREAMFORGE_NODE_HEARTBEAT_TARGETED_RETRY_V1127:
# STREAMFORGE_NODE_HEARTBEAT_TARGETED_LOGO_RETRY_V1129:
def sync_node_pending_channels_background(node_id: int) -> None:
    """Retry only queued config/logo work for this Node; never full-reconcile."""
    success = False
    errors: list[str] = []
    pending_ids: list[int] = []
    pending_logo_ids: list[int] = []
    try:
        with SessionLocal() as pending_db:
            node = pending_db.get(Node, int(node_id))
            if not node or node.node_type != "remote":
                success = True
                return
            pending_ids = node_channel_sync_pending_ids(pending_db, int(node_id), limit=500)
            pending_logo_ids = node_channel_logo_pending_ids(pending_db, int(node_id), limit=500)
            pending_db.commit()
        if pending_ids:
            errors.extend(
                node_controller.sync_channel_batch_to_node(
                    pending_ids, int(node_id), bypass_transport_backoff=True
                )
            )
        if pending_logo_ids:
            errors.extend(
                node_controller.sync_channel_logo_batch_to_node(
                    pending_logo_ids, int(node_id), bypass_transport_backoff=True
                )
            )
        success = not errors
        log_event(
            "Node targeted channel retry completed" if success else "Node targeted channel retry completed with errors",
            scope="node", level="info" if success else "warning", node_id=int(node_id),
            details={
                "configs": len(pending_ids),
                "logos": len(pending_logo_ids),
                "errors": [str(item) for item in errors[:50]],
            },
        )
    except Exception as exc:
        errors.append(str(exc))
        log_event(
            "Node targeted channel retry failed", scope="node", level="error",
            node_id=int(node_id),
            details={"configs": len(pending_ids), "logos": len(pending_logo_ids), "error": str(exc)},
        )
    finally:
        _finish_node_targeted_retry(int(node_id), success)

def slugify(value: str) -> str:
    value = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return value or secrets.token_hex(4)


def unique_channel_slug(db: Session, value: str, existing_id: int | None = None) -> str:
    base = slugify(value)
    candidate = base
    suffix = 2
    while True:
        statement = select(Channel).where(Channel.slug == candidate)
        if existing_id is not None:
            statement = statement.where(Channel.id != existing_id)
        if not db.scalar(statement):
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def category_by_name(db: Session, name: str) -> ChannelCategory | None:
    cleaned = name.strip()
    if not cleaned:
        return None
    return db.scalar(select(ChannelCategory).where(func.lower(ChannelCategory.name) == cleaned.lower()))


def get_or_create_category(db: Session, name: str) -> ChannelCategory | None:
    cleaned = name.strip()[:120]
    if not cleaned:
        return None
    existing = category_by_name(db, cleaned)
    if existing:
        return existing
    base_slug = slugify(cleaned)[:120]
    slug = base_slug
    suffix = 2
    while db.scalar(select(ChannelCategory).where(ChannelCategory.slug == slug)):
        tail = f"-{suffix}"
        slug = f"{base_slug[:120-len(tail)]}{tail}"
        suffix += 1
    next_order = int(db.scalar(select(func.max(ChannelCategory.sort_order))) or 0) + 10
    category = ChannelCategory(name=cleaned, slug=slug, sort_order=next_order)
    db.add(category)
    db.flush()
    return category


def unique_category_slug(db: Session, value: str, existing_id: int | None = None) -> str:
    base = slugify(value)[:120]
    candidate = base
    suffix = 2
    while True:
        statement = select(ChannelCategory).where(ChannelCategory.slug == candidate)
        if existing_id is not None:
            statement = statement.where(ChannelCategory.id != existing_id)
        if not db.scalar(statement):
            return candidate
        tail = f"-{suffix}"
        candidate = f"{base[:120-len(tail)]}{tail}"
        suffix += 1


def selected_category(db: Session, category_id: str | int | None) -> ChannelCategory | None:
    if category_id in {None, "", 0, "0"}:
        return None
    try:
        value = int(category_id)
    except (TypeError, ValueError):
        return None
    return db.get(ChannelCategory, value)


def channel_category_list(channel: Channel) -> list[ChannelCategory]:
    items: list[ChannelCategory] = []
    seen: set[int] = set()
    primary = getattr(channel, "category", None)
    if primary is not None:
        items.append(primary)
        seen.add(int(primary.id))
    for item in list(getattr(channel, "categories", []) or []):
        if int(item.id) not in seen:
            items.append(item)
            seen.add(int(item.id))
    return sorted(items, key=lambda item: (0 if primary is not None and item.id == primary.id else 1, int(item.sort_order or 100000), item.name.lower()))


def channel_category_names(channel: Channel) -> str:
    items = channel_category_list(channel)
    return ", ".join(item.name for item in items) if items else "Uncategorized"


def channel_catalogue_sql_order():
    """SQL expressions matching channel_catalogue_order_key without loading the catalogue."""
    # STREAMFORGE_MAIN_CHANNELS_CATALOGUE_SQL_ORDER_V1111:
    # v11.7 server pagination accidentally ordered by Channel.sort_order first,
    # while public catalogue numbers are category-first. That made the visible
    # ID/serial column jump (106, 109, 108, ...). Keep the bounded SQL page,
    # but order/rank it exactly like playlists and the Web Player catalogue.
    link = channel_category_links.alias("catalogue_order_link")
    linked_category = ChannelCategory.__table__.alias("catalogue_order_category")
    linked_sort = (
        select(linked_category.c.sort_order)
        .select_from(link.join(linked_category, linked_category.c.id == link.c.category_id))
        .where(link.c.channel_id == Channel.id)
        .order_by(linked_category.c.sort_order, func.lower(linked_category.c.name), linked_category.c.id)
        .limit(1)
        .scalar_subquery()
    )
    linked_name = (
        select(func.lower(linked_category.c.name))
        .select_from(link.join(linked_category, linked_category.c.id == link.c.category_id))
        .where(link.c.channel_id == Channel.id)
        .order_by(linked_category.c.sort_order, func.lower(linked_category.c.name), linked_category.c.id)
        .limit(1)
        .scalar_subquery()
    )
    legacy_sort = (
        select(ChannelCategory.sort_order)
        .where(ChannelCategory.id == Channel.category_id)
        .limit(1)
        .scalar_subquery()
    )
    legacy_name = (
        select(func.lower(ChannelCategory.name))
        .where(ChannelCategory.id == Channel.category_id)
        .limit(1)
        .scalar_subquery()
    )
    return (
        func.coalesce(linked_sort, legacy_sort, 1_000_000_000),
        func.coalesce(linked_name, legacy_name, "\uffff"),
        Channel.sort_order,
        func.lower(Channel.name),
        Channel.id,
    )


def channel_catalogue_order_key(channel: Channel) -> tuple[int, str, int, str, int]:
    """Stable public catalogue order; database IDs remain internal only."""
    linked = list(getattr(channel, "categories", []) or [])
    primary = min(
        linked or ([channel.category] if getattr(channel, "category", None) is not None else []),
        key=lambda item: (int(item.sort_order), item.name.lower(), int(item.id)),
        default=None,
    )
    return (
        int(primary.sort_order) if primary else 1_000_000_000,
        primary.name.lower() if primary else "\uffff",
        int(channel.sort_order),
        channel.name.lower(),
        int(channel.id),
    )


def channel_public_number_map(db: Session) -> dict[int, int]:
    """Map internal channel IDs to contiguous public numbers starting at 101."""
    channels = db.scalars(
        select(Channel).options(
            selectinload(Channel.category),
            selectinload(Channel.categories),
        )
    ).all()
    channels.sort(key=channel_catalogue_order_key)
    return {int(channel.id): 101 + index for index, channel in enumerate(channels)}


def selected_channel_categories(
    db: Session, category_ids: list[int] | None = None, legacy_category_id: str | int | None = None
) -> list[ChannelCategory]:
    requested: list[int] = []
    for item in category_ids or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in requested:
            requested.append(value)
    if not requested and legacy_category_id not in {None, "", 0, "0"}:
        try:
            requested.append(int(legacy_category_id))
        except (TypeError, ValueError):
            pass
    if not requested:
        return []
    found = {item.id: item for item in db.scalars(select(ChannelCategory).where(ChannelCategory.id.in_(requested))).all()}
    return [found[item] for item in requested if item in found]


def assign_channel_categories(channel: Channel, categories: list[ChannelCategory]) -> None:
    channel.categories = list(categories)
    channel.category = categories[0] if categories else None


def next_channel_sort_order(db: Session, category_id: int | None) -> int:
    if category_id is None:
        current = db.scalar(select(func.max(Channel.sort_order)).where(Channel.category_id.is_(None)))
    else:
        current = db.scalar(select(func.max(Channel.sort_order)).where(Channel.category_id == int(category_id)))
    return int(current or 0) + 10


def ordered_category_channels(db: Session, category: ChannelCategory) -> list[Channel]:
    return db.scalars(
        select(Channel)
        .join(channel_category_links, channel_category_links.c.channel_id == Channel.id)
        .where(channel_category_links.c.category_id == category.id)
        .order_by(Channel.sort_order, Channel.name, Channel.id)
    ).all()


def selected_node(db: Session, node_id: str | int | None) -> Node:
    local = ensure_local_node(db)
    if node_id in {None, "", 0, "0"}:
        return local
    try:
        value = int(node_id)
    except (TypeError, ValueError):
        return local
    node = db.get(Node, value)
    return node if node and node.enabled else local


def selected_nodes(db: Session, node_ids: list[int] | None) -> list[Node]:
    local = ensure_local_node(db)
    values = sorted({int(item) for item in (node_ids or []) if int(item) > 0})
    nodes = db.scalars(select(Node).where(Node.id.in_(values), Node.enabled.is_(True))).all() if values else []
    return sorted(nodes or [local], key=lambda item: (0 if item.node_type == "local" else 1, item.name.lower(), item.id))


def set_channel_nodes(channel: Channel, nodes: list[Node]) -> None:
    selected = nodes or ([channel.node] if channel.node else [])
    channel.nodes = selected
    channel.node = selected[0] if selected else None


def prefix_conflict(db: Session, normalized_prefixes: str | None, existing_id: int | None = None) -> str | None:
    requested = {item.strip() for item in (normalized_prefixes or "").splitlines() if item.strip()}
    if not requested:
        return None
    statement = select(Node).where(Node.client_prefixes.is_not(None))
    if existing_id is not None:
        statement = statement.where(Node.id != existing_id)
    for node in db.scalars(statement).all():
        existing = {item.strip() for item in (node.client_prefixes or "").splitlines() if item.strip()}
        duplicate = sorted(requested & existing)
        if duplicate:
            return f"Prefix {duplicate[0]} is already assigned to {node.name}"
    return None


def unique_node_slug(db: Session, value: str, existing_id: int | None = None) -> str:
    base = slugify(value)[:120]
    candidate = base
    suffix = 2
    while True:
        statement = select(Node).where(Node.slug == candidate)
        if existing_id is not None:
            statement = statement.where(Node.id != existing_id)
        if not db.scalar(statement):
            return candidate
        tail = f"-{suffix}"
        candidate = f"{base[:120-len(tail)]}{tail}"
        suffix += 1


def normalize_dns_name(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower().rstrip(".")
    if not cleaned:
        return None
    parsed = urlsplit(f"//{cleaned}")
    if not parsed.hostname or parsed.username or parsed.password or parsed.port is not None or parsed.path not in {"", "/"}:
        raise ValueError("DNS name must contain only a hostname; configure the port in API URL")
    host = parsed.hostname.rstrip(".")
    if host.replace(".", "").isdigit():
        raise ValueError("DNS name cannot be an IP address")
    if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host or host.startswith("-") or host.endswith("-"):
        raise ValueError("Invalid DNS hostname")
    return cleaned


def normalize_dns_scheme(value: str | None) -> str:
    scheme = (value or "http").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("DNS scheme must be http or https")
    return scheme


def normalize_remote_input_mode(value: str | None) -> str:
    mode = (value or "source").strip().lower()
    if mode not in {"source", "local_relay"}:
        raise ValueError("Invalid remote input mode")
    return mode


def parse_node_input_modes(values: list[str] | None, nodes: list[Node], legacy_mode: str = "source") -> dict[int, str]:
    result: dict[int, str] = {}
    valid_ids = {node.id for node in nodes if node.node_type == "remote"}
    for raw in values or []:
        try:
            node_text, mode_text = str(raw).split(":", 1)
            node_id = int(node_text)
            mode = normalize_remote_input_mode(mode_text)
        except (TypeError, ValueError):
            continue
        if node_id in valid_ids:
            result[node_id] = mode
    fallback = normalize_remote_input_mode(legacy_mode)
    for node_id in valid_ids:
        result.setdefault(node_id, fallback)
    return result


def validate_remote_relay_selection(modes: dict[int, str], nodes: list[Node], output_type: str) -> None:
    relay_ids = {node_id for node_id, mode in modes.items() if mode == "local_relay"}
    if not relay_ids:
        return
    if output_type != "hls":
        raise ValueError("Main/Local relay mode requires HTTP HLS output")
    if not any(node.node_type == "local" for node in nodes):
        raise ValueError("Select Local Node together with every remote node that uses Local relay")


def set_channel_node_input_modes(db: Session, channel: Channel, modes: dict[int, str]) -> None:
    for node in channel.nodes:
        mode = "source" if node.node_type == "local" else modes.get(node.id, "source")
        db.execute(
            text("UPDATE channel_nodes SET input_mode=:mode WHERE channel_id=:channel_id AND node_id=:node_id"),
            {"mode": mode, "channel_id": channel.id, "node_id": node.id},
        )
    remote_modes = {modes.get(node.id, "source") for node in channel.nodes if node.node_type == "remote"}
    channel.remote_input_mode = remote_modes.pop() if len(remote_modes) == 1 else ("mixed" if remote_modes else "source")


def channel_node_input_modes(db: Session, channel: Channel) -> dict[int, str]:
    rows = db.execute(
        text("SELECT node_id,input_mode FROM channel_nodes WHERE channel_id=:channel_id"),
        {"channel_id": channel.id},
    ).all()
    return {int(node_id): str(mode or "source") for node_id, mode in rows}


_NODE_VIDEO_CODECS = {
    "inherit", "copy", "auto", "auto_hevc",
    "libx264", "libx265", "h264_nvenc", "hevc_nvenc",
    "h264_qsv", "hevc_qsv", "h264_vaapi", "hevc_vaapi",
}
_NODE_AUDIO_CODECS = {
    "inherit", "auto", "copy", "aac", "ac3", "eac3", "mp2",
    "libmp3lame", "libopus", "flac", "libvorbis", "pcm_s16le",
}
_NODE_RESOLUTIONS = {
    "inherit", "source", "3840x2160", "2560x1440", "1920x1080", "1600x900",
    "1280x720", "1024x576", "854x480", "720x576", "720x480", "640x360", "426x240",
}
_BITRATE_VALUE_RE = re.compile(r"^[1-9][0-9]*(?:[kKmM])?$")


def _parallel_node_values(node_ids: list[int] | None, values: list[str] | None) -> dict[int, str]:
    result: dict[int, str] = {}
    for index, raw_node_id in enumerate(node_ids or []):
        try:
            node_id = int(raw_node_id)
        except (TypeError, ValueError):
            continue
        if node_id <= 0:
            continue
        value = str((values or [])[index] if index < len(values or []) else "").strip()
        result[node_id] = value
    return result


def parse_node_encoding_profiles(
    profile_node_ids: list[int] | None,
    video_codecs: list[str] | None,
    video_bitrates: list[str] | None,
    resolutions: list[str] | None,
    audio_codecs: list[str] | None,
    audio_bitrates: list[str] | None,
    hls_segment_times: list[str] | None,
    nodes: list[Node],
) -> dict[int, dict[str, object | None]]:
    valid_ids = {node.id for node in nodes}
    codec_map = _parallel_node_values(profile_node_ids, video_codecs)
    video_bitrate_map = _parallel_node_values(profile_node_ids, video_bitrates)
    resolution_map = _parallel_node_values(profile_node_ids, resolutions)
    audio_map = _parallel_node_values(profile_node_ids, audio_codecs)
    audio_bitrate_map = _parallel_node_values(profile_node_ids, audio_bitrates)
    hls_segment_map = _parallel_node_values(profile_node_ids, hls_segment_times)
    profiles: dict[int, dict[str, object | None]] = {}
    for node_id in valid_ids:
        video_codec = (codec_map.get(node_id) or "inherit").lower()
        if video_codec not in _NODE_VIDEO_CODECS:
            raise ValueError("Invalid per-node video codec")
        video_bitrate = video_bitrate_map.get(node_id, "").strip()
        if video_bitrate and not _BITRATE_VALUE_RE.fullmatch(video_bitrate):
            raise ValueError("Per-node video bitrate must look like 2500k or 5M")
        resolution = (resolution_map.get(node_id) or "inherit").lower()
        if resolution not in _NODE_RESOLUTIONS:
            raise ValueError("Invalid per-node resolution")
        audio_codec = (audio_map.get(node_id) or "inherit").lower()
        if audio_codec not in _NODE_AUDIO_CODECS:
            raise ValueError("Invalid per-node audio codec")
        audio_bitrate = audio_bitrate_map.get(node_id, "").strip()
        if audio_bitrate and not _BITRATE_VALUE_RE.fullmatch(audio_bitrate):
            raise ValueError("Per-node audio bitrate must look like 128k or 1M")
        hls_segment_raw = (hls_segment_map.get(node_id) or "inherit").lower()
        if hls_segment_raw == "inherit":
            hls_segment_time = None
        else:
            try:
                hls_segment_time = int(hls_segment_raw)
            except ValueError as exc:
                raise ValueError("Invalid per-node HLS segment seconds") from exc
            if not 1 <= hls_segment_time <= 20:
                raise ValueError("Per-node HLS segment seconds must be between 1 and 20")
        profiles[node_id] = {
            "video_codec": None if video_codec == "inherit" else video_codec,
            "video_bitrate": video_bitrate or None,
            "resolution": None if resolution == "inherit" else resolution,
            "audio_codec": None if audio_codec == "inherit" else audio_codec,
            "audio_bitrate": audio_bitrate or None,
            "hls_segment_time": hls_segment_time,
        }
    return profiles


def set_channel_node_encoding_profiles(
    db: Session,
    channel: Channel,
    profiles: dict[int, dict[str, object | None]],
) -> None:
    for node in channel.nodes:
        profile = profiles.get(node.id, {})
        video_codec = profile.get("video_codec")
        resolution = profile.get("resolution")
        audio_codec = profile.get("audio_codec")
        db.execute(
            text(
                "UPDATE channel_nodes SET video_codec=:video_codec, video_bitrate=:video_bitrate, "
                "resolution=:resolution, audio_codec=:audio_codec, audio_bitrate=:audio_bitrate, "
                "hls_segment_time=:hls_segment_time "
                "WHERE channel_id=:channel_id AND node_id=:node_id"
            ),
            {
                "channel_id": channel.id,
                "node_id": node.id,
                "video_codec": None if video_codec in {None, "", "inherit"} else video_codec,
                "video_bitrate": profile.get("video_bitrate") or None,
                "resolution": None if resolution in {None, "", "inherit"} else resolution,
                "audio_codec": None if audio_codec in {None, "", "inherit"} else audio_codec,
                "audio_bitrate": profile.get("audio_bitrate") or None,
                "hls_segment_time": profile.get("hls_segment_time"),
            },
        )


def channel_node_encoding_profiles(db: Session, channel: Channel | None) -> dict[int, dict[str, object | None]]:
    if not channel:
        return {}
    rows = db.execute(
        text(
            "SELECT node_id,video_codec,video_bitrate,resolution,audio_codec,audio_bitrate,hls_segment_time "
            "FROM channel_nodes WHERE channel_id=:channel_id"
        ),
        {"channel_id": channel.id},
    ).all()
    # STREAMFORGE_BULK_PROFILE_EDIT_EFFECTIVE_VALUES_V1026:
    # Keep the stored per-node override values separate from the values that are
    # actually effective on that node. Bulk Encoding profile changes update the
    # channel defaults, so inherited node controls must display those fresh
    # defaults without turning them into explicit node overrides on Save.
    channel_resolution = (
        f"{int(channel.width)}x{int(channel.height)}"
        if channel.width and channel.height else "source"
    )
    profiles: dict[int, dict[str, object | None]] = {}
    for node_id, video_codec, video_bitrate, resolution, audio_codec, audio_bitrate, hls_segment_time in rows:
        stored_video_codec = video_codec or "inherit"
        stored_video_bitrate = video_bitrate or ""
        stored_resolution = resolution or "inherit"
        stored_audio_codec = audio_codec or "inherit"
        stored_audio_bitrate = audio_bitrate or ""
        stored_hls_segment_time = hls_segment_time if hls_segment_time is not None else "inherit"
        profiles[int(node_id)] = {
            "video_codec": stored_video_codec,
            "video_bitrate": stored_video_bitrate,
            "resolution": stored_resolution,
            "audio_codec": stored_audio_codec,
            "audio_bitrate": stored_audio_bitrate,
            "hls_segment_time": stored_hls_segment_time,
            "effective_video_codec": video_codec or channel.video_codec,
            "effective_video_bitrate": video_bitrate or channel.video_bitrate,
            "effective_resolution": resolution or channel_resolution,
            "effective_audio_codec": audio_codec or channel.audio_codec,
            "effective_audio_bitrate": audio_bitrate or channel.audio_bitrate,
            "effective_hls_segment_time": (
                int(hls_segment_time) if hls_segment_time is not None else int(channel.hls_segment_time or 1)
            ),
        }
    return profiles


def as_bool(value: Optional[str]) -> bool:
    return value in {"1", "true", "on", "yes"}


def to_int(value: Optional[str]) -> Optional[int]:
    if value is None or value.strip() == "":
        return None
    return int(value)


def normalize_expiry(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def user_valid(user: StreamUser) -> bool:
    if not user.enabled:
        return False
    if user.expires_at:
        expiry = user.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            return False
    return True


VIDEO_CODEC_ALIASES = {
    "auto": "auto",
    "auto_h264": "auto",
    "auto_hevc": "auto_hevc",
    "auto_h265": "auto_hevc",
    "copy": "copy",
    "h264": "auto",
    "libx264": "auto",
    "h264_nvenc": "h264_nvenc",
    "h264_qsv": "auto",
    "h264_vaapi": "auto",
    "hevc": "auto_hevc",
    "libx265": "auto_hevc",
    "hevc_nvenc": "auto_hevc",
    "hevc_qsv": "auto_hevc",
    "hevc_vaapi": "auto_hevc",
}


def normalize_video_codec(value: str) -> str:
    return VIDEO_CODEC_ALIASES.get(value.strip().lower(), "auto")


def normalize_source_configs(
    source_urls: list[str] | None,
    source_program_ids: list[str] | None = None,
    input_url: str = "",
    backup_inputs: str = "",
    legacy_program_id: str | int | None = None,
) -> tuple[str, str, list[int | None]]:
    """Normalize sources and keep each optional MPTS program ID attached to its row."""
    values: list[str] = []
    programs: list[int | None] = []
    raw_programs = list(source_program_ids or [])
    for index, item in enumerate(source_urls or []):
        cleaned = str(item or "").strip()
        if not cleaned or cleaned in values:
            continue
        raw_program = raw_programs[index] if index < len(raw_programs) else ""
        try:
            parsed = int(raw_program) if str(raw_program or "").strip() else None
        except (TypeError, ValueError):
            parsed = None
        values.append(cleaned)
        programs.append(parsed if parsed and 1 <= parsed <= 65535 else None)
    if not values:
        primary = str(input_url or "").strip()
        if primary:
            values.append(primary)
            try:
                parsed_legacy = int(legacy_program_id) if str(legacy_program_id or "").strip() else None
            except (TypeError, ValueError):
                parsed_legacy = None
            programs.append(parsed_legacy if parsed_legacy and 1 <= parsed_legacy <= 65535 else None)
        for item in str(backup_inputs or "").splitlines():
            cleaned = item.strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
                programs.append(None)
    if not values:
        raise ValueError("Primary input URL is required")
    if any(len(item) > 8000 for item in values):
        raise ValueError("An input URL is too long")
    while len(programs) < len(values):
        programs.append(None)
    return values[0], "\n".join(values[1:]), programs[:len(values)]


def normalize_source_urls(source_urls: list[str] | None, input_url: str = "", backup_inputs: str = "") -> tuple[str, str]:
    primary, backups, _ = normalize_source_configs(source_urls, None, input_url, backup_inputs)
    return primary, backups


OUTPUT_TYPE_ALIASES = {
    "hls": "hls",
    "http_hls": "hls",
    "udp": "udp",
    "srt": "srt",
    "http": "http_post",
    "https": "http_post",
    "http_post": "http_post",
    "http_put": "http_put",
}


def normalize_output_type(value: str) -> str:
    return OUTPUT_TYPE_ALIASES.get(value.strip().lower(), "hls")


def normalize_remote_output_urls(values: list[str] | None, legacy_value: str = "") -> str:
    # STREAMFORGE_MULTI_REMOTE_OUTPUT_URLS_V54:
    # Keep the existing Text column for backwards compatibility while allowing
    # the editor/runtime to treat one URL per line as independent outputs.
    cleaned: list[str] = []
    for raw in list(values or []) + ([legacy_value] if legacy_value else []):
        for line in str(raw or "").splitlines():
            item = line.strip()
            if not item or item in cleaned:
                continue
            target = item.split("|", 1)[0].strip().lower()
            if not target.startswith(("http://", "https://", "udp://", "srt://")):
                raise ValueError(f"Unsupported remote output URL: {item}")
            cleaned.append(item)
    return "\n".join(cleaned)


def import_category_for_item(
    db: Session,
    item: ImportItem,
    category_mode: str,
    fallback_category: ChannelCategory | None,
    create_missing_categories: bool,
) -> ChannelCategory | None:
    if category_mode == "none":
        return None
    if category_mode == "selected":
        return fallback_category
    if item.category:
        existing = category_by_name(db, item.category)
        if existing:
            return existing
        if create_missing_categories:
            return get_or_create_category(db, item.category)
    return fallback_category


def clean_import_profile(value: str | None, fallback: str) -> str:
    cleaned = (value or "").strip()
    return cleaned or fallback


def channel_stream_key(channel: Channel) -> str:
    message = f"streamforge-channel:{channel.id}:{channel.slug}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def channel_http_urls(channel: Channel, request: Request, db: Session, *, public_base: str | None = None) -> dict[str, str]:
    # STREAMFORGE_MAIN_CHANNEL_URL_BASE_ONCE_V48: catalogue pages can resolve
    # the Main public base once and reuse it for every HLS channel. Older code
    # queried the Local Node once per channel, which made large pages slow.
    base = str(public_base or "").strip().rstrip("/") or main_playlist_public_base(db, request)
    key = channel_stream_key(channel)
    prefix = f"{base}/live/{key}/{channel.slug}"
    return {
        "hls": f"{prefix}/master.m3u8",
        "player": prefix,
    }


def auto_encoder_label(family: str) -> str:
    labels = {
        "h264_nvenc": "NVIDIA NVENC H.264 (GPU)",
        "h264_qsv": "Intel Quick Sync H.264 (iGPU)",
        "h264_vaapi": "VAAPI H.264 (GPU/iGPU)",
        "libx264": "CPU H.264 fallback",
        "h264": "FFmpeg H.264 CPU fallback",
        "hevc_nvenc": "NVIDIA NVENC H.265 (GPU)",
        "hevc_qsv": "Intel Quick Sync H.265 (iGPU)",
        "hevc_vaapi": "VAAPI H.265 (GPU/iGPU)",
        "libx265": "CPU H.265 fallback",
        "hevc": "FFmpeg H.265 CPU fallback",
    }
    try:
        codec = stream_manager.detect_auto_video_encoder(family)
        return labels.get(codec, codec)
    except RuntimeError as exc:
        return f"unavailable ({exc})"


def encoder_template_context() -> dict[str, str]:
    return {
        "auto_h264_encoder": auto_encoder_label("h264"),
        "auto_h265_encoder": auto_encoder_label("h265"),
    }


def channel_encoder_details(channel: Channel) -> dict[str, object]:
    if channel.video_codec == "copy":
        return {"codec": "copy", "label": "Copy / Passthrough", "hardware": False}
    # STREAMFORGE_MAIN_EXPLICIT_NVENC_PROFILE_V1117: do not describe an
    # explicitly selected NVIDIA profile as the AUTO encoder/fallback result.
    if channel.video_codec == "h264_nvenc":
        return {"codec": "h264_nvenc", "label": "NVIDIA NVENC H.264", "hardware": True}
    family = "h265" if channel.video_codec in {"auto_hevc", "auto_h265"} else "h264"
    try:
        status = stream_manager.auto_video_encoder_status(family)
        selected = str(status["selected"])
        labels = {
            "h264_nvenc": "NVIDIA NVENC H.264",
            "h264_qsv": "Intel Quick Sync H.264",
            "h264_vaapi": "VAAPI H.264",
            "libx264": "CPU H.264 / libx264",
            "h264": "CPU H.264",
            "hevc_nvenc": "NVIDIA NVENC H.265",
            "hevc_qsv": "Intel Quick Sync H.265",
            "hevc_vaapi": "VAAPI H.265",
            "libx265": "CPU H.265 / libx265",
            "hevc": "CPU H.265",
        }
        return {
            "codec": selected,
            "label": labels.get(selected, selected),
            "hardware": bool(status["hardware"]),
        }
    except RuntimeError as exc:
        return {"codec": None, "label": f"Unavailable: {exc}", "hardware": False}


def channel_output_probe_target(channel: Channel) -> Path | None:
    if channel.output_type != "hls" or not any(node.node_type == "local" for node in node_controller.assigned_nodes(channel)):
        return None
    path = settings.hls_root / channel.slug / "index.m3u8"
    return path if path.exists() else None


def channel_hls_playback_ready(channel: Channel) -> bool:
    return any(node_controller.hls_ready(channel, node) for node in node_controller.assigned_nodes(channel))


def channel_delivery_status(channel: Channel, runtime_status: str, hls_ready: bool) -> str:
    """Return the user-facing delivery state for the Channels page.

    ``Up`` is deliberately stricter than an alive FFmpeg process: the channel
    must have a complete, playable HLS output. A requested/running channel that
    has not reached that point is ``Waiting``; only an inactive channel is
    ``Down``.
    """
    process_status = str(runtime_status or "unknown").strip().lower()
    if bool(channel.enabled) and channel.output_type == "hls" and bool(hls_ready):
        return "up"
    if bool(channel.enabled) and (
        bool(channel.desired_running)
        or process_status in {"running", "starting", "restarting", "degraded"}
    ):
        return "waiting"
    return "down"


def request_base_url(request: Request) -> str:
    configured = settings.public_base_url.rstrip("/")
    if configured and configured != "http://127.0.0.1":
        return configured
    return str(request.base_url).rstrip("/")


def _first_access_url(value: object) -> str:
    for row in str(value or "").replace("\r", "").split("\n"):
        cleaned = row.strip().rstrip("/")
        if cleaned:
            return cleaned
    return ""


def local_node_for_public_urls(db: Session) -> Node | None:
    """Read the Local/Main node without mutating it or opening a write lock.

    Public-base helpers are used while Remote Node synchronization is queued as
    a Starlette background task. Calling ensure_local_node() here updates
    last_seen_at and channel assignments, which starts a SQLite write
    transaction that can remain open while the background network calls run.
    A browser following the redirect to /nodes then collides with that lock and
    receives an Internal Server Error. URL lookup must therefore be read-only.
    """
    return db.scalar(
        select(Node)
        .where(Node.node_type == "local")
        .order_by(Node.id)
        .limit(1)
    )


def public_local_node(db: Session) -> Node:
    """Return the existing Local Node without taking a playlist write lock."""
    local = local_node_for_public_urls(db)
    if local is None:
        raise HTTPException(503, "Local Node is not initialized")
    return local


def main_panel_public_base(db: Session, request: Request) -> str:
    local = local_node_for_public_urls(db)
    if local is None:
        return request_base_url(request)
    return _first_access_url(local.api_urls or local.api_url) or request_base_url(request)


def main_playlist_public_base(db: Session, request: Request) -> str:
    local = local_node_for_public_urls(db)
    if local is None:
        return request_base_url(request)
    # STREAMFORGE_MAIN_REQUEST_MATCHED_PLAYLIST_BASE_V3037:
    # A Main Server can publish several Playlist/App aliases on different
    # ports and paths.  The playlist body must retain the exact alias used by
    # the client; otherwise a request through :8088 or /125 is silently
    # rewritten to the first configured URL and may become unreachable.
    request_scheme, request_host, request_port = request_main_authority(request)
    request_prefix = str(
        request.scope.get("root_path")
        or request.scope.get("state", {}).get("main_access_prefix")
        or ""
    ).strip("/")
    for raw in str(local.playlist_urls or local.playlist_url or "").replace("\r", "").splitlines():
        cleaned = raw.strip().rstrip("/")
        if not cleaned:
            continue
        try:
            parsed = urlsplit(cleaned)
            configured_host = str(parsed.hostname or "").strip().lower().rstrip(".")
            configured_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
            configured_prefix = normalize_node_access_slug(parsed.path.strip("/"))
        except (TypeError, ValueError):
            continue
        if (
            parsed.scheme == request_scheme
            and configured_host == request_host
            and configured_port == int(request_port)
            and configured_prefix == request_prefix
        ):
            return cleaned
    return (
        _first_access_url(local.playlist_urls or local.playlist_url)
        or _first_access_url(local.api_urls or local.api_url)
        or request_base_url(request)
    )


MAX_CHANNEL_LOGO_BYTES = 2 * 1024 * 1024
MAX_WEBPLAYER_DOWNLOAD_BYTES = 100 * 1024 * 1024
LOCAL_LOGO_PREFIX = "/channel-logos/"


# STREAMFORGE_MAIN_LOCAL_RUNTIME_ONLY_V33:
def main_channel_runtime(channel: Channel) -> dict[str, object]:
    """Return only the Main Server Local Node runtime for Main Panel controls.

    Remote Node runtimes remain available to playback/load-balancing code, but
    they must not change the Main channel's status/uptime/control buttons.
    """
    assigned = node_controller.assigned_nodes(channel)
    local = next((node for node in assigned if node.node_type == "local"), None)
    if local is None:
        return {
            "managed": False, "alive": False, "status": "stopped", "pid": None,
            "bitrate_kbps": 0, "speed_x": 0.0, "fps": 0.0, "width": None, "height": None,
            "uptime_seconds": 0, "last_error": None, "hls_ready": False,
            "metrics_fresh": False, "node_id": None, "node_name": "Main Server", "nodes": [],
        }
    item = dict(stream_manager.runtime_snapshot(int(channel.id)))
    alive = bool(item.get("alive"))
    db_status = str(channel.status or "").strip().lower()
    if alive:
        status = db_status if db_status in {"starting", "restarting"} else "running"
    elif bool(channel.desired_running):
        status = db_status if db_status in {"starting", "restarting", "error"} else "error"
    else:
        status = "stopped"
    # STREAMFORGE_MAIN_CHANNEL_STATUS_CACHE_ONLY_V114: status.json is a hot
    # panel endpoint. Never parse/stat HLS on this request thread; a bounded
    # background refresher maintains the readiness cache.
    try:
        hls_ready = bool(node_controller.cached_local_hls_ready(channel, local, alive=alive))
    except (OSError, RuntimeError):
        hls_ready = False
    last_error = str(channel.last_error or "").strip() if status in {"error", "starting", "restarting"} else ""
    item.update({
        "status": status,
        "last_error": last_error or None,
        "hls_ready": hls_ready,
        "node_id": int(local.id),
        "node_name": local.name or "Main Server",
    })
    local_row = dict(item)
    local_row.pop("nodes", None)
    item["nodes"] = [local_row]
    return item


# STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6:
def channel_info_replica_seed(channel: Channel) -> list[dict[str, object]]:
    """Return assigned-node cards without performing any Remote Node request."""
    rows: list[dict[str, object]] = []
    for node in node_controller.assigned_nodes(channel):
        rows.append({
            "node_id": int(node.id),
            "node_name": node.name or ("Local Node" if node.node_type == "local" else f"Node {node.id}"),
            "node_type": node.node_type,
            "node_state": "online" if node.node_type == "local" else "pending",
            "channel_state": "checking",
        })
    return rows


def normalize_channel_info_replica(item: dict[str, object]) -> dict[str, object]:
    node_online = bool(item.get("node_online"))
    raw_status = str(item.get("status") or "").strip().lower()
    alive = bool(item.get("alive"))
    if not node_online:
        channel_state = "offline"
    elif alive:
        channel_state = "running" if raw_status in {"", "up", "running"} else raw_status
    elif raw_status in {"down", "stopped"}:
        channel_state = "stopped"
    elif raw_status in {"waiting", "starting", "restarting", "degraded", "error"}:
        channel_state = raw_status
    else:
        channel_state = "stopped"
    return {
        "node_id": int(item.get("node_id") or 0),
        "node_name": str(item.get("node_name") or "Node"),
        "node_state": "online" if node_online else "offline",
        "channel_state": channel_state,
        "alive": alive,
        "hls_ready": bool(item.get("hls_ready")),
        "bitrate_kbps": int(item.get("bitrate_kbps") or 0),
        "speed_x": float(item.get("speed_x") or 0.0),
        # STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7:
        # Keep the two clocks explicit: host uptime is independent from this
        # channel's FFmpeg process uptime. Both are captured only on demand.
        "node_uptime_seconds": int(item.get("node_uptime_seconds") or 0),
        "channel_uptime_seconds": int(item.get("uptime_seconds") or 0),
        "uptime_seconds": int(item.get("uptime_seconds") or 0),
        "pid": item.get("pid"),
        "last_error": str(item.get("last_error") or "").strip() or None,
    }


def normalize_channel_logo_url(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > 2048:
        raise ValueError("Logo URL is too long")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Logo URL must start with http:// or https://")
    return cleaned


def _logo_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    return None


def save_channel_logo(upload: UploadFile | None, channel_slug: str) -> str | None:
    if not upload or not (upload.filename or "").strip():
        return None
    data = upload.file.read(MAX_CHANNEL_LOGO_BYTES + 1)
    if not data:
        raise ValueError("Selected logo file is empty")
    if len(data) > MAX_CHANNEL_LOGO_BYTES:
        raise ValueError("Logo file must be 2 MB or smaller")
    extension = _logo_extension(data)
    if not extension:
        raise ValueError("Logo file must be PNG, JPG, WEBP or GIF")
    settings.logo_root.mkdir(parents=True, exist_ok=True)
    safe_slug = slugify(channel_slug)
    filename = safe_slug + extension
    final_path = settings.logo_root / filename
    temporary = settings.logo_root / f".{filename}.{secrets.token_hex(4)}.tmp"
    temporary.write_bytes(data)
    temporary.chmod(0o644)
    temporary.replace(final_path)
    return f"{LOCAL_LOGO_PREFIX}{filename}"


def save_webplayer_download(
    upload: UploadFile | None,
    node_id: int,
    current_stored_name: str = "",
) -> tuple[str, str] | None:
    """Stream one admin-managed download into durable storage."""
    if not upload or not str(upload.filename or "").strip():
        return None
    original_name = Path(str(upload.filename).replace("\\", "/")).name.strip()
    if not original_name or original_name in {".", ".."} or len(original_name) > 180:
        raise ValueError("Download filename is invalid or too long")
    suffix = Path(original_name).suffix.lower()
    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ".bin"
    stored_name = f"node-{int(node_id)}-{secrets.token_hex(12)}{suffix}"
    root = settings.webplayer_download_root
    root.mkdir(parents=True, exist_ok=True)
    target = root / stored_name
    temporary = root / f".{stored_name}.{secrets.token_hex(4)}.tmp"
    total = 0
    try:
        with temporary.open("wb") as output:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_WEBPLAYER_DOWNLOAD_BYTES:
                    raise ValueError("Web Player download file must be 100 MB or smaller")
                output.write(chunk)
        if total <= 0:
            raise ValueError("Selected download file is empty")
        temporary.chmod(0o644)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    old_name = Path(str(current_stored_name or "")).name
    if old_name and old_name != stored_name:
        (root / old_name).unlink(missing_ok=True)
    return original_name, stored_name


def remove_webplayer_download(stored_name: object) -> None:
    safe = Path(str(stored_name or "")).name
    if safe:
        (settings.webplayer_download_root / safe).unlink(missing_ok=True)



# STREAMFORGE_MAIN_YOUTUBE_COOKIES_UPLOAD_V1013:
def save_youtube_cookies(upload: UploadFile | None) -> bool:
    if not upload or not str(upload.filename or "").strip():
        return False
    data = upload.file.read(4 * 1024 * 1024 + 1)
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


def remove_youtube_cookies() -> None:
    youtube_cookie_path().unlink(missing_ok=True)

def save_branding_logo(upload: UploadFile | None) -> str | None:
    if not upload or not (upload.filename or "").strip():
        return None
    data = upload.file.read(MAX_CHANNEL_LOGO_BYTES + 1)
    if not data:
        raise ValueError("Selected logo file is empty")
    if len(data) > MAX_CHANNEL_LOGO_BYTES:
        raise ValueError("Logo file must be 2 MB or smaller")
    extension = _logo_extension(data)
    if not extension:
        raise ValueError("Logo file must be PNG, JPG, WEBP or GIF")
    branding_root = settings.logo_root / "branding"
    branding_root.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(data).hexdigest()[:32] + extension
    final_path = branding_root / filename
    if not final_path.exists():
        temporary = branding_root / f".{filename}.{secrets.token_hex(4)}.tmp"
        temporary.write_bytes(data)
        temporary.replace(final_path)
    return f"{BRANDING_LOGO_PREFIX}{filename}"


def _favicon_extension(data: bytes) -> str | None:
    if data.startswith(b"\x00\x00\x01\x00"):
        return ".ico"
    return _logo_extension(data)


def save_branding_favicon(upload: UploadFile | None) -> str | None:
    if not upload or not (upload.filename or "").strip():
        return None
    data = upload.file.read(MAX_CHANNEL_LOGO_BYTES + 1)
    if not data:
        raise ValueError("Selected favicon file is empty")
    if len(data) > MAX_CHANNEL_LOGO_BYTES:
        raise ValueError("Favicon file must be 2 MB or smaller")
    extension = _favicon_extension(data)
    if not extension:
        raise ValueError("Favicon must be ICO, PNG, JPG, WEBP or GIF")
    root = settings.logo_root / "branding"
    root.mkdir(parents=True, exist_ok=True)
    filename = "favicon-" + hashlib.sha256(data).hexdigest()[:32] + extension
    final = root / filename
    if not final.exists():
        temporary = root / f".{filename}.{secrets.token_hex(4)}.tmp"
        temporary.write_bytes(data)
        temporary.chmod(0o644)
        temporary.replace(final)
    return f"{BRANDING_LOGO_PREFIX}{filename}"


def local_branding_asset_path(asset_url: str | None) -> Path | None:
    if not asset_url or not asset_url.startswith(BRANDING_LOGO_PREFIX):
        return None
    filename = asset_url[len(BRANDING_LOGO_PREFIX):]
    if not filename or filename != Path(filename).name:
        return None
    root = (settings.logo_root / "branding").resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def cleanup_branding_asset(asset_url: str | None) -> None:
    path = local_branding_asset_path(asset_url)
    if path is not None:
        try: path.unlink(missing_ok=True)
        except OSError: pass


def local_logo_path(logo_url: str | None) -> Path | None:
    if not logo_url or not logo_url.startswith(LOCAL_LOGO_PREFIX):
        return None
    filename = logo_url[len(LOCAL_LOGO_PREFIX):]
    if not filename or filename != Path(filename).name:
        return None
    candidate = (settings.logo_root / filename).resolve()
    root = settings.logo_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def cleanup_logo_if_unused(db: Session, logo_url: str | None) -> None:
    path = local_logo_path(logo_url)
    if path is None:
        return
    references = db.scalar(select(func.count(Channel.id)).where(Channel.logo_url == logo_url)) or 0
    if references == 0:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# STREAMFORGE_MAIN_PLAYLIST_LOGO_ALIAS_BASE_V1056:
# Uploaded channel logos follow the exact Playlist/App public base selected for
# the request.  For a configured alias such as https://host/123 the playlist
# therefore emits https://host/123/channel-logos/... .
# STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057:
# Managed Nginx normalizes the alias-prefixed logo URI to /channel-logos/...
# before proxying to the Public plane.  This keeps the public alias in the M3U
# while avoiding alias/root-path ambiguity inside the shared StaticFiles mount.
def channel_logo_public_url(
    channel: Channel, request: Request, db: Session | None = None,
    *, public_base: str = "",
) -> str:
    logo = (channel.logo_url or "").strip()
    if not logo:
        return ""
    if logo.startswith(LOCAL_LOGO_PREFIX):
        base = public_base or (main_playlist_public_base(db, request) if db is not None else request_base_url(request))
        return f"{str(base).rstrip('/')}{logo}"
    return logo


MAX_NODE_LOGO_BYTES = 2 * 1024 * 1024
LOCAL_NODE_LOGO_PREFIX = "/node-logos/"


def normalize_node_logo_url(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > 2048:
        raise ValueError("Node logo URL is too long")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Node logo URL must start with http:// or https://")
    return cleaned


def save_node_logo(upload: UploadFile | None) -> str | None:
    if not upload or not (upload.filename or "").strip():
        return None
    data = upload.file.read(MAX_NODE_LOGO_BYTES + 1)
    if not data:
        raise ValueError("Selected node logo file is empty")
    if len(data) > MAX_NODE_LOGO_BYTES:
        raise ValueError("Node logo file must be 2 MB or smaller")
    extension = _logo_extension(data)
    if not extension:
        raise ValueError("Node logo file must be PNG, JPG, WEBP or GIF")
    settings.node_logo_root.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(data).hexdigest()[:32] + extension
    final_path = settings.node_logo_root / filename
    if not final_path.exists():
        temporary = settings.node_logo_root / f".{filename}.{secrets.token_hex(4)}.tmp"
        temporary.write_bytes(data)
        temporary.replace(final_path)
    return f"{LOCAL_NODE_LOGO_PREFIX}{filename}"


def local_node_logo_path(logo_url: str | None) -> Path | None:
    if not logo_url or not logo_url.startswith(LOCAL_NODE_LOGO_PREFIX):
        return None
    filename = logo_url[len(LOCAL_NODE_LOGO_PREFIX):]
    if not filename or filename != Path(filename).name:
        return None
    candidate = (settings.node_logo_root / filename).resolve()
    root = settings.node_logo_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def cleanup_node_logo_if_unused(db: Session, logo_url: str | None) -> None:
    path = local_node_logo_path(logo_url)
    if path is None:
        return
    references = db.scalar(select(func.count(Node.id)).where(Node.logo_url == logo_url)) or 0
    if references == 0:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _node_favicon_setting_key(node_id: int) -> str:
    return f"node_favicon_{int(node_id)}"


def node_favicon_url(db: Session, node_id: int) -> str:
    row = db.get(AppSetting, _node_favicon_setting_key(node_id))
    return str(row.value or "").strip() if row else ""


def save_node_favicon(upload: UploadFile | None, node_id: int) -> str | None:
    if not upload or not (upload.filename or "").strip():
        return None
    data = upload.file.read(MAX_CHANNEL_LOGO_BYTES + 1)
    if not data:
        raise ValueError("Selected favicon file is empty")
    if len(data) > MAX_CHANNEL_LOGO_BYTES:
        raise ValueError("Favicon file must be 2 MB or smaller")
    extension = _favicon_extension(data)
    if not extension:
        raise ValueError("Favicon must be ICO, PNG, JPG, WEBP or GIF")
    settings.node_logo_root.mkdir(parents=True, exist_ok=True)
    filename = f"node-{int(node_id)}-favicon-" + hashlib.sha256(data).hexdigest()[:24] + extension
    final = settings.node_logo_root / filename
    if not final.exists():
        temp = settings.node_logo_root / f".{filename}.{secrets.token_hex(4)}.tmp"
        temp.write_bytes(data)
        temp.chmod(0o644)
        temp.replace(final)
    return f"{LOCAL_NODE_LOGO_PREFIX}{filename}"


def cleanup_node_favicon_file(favicon_url: str | None) -> None:
    path = local_node_logo_path(favicon_url)
    if path is not None:
        try: path.unlink(missing_ok=True)
        except OSError: pass


def node_logo_public_url(node: Node, request: Request) -> str:
    logo = (node.logo_url or "").strip()
    if not logo:
        return ""
    if logo.startswith(LOCAL_NODE_LOGO_PREFIX):
        return f"{request_base_url(request)}{logo}"
    return logo



_MAIN_CLIENT_SESSION_RESET_SECONDS = 60 * 60
_MAIN_CLIENT_SESSION_RESET_REFRESH_AT = 0.0
_MAIN_CLIENT_SESSION_RESET_LOCK = threading.RLock()

def _main_client_session_reset_seconds() -> int:
    # STREAMFORGE_MAIN_CLIENT_SESSION_RESET_OFFLINE_V1081
    global _MAIN_CLIENT_SESSION_RESET_SECONDS, _MAIN_CLIENT_SESSION_RESET_REFRESH_AT
    now = time.monotonic()
    with _MAIN_CLIENT_SESSION_RESET_LOCK:
        if now < _MAIN_CLIENT_SESSION_RESET_REFRESH_AT:
            return int(_MAIN_CLIENT_SESSION_RESET_SECONDS)
        minutes = 60
        try:
            with SessionLocal() as db:
                row = db.get(AppSetting, CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY)
                minutes = max(1, min(10080, int(row.value) if row else 60))
        except (TypeError, ValueError, OSError):
            minutes = 60
        _MAIN_CLIENT_SESSION_RESET_SECONDS = minutes * 60
        _MAIN_CLIENT_SESSION_RESET_REFRESH_AT = now + 60.0
        return int(_MAIN_CLIENT_SESSION_RESET_SECONDS)

_MAIN_CLIENT_HISTORY_TTL_SECONDS = 30 * 86400 + 3600
_MAIN_CLIENT_HISTORY_TTL_REFRESH_AT = 0.0
_MAIN_CLIENT_HISTORY_TTL_LOCK = threading.RLock()


def _main_client_session_history_ttl_seconds() -> int:
    # STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060: retain final Session
    # age for the same window as Main Client logs, plus a small handover margin.
    global _MAIN_CLIENT_HISTORY_TTL_SECONDS, _MAIN_CLIENT_HISTORY_TTL_REFRESH_AT
    now = time.monotonic()
    with _MAIN_CLIENT_HISTORY_TTL_LOCK:
        if now < _MAIN_CLIENT_HISTORY_TTL_REFRESH_AT:
            return int(_MAIN_CLIENT_HISTORY_TTL_SECONDS)
        days = 30
        try:
            with SessionLocal() as db:
                row = db.get(AppSetting, "log_retention_days")
                days = max(1, min(3650, int(row.value) if row else 30))
        except (TypeError, ValueError, OSError):
            days = 30
        _MAIN_CLIENT_HISTORY_TTL_SECONDS = max(3600, min(3650 * 86400 + 3600, days * 86400 + 3600))
        _MAIN_CLIENT_HISTORY_TTL_REFRESH_AT = now + 60.0
        return int(_MAIN_CLIENT_HISTORY_TTL_SECONDS)


def _main_client_session_history_observer_loop() -> None:
    """Persist Main Client-log session age from shared active viewer state."""
    while True:
        time.sleep(2.0)
        try:
            snapshot = viewer_tracker.snapshot()
        except Exception:
            continue
        if not snapshot.sessions:
            continue
        now_epoch = time.time()
        rows: list[tuple[int, str, float, float, str, str]] = []
        for session in snapshot.sessions:
            try:
                first_epoch = now_epoch - max(0.0, snapshot.captured_monotonic - float(session.first_seen_monotonic))
                last_epoch = now_epoch - max(0.0, snapshot.captured_monotonic - float(session.last_seen_monotonic))
            except (TypeError, ValueError):
                continue
            if first_epoch <= 0 or last_epoch < first_epoch:
                continue
            rows.append((
                int(session.user_id), str(session.session_id), first_epoch, last_epoch,
                str(session.client_ip or ""), str(session.user_agent or "")[:300],
            ))
        if rows:
            viewer_tracker.touch_history_batch(rows, history_ttl=_main_client_session_history_ttl_seconds())
            new_logical_sessions = viewer_tracker.touch_client_log_sessions_batch(
                [(int(row[0]), str(row[1])) for row in rows],
                reset_ttl=_main_client_session_reset_seconds(),
            )
            # STREAMFORGE_MAIN_CLIENT_SESSION_OBSERVER_ROW_V1081: if a stream
            # starts without a fresh login/catalog request, create the one
            # Client-log row here, off the playback hot path. A preceding login
            # already owns the same Redis key and therefore suppresses this row.
            if new_logical_sessions:
                by_identity = {(int(session.user_id), str(session.session_id)): session for session in snapshot.sessions}
                try:
                    with SessionLocal() as db:
                        for uid, sid in sorted(new_logical_sessions):
                            session = by_identity.get((uid, sid))
                            if session is None:
                                continue
                            user = db.get(StreamUser, uid)
                            actor = str(user.name if user is not None else f"User {uid}")
                            first_epoch = now_epoch - max(0.0, snapshot.captured_monotonic - float(session.first_seen_monotonic))
                            log_event(
                                "Playback session started", scope="client", actor=actor,
                                channel_id=int(session.channel_id) if int(session.channel_id) > 0 else None,
                                details={
                                    "ip": str(session.client_ip or ""),
                                    "client": str(session.user_agent or "")[:300],
                                    "user_id": uid, "session_id": sid,
                                    "session_started_epoch": first_epoch,
                                }, db=db,
                            )
                        db.commit()
                except Exception:
                    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # STREAMFORGE_PUBLIC_PROCESS_ISOLATION_V62:
    # Public WebPlayer/API workers run the same route module for compatibility,
    # but they must never execute control-plane startup/shutdown side effects.
    # This keeps channel restore, FFmpeg ownership, metrics and backup scheduling
    # exclusively inside the single Main control worker. Redis-backed runtime
    # state remains shared across every Public worker.
    process_role = str(os.getenv("STREAMFORGE_PROCESS_ROLE", "control") or "control").strip().lower()
    if process_role == "public":
        with SessionLocal() as db:
            connection_tracker.set_total_limit(app_setting_int(db, MAIN_TOTAL_CONNECTIONS_KEY, 0))
            viewer_ttl = max(5, min(3600, app_setting_int(db, VIEWER_SESSION_TIMEOUT_KEY, 5)))
            connection_tracker.set_ttl(viewer_ttl)
            viewer_tracker.ttl_seconds = viewer_ttl
        yield
        return

    Path(settings.hls_root).mkdir(parents=True, exist_ok=True)
    Path(settings.logo_root).mkdir(parents=True, exist_ok=True)
    Path(settings.node_logo_root).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    with SessionLocal() as db:
        super_role = ensure_default_roles(db)
        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_user))
        if not existing:
            db.add(
                AdminUser(
                    username=settings.admin_user,
                    password_hash=hash_password(settings.admin_password),
                    role=super_role,
                )
            )
        elif existing.role_id is None:
            existing.role = super_role
        connection_tracker.set_total_limit(app_setting_int(db, MAIN_TOTAL_CONNECTIONS_KEY, 0))
        viewer_ttl = max(5, min(3600, app_setting_int(db, VIEWER_SESSION_TIMEOUT_KEY, 5)))
        connection_tracker.set_ttl(viewer_ttl)
        viewer_tracker.ttl_seconds = viewer_ttl
        system_metrics.set_network_interfaces(selected_network_interfaces(db))
        db.commit()
    with SessionLocal() as db:
        ensure_local_node(db)
        db.commit()
    # STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_CONTROL_ISOLATION_V1115:
    # The HTTP control worker no longer resets/restores/stops Local FFmpeg. The
    # dedicated streamforge-channel-supervisor service owns that lifecycle, so
    # panel reloads/restarts cannot accumulate hundreds of FFmpeg reader threads
    # in Gunicorn or interrupt live channels.
    if not stream_manager.supervisor_ping(timeout=1.0):
        log_event(
            "Main channel supervisor unavailable at control-worker startup",
            scope="system", level="warning",
        )
    # STREAMFORGE_MAIN_GPU_BACKGROUND_START_V1069: dashboard/API reads cached GPU metrics only.
    system_metrics.start()
    threading.Thread(target=_resync_restored_logo_assets, daemon=True).start()
    metrics_history.start()
    threading.Thread(target=_main_client_session_history_observer_loop, name="streamforge-client-session-history", daemon=True).start()
    threading.Thread(target=_backup_scheduler_loop, name="streamforge-backup-scheduler", daemon=True).start()
    yield
    metrics_history.stop()
    system_metrics.stop()
    # STREAMFORGE_MAIN_SUPERVISOR_SURVIVES_PANEL_RESTART_V1115:
    # Deliberately do not stop Local channels here.


class RestoredAssetStaticFiles(StaticFiles):
    """Static logo responses that never retain a pre-restore 404/success."""

    async def get_response(self, path: str, scope: dict) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            response = Response(status_code=exc.status_code)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-StreamForge-Asset-Version"] = APP_VERSION
        return response


app = FastAPI(title="StreamForge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/channel-logos", RestoredAssetStaticFiles(directory=str(settings.logo_root), check_dir=False), name="channel-logos")
app.mount("/node-logos", RestoredAssetStaticFiles(directory=str(settings.node_logo_root), check_dir=False), name="node-logos")


@app.exception_handler(AuthenticationRequired)
async def authentication_required_handler(request: Request, _exc: AuthenticationRequired):
    if request.url.path.endswith(".json") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    # STREAMFORGE_MAIN_LOGIN_CLEAN_REDIRECT_V122:
    # Internal panel navigation is already retained by the panel-route cookie;
    # the legacy ?next= query was not consumed by login and only exposed the
    # internal route in the address bar.
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(PermissionDenied)
async def permission_denied_handler(request: Request, exc: PermissionDenied):
    if request.url.path.endswith(".json") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": "Permission denied", "permission": exc.permission}, status_code=403)
    with SessionLocal() as db:
        admin = current_admin(request, db)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="access_denied.html",
            context={
                "admin": admin,
                "app_version": APP_VERSION,
                "permission": exc.permission,
                "can": lambda key: has_permission(admin, key),
            },
            status_code=403,
        )


@app.middleware("http")
async def route_and_enforce_main_access_aliases(request: Request, call_next):
    """Route exact Main aliases and enforce host, port, slug and endpoint role.

    A playlist-only root alias can no longer expose the control panel, and a
    Panel/API alias such as ``host:8080/admin`` is authoritative for panel
    routes. Public application routes are accepted only below their exact
    configured role alias; no legacy or sibling-path redirect is performed.
    """
    enabled, aliases = main_access_policy()
    request_scheme, request_host, request_port = request_main_authority(request)
    # STREAMFORGE_MAIN_INTERNAL_RELAY_FALLBACK_V111: Nginx direct relay hits
    # already bypass alias/panel policy. A filesystem miss is proxied to the
    # Public worker for relay-key validation and safe 503 fallback; allow that
    # exact loopback handoff even when the public Playlist URL uses an alias.
    original_request_path = str(request.scope.get("path") or "/")
    # The public proxy explicitly clears this header; only the named Nginx
    # try_files miss handler sets it. Do not key this on request.client because
    # Uvicorn may replace the loopback peer with X-Forwarded-For.
    internal_relay_fallback = bool(
        str(request.headers.get("x-streamforge-internal-relay-fallback") or "").strip() == "1"
        and original_request_path.startswith("/relay/")
    )
    if internal_relay_fallback:
        return await call_next(request)
    if enabled and loopback_host_request(request, request_host):
        return await call_next(request)

    if enabled:
        canonical_protocol_target = main_canonical_protocol_redirect_target(request, aliases)
        if canonical_protocol_target:
            return RedirectResponse(
                canonical_protocol_target,
                status_code=307,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "X-StreamForge-Version": APP_VERSION,
                    "X-StreamForge-Route": "main-canonical-protocol-redirect",
                },
            )

    authority_aliases = [
        item for item in aliases
        if item[0] == request_scheme and item[1] == request_host and item[2] == request_port
    ]
    matched = match_main_access_alias(request, aliases)
    prefix = ""
    original_path = str(request.scope.get("path") or "/")

    if matched is not None:
        slug, roles = matched
        if slug:
            prefix = f"/{slug}"
            stripped = original_path[len(prefix):] or "/"
            if not stripped.startswith("/"):
                stripped = "/" + stripped
        else:
            stripped = original_path

        # STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49: when the browser requests
        # the visible Panel/API root, internally dispatch the last selected
        # panel GET route from the browser cookie. This keeps a real full-page
        # request/reload while /channels, /nodes, etc. never need to remain in
        # the address bar. The cookie cannot escape the panel route allow-list.
        if stripped == "/" and "panel" in roles:
            hidden_target = normalize_hidden_panel_target(request.cookies.get(STREAMFORGE_PANEL_ROUTE_COOKIE))
            if hidden_target and hidden_target != "/":
                hidden_parts = urlsplit(hidden_target)
                stripped = hidden_parts.path or "/"
                request.scope["query_string"] = hidden_parts.query.encode("utf-8")
                request.scope.setdefault("state", {})["streamforge_hidden_panel_target"] = hidden_target

        # STREAMFORGE_MAIN_ROOT_PANEL_PREFIX_EXCLUSION_V3059:
        # When the configured Panel/API URL is the authority root, /panel is
        # an internal implementation prefix and must not become a second public
        # entry point.  An explicitly configured /panel alias remains valid
        # because longest-prefix matching selects its non-empty slug above.
        if (
            enabled
            and not slug
            and "panel" in roles
            and (original_path == "/panel" or original_path.startswith("/panel/"))
        ):
            return Response(
                status_code=418,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Connection": "close",
                    "X-StreamForge-Version": APP_VERSION,
                    "X-StreamForge-Route": "main-root-panel-prefix-excluded-silent-drop",
                },
            )
        # STREAMFORGE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R4:
        # /web-player is an internal implementation route. When it is reached
        # through a dedicated Playlist/App alias, canonicalize the browser back
        # to the configured alias root while keeping the internal route available.
        if (
            "playlist" in roles
            and "panel" not in roles
            and stripped == "/web-player"
            and original_path != (prefix or "/")
            and request.method.upper() in {"GET", "HEAD"}
        ):
            target = prefix or "/"
            query = bytes(request.scope.get("query_string") or b"").decode("utf-8", "ignore")
            if query:
                target += ("&" if "?" in target else "?") + query
            return RedirectResponse(
                target,
                status_code=307,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "X-StreamForge-Version": APP_VERSION,
                    "X-StreamForge-Route": "webplayer-clean-canonical-root",
                },
            )

        # STREAMFORGE_PLAYLIST_ROOT_DIRECT_PLAYER_V2188:
        # The configured Playlist/App URL itself is the Web Player location.
        # Internally rewrite it to /web-player instead of issuing a browser
        # redirect that can be contaminated by a Panel/API prefix.
        if stripped == "/" and "playlist" in roles and "panel" not in roles:
            stripped = "/web-player"
        required_role = main_route_role(stripped)

        # STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036: a route belonging to
        # another role must fail at the alias where it was requested. Never
        # redirect /get.php to /test/get.php or /admin/get.php to /test/get.php.
        if enabled and required_role != "shared" and required_role not in roles:
            return Response(
                status_code=418,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Connection": "close",
                    "X-StreamForge-Version": APP_VERSION,
                    "X-StreamForge-Route": "main-exact-alias-required-silent-drop",
                },
            )
        # STREAMFORGE_MAIN_ROOT_PLAYLIST_SCOPE_REWRITE_V2196:
        # A root Playlist/App alias has prefix == "". When its public "/" path is
        # internally mapped to "/web-player", the scope still must be rewritten.
        # The old `if prefix:` gate skipped that rewrite and let the application's
        # normal "/" route redirect the browser to the Panel/API alias (/admin).
        if prefix or stripped != original_path:
            request.scope["path"] = stripped
            request.scope["raw_path"] = stripped.encode("utf-8")
        if prefix:
            request.scope["root_path"] = prefix
            request.scope.setdefault("state", {})["main_access_prefix"] = prefix
    elif enabled:
        required_role = main_route_role(original_path)
        if authority_aliases and required_role == "shared":
            return await call_next(request)
        return Response(
            status_code=418,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "close",
                "X-StreamForge-Version": APP_VERSION,
                "X-StreamForge-Route": "main-unmatched-link-silent-drop",
            },
        )

    # STREAMFORGE_MAIN_PANEL_PLAYBACK_POLICY_ENFORCEMENT_V100R3:
    # Local/Main Panel rules protect Main browser/API routes. Local/Main
    # Playback & catalog rules are a global Main Playlist/App gate; candidate
    # playback Node policies remain a separate per-channel check.
    policy_path = str(request.scope.get("path") or original_path or "/")
    policy_role = main_route_role(policy_path)

    # STREAMFORGE_NODE_INTERNAL_AUTH_PANEL_POLICY_BYPASS_V102:
    # Remote Nodes call a small set of Main control/live-auth endpoints with
    # X-Node-Token authentication. Those requests are machine-to-machine
    # traffic, not browser Panel visits, so the Main browser IP/ASN policy must
    # not reject the Node transport address before the endpoint can validate
    # its per-node token. Host/path authority checks above still apply and each
    # endpoint remains fail-closed on its own token validation.
    node_internal_prefixes = (
        "/api/v1/node-access-back-sync/",
        "/api/v1/node-panel-live-auth/",
        "/api/v1/node-heartbeat/",
        "/api/v1/node-control/",
    )
    node_internal_auth = bool(str(request.headers.get("x-node-token") or "").strip()) and any(
        policy_path == prefix.rstrip("/") or policy_path.startswith(prefix)
        for prefix in node_internal_prefixes
    )
    if policy_role in {"panel", "playlist"} and not node_internal_auth:
        decision = _main_public_access_decision(request, policy_role)
        if not decision.allowed:
            return _main_access_restricted_response(request, policy_role, decision.reason)

    response = await call_next(request)

    # STREAMFORGE_MAIN_PANEL_UNKNOWN_SILENT_DROP_V2198:
    # Unknown descendants of a valid Main Panel/API alias must fail closed.
    # Nginx maps this internal 418 trigger to @streamforge_silent_drop (444).
    if (
        enabled
        and matched is not None
        and "panel" in matched[1]
        and response.status_code == 404
    ):
        return Response(
            status_code=418,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "close",
                "X-StreamForge-Version": APP_VERSION,
                "X-StreamForge-Route": "main-panel-unknown-silent-drop",
            },
        )

    response.headers["X-StreamForge-Version"] = APP_VERSION
    prefix = str(request.scope.get("state", {}).get("main_access_prefix") or "")
    if prefix:
        location = response.headers.get("location") or ""
        if location.startswith("/") and location != prefix and not location.startswith(prefix + "/"):
            response.headers["location"] = prefix + location
    return response


@app.middleware("http")
async def prevent_stale_panel_html(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# STREAMFORGE_MAIN_PANEL_LINK_PREFIX_V2199:
# The Main Panel exposes its active access prefix to base.html so existing
# root-relative links/forms can be rewritten client-side without changing
# Playlist/App URLs or every individual template.
def render(request: Request, name: str, db: Session, **context):
    admin = current_admin(request, db)
# Compatibility invariant: admin_is_super_admin(admin) or has_permission(admin, "roles.create")
    # STREAMFORGE_MAIN_TEMPLATE_PERMISSION_CACHE_V1114:
    # Dense tables ask can() for the same permissions dozens/hundreds of times.
    # Decode the role JSON once per response instead of once per button/cell.
    permission_set = role_permission_set(admin.role) if admin and admin.is_active else set()
    is_super = SUPERUSER_PERMISSION in permission_set
    can_permission = lambda key: bool(is_super or key in permission_set)
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context={
            "admin": admin,
            "app_version": APP_VERSION,
            "app_root": str(request.scope.get("root_path") or "").rstrip("/"),
            "branding": branding_settings(db),
            # STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49: normal document reloads
            # are retained, while the browser-facing URL stays at the configured
            # Panel/API root. Internal route state is exposed only to panel JS.
            # STREAMFORGE_MAIN_PANEL_HOVER_SETTING_RUNTIME_V99R18:
            "hide_panel_hover_urls": app_setting_str(db, HIDE_PANEL_HOVER_URLS_KEY, "1").lower() not in {"0", "false", "off", "no"},
            "panel_internal_url": (
                str(request.scope.get("path") or "/")
                + (("?" + bytes(request.scope.get("query_string") or b"").decode("utf-8", "ignore")) if request.scope.get("query_string") else "")
            ),
            "can": can_permission,
            "is_super_admin": is_super,
            "can_create_roles": is_super or can_permission("roles.create"),
            "permission_groups": PERMISSION_GROUPS,
            "format_local_time": format_local_time,
            "source_endpoint": source_endpoint,
            "channel_active_source_endpoint": channel_active_source_endpoint,
            "human_duration": human_duration,
            "channel_category_names": channel_category_names,
            "channel_category_list": channel_category_list,
            "channel_source_program_ids": channel_source_program_ids,
            "playlist_channel_count": lambda playlist: len(ordered_playlist_channels(playlist)),
            **context,
        },
    )


def redirect_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.get("/health", response_class=PlainTextResponse)
def health():
    role = str(os.getenv("STREAMFORGE_PROCESS_ROLE", "control") or "control").strip().lower()
    return PlainTextResponse("ok", headers={"X-StreamForge-Process-Role": role})


@app.get("/version", response_class=PlainTextResponse)
def version():
    return APP_VERSION


@app.get("/branding-assets/{filename}")
def branding_asset(filename: str):
    safe = Path(filename).name
    if safe != filename or not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico")):
        raise HTTPException(404)
    path = settings.logo_root / "branding" / safe
    if not path.is_file():
        raise HTTPException(404)
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon"}
    # STREAMFORGE_MAIN_BRANDING_HASH_CACHE_V1116:
    # The filename contains a SHA-256 content hash, so it is safe to cache for
    # a year. Previous no-store headers forced the ~1 MB panel logo/favicon to
    # download again on every Dashboard/Channels/Users navigation.
    return FileResponse(path, media_type=media.get(path.suffix.lower(), "application/octet-stream"), headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-StreamForge-Asset-Version": APP_VERSION,
    })


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if admin:
        return RedirectResponse(first_allowed_path(admin), status_code=303)
    return render(request, "login.html", db, error=None)


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))
    if not admin or not admin.is_active or not admin.main_panel_access or not verify_password(password, admin.password_hash):
        # STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73: Access logs always retain the real browser/client IP.
        log_event(f"Failed panel login for {username.strip() or 'unknown'}", scope="auth", level="warning", actor=username.strip() or None, details={"ip": client_ip(request)})
        return render(request, "login.html", db, error="Invalid username or password")
    request.session["admin_id"] = admin.id
    log_event("Panel login succeeded", scope="auth", actor=admin.username, details={"ip": client_ip(request)})
    return RedirectResponse(first_allowed_path(admin), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(STREAMFORGE_PANEL_ROUTE_COOKIE, path="/")
    return response


@app.get("/system/branding", response_class=HTMLResponse, dependencies=[Depends(permission_required("settings.view"))])
def system_branding_page(
    request: Request,
    saved: int = 0,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    selected_interfaces = selected_network_interfaces(db)
    return render(
        request, "system_branding.html", db,
        saved=bool(saved), message=message, error=error or None,
        main_total_max_connections=app_setting_int(db, MAIN_TOTAL_CONNECTIONS_KEY, 0),
        metrics_retention_days=app_setting_int(db, METRICS_RETENTION_DAYS_KEY, 30),
        log_retention_days=app_setting_int(db, LOG_RETENTION_DAYS_KEY, 30),
        metrics_default_span_hours=app_setting_int(db, METRICS_DEFAULT_SPAN_HOURS_KEY, 24),
        metrics_sample_interval_seconds=app_setting_int(db, METRICS_SAMPLE_INTERVAL_SECONDS_KEY, 60),
        viewer_session_timeout_seconds=max(5, min(3600, app_setting_int(db, VIEWER_SESSION_TIMEOUT_KEY, 5))),
        client_session_reset_offline_minutes=max(1, min(10080, app_setting_int(db, CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY, 60))),
        hide_panel_hover_urls=app_setting_str(db, HIDE_PANEL_HOVER_URLS_KEY, "1").lower() not in {"0", "false", "off", "no"},
        available_network_interfaces=sorted(set(available_network_interfaces()) | set(selected_interfaces)),
        selected_network_interfaces=selected_interfaces,
        youtube_cookies_configured=youtube_cookie_configured(),
    )


@app.post("/system/branding", response_class=HTMLResponse, dependencies=[Depends(permission_required("settings.edit"))])
def system_branding_save(
    request: Request,
    brand_name: str = Form("StreamForge"),
    brand_subtitle: str = Form(""),
    logo_file: UploadFile | None = File(None),
    remove_logo: Optional[str] = Form(None),
    favicon_file: UploadFile | None = File(None),
    remove_favicon: Optional[str] = Form(None),
    youtube_cookies_file: UploadFile | None = File(None),
    remove_youtube_cookies_file: Optional[str] = Form(None),
    total_max_connections: int = Form(0),
    metrics_retention_days: int = Form(30),
    log_retention_days: int = Form(30),
    metrics_default_span_hours: int = Form(24),
    metrics_sample_interval_seconds: int = Form(60),
    viewer_session_timeout_seconds: int = Form(5),
    client_session_reset_offline_minutes: int = Form(60),
    hide_panel_hover_urls: Optional[str] = Form(None),
    network_interfaces: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    cleaned_name = " ".join(brand_name.split())[:80]
    submitted_subtitle = str(brand_subtitle or "")
    cleaned_subtitle = " ".join(submitted_subtitle.split())[:120] if submitted_subtitle.strip() else ""
    if not cleaned_name:
        return render(request, "system_branding.html", db, saved=False, message="", backup_targets=load_backup_targets(), backup_rclone=backup_rclone_status(), error="Brand name is required", main_total_max_connections=max(0, min(1000000, int(total_max_connections or 0))), metrics_retention_days=max(1, min(3650, int(metrics_retention_days or 30))), log_retention_days=max(1, min(3650, int(log_retention_days or 30))), available_network_interfaces=available_network_interfaces(), selected_network_interfaces=selected_network_interfaces(db))
    branding = branding_settings(db)
    old_logo_url = branding["logo_url"]
    old_favicon_url = branding["favicon_url"]
    logo_url = old_logo_url
    favicon_url = old_favicon_url
    try:
        uploaded_logo = save_branding_logo(logo_file)
        if uploaded_logo:
            logo_url = uploaded_logo
        elif as_bool(remove_logo):
            logo_url = ""
        uploaded_favicon = save_branding_favicon(favicon_file)
        if uploaded_favicon:
            favicon_url = uploaded_favicon
        elif as_bool(remove_favicon):
            favicon_url = ""
        if as_bool(remove_youtube_cookies_file):
            remove_youtube_cookies()
        save_youtube_cookies(youtube_cookies_file)
    except ValueError as exc:
        return render(request, "system_branding.html", db, saved=False, message="", backup_targets=load_backup_targets(), backup_rclone=backup_rclone_status(), error=str(exc), main_total_max_connections=max(0, min(1000000, int(total_max_connections or 0))), metrics_retention_days=max(1, min(3650, int(metrics_retention_days or 30))), log_retention_days=max(1, min(3650, int(log_retention_days or 30))), available_network_interfaces=available_network_interfaces(), selected_network_interfaces=selected_network_interfaces(db))
    save_app_setting(db, BRANDING_NAME_KEY, cleaned_name)
    save_app_setting(db, BRANDING_SUBTITLE_KEY, cleaned_subtitle)
    # Flush immediately so an explicitly blank subtitle is guaranteed to become
    # a real app_settings row instead of being confused with a missing setting.
    db.flush()
    subtitle_row = db.get(AppSetting, BRANDING_SUBTITLE_KEY)
    if subtitle_row is None:
        raise RuntimeError("Subtitle setting could not be persisted")
    subtitle_row.value = cleaned_subtitle
    save_app_setting(db, BRANDING_LOGO_KEY, logo_url)
    save_app_setting(db, BRANDING_FAVICON_KEY, favicon_url)
    db.flush()
    if old_logo_url and old_logo_url != logo_url:
        cleanup_branding_asset(old_logo_url)
    if old_favicon_url and old_favicon_url != favicon_url:
        cleanup_branding_asset(old_favicon_url)
    connection_limit = max(0, min(1000000, int(total_max_connections or 0)))
    save_app_setting(db, MAIN_TOTAL_CONNECTIONS_KEY, connection_limit)
    log_days = max(1, min(3650, int(log_retention_days or 30)))
    save_app_setting(db, LOG_RETENTION_DAYS_KEY, log_days)
    retention_days = max(1, min(3650, int(metrics_retention_days or 30)))
    save_app_setting(db, METRICS_RETENTION_DAYS_KEY, retention_days)
    default_span_hours = max(1, min(retention_days * 24, int(metrics_default_span_hours or 24)))
    save_app_setting(db, METRICS_DEFAULT_SPAN_HOURS_KEY, default_span_hours)
    sample_interval_seconds = max(5, min(3600, int(metrics_sample_interval_seconds or 60)))
    save_app_setting(db, METRICS_SAMPLE_INTERVAL_SECONDS_KEY, sample_interval_seconds)
    viewer_ttl_seconds = max(5, min(3600, int(viewer_session_timeout_seconds or 5)))
    save_app_setting(db, VIEWER_SESSION_TIMEOUT_KEY, viewer_ttl_seconds)
    session_reset_minutes = max(1, min(10080, int(client_session_reset_offline_minutes or 60)))
    save_app_setting(db, CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY, session_reset_minutes)
    hide_hover_urls = as_bool(hide_panel_hover_urls)
    save_app_setting(db, HIDE_PANEL_HOVER_URLS_KEY, "1" if hide_hover_urls else "0")
    available_interfaces = set(available_network_interfaces())
    selected_interfaces = [] if "__all__" in network_interfaces else sorted({item for item in network_interfaces if item in available_interfaces})
    save_app_setting(db, METRICS_NETWORK_INTERFACES_KEY, ",".join(selected_interfaces))
    prune_logs(log_days, db=db, force=True)
    db.commit()
    connection_tracker.set_total_limit(connection_limit)
    connection_tracker.set_ttl(viewer_ttl_seconds)
    viewer_tracker.ttl_seconds = viewer_ttl_seconds
    system_metrics.set_network_interfaces(selected_interfaces)
    metrics_history.prune()

    # STREAMFORGE_VIEWER_TTL_NODE_SYNC_V2254:
    # Push the viewer-session timeout + hover-link preference to every enabled Remote Node.
    node_sync_errors: list[str] = []
    remote_nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.id)).all()
    for item in remote_nodes:
        try:
            node_controller.sync_access_settings(
                item,
                request_base_url(request),
                webplayer_settings=webplayer_settings_for_node(db, item.id),
            )
        except Exception as exc:
            node_sync_errors.append(f"{item.name}: {exc}")

    log_event("Main settings updated", scope="system", actor=admin.username, details={
        "total_max_connections": connection_limit,
        "log_retention_days": log_days,
        "metrics_retention_days": retention_days,
        "metrics_default_span_hours": default_span_hours,
        "metrics_sample_interval_seconds": sample_interval_seconds,
        "viewer_session_timeout_seconds": viewer_ttl_seconds,
        "client_session_reset_offline_minutes": session_reset_minutes,
        "hide_panel_hover_urls": hide_hover_urls,
        "network_interfaces": selected_interfaces or ["all"],
        "youtube_cookies_configured": youtube_cookie_configured(),
    })
    suffix = "&message=" + quote_plus("Settings saved; Node sync warnings: " + "; ".join(node_sync_errors)) if node_sync_errors else ""
    return RedirectResponse("/system/branding?saved=1" + suffix, status_code=303)





@app.get("/system/backups", response_class=HTMLResponse, dependencies=[Depends(permission_required("backups.view"))])
def system_backups_page(request: Request, message: str = "", error: str = "", db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    return render(
        request, "backups.html", db,
        message=message, error=error or None,
        backup_targets=load_backup_targets(),
        backup_google_drive=backup_google_drive_status(),
        local_backup=local_backup_settings(),
    )


@app.post("/system/backups/google/credentials", dependencies=[Depends(permission_required("backups.edit"))])
def system_backup_google_credentials(
    request: Request,
    client_id: str = Form(""),
    client_secret: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        save_google_oauth_credentials(client_id, client_secret)
        log_event("Google Drive OAuth credentials saved", scope="system", actor=admin.username)
        return RedirectResponse("/system/backups?message=" + quote_plus("Google OAuth credentials saved"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/system/backups?error=" + quote_plus(str(exc)), status_code=303)


@app.get("/system/backups/google/connect", dependencies=[Depends(permission_required("backups.edit"))])
def system_backup_google_connect(request: Request, remote: str = "gdrive", db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        creds = load_google_oauth_credentials()
    except Exception as exc:
        return RedirectResponse("/system/backups?error=" + quote_plus(str(exc)), status_code=303)

    state = secrets.token_urlsafe(32)
    request.session["google_drive_oauth_state"] = state
    redirect_uri = str(request.url_for("system_backup_google_callback"))

    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=302)


@app.get("/system/backups/google/callback", name="system_backup_google_callback")
def system_backup_google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()

    expected_state = str(request.session.pop("google_drive_oauth_state", "") or "")

    if error:
        return RedirectResponse("/system/backups?error=" + quote_plus("Google authorization failed: " + error), status_code=303)
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return RedirectResponse("/system/backups?error=" + quote_plus("Google authorization state check failed"), status_code=303)
    if not code:
        return RedirectResponse("/system/backups?error=" + quote_plus("Google did not return an authorization code"), status_code=303)

    try:
        creds = load_google_oauth_credentials()
        redirect_uri = str(request.url_for("system_backup_google_callback"))
        payload = urlencode({
            "code": code,
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode("utf-8")
        token_request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(token_request, timeout=30) as response:
            token_response = json.loads(response.read().decode("utf-8"))

        access_token = str(token_response.get("access_token") or "")
        refresh_token = str(token_response.get("refresh_token") or "")
        if not access_token or not refresh_token:
            raise RuntimeError("Google did not return a refresh token. Remove previous app access in your Google Account and connect again.")

        expires_in = int(token_response.get("expires_in") or 3600)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in))).isoformat().replace("+00:00", "Z")
        direct_token = {
            "access_token": access_token,
            "token_type": str(token_response.get("token_type") or "Bearer"),
            "refresh_token": refresh_token,
            "expiry": expiry,
        }
        save_google_drive_token(direct_token)
        test_backup_google_drive_connection()
        log_event("Google Drive connected", scope="system", actor=admin.username)
        return RedirectResponse("/system/backups?message=" + quote_plus("Google Drive connected successfully"), status_code=303)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = str(exc)
        return RedirectResponse("/system/backups?error=" + quote_plus("Google token exchange failed: " + detail[-500:]), status_code=303)
    except Exception as exc:
        log_event("Google Drive direct sign-in failed", scope="system", level="error", actor=admin.username, details={"error": type(exc).__name__})
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Google Drive connection could not be saved. Please try Sign in with Google again."),
            status_code=303,
        )



@app.post("/system/backups/test-destination", dependencies=[Depends(permission_required("backups.edit"))])
def system_backup_test_destination(
    request: Request,
    kind: str = Form("local"),
    destination: str = Form(""),
    key_path: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    domain: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)
    try:
        result = test_backup_destination(kind, destination, key_path, username, password, domain)
        log_event(
            "Backup destination tested",
            scope="system",
            actor=admin.username,
            details={"kind": kind, "ok": True},
        )
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        log_event(
            "Backup destination test failed",
            scope="system",
            level="error",
            actor=admin.username,
            details={"kind": kind, "error": type(exc).__name__},
        )
        return JSONResponse({"ok": False, "error": str(exc)[-500:]}, status_code=400)


@app.post("/system/backups/google/test", dependencies=[Depends(permission_required("backups.edit"))])
def system_backup_google_test(request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)
    try:
        result = test_backup_google_drive_connection()
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[-500:]}, status_code=400)


@app.post("/system/backups/add", dependencies=[Depends(permission_required("backups.add"))])
def system_backup_add(request: Request, name: str = Form(""), kind: str = Form("local"), destination: str = Form(""), key_path: str = Form(""), username: str = Form(""), password: str = Form(""), domain: str = Form(""), schedule_hours: int = Form(24), schedule_time: str = Form("00:00"), rotation_keep: int = Form(20), run_now: int = Form(0), db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        target = add_backup_target(name, kind, destination, key_path, schedule_hours, username, password, domain, rotation_keep, schedule_time)
        log_event("Backup destination added", scope="system", actor=admin.username, details={"name": target["name"], "kind": target["kind"]})
        message = "Backup destination added"
        if int(run_now or 0) == 1:
            result = run_backup_target(str(target["id"]))
            log_event("Backup completed", scope="system", actor=admin.username, details={"target": result.get("name"), "archive": result.get("last_archive")})
            message = "Backup destination added and backup completed successfully"
    except (ValueError, TypeError) as exc:
        return RedirectResponse("/system/backups?error=" + quote_plus(str(exc)), status_code=303)
    except Exception as exc:
        log_event("Backup failed after destination creation", scope="system", level="error", actor=admin.username, details={"error": str(exc)[-300:]})
        return RedirectResponse("/system/backups?error=" + quote_plus("Backup destination was saved, but backup failed: " + str(exc)[-300:]), status_code=303)
    return RedirectResponse("/system/backups?message=" + quote_plus(message), status_code=303)



@app.post("/system/backups/local/update", dependencies=[Depends(permission_required("backups.edit"))])
def system_local_backup_update(
    request: Request,
    name: str = Form("Main Server local backups"),
    rotation_keep: int = Form(20),
    schedule_hours: int = Form(24),
    schedule_time: str = Form("00:00"),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        settings = save_local_backup_settings(name, rotation_keep, schedule_hours, schedule_time)
        from .backup_manager import _prune_local_backup_archives
        _prune_local_backup_archives(keep=int(settings.get("rotation_keep") or 20))
        log_event(
            "Main Server local backup settings updated",
            scope="system",
            actor=admin.username,
            details={"rotation_keep": settings.get("rotation_keep"), "schedule_hours": settings.get("schedule_hours"), "schedule_time": settings.get("schedule_time")},
        )
        return RedirectResponse(
            "/system/backups?message=" + quote_plus("Main Server local backup settings updated"),
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Local backup update failed: " + str(exc)[-300:]),
            status_code=303,
        )



@app.post("/system/backups/local/run", dependencies=[Depends(permission_required("backups.run"))])
def system_local_backup_run(
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        result = run_local_backup()
        log_event(
            "Main Server local backup completed",
            scope="system",
            actor=admin.username,
            details={"archive": result.get("last_archive")},
        )
        return RedirectResponse(
            "/system/backups?message=" + quote_plus("Main Server local backup completed successfully"),
            status_code=303,
        )
    except Exception as exc:
        log_event(
            "Main Server local backup failed",
            scope="system",
            level="error",
            actor=admin.username,
            details={"error": str(exc)[-300:]},
        )
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Local backup failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.post("/system/backups/{target_id}/update", dependencies=[Depends(permission_required("backups.edit"))])
def system_backup_update(
    target_id: str,
    request: Request,
    name: str = Form(""),
    kind: str = Form("local"),
    destination: str = Form(""),
    key_path: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    domain: str = Form(""),
    schedule_hours: int = Form(24),
    schedule_time: str = Form("00:00"),
    rotation_keep: int = Form(20),
    run_now: int = Form(0),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        target = update_backup_target(
            target_id,
            name,
            kind,
            destination,
            key_path,
            schedule_hours,
            username,
            password,
            domain,
            rotation_keep,
            schedule_time,
        )
        log_event(
            "Backup destination updated",
            scope="system",
            actor=admin.username,
            details={"name": target.get("name"), "kind": target.get("kind"), "schedule_time": target.get("schedule_time")},
        )
        message = "Backup destination updated"
        if int(run_now or 0) == 1:
            result = run_backup_target(target_id)
            log_event(
                "Backup completed",
                scope="system",
                actor=admin.username,
                details={"target": result.get("name"), "archive": result.get("last_archive")},
            )
            message = "Backup destination updated and backup completed successfully"
        return RedirectResponse("/system/backups?message=" + quote_plus(message), status_code=303)
    except (ValueError, TypeError) as exc:
        return RedirectResponse("/system/backups?error=" + quote_plus(str(exc)), status_code=303)
    except Exception as exc:
        log_event(
            "Backup destination update failed",
            scope="system",
            level="error",
            actor=admin.username,
            details={"error": str(exc)[-300:]},
        )
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Backup destination update failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.post("/system/backups/{target_id}/run", dependencies=[Depends(permission_required("backups.run"))])
def system_backup_run(target_id: str, request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        target = run_backup_target(target_id)
        log_event("Backup completed", scope="system", actor=admin.username, details={"target": target.get("name"), "archive": target.get("last_archive")})
        return RedirectResponse("/system/backups?message=" + quote_plus("Backup completed successfully"), status_code=303)
    except Exception as exc:
        log_event("Backup failed", scope="system", level="error", actor=admin.username, details={"error": str(exc)[-300:]})
        return RedirectResponse("/system/backups?error=" + quote_plus("Backup failed: " + str(exc)[-300:]), status_code=303)


@app.post("/system/backups/{target_id}/delete", dependencies=[Depends(permission_required("backups.delete"))])
def system_backup_delete(target_id: str, request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    delete_backup_target(target_id)
    log_event("Backup destination removed", scope="system", actor=admin.username)
    return RedirectResponse("/system/backups?message=" + quote_plus("Backup destination removed"), status_code=303)









@app.get("/system/backups/local-files", response_class=HTMLResponse, dependencies=[Depends(permission_required("backups.view"))])
def system_local_backup_files(
    request: Request,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()

    backup_archives = list_local_backup_archives()
    for item in backup_archives:
        size = int(item.get("size_bytes") or 0)
        item["size_label"] = (
            f"{size / (1024 ** 3):.2f} GB" if size >= 1024 ** 3 else
            f"{size / (1024 ** 2):.1f} MB" if size >= 1024 ** 2 else
            f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
        )
        item["modified_label"] = datetime.fromtimestamp(
            float(item.get("modified_ts") or 0), tz=timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    return render(
        request,
        "local_backup_files.html",
        db,
        message=message,
        error=error or None,
        backup_archives=backup_archives,
    )


@app.post("/system/backups/archive/{archive_name}/delete", dependencies=[Depends(permission_required("backups.delete"))])
def system_backup_archive_delete(
    archive_name: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        path = resolve_local_backup_archive(archive_name)
        path.unlink()
        log_event(
            "Saved Main backup deleted",
            scope="system",
            actor=admin.username,
            details={"archive": archive_name},
        )
        return RedirectResponse(
            "/system/backups/local-files?message=" + quote_plus(f"Deleted saved backup {archive_name}"),
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            "/system/backups/local-files?error=" + quote_plus("Delete failed: " + str(exc)[-300:]),
            status_code=303,
        )



@app.get("/system/backups/{target_id}/files", response_class=HTMLResponse, dependencies=[Depends(permission_required("backups.view"))])
def system_backup_target_files(
    target_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()

    target = next((item for item in load_backup_targets() if str(item.get("id")) == str(target_id)), None)
    if not target:
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Backup destination was not found"),
            status_code=303,
        )

    files = []
    backup_files_error = ""
    try:
        files = list_target_backup_archives(target_id, limit=200)
        for item in files:
            size = int(item.get("size_bytes") or 0)
            if size >= 1024 ** 3:
                item["size_label"] = f"{size / (1024 ** 3):.2f} GB"
            elif size >= 1024 ** 2:
                item["size_label"] = f"{size / (1024 ** 2):.1f} MB"
            elif size >= 1024:
                item["size_label"] = f"{size / 1024:.1f} KB"
            else:
                item["size_label"] = f"{size} B"
            modified = float(item.get("modified_ts") or 0)
            item["modified_label"] = (
                datetime.fromtimestamp(modified, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if modified > 0 else "—"
            )
    except Exception as exc:
        backup_files_error = str(exc)

    return render(
        request,
        "backup_files.html",
        db,
        target=target,
        backup_files=files,
        backup_files_error=backup_files_error,
    )


@app.get("/system/backups/{target_id}/files/{archive_name}/download", dependencies=[Depends(permission_required("backups.download"))])
def system_backup_target_file_download(
    target_id: str,
    archive_name: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()

    temp_dir = Path("/var/lib/streamforge/download-cache")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"{secrets.token_hex(8)}-{Path(archive_name).name}"
    try:
        fetch_target_backup_archive(target_id, archive_name, temp)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        return RedirectResponse(
            f"/system/backups/{quote_plus(target_id)}/files?error=" + quote_plus("Download failed: " + str(exc)[-300:]),
            status_code=303,
        )
    return FileResponse(
        path=str(temp),
        filename=Path(archive_name).name,
        media_type="application/gzip",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(lambda: temp.unlink(missing_ok=True)),
    )


@app.post("/system/backups/{target_id}/files/{archive_name}/delete", dependencies=[Depends(permission_required("backups.delete"))])
def system_backup_target_file_delete(
    target_id: str,
    archive_name: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        delete_target_backup_archive(target_id, archive_name)
        log_event(
            "Remote backup file deleted",
            scope="system",
            actor=admin.username,
            details={"target_id": target_id, "archive": archive_name},
        )
        return RedirectResponse(
            f"/system/backups/{quote_plus(target_id)}/files?message=" + quote_plus(f"Deleted {archive_name}"),
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            f"/system/backups/{quote_plus(target_id)}/files?error=" + quote_plus("Delete failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.post("/system/backups/{target_id}/files/{archive_name}/restore", dependencies=[Depends(permission_required("backups.restore"))])
def system_backup_target_file_restore(
    target_id: str,
    archive_name: str,
    request: Request,
    confirm_restore: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    if str(confirm_restore or "").strip().lower() != "yes":
        return RedirectResponse(
            f"/system/backups/{quote_plus(target_id)}/files?error=" + quote_plus("Restore confirmation is required"),
            status_code=303,
        )

    staged: Path | None = None
    try:
        MAIN_RESTORE_INBOX.mkdir(parents=True, exist_ok=True)
        staged = MAIN_RESTORE_INBOX / f"restore-{int(time.time())}-{secrets.token_hex(8)}.tar.gz"
        fetch_target_backup_archive(target_id, archive_name, staged)
        staged.chmod(0o600)

        _validate_main_restore_archive(staged)

        _write_main_system_request(
            "restore_backup", admin, str(staged), _restore_recovery_access_url(request)
        )
        log_event(
            "Main Server restore scheduled from remote backup",
            scope="system",
            actor=admin.username,
            details={"target_id": target_id, "archive": archive_name},
        )
        return RedirectResponse(
            "/system/backups?message=" + quote_plus(
                f"Restore accepted from {archive_name}. Main will restart in a few seconds."
            ),
            status_code=303,
        )
    except Exception as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        return RedirectResponse(
            f"/system/backups/{quote_plus(target_id)}/files?error=" + quote_plus("Restore failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.get("/system/backups/archive/{archive_name}/download", dependencies=[Depends(permission_required("backups.download"))])
def system_backup_archive_download(
    archive_name: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        path = resolve_local_backup_archive(archive_name)
    except ValueError as exc:
        return RedirectResponse("/system/backups/local-files?error=" + quote_plus(str(exc)), status_code=303)
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/gzip",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/system/backups/archive/{archive_name}/restore", dependencies=[Depends(permission_required("backups.restore"))])
def system_backup_archive_restore(
    archive_name: str,
    request: Request,
    confirm_restore: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    if str(confirm_restore or "").strip().lower() != "yes":
        return RedirectResponse(
            "/system/backups/local-files?error=" + quote_plus("Restore confirmation is required"),
            status_code=303,
        )

    staged: Path | None = None
    try:
        source = resolve_local_backup_archive(archive_name)
        MAIN_RESTORE_INBOX.mkdir(parents=True, exist_ok=True)
        staged = MAIN_RESTORE_INBOX / f"restore-{int(time.time())}-{secrets.token_hex(8)}.tar.gz"
        shutil.copy2(source, staged)
        staged.chmod(0o600)

        _validate_main_restore_archive(staged)

        _write_main_system_request(
            "restore_backup", admin, str(staged), _restore_recovery_access_url(request)
        )
        log_event(
            "Main Server restore scheduled from saved backup",
            scope="system",
            actor=admin.username,
            details={"archive": source.name, "size_bytes": source.stat().st_size},
        )
        return RedirectResponse(
            "/system/backups/local-files?message=" + quote_plus(
                f"Restore accepted from {source.name}. Main will restart in a few seconds."
            ),
            status_code=303,
        )
    except Exception as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        return RedirectResponse(
            "/system/backups/local-files?error=" + quote_plus("Restore failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.post("/system/backups/restore", dependencies=[Depends(permission_required("backups.restore"))])
async def system_backup_restore(
    request: Request,
    archive: UploadFile = File(...),
    confirm_restore: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    if str(confirm_restore or "").strip().lower() != "yes":
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Confirm the restore checkbox before continuing"),
            status_code=303,
        )

    filename = Path(str(archive.filename or "")).name
    if not filename.lower().endswith((".tar.gz", ".tgz")):
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Restore requires a StreamForge .tar.gz or .tgz backup"),
            status_code=303,
        )

    MAIN_RESTORE_INBOX.mkdir(parents=True, exist_ok=True)
    try:
        MAIN_RESTORE_INBOX.chmod(0o700)
    except OSError:
        pass

    staged = MAIN_RESTORE_INBOX / f"restore-{int(time.time())}-{secrets.token_hex(8)}.tar.gz"
    max_bytes = 4 * 1024 * 1024 * 1024
    written = 0

    try:
        with staged.open("wb") as handle:
            while True:
                chunk = await archive.read(8 * 1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("Restore archive is larger than the 4 GB upload limit")
                handle.write(chunk)
        staged.chmod(0o600)

        if written < 128:
            raise ValueError("Restore archive is empty or invalid")

        _validate_main_restore_archive(staged)

        _write_main_system_request(
            "restore_backup", admin, str(staged), _restore_recovery_access_url(request)
        )
        log_event(
            "Main Server restore scheduled",
            scope="system",
            actor=admin.username,
            details={"archive": filename, "size_bytes": written},
        )
        return RedirectResponse(
            "/system/backups?message=" + quote_plus(
                "Restore accepted. The Main service will restore the backup and restart in a few seconds."
            ),
            status_code=303,
        )
    except (ValueError, RuntimeError) as exc:
        staged.unlink(missing_ok=True)
        return RedirectResponse(
            "/system/backups?error=" + quote_plus(str(exc)),
            status_code=303,
        )
    except Exception as exc:
        staged.unlink(missing_ok=True)
        log_event(
            "Main Server restore upload failed",
            scope="system",
            level="error",
            actor=admin.username,
            details={"error": str(exc)[-400:]},
        )
        return RedirectResponse(
            "/system/backups?error=" + quote_plus("Restore failed: " + str(exc)[-300:]),
            status_code=303,
        )


@app.get("/no-access", response_class=HTMLResponse)
def no_access_page(request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    return render(request, "access_denied.html", db, permission="No page permissions assigned")


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(permission_required("dashboard.view"))])
def dashboard(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    # STREAMFORGE_DASHBOARD_FAST_RENDER_V3052:
    # v11.14 keeps Dashboard navigation local/cache-only and narrows first paint further.
    # Legacy guard shape retained: .where(func.lower(Channel.status).in_(dashboard_live_statuses))
    # STREAMFORGE_MAIN_DASHBOARD_LATEST_METRIC_TAIL_V1113:
    # Retained compatibility marker: metrics_history.latest() remains tail-only;
    # v11.14 no longer needs that historical sample to decide which rows are Live.
    # STREAMFORGE_MAIN_DASHBOARD_READY_COUNT_V1024:
    # v11.14 preserves the delivery-ready Dashboard count invariant.
    # STREAMFORGE_MAIN_DASHBOARD_ONLINE_ONLY_FIRST_PAINT_V1114:
    # Dashboard is an observation page, not a process-intent list.  Do not put
    # Starting/Restarting/Running database rows into the Live table and wait for
    # JavaScript to hide them later.  Consume only Main-local in-memory runtime
    # plus the non-blocking asynchronous HLS readiness cache and render rows that
    # are already playable at first paint.
    dashboard_live_statuses = ("starting", "running", "restarting", "degraded")
    total_channel_count = int(db.scalar(select(func.count(Channel.id))) or 0)
    candidates = list(db.scalars(
        select(Channel)
        .options(
            selectinload(Channel.category),
            selectinload(Channel.categories),
            selectinload(Channel.nodes),
            selectinload(Channel.node),
        )
        .where(
            Channel.enabled.is_(True),
            Channel.output_type == "hls",
            func.lower(Channel.status).in_(dashboard_live_statuses),
        )
        .order_by(Channel.id)
    ).unique().all())
    dashboard_runtime: dict[int, dict[str, object]] = {}
    channels: list[Channel] = []
    for channel in candidates:
        runtime = main_channel_runtime(channel)
        if bool(runtime.get("alive")) and bool(runtime.get("hls_ready")):
            channels.append(channel)
            dashboard_runtime[int(channel.id)] = runtime

    user_count = int(db.scalar(select(func.count(StreamUser.id))) or 0)
    direct_user_count = 0
    central_user_count = user_count
    running_count = len(channels)
    total_bitrate = sum(int((dashboard_runtime.get(int(c.id)) or {}).get("bitrate_kbps") or c.live_bitrate_kbps or 0) for c in channels)

    # STREAMFORGE_MAIN_DASHBOARD_NODE_SQL_COUNTS_V1114:
    # Overview needs only two numbers. Do not materialize every Node ORM row.
    node_count = int(db.scalar(select(func.count(Node.id))) or 0)
    online_node_count = int(db.scalar(
        select(func.count(Node.id)).where(or_(Node.node_type == "local", Node.status == "online"))
    ) or 0)

    viewer_stats = collect_viewer_counts_fast(db)
    playlist_public_base = main_playlist_public_base(db, request)
    dashboard_http_outputs = {
        int(channel.id): channel_http_urls(channel, request, db, public_base=playlist_public_base).get("hls", "")
        for channel in channels
    }
    return render(
        request,
        "dashboard.html",
        db,
        channels=channels,
        dashboard_runtime=dashboard_runtime,
        dashboard_http_outputs=dashboard_http_outputs,
        total_channel_count=total_channel_count,
        user_count=user_count,
        central_user_count=central_user_count,
        direct_user_count=direct_user_count,
        online_user_count=viewer_stats["total_users"],
        viewer_by_channel=viewer_stats["by_channel"],
        running_count=running_count,
        total_bitrate=total_bitrate,
        node_count=node_count,
        online_node_count=online_node_count,
        system_metrics=system_metrics.snapshot(),
    )





# STREAMFORGE_MAIN_LIVE_SESSION_DELIVERY_FILTER_V92:
def _viewer_session_delivery_options(items: list[dict[str, object]] | tuple[dict[str, object], ...]) -> list[dict[str, str]]:
    options: dict[str, str] = {}
    for item in items or []:
        key = str(item.get("source_key") or "").strip()
        label = str(item.get("source") or key or "Unknown").strip()
        if not key:
            continue
        options.setdefault(key, label)
    return [
        {"key": key, "label": options[key]}
        for key in sorted(options, key=lambda value: (options[value].lower(), value.lower()))
    ]

# STREAMFORGE_MAIN_LIVE_SESSION_EMPTY_FILTER_SAFE_V47:
# Browser select controls submit an empty string for "All nodes/channels".
# Normalize those optional numeric query values before filtering so a blank
# selection never becomes a FastAPI integer-parsing 422 response.
def _viewer_session_filter_int(value: str | int | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@app.get("/viewer-sessions/live-data", dependencies=[Depends(permission_required("dashboard.view"))])
def viewer_sessions_live_data(
    request: Request,
    user: str = "",
    node_id: str = "",
    channel_id: str = "",
    delivery: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)

    # STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_ROUTE_V1123:
    request_remote_viewer_details_refresh()
    stats = collect_viewer_stats(db, allow_remote_fetch=False, include_geo=False)
    selected_user = user.strip()
    selected_node_id = _viewer_session_filter_int(node_id)
    selected_channel_id = _viewer_session_filter_int(channel_id)
    selected_delivery = str(delivery or "").strip()
    all_sessions = list(stats.get("sessions", []))
    sessions = [
        item for item in all_sessions
        if (not selected_user or str(item.get("user_name") or "Unknown user") == selected_user)
        and (selected_node_id is None or item.get("node_id") == selected_node_id)
        and (selected_channel_id is None or item.get("channel_id") == selected_channel_id)
        and (not selected_delivery or str(item.get("source_key") or "") == selected_delivery)
    ]

    payload_sessions = []
    for item in sessions:
        payload_sessions.append({
            "user_name": str(item.get("user_name") or "Unknown user"),
            "session_id": str(item.get("session_id") or ""),
            "client_ip": str(item.get("client_ip") or ""),
            "business_name": str(item.get("business_name") or "Unknown network"),
            "asn": item.get("asn"),
            "country_name": str(item.get("country_name") or "Unknown"),
            "country_code": str(item.get("country_code") or ""),
            "channel_name": str(item.get("channel_name") or ""),
            "node_name": str(item.get("node_name") or ""),
            "source": str(item.get("source") or ""),
            "source_key": str(item.get("source_key") or ""),
            "duration_text": human_duration(item.get("duration_seconds") or 0),
            "idle_text": human_duration(item.get("idle_seconds") or 0),
            "last_seen_text": format_local_time(item.get("last_seen_at")),
            "user_agent": str(item.get("user_agent") or "Unknown client"),
            "user_id": item.get("user_id") or "",
            "node_id": item.get("node_id") or "",
        })

    online_user_names = sorted(
        {str(item.get("user_name") or "Unknown user") for item in all_sessions},
        key=str.lower,
    )

    response = JSONResponse({
        "ok": True,
        "sessions": payload_sessions,
        "filtered_count": len(payload_sessions),
        "total_online": int(stats.get("total_users", 0) or 0),
        "online_user_names": online_user_names,
        "delivery_options": _viewer_session_delivery_options(all_sessions),
        "details_pending": int(stats.get("total_users", 0) or 0) > len(all_sessions),
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/viewer-sessions", response_class=HTMLResponse, dependencies=[Depends(permission_required("dashboard.view"))])
def viewer_sessions_page(
    request: Request,
    user: str = "",
    node_id: str = "",
    channel_id: str = "",
    delivery: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    # STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_PAGE_V1123:
    request_remote_viewer_details_refresh()
    stats = collect_viewer_stats(db, allow_remote_fetch=False, include_geo=False)
    selected_user = user.strip()
    selected_node_id = _viewer_session_filter_int(node_id)
    selected_channel_id = _viewer_session_filter_int(channel_id)
    selected_delivery = str(delivery or "").strip()
    all_sessions = list(stats.get("sessions", []))
    online_user_names = sorted({str(item.get("user_name") or "Unknown user") for item in all_sessions}, key=str.lower)
    sessions = [
        item for item in all_sessions
        if (not selected_user or str(item.get("user_name") or "Unknown user") == selected_user)
        and (selected_node_id is None or item.get("node_id") == selected_node_id)
        and (selected_channel_id is None or item.get("channel_id") == selected_channel_id)
        and (not selected_delivery or str(item.get("source_key") or "") == selected_delivery)
    ]
    return render(
        request,
        "viewer_sessions.html",
        db,
        sessions=sessions,
        total_online=stats.get("total_users", 0),
        selected_user=selected_user,
        online_user_names=online_user_names,
        selected_node_id=selected_node_id,
        selected_channel_id=selected_channel_id,
        selected_delivery=selected_delivery,
        delivery_options=_viewer_session_delivery_options(all_sessions),
        details_pending=int(stats.get("total_users", 0) or 0) > len(all_sessions),
        nodes=db.scalars(select(Node).order_by(Node.node_type, Node.name)).all(),
        channels=db.scalars(select(Channel).order_by(Channel.name)).all(),
    )


@app.post("/viewer-sessions/kill", dependencies=[Depends(permission_required("dashboard.view"))])
def viewer_session_kill(
    request: Request, session_id: str = Form(...), source_key: str = Form("main_proxy"),
    user_id: int | None = Form(None), node_id: int | None = Form(None), db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    killed = 0
    error = ""
    if source_key == "direct_node" and node_id:
        node = db.get(Node, node_id)
        if node and node.node_type == "remote":
            try:
                result = node_controller.kill_viewer_session(node, session_id)
                killed = int(result.get("killed") or 0)
            except NodeError as exc:
                error = str(exc)
    else:
        killed += viewer_tracker.kill(session_id, user_id)
        if user_id:
            killed += connection_tracker.kill(user_id, session_id)
    query = "message=" + urllib.parse.quote("Online session killed" if killed else "Session was already offline")
    if error:
        query += "&error=" + urllib.parse.quote(error)
    return RedirectResponse("/viewer-sessions?" + query, status_code=303)


@app.get("/system-metrics.json", dependencies=[Depends(permission_required("system_metrics.view"))])
def system_metrics_json(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401)
    return system_metrics.snapshot()


@app.get("/metrics-history.json", dependencies=[Depends(permission_required("system_metrics.view"))])
def metrics_history_json(request: Request, span_hours: int = 0, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401)
    retention_days = _metrics_retention_days()
    default_hours = max(1, min(retention_days * 24, app_setting_int(db, METRICS_DEFAULT_SPAN_HOURS_KEY, 24)))
    selected_hours = max(1, min(retention_days * 24, int(span_hours or default_hours)))
    cutoff = int(time.time()) - selected_hours * 3600
    # STREAMFORGE_MAIN_METRICS_WINDOW_TAIL_V1113:
    # The history file is chronological; scan backwards only through the
    # requested window and downsample there instead of parsing all retention.
    points = metrics_history.read_since(cutoff, max_points=1440)
    return {"retention_days": retention_days, "default_span_hours": default_hours, "span_hours": selected_hours, "points": points}


def fast_node_channel_counts(db: Session) -> dict[int, dict[str, int]]:
    rows = db.execute(text("""
        SELECT cn.node_id, COUNT(cn.channel_id) AS total,
               SUM(CASE WHEN c.status IN ('running','starting','restarting','degraded') THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN c.status IN ('error') OR (c.desired_running = 1 AND c.status NOT IN ('running','starting','restarting','degraded')) THEN 1 ELSE 0 END) AS down
        FROM channel_nodes AS cn
        JOIN channels AS c ON c.id = cn.channel_id
        GROUP BY cn.node_id
    """)).all()
    return {
        int(node_id): {"total": int(total or 0), "up": int(up or 0), "waiting": 0, "down": int(down or 0)}
        for node_id, total, up, down in rows
    }


# STREAMFORGE_NODE_CARD_STRICT_DELIVERY_COUNTS_V45:
# The Nodes page must use the same Up / Waiting / Down definition as the
# Channels page. An alive FFmpeg process is not Up until its HLS playlist and
# newest segment are playable. Keep this local-only path cached briefly so the
# Nodes page does not reintroduce the heavy status polling fixed in v3.4.
_MAIN_LOCAL_DELIVERY_COUNT_CACHE: dict[str, object] = {"at": 0.0, "node_id": 0, "value": None}
_MAIN_LOCAL_DELIVERY_COUNT_LOCK = threading.RLock()


def main_local_delivery_counts(node: Node) -> dict[str, int]:
    now = time.monotonic()
    node_id = int(node.id or 0)
    with _MAIN_LOCAL_DELIVERY_COUNT_LOCK:
        cached = _MAIN_LOCAL_DELIVERY_COUNT_CACHE.get("value")
        if (
            node_id
            and int(_MAIN_LOCAL_DELIVERY_COUNT_CACHE.get("node_id") or 0) == node_id
            and isinstance(cached, dict)
            and now - float(_MAIN_LOCAL_DELIVERY_COUNT_CACHE.get("at") or 0.0) < 10.0
        ):
            return {key: int(cached.get(key) or 0) for key in ("total", "up", "waiting", "down")}

    counts = {"total": 0, "up": 0, "waiting": 0, "down": 0}
    channels = list(node.channels)
    counts["total"] = len(channels)
    for channel in channels:
        runtime = stream_manager.runtime_snapshot(int(channel.id))
        alive = bool(runtime.get("alive"))
        db_status = str(channel.status or "").strip().lower()
        if alive:
            runtime_status = db_status if db_status in {"starting", "restarting"} else "running"
        elif bool(channel.desired_running):
            runtime_status = db_status if db_status in {"starting", "restarting", "error"} else "error"
        else:
            runtime_status = "stopped"
        try:
            hls_ready = bool(alive and node_controller.hls_ready(channel, node))
        except (OSError, RuntimeError):
            hls_ready = False
        delivery = channel_delivery_status(channel, runtime_status, hls_ready)
        counts[delivery if delivery in counts else "down"] += 1

    with _MAIN_LOCAL_DELIVERY_COUNT_LOCK:
        _MAIN_LOCAL_DELIVERY_COUNT_CACHE.update({"at": now, "node_id": node_id, "value": dict(counts)})
    return counts


# STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46:
def node_card_display_counts(counts: dict[str, int]) -> dict[str, int]:
    """Fold Waiting into Down for the compact Nodes-page summary only."""
    total = int(counts.get("total") or 0)
    up = int(counts.get("up") or 0)
    waiting = int(counts.get("waiting") or 0)
    down = int(counts.get("down") or 0) + waiting
    return {"total": total, "up": up, "down": down}


def node_channel_health_counts(node: Node) -> dict[str, int]:
    # Retained for non-page callers. Remote status pages use the Node Agent's
    # single health response instead of one request per channel.
    if node.node_type == "remote":
        try:
            health = node_controller.health(node)
            total = len(node.channels)
            return {
                "up": int(health.get("up_channels") or health.get("active_channels") or 0),
                "down": int(health.get("down_channels") or 0),
                "total": total,
            }
        except NodeError:
            return {"up": 0, "down": 0, "total": len(node.channels)}
    up = down = 0
    for channel in list(node.channels):
        snapshot = node_controller.runtime_snapshot_on_node(channel.id, node.id)
        if snapshot.get("alive"):
            up += 1
        elif channel.status in {"running", "starting", "restarting", "degraded", "error"}:
            down += 1
    return {"up": up, "down": down, "total": len(node.channels)}


# STREAMFORGE_LOG_TAB_SCOPED_CLEAR_V66:
_LOG_TYPE_SYSTEM_SCOPES = {"system", "node", "update", "settings"}
_LOG_TYPE_EXCLUDED_FROM_ACTIVITY = {"auth", "client", *_LOG_TYPE_SYSTEM_SCOPES}


# STREAMFORGE_MAIN_LOG_DIRECT_DETAILS_IP_V72:
def _log_details_ip(value: object) -> str:
    """Extract the most useful client IP from JSON or Node key=value log details."""
    if value in (None, ""):
        return ""
    parsed: object = value
    raw = str(value or "").strip()
    if isinstance(value, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = value
    if isinstance(parsed, dict):
        for key in ("ip", "client_ip", "remote_ip", "address"):
            candidate = str(parsed.get(key) or "").strip()
            if candidate:
                return candidate[:120]
    match = re.search(r"(?:^|[;,{\s])(?:ip|client_ip|remote_ip|address)\s*[:=]\s*[\"']?([^;,'\"}\s]+)", raw, flags=re.I)
    return match.group(1)[:120] if match else ""

# STREAMFORGE_CLIENT_LOG_USER_COLUMN_V99R17:
def _log_details_user(value: object) -> str:
    """Extract client/user identity from JSON or Node key=value log details."""
    if value in (None, ""):
        return ""
    raw = str(value or "").strip()
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = value
    if isinstance(parsed, dict):
        for key in ("user", "username", "client_user", "actor"):
            candidate = str(parsed.get(key) or "").strip()
            if candidate:
                return candidate[:120]
    match = re.search(r"(?:^|[;,{\s])(?:user|username|client_user|actor)\s*[:=]\s*[\"']?([^;,\"'}]+)", raw, flags=re.I)
    return match.group(1).strip()[:120] if match else ""


# STREAMFORGE_MAIN_CLIENT_LOG_SESSION_AGE_INLINE_V1060:
def _log_details_session_id(value: object) -> str:
    """Extract a playback SID from JSON or key=value Client-log details."""
    if value in (None, ""):
        return ""
    raw = str(value or "").strip()
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = value
    if isinstance(parsed, dict):
        for key in ("session_id", "sid", "playback_session_id"):
            candidate = re.sub(r"[^A-Za-z0-9._~-]+", "", str(parsed.get(key) or "").strip())[:96]
            if candidate:
                return candidate
    match = re.search(r"(?:^|[;,\s])(?:session_id|sid|playback_session_id)\s*[:=]\s*[\"']?([A-Za-z0-9._~-]{1,96})", raw, flags=re.I)
    return str(match.group(1) if match else "")[:96]


def _log_details_user_id(value: object) -> int:
    if value in (None, ""):
        return 0
    raw = str(value or "").strip()
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = value
    candidate: object = 0
    if isinstance(parsed, dict):
        candidate = parsed.get("user_id") or parsed.get("uid") or 0
    else:
        match = re.search(r"(?:^|[;,\s])(?:user_id|uid)\s*[:=]\s*[\"']?(\d+)", raw, flags=re.I)
        candidate = match.group(1) if match else 0
    try:
        return max(0, int(candidate or 0))
    except (TypeError, ValueError):
        return 0


def _apply_log_type_clause(stmt, log_type: str):
    selected = log_type if log_type in {"access", "client", "system", "activity"} else "activity"
    if selected == "access":
        return stmt.where(LogEntry.scope == "auth")
    if selected == "client":
        return stmt.where(LogEntry.scope == "client")
    if selected == "system":
        return stmt.where(LogEntry.scope.in_(sorted(_LOG_TYPE_SYSTEM_SCOPES)))
    return stmt.where(LogEntry.scope.notin_(sorted(_LOG_TYPE_EXCLUDED_FROM_ACTIVITY)))


# STREAMFORGE_MAIN_LOG_SEARCH_V1035:
# Search is server-side so it covers every matching page, not just the rows
# currently rendered in the browser. Whitespace-separated terms are ANDed;
# every term may match any visible/log-backed field.
def _normalize_log_search(value: object) -> str:
    return " ".join(str(value or "").strip().split())[:240]


def _log_search_terms(value: object) -> list[str]:
    normalized = _normalize_log_search(value).casefold()
    return [term for term in normalized.split(" ") if term][:12]


def _log_like_pattern(term: str) -> str:
    escaped = str(term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _apply_main_log_search(stmt, search: str, channel_names: dict[int, str], node_names: dict[int, str]):
    terms = _log_search_terms(search)
    if not terms:
        return stmt
    for term in terms:
        # Every local row has Source=Main panel, so a source-name term already
        # matches this source and should not unnecessarily narrow the row set.
        if term in "main panel":
            continue
        pattern = _log_like_pattern(term)
        clauses = [
            func.lower(LogEntry.scope).like(pattern, escape="\\"),
            func.lower(LogEntry.level).like(pattern, escape="\\"),
            func.lower(LogEntry.message).like(pattern, escape="\\"),
            func.lower(func.coalesce(LogEntry.actor, "")).like(pattern, escape="\\"),
            func.lower(func.coalesce(LogEntry.details, "")).like(pattern, escape="\\"),
        ]
        matching_channel_ids = [key for key, name in channel_names.items() if term in str(name or "").casefold()]
        matching_node_ids = [key for key, name in node_names.items() if term in str(name or "").casefold()]
        if matching_channel_ids:
            clauses.append(LogEntry.channel_id.in_(matching_channel_ids))
        if matching_node_ids:
            clauses.append(LogEntry.node_id.in_(matching_node_ids))
        stmt = stmt.where(or_(*clauses))
    return stmt


def _log_item_matches_search(item: dict[str, object], search: str) -> bool:
    terms = _log_search_terms(search)
    if not terms:
        return True
    searchable = (
        "source", "level", "scope", "subject", "ip", "user",
        "message", "details", "actor",
    )
    haystack = "\n".join(str(item.get(key) or "") for key in searchable).casefold()
    return all(term in haystack for term in terms)


# STREAMFORGE_LOG_SUBJECT_MAPPING_V74:
# Keep Channel/Node useful for both newly structured audit rows and historical
# auth rows that predate node_id persistence.  New Node panel auth events save
# node_id directly; old rows are display-backfilled from the known Node name in
# the message.  Main-panel auth events are labelled Main Panel.
def _client_log_duration_label(seconds: object, online: bool = False) -> str:
    # STREAMFORGE_MAIN_REMOTE_CLIENT_LOG_DURATION_V1045
    # STREAMFORGE_MAIN_REMOTE_CLIENT_SESSION_AGE_PERSIST_V1046: Remote Node
    # API duration_seconds is final/frozen after disconnect, while active rows
    # retain the Online prefix.
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        return "—"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        text = f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    elif hours:
        text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        text = f"{minutes:02d}:{secs:02d}"
    return ("Online · " if online else "") + text


def _main_session_generation_clusters(rows: list[dict[str, object]], reset_seconds: int) -> list[list[dict[str, object]]]:
    # STREAMFORGE_MAIN_CLIENT_SESSION_CLUSTER_V1081: physical viewer generations
    # separated only by the short Online TTL remain one logical session until
    # the configured offline reset gap is exceeded.
    ordered = sorted(rows, key=lambda value: float(value.get("first_seen_epoch") or 0.0))
    clusters: list[list[dict[str, object]]] = []
    for row in ordered:
        first = float(row.get("first_seen_epoch") or 0.0)
        last = float(row.get("last_seen_epoch") or first)
        if first <= 0 or last < first:
            continue
        if not clusters:
            clusters.append([row])
            continue
        previous_last = max(float(item.get("last_seen_epoch") or 0.0) for item in clusters[-1])
        if first - previous_last > max(60, int(reset_seconds)):
            clusters.append([row])
        else:
            clusters[-1].append(row)
    return clusters

def _main_pick_session_cluster(clusters: list[list[dict[str, object]]], event_epoch: float) -> list[dict[str, object]]:
    if not clusters:
        return []
    if event_epoch <= 0:
        return clusters[-1]
    best: list[dict[str, object]] = clusters[0]
    best_distance = float("inf")
    for cluster in clusters:
        start = min(float(row.get("first_seen_epoch") or 0.0) for row in cluster)
        end = max(float(row.get("last_seen_epoch") or start) for row in cluster)
        if start <= event_epoch <= end:
            return cluster
        distance = start - event_epoch if event_epoch < start else event_epoch - end
        if distance < best_distance:
            best = cluster
            best_distance = distance
    return best

def _main_client_log_duration_states(items: list[dict[str, object]], db: Session) -> list[dict[str, object] | None]:
    """Attach Main playback age to existing login/playlist Client rows.

    STREAMFORGE_MAIN_CLIENT_LOG_SESSION_AGE_INLINE_V1060: unlike Remote Node
    releases that added a separate "Playback session started" event, Main keeps
    the existing client event as the visible row.  Exact SID matching is preferred;
    user+IP+time is the compatibility fallback for clients whose API/login user
    agent differs from their media engine (for example Smarters -> LibVLC).
    """
    if not items:
        return []

    # Resolve only the displayed historical identities that predate user_id/SID
    # details.  Do not scan the complete subscriber table on every Logs refresh.
    # New v10.60 rows carry user_id and SID explicitly and skip this fallback.
    fallback_names = sorted({
        str(item.get("user") or "").strip()
        for item in items
        if _log_details_user_id(item.get("details")) <= 0 and str(item.get("user") or "").strip()
    })
    name_to_ids: dict[str, list[int]] = {}
    if fallback_names:
        folded_names = [value.casefold() for value in fallback_names]
        user_rows = db.execute(
            select(StreamUser.id, StreamUser.name, StreamUser.xtream_username).where(
                or_(
                    func.lower(StreamUser.name).in_(folded_names),
                    func.lower(StreamUser.xtream_username).in_(folded_names),
                )
            )
        ).all()
        for uid, name, xtream_name in user_rows:
            for value in (name, xtream_name):
                cleaned = str(value or "").strip().casefold()
                if cleaned:
                    name_to_ids.setdefault(cleaned, []).append(int(uid))

    item_meta: list[dict[str, object]] = []
    wanted_uids: set[int] = set()
    for item in items:
        details = item.get("details")
        uid = _log_details_user_id(details)
        candidate_uids: list[int] = [uid] if uid > 0 else list(dict.fromkeys(name_to_ids.get(str(item.get("user") or "").strip().casefold(), [])))
        wanted_uids.update(candidate_uids)
        item_meta.append({
            "uids": candidate_uids,
            "sid": _log_details_session_id(details),
            "ip": str(item.get("ip") or "").strip(),
            "event": float(item.get("_sort_time") or 0.0),
        })

    if not wanted_uids:
        return [None for _ in items]

    history_rows = viewer_tracker.history_for_users(sorted(wanted_uids))

    # Merge active sessions immediately. The observer runs every two seconds, so
    # this prevents a newly-started stream from showing an em dash while waiting
    # for the first retained-history sample.
    active_snapshot = viewer_tracker.snapshot()
    now_epoch = time.time()
    active_keys: set[tuple[int, str, int]] = set()
    merged: list[dict[str, object]] = list(history_rows)
    for session in active_snapshot.sessions:
        first_epoch = now_epoch - max(0.0, active_snapshot.captured_monotonic - float(session.first_seen_monotonic))
        last_epoch = now_epoch - max(0.0, active_snapshot.captured_monotonic - float(session.last_seen_monotonic))
        generation_key = (int(session.user_id), str(session.session_id), int(round(first_epoch)))
        active_keys.add(generation_key)
        existing = next((row for row in merged if int(row.get("user_id") or 0) == int(session.user_id) and str(row.get("sid") or "") == str(session.session_id) and abs(float(row.get("first_seen_epoch") or 0.0) - first_epoch) < 2.5), None)
        if existing is None:
            merged.append({
                "user_id": int(session.user_id),
                "sid": str(session.session_id),
                "first_seen_epoch": first_epoch,
                "last_seen_epoch": last_epoch,
                "ip": str(session.client_ip or ""),
                "user_agent": str(session.user_agent or ""),
                "_online": True,
            })
        else:
            existing["last_seen_epoch"] = max(float(existing.get("last_seen_epoch") or 0.0), last_epoch)
            existing["ip"] = str(existing.get("ip") or session.client_ip or "")
            existing["_online"] = True

    by_uid: dict[int, list[dict[str, object]]] = {}
    for row in merged:
        try:
            uid = int(row.get("user_id") or 0)
            first = float(row.get("first_seen_epoch") or 0.0)
            last = float(row.get("last_seen_epoch") or 0.0)
        except (TypeError, ValueError):
            continue
        if uid <= 0 or first <= 0 or last < first:
            continue
        by_uid.setdefault(uid, []).append(row)
    for rows in by_uid.values():
        rows.sort(key=lambda row: float(row.get("first_seen_epoch") or 0.0))

    states: list[dict[str, object] | None] = []
    for meta in item_meta:
        uids = [int(value) for value in list(meta.get("uids") or []) if int(value) > 0]
        sid = str(meta.get("sid") or "")
        ip = str(meta.get("ip") or "")
        event_epoch = float(meta.get("event") or 0.0)
        candidates: list[tuple[dict[str, object], bool]] = []
        for uid in uids:
            for row in by_uid.get(uid, []):
                exact_sid = bool(sid and str(row.get("sid") or "") == sid)
                row_ip = str(row.get("ip") or "").strip()
                if not exact_sid and (not ip or not row_ip or row_ip != ip):
                    continue
                candidates.append((row, exact_sid))
        if not candidates:
            states.append(None)
            continue

        # STREAMFORGE_MAIN_CLIENT_LOG_CUMULATIVE_SESSION_AGE_V1063:
        # A stable SID can have multiple playback generations while one login /
        # playlist Client-log row remains visible.  Sum the actual watched time
        # from every generation that belongs to this row instead of replacing
        # the age with only the newest generation.  Gaps on the channel list are
        # deliberately excluded because no viewer generation is active then.
        # Historical rows without a usable event timestamp keep the legacy
        # nearest/latest-generation selector below to avoid aggregating an
        # unbounded device history onto an ambiguous old log entry.
        exact_rows = [row for row, exact_sid in candidates if exact_sid]
        if sid and event_epoch > 0 and exact_rows:
            reset_seconds = _main_client_session_reset_seconds()
            cluster = _main_pick_session_cluster(
                _main_session_generation_clusters(exact_rows, reset_seconds),
                event_epoch,
            )
            cumulative_seconds = 0.0
            cumulative_online = False
            counted_generations: list[float] = []
            for row in cluster:
                first = float(row.get("first_seen_epoch") or 0.0)
                last = float(row.get("last_seen_epoch") or first)
                if first <= 0 or last < first:
                    continue
                if any(abs(first - seen) < 2.5 for seen in counted_generations):
                    continue
                counted_generations.append(first)
                row_online = bool(row.get("_online")) or (
                    (int(row.get("user_id") or 0), str(row.get("sid") or ""), int(round(first))) in active_keys
                )
                effective_first = first
                # A retained compatibility log emitted in the middle of a
                # cluster should not claim playback that happened before it.
                if event_epoch > first and event_epoch <= last:
                    effective_first = event_epoch
                effective_last = now_epoch if row_online else last
                if effective_last < effective_first:
                    continue
                cumulative_seconds += effective_last - effective_first
                cumulative_online = cumulative_online or row_online
            if counted_generations:
                states.append({
                    "duration_seconds": max(0, int(cumulative_seconds)),
                    "online": cumulative_online,
                })
                continue

        chosen: dict[str, object] | None = None
        chosen_exact = False
        chosen_distance = 0.0
        best_score: tuple[float, float, float] | None = None
        for row, exact_sid in candidates:
            first = float(row.get("first_seen_epoch") or 0.0)
            last = float(row.get("last_seen_epoch") or first)
            if event_epoch <= 0 or first <= event_epoch <= last:
                distance = 0.0
            elif event_epoch < first:
                distance = first - event_epoch
            else:
                distance = event_epoch - last
            row_online = bool(row.get("_online")) or (
                (int(row.get("user_id") or 0), str(row.get("sid") or ""), int(round(first))) in active_keys
            )
            # STREAMFORGE_MAIN_CLIENT_LOG_REPLAY_AGE_V1062:
            # An exact SID is the stable device/login identity. After the user
            # returns to the channel list and starts playback again, prefer the
            # active generation; otherwise show the newest retained generation.
            # This keeps one existing login/playlist row while its Session age
            # follows the latest playback instead of staying pinned to the first.
            if exact_sid:
                score = (0.0, 0.0 if row_online else 1.0, -first)
            else:
                # User/IP is only a compatibility fallback for split app/media
                # User-Agents, so keep it tied to the closest event in time.
                score = (1.0, distance, -first)
            if best_score is None or score < best_score:
                best_score = score
                chosen = row
                chosen_exact = exact_sid
                chosen_distance = distance
        if chosen is None:
            states.append(None)
            continue
        # Exact SID is already a strong device identity and retained history is
        # bounded by the configured log-retention TTL. Only the user/IP fallback
        # needs a tight time window to avoid merging devices behind the same NAT.
        if not chosen_exact and event_epoch > 0 and chosen_distance > 600.0:
            states.append(None)
            continue
        first = float(chosen.get("first_seen_epoch") or 0.0)
        last = float(chosen.get("last_seen_epoch") or first)
        online = bool(chosen.get("_online")) or ((int(chosen.get("user_id") or 0), str(chosen.get("sid") or ""), int(round(first))) in active_keys)
        duration = max(0, int((now_epoch if online else last) - first))
        states.append({"duration_seconds": duration, "online": online})
    return states


def _main_log_subject(entry: LogEntry, channel_names: dict[int, str], node_names: dict[int, str]) -> str:
    if entry.channel_id:
        return str(channel_names.get(entry.channel_id) or "")
    if entry.node_id:
        return str(node_names.get(entry.node_id) or "")
    message = str(entry.message or "")
    _scope = str(entry.scope or "").strip().lower()
    if _scope == "auth":
        for _node_name in sorted((str(name) for name in node_names.values() if name), key=len, reverse=True):
            if (f" on {_node_name}" in message) or (f" from Node {_node_name}" in message):
                return _node_name
        return "Main Panel"
    # STREAMFORGE_CLIENT_LOG_SUBJECT_MAPPING_V75:
    # Main-side WebPlayer/playlist client events are emitted by the Main public
    # plane and normally have no channel_id/node_id.  Keep Channel/Node useful
    # by identifying that origin explicitly.  Remote-node Client logs already
    # receive their Node name when they are merged in logs_page below.
    if _scope == "client":
        return "Main Panel"
    return ""


@app.get("/logs", response_class=HTMLResponse, dependencies=[Depends(permission_required("logs.view"))])
def logs_page(
    request: Request,
    log_type: str = "activity",
    source: str = "local",
    scope: str = "",
    level: str = "",
    channel_id: str = "",
    node_id: str = "",
    q: str = "",
    limit: str = "10",
    page: str = "1",
    sort: str = "time",
    order: str = "desc",
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    # STREAMFORGE_MAIN_LOG_PAGE_LOCAL_FAST_LOAD_V60R3:
    # STREAMFORGE_MAIN_LOG_LIMIT_SELECTOR_V84:
    # STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85:
    # STREAMFORGE_MAIN_LOG_PAGINATION_SORT_V93:
    # Show is a page size, not a hard truncation. All matching rows remain
    # reachable with Previous/Next, and every table heading can sort ASC/DESC.
    selected_log_type = log_type if log_type in {"access", "client", "system", "activity"} else "activity"
    selected_search = _normalize_log_search(q)
    # STREAMFORGE_CONTEXTUAL_LOG_FILTERS_V106: fixed-scope tabs do not carry stale scope filters; only emitted levels are selectable.
    scope = str(scope or "").strip().lower()
    level = str(level or "").strip().lower()
    if selected_log_type in {"access", "client"}:
        scope = ""
    elif selected_log_type == "system" and scope not in {"", "system", "node", "update", "settings"}:
        scope = ""
    elif selected_log_type == "activity" and scope not in {"", "channel", "user", "playlist", "backup"}:
        scope = ""
    if level not in {"", "info", "warning", "error"}:
        level = ""
    limit_options = {"10": 10, "20": 20, "50": 50, "100": 100, "200": 200, "500": 500, "1000": 1000, "all": 0}
    selected_limit = str(limit or "10").strip().lower()
    if selected_limit not in limit_options:
        selected_limit = "10"
    requested_limit = int(limit_options[selected_limit])
    allowed_sorts = {"time", "source", "level", "scope", "subject", "ip", "user", "message", "details", "actor"}
    selected_sort = str(sort or "time").strip().lower()
    if selected_sort not in allowed_sorts:
        selected_sort = "time"
    if selected_log_type == "client" and selected_sort in {"details", "actor", "scope"}:
        selected_sort = "time"
    if selected_log_type == "access" and selected_sort == "scope":
        selected_sort = "time"
    selected_order = "asc" if str(order or "").strip().lower() == "asc" else "desc"
    try:
        requested_page = max(1, int(str(page or "1").strip()))
    except (TypeError, ValueError):
        requested_page = 1

    nodes = db.scalars(select(Node).order_by(Node.node_type, Node.name)).all()
    remote_nodes = [item for item in nodes if item.node_type == "remote"]
    channels = db.scalars(select(Channel).order_by(Channel.name)).all()
    channel_names = {item.id: item.name for item in channels}
    node_names = {item.id: item.name for item in nodes}

    def apply_local_filters(stmt):
        stmt = _apply_log_type_clause(stmt, selected_log_type)
        if scope:
            stmt = stmt.where(LogEntry.scope == scope)
        if level:
            stmt = stmt.where(LogEntry.level == level)
        if channel_id.isdigit():
            stmt = stmt.where(LogEntry.channel_id == int(channel_id))
        if node_id.isdigit():
            stmt = stmt.where(LogEntry.node_id == int(node_id))
        stmt = _apply_main_log_search(stmt, selected_search, channel_names, node_names)
        return stmt

    def timestamp_value(value: object) -> float:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return 0.0
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        try:
            return float(parsed.timestamp())
        except (OverflowError, OSError, ValueError):
            return 0.0

    def local_item(entry: LogEntry) -> dict[str, object]:
        return {
            "time": format_local_time(entry.created_at),
            "_sort_time": timestamp_value(entry.created_at),
            "source": "Main panel",
            "level": entry.level,
            "scope": entry.scope,
            "subject": _main_log_subject(entry, channel_names, node_names),
            "ip": _log_details_ip(entry.details),
            "user": (entry.actor or _log_details_user(entry.details) or ("Guest" if str(entry.scope or "").strip().lower() == "client" else "")),
            "message": entry.message,
            "duration": "—",
            "details": entry.details or "",
            "actor": entry.actor or "",
            "_entry_id": int(entry.id),
        }

    def remote_scope_allowed(entry_scope: str) -> bool:
        allowed_scopes = {
            "access": {"auth"},
            "client": {"client"},
            "system": {"system", "node", "update", "settings"},
            "activity": None,
        }[selected_log_type]
        if allowed_scopes is None:
            return entry_scope not in {"auth", "client", "system", "node", "update", "settings"}
        return entry_scope in allowed_scopes

    def remote_item(node: Node, entry: dict[str, object]) -> dict[str, object]:
        raw_time = entry.get("time")
        details = str(entry.get("details") or "")
        channel_key = str(entry.get("channel") or "")
        return {
            "time": format_local_time(raw_time),
            "_sort_time": timestamp_value(raw_time),
            "source": node.name,
            "level": str(entry.get("level") or "info"),
            "scope": str(entry.get("scope") or "system"),
            "subject": channel_key or node.name,
            "ip": _log_details_ip(details),
            "user": (str(entry.get("user") or "").strip() or _log_details_user(details) or ("Guest" if str(entry.get("scope") or "").strip().lower() == "client" else "")),
            "message": str(entry.get("message") or ""),
            "duration": (_client_log_duration_label(entry.get("duration_seconds"), bool(entry.get("session_online"))) if entry.get("duration_seconds") is not None else "—"),
            "details": details,
            "actor": "node-agent",
        }

    def sort_value(item: dict[str, object]):
        if selected_sort == "time":
            return float(item.get("_sort_time") or 0.0)
        if selected_sort == "level":
            severity = {"trace": 0, "debug": 1, "info": 2, "notice": 3, "warning": 4, "warn": 4, "error": 5, "critical": 6, "fatal": 7}
            name = str(item.get("level") or "").lower()
            return (severity.get(name, 99), name)
        return str(item.get(selected_sort) or "").casefold()

    include_local = source in {"all", "local", ""}
    selected_remote = remote_nodes if source == "all" else [item for item in remote_nodes if source == f"node:{item.id}"]
    total_count = 0
    displayed_items: list[dict[str, object]] = []

    fast_local_time = include_local and not selected_remote and source in {"local", ""} and selected_sort == "time"
    if fast_local_time:
        count_stmt = apply_local_filters(select(func.count()).select_from(LogEntry))
        total_count = int(db.scalar(count_stmt) or 0)
        page_count = 1 if requested_limit <= 0 else max(1, (total_count + requested_limit - 1) // requested_limit)
        selected_page = min(requested_page, page_count)
        stmt = apply_local_filters(select(LogEntry)).order_by(LogEntry.created_at.asc() if selected_order == "asc" else LogEntry.created_at.desc())
        if requested_limit > 0:
            stmt = stmt.offset((selected_page - 1) * requested_limit).limit(requested_limit)
        displayed_items = [local_item(entry) for entry in db.scalars(stmt).all()]
    else:
        items: list[dict[str, object]] = []
        if include_local:
            items.extend(local_item(entry) for entry in db.scalars(apply_local_filters(select(LogEntry))).all())
        for node in selected_remote:
            try:
                remote_items = node_controller.logs(node, limit=0, level=level, scope=scope, q=selected_search)
            except NodeError as exc:
                now = datetime.now(timezone.utc)
                items.append({
                    "time": format_local_time(now), "_sort_time": timestamp_value(now), "source": node.name,
                    "level": "error", "scope": "node", "subject": node.name, "ip": "", "user": "",
                    "message": "Could not read node logs", "details": str(exc), "actor": "",
                })
                continue
            for entry in remote_items:
                entry_scope = str(entry.get("scope") or "system")
                if remote_scope_allowed(entry_scope):
                    normalized_remote = remote_item(node, entry)
                    # Rolling compatibility: old Nodes ignore the q parameter,
                    # so Main always performs the same final predicate too.
                    if _log_item_matches_search(normalized_remote, selected_search):
                        items.append(normalized_remote)
        total_count = len(items)
        items.sort(key=lambda item: (sort_value(item), float(item.get("_sort_time") or 0.0)), reverse=(selected_order == "desc"))
        page_count = 1 if requested_limit <= 0 else max(1, (total_count + requested_limit - 1) // requested_limit)
        selected_page = min(requested_page, page_count)
        if requested_limit <= 0:
            displayed_items = items
        else:
            offset = (selected_page - 1) * requested_limit
            displayed_items = items[offset:offset + requested_limit]

    # STREAMFORGE_MAIN_CLIENT_LOG_SESSION_AGE_INLINE_V1060: Main Client log
    # rows reuse the existing login/playlist event and receive live/frozen age
    # from shared viewer history. No separate Playback-session row is emitted.
    if selected_log_type == "client" and displayed_items:
        local_positions = [index for index, item in enumerate(displayed_items) if str(item.get("source") or "") == "Main panel"]
        if local_positions:
            local_rows = [displayed_items[index] for index in local_positions]
            duration_states = _main_client_log_duration_states(local_rows, db)
            for index, state in zip(local_positions, duration_states):
                if state:
                    displayed_items[index]["duration"] = _client_log_duration_label(state.get("duration_seconds"), bool(state.get("online")))

    if total_count <= 0:
        shown_from = shown_to = 0
    elif requested_limit <= 0:
        shown_from, shown_to = 1, total_count
    else:
        shown_from = (selected_page - 1) * requested_limit + 1
        shown_to = min(total_count, shown_from + len(displayed_items) - 1)

    pagination_query = urlencode({
        "log_type": selected_log_type, "source": source or "local", "scope": scope, "level": level,
        "channel_id": channel_id, "node_id": node_id, "q": selected_search, "limit": selected_limit,
        "sort": selected_sort, "order": selected_order,
    })
    return render(
        request, "logs.html", db,
        items=displayed_items, total_count=total_count, shown_count=len(displayed_items),
        shown_from=shown_from, shown_to=shown_to, page_count=page_count, selected_page=selected_page,
        prev_page=max(1, selected_page - 1), next_page=min(page_count, selected_page + 1),
        page_numbers=list(range(max(1, min(selected_page - 2, max(1, page_count - 4))), min(page_count, max(1, min(selected_page - 2, max(1, page_count - 4))) + 4) + 1)),
        pagination_query=pagination_query,
        limit_options=list(limit_options.keys()), selected_limit=selected_limit,
        selected_sort=selected_sort, selected_order=selected_order,
        nodes=nodes, remote_nodes=remote_nodes, channels=channels,
        selected_log_type=selected_log_type, selected_source=source or "local", selected_scope=scope,
        selected_level=level, selected_channel_id=channel_id, selected_node_id=node_id,
        selected_search=selected_search, message=message, error=error,
    )


@app.post("/logs/clear", dependencies=[Depends(permission_required("logs.clear"))])
def logs_clear(
    request: Request,
    log_type: str = Form("activity"),
    source: str = Form("local"),
    scope: str = Form(""),
    level: str = Form(""),
    channel_id: str = Form(""),
    node_id: str = Form(""),
    q: str = Form(""),
    db: Session = Depends(get_db),
):
    cleared = 0
    errors: list[str] = []
    selected_log_type = log_type if log_type in {"access", "client", "system", "activity"} else "activity"
    selected_search = _normalize_log_search(q)
    nodes = db.scalars(select(Node).order_by(Node.node_type, Node.name)).all()
    channels = db.scalars(select(Channel).order_by(Channel.name)).all()
    channel_names = {item.id: item.name for item in channels}
    node_names = {item.id: item.name for item in nodes}
    targets = [item for item in nodes if item.node_type == "remote"] if source == "all" else ([db.get(Node, int(source.split(":",1)[1]))] if source.startswith("node:") and source.split(":",1)[1].isdigit() else [])
    targets = [item for item in targets if item]
    # Search-filtered remote clearing requires a Node that understands q.
    # Preflight before deleting local rows so an old Node cannot cause a
    # surprising partial clear or a broader-than-visible remote clear.
    if selected_search:
        old_targets = [item for item in targets if _version_tuple(getattr(item, "agent_version", "")) < (10, 35)]
        if old_targets:
            names = ", ".join(item.name for item in old_targets)
            query = urlencode({
                "log_type": selected_log_type, "source": source, "scope": scope, "level": level,
                "channel_id": channel_id, "node_id": node_id, "q": selected_search,
                "error": f"Update {names} to v10.35 before clearing search-filtered Node logs",
            })
            return RedirectResponse(f"/logs?{query}", status_code=303)
    if source in {"local", "all", ""}:
        stmt = _apply_log_type_clause(delete(LogEntry), selected_log_type)
        if scope:
            stmt = stmt.where(LogEntry.scope == scope)
        if level:
            stmt = stmt.where(LogEntry.level == level)
        if channel_id.isdigit():
            stmt = stmt.where(LogEntry.channel_id == int(channel_id))
        if node_id.isdigit():
            stmt = stmt.where(LogEntry.node_id == int(node_id))
        stmt = _apply_main_log_search(stmt, selected_search, channel_names, node_names)
        result = db.execute(stmt)
        cleared += int(result.rowcount or 0)
        db.commit()
        if selected_log_type == "client":
            # STREAMFORGE_MAIN_CLIENT_SESSION_CLEAR_DEDUPE_V1081: after an
            # operator clears Client logs, the next valid request may create a
            # fresh row even if the same device SID is still within its reset gap.
            def _clear_client_session_keys(client):
                batch: list[str] = []
                for redis_key in client.scan_iter(match=redis_state.key("client-log-session", "*"), count=500):
                    batch.append(str(redis_key))
                    if len(batch) >= 500:
                        client.delete(*batch)
                        batch.clear()
                if batch:
                    client.delete(*batch)
                return True
            redis_state.best_effort_call(_clear_client_session_keys)
    for node in [item for item in targets if item]:
        try:
            if _version_tuple(getattr(node, "agent_version", "")) < (6, 6):
                raise NodeError("Update this Node to v6.6 before using scoped log clearing")
            result = node_controller.clear_logs(
                node,
                log_type=selected_log_type,
                level=level,
                scope=scope,
                q=selected_search,
            )
            cleared += int(result.get("cleared") or 0)
        except NodeError as exc:
            errors.append(f"{node.name}: {exc}")
    admin = current_admin(request, db)
    log_event(
        "Logs cleared",
        scope="system",
        level="warning",
        actor=admin.username if admin else None,
        details={"count": cleared, "source": source, "log_type": selected_log_type, "scope": scope, "level": level, "q": selected_search},
    )
    query = urlencode({
        "log_type": selected_log_type,
        "source": source,
        "scope": scope,
        "level": level,
        "channel_id": channel_id,
        "node_id": node_id,
        "q": selected_search,
        "message": f"Cleared {cleared} log entries",
        "error": "; ".join(errors),
    })
    return RedirectResponse(f"/logs?{query}", status_code=303)


@app.get("/nodes", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.view"))])
def nodes_page(
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    # STREAMFORGE_MAIN_READONLY_PAGE_INIT_V48: do not update Local Node
    # last_seen/status and commit a SQLite write transaction on every page load.
    # Fresh installs still self-heal if the Local Node row is genuinely absent.
    if local_node_for_public_urls(db) is None:
        ensure_local_node(db)
        db.commit()
    nodes = db.scalars(select(Node).order_by(Node.node_type, Node.name)).all()
    node_counts = fast_node_channel_counts(db)
    node_initial_metrics: dict[int, dict[str, float]] = {}
    local_metrics = normalize_node_card_metrics(system_metrics.snapshot())
    viewer_by_node: dict[int, int] = {}
    central_snapshot = viewer_tracker.snapshot()
    for session in central_snapshot.sessions:
        viewer_by_node[session.node_id] = viewer_by_node.get(session.node_id, 0) + 1
    for item in nodes:
        # STREAMFORGE_NODE_CARD_HEARTBEAT_COUNTS_V65R6:
        # Assigned channel total remains authoritative on Main, while Remote
        # Up/Waiting/Down and direct viewer totals come from the latest Node
        # heartbeat.  This avoids the old bug where Main DB channel status made
        # every assigned channel appear Up on every Node.
        base_counts = dict(node_counts.get(item.id, {"total": 0, "up": 0, "waiting": 0, "down": 0}))
        if item.node_type == "remote":
            live = cached_node_live_summary(item.id)
            if live:
                base_counts["up"] = int(live.get("up", 0))
                base_counts["waiting"] = int(live.get("waiting", 0))
                base_counts["down"] = int(live.get("down", 0))
                viewer_by_node[item.id] = int(live.get("active_connections", 0))
        node_counts[item.id] = node_card_display_counts(base_counts)
        node_initial_metrics[item.id] = dict(local_metrics) if item.node_type == "local" else cached_node_metrics(item.id)
    return render(
        request, "nodes.html", db, nodes=nodes, error=error, message=message,
        viewer_by_node=viewer_by_node, node_counts=node_counts,
        node_initial_metrics=node_initial_metrics,
        node_public_urls={item.id: node_public_base(item) for item in nodes},
        node_panel_urls={item.id: effective_node_url(item).strip().rstrip("/") for item in nodes},
        app_version=APP_VERSION,
    )



def webplayer_users_for_node(db: Session, node: Node) -> list[object]:
    # STREAMFORGE_NODE_AUTO_LOGIN_REMOTE_USER_LOAD_V2224:
    # Remote Node playlist users are Node-local accounts stored by the Node Agent,
    # not rows in Main's stream_users table.
    if node.node_type == "remote":
        if node_controller.is_effectively_offline(node):
            return []
        try:
            return node_controller.webplayer_users_on_node(node)
        except NodeError:
            return []
    users = list(db.scalars(select(StreamUser).order_by(StreamUser.name)).all())
    return [user for user in users if user_valid(user) and user.delivery_mode != "direct_node"]


def _webplayer_selected_user(db: Session, node: Node, settings: dict[str, object]) -> StreamUser | None:
    if node.node_type != "local":
        return None
    user_id = int(settings.get("auto_user_id") or 0)
    if not user_id:
        return None
    user = db.get(StreamUser, user_id)
    if not user or not user_valid(user) or user.delivery_mode == "direct_node":
        return None
    return user


def _webplayer_auto_user(db: Session, node: Node, settings: dict[str, object]) -> StreamUser | None:
    if _webplayer_login_mode(settings.get("login_mode")) != "auto":
        return None
    return _webplayer_selected_user(db, node, settings)


@app.get("/nodes/{node_id}/web-player", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.view"))])
def node_webplayer_manage_page(
    node_id: int,
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    webplayer_users = webplayer_users_for_node(db, node)
    if node.node_type == "remote" and not webplayer_users and not error:
        error = "No active Node-local users were returned. Update this Node to v2.1.224 and verify it is online."
    return render(
        request,
        "node_webplayer_manage.html",
        db,
        node=node,
        settings=webplayer_settings_for_node(db, node.id),
        brands=webplayer_brands_for_node(db, node.id),
        webplayer_users=webplayer_users,
        update_json_url=((node.playlist_url or "").strip().rstrip("/") + "/update.json") if (node.playlist_url or "").strip() else "",
        error=error,
        message=message,
    )


@app.post("/nodes/{node_id}/web-player", dependencies=[Depends(permission_required("nodes.view"))])
def node_webplayer_manage_save(
    node_id: int,
    request: Request,
    show_user_info: Optional[str] = Form(None),
    show_connection_info: Optional[str] = Form(None),
    login_mode: str = Form("manual"),
    auto_user_id: int = Form(0),
    auto_user_token: str = Form(""),
    page_color: str = Form("#04080d"),
    page_alpha: int = Form(100),
    panel_color: str = Form("#071019"),
    panel_alpha: int = Form(100),
    accent_color: str = Form("#ff2020"),
    accent_alpha: int = Form(100),
    text_color: str = Form("#f6fbff"),
    text_alpha: int = Form(100),
    download_file: UploadFile | None = File(None),
    remove_download: Optional[str] = Form(None),
    android_version_name: str = Form(""),
    android_description: str = Form(""),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)

    current_settings = webplayer_settings_for_node(db, node.id)
    normalized_login_mode = _webplayer_login_mode(login_mode)
    eligible_users = webplayer_users_for_node(db, node)
    selected_auto_id = 0
    selected_auto_token = ""
    if normalized_login_mode in {"auto", "quick"}:
        selected_mode_label = "Auto Login" if normalized_login_mode == "auto" else "Quick Login"
        if node.node_type == "remote":
            eligible_tokens = {
                str(item.get("token") or "").strip()
                for item in eligible_users
                if isinstance(item, dict)
            }
            saved_token = str(current_settings.get("auto_user_token") or "").strip()
            if saved_token:
                eligible_tokens.add(saved_token)
            selected_auto_token = str(auto_user_token or "").strip()
            if not selected_auto_token or selected_auto_token not in eligible_tokens:
                query = urlencode({"error": f"{selected_mode_label} requires an active user created on this Remote Node"})
                return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)
        else:
            eligible_ids = {int(item.id) for item in eligible_users}
            selected_auto_id = int(auto_user_id or 0)
            if selected_auto_id not in eligible_ids:
                query = urlencode({"error": f"{selected_mode_label} requires an active Main Server user"})
                return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)

    submitted = {
        "show_user_info": bool(show_user_info),
        "show_connection_info": bool(show_connection_info),
        "login_mode": normalized_login_mode,
        "auto_user_id": selected_auto_id,
        "auto_user_token": selected_auto_token,
        "page_color": page_color.strip(), "page_alpha": _webplayer_alpha(page_alpha),
        "panel_color": panel_color.strip(), "panel_alpha": _webplayer_alpha(panel_alpha),
        "accent_color": accent_color.strip(), "accent_alpha": _webplayer_alpha(accent_alpha),
        "text_color": text_color.strip(), "text_alpha": _webplayer_alpha(text_alpha),
        "download_name": str(current_settings.get("download_name") or ""),
        "download_stored_name": str(current_settings.get("download_stored_name") or ""),
        "android_version_name": str(android_version_name or "").strip()[:64],
        "android_description": str(android_description or "").strip()[:2000],
    }
    for name in ("page_color", "panel_color", "accent_color", "text_color"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(submitted[name])):
            query = urlencode({"error": f"{name.replace('_', ' ').title()} must be a 6-digit hex color, for example #ff2020"})
            return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)
        submitted[name] = str(submitted[name]).lower()

    try:
        uploaded_download = save_webplayer_download(
            download_file,
            node.id,
            str(current_settings.get("download_stored_name") or ""),
        )
        if uploaded_download:
            submitted["download_name"], submitted["download_stored_name"] = uploaded_download
        elif as_bool(remove_download):
            remove_webplayer_download(current_settings.get("download_stored_name"))
            submitted["download_name"] = ""
            submitted["download_stored_name"] = ""
            submitted["android_version_name"] = ""
    except ValueError as exc:
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)

    # STREAMFORGE_ANDROID_UPDATE_APK_VALIDATION_V3061:
    # A non-empty version enables the public update manifest. It must point at
    # an actual uploaded APK; blanking the version safely disables the manifest.
    if submitted["android_version_name"] and not str(submitted.get("download_name") or "").lower().endswith(".apk"):
        query = urlencode({"error": "Android update version requires an uploaded .apk file in Player download"})
        return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)

    save_webplayer_settings(db, node.id, submitted)
    db.commit()

    # STREAMFORGE_WEBPLAYER_MAIN_SAVE_PRESERVE_BRANDS_V1214:
    # Access sync treats webplayer_brands as authoritative. Re-read the complete
    # saved snapshot (including existing brands and shared runtime controls)
    # instead of sending the form-only dictionary, which previously cleared all
    # Remote Node brand host mappings after a server-level Web Player save.
    saved_sync_settings = webplayer_settings_for_node(db, node.id)

    message = "Web Player settings saved"
    error = ""
    if node.node_type == "remote":
        try:
            node_controller.sync_access_settings(
                node,
                _sync_main_base(request),
                webplayer_settings=saved_sync_settings,
            )
            node_controller.sync_webplayer_download(node, saved_sync_settings)
            node_controller.sync_webplayer_brand_assets(node, saved_sync_settings)
            message += " and synced to Remote Node"
        except NodeError as exc:
            node_controller.note_control_failure(node, exc)
            mark_node_sync_pending(db, node, "Web Player settings/download queued")
            db.commit()
            message += "; Remote Node sync queued until it is online"

    admin = current_admin(request, db)
    log_event(
        "Web Player settings updated",
        scope="node",
        node_id=node.id,
        actor=admin.username if admin else None,
        details={
            "show_user_info": bool(submitted["show_user_info"]),
            "show_connection_info": bool(submitted["show_connection_info"]),
            "login_mode": submitted["login_mode"],
            "auto_user_id": submitted["auto_user_id"],
            "remote_auto_user_selected": bool(submitted["auto_user_token"]),
            "page_color": submitted["page_color"], "page_alpha": submitted["page_alpha"],
            "panel_color": submitted["panel_color"], "panel_alpha": submitted["panel_alpha"],
            "accent_color": submitted["accent_color"], "accent_alpha": submitted["accent_alpha"],
            "text_color": submitted["text_color"], "text_alpha": submitted["text_alpha"],
            "download_available": bool(submitted["download_stored_name"]),
        },
        db=db,
    )
    query = urlencode({"message": message, "error": error})
    return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)


# STREAMFORGE_WEBPLAYER_MULTI_BRAND_MANAGE_V1155:
# STREAMFORGE_WEBPLAYER_BRAND_LIVE_ALIAS_ASSET_DOWNLOAD_V1158:
@app.post("/nodes/{node_id}/web-player/brand", dependencies=[Depends(permission_required("nodes.view"))])
def node_webplayer_brand_save(
    node_id: int,
    request: Request,
    brand_id: str = Form(""),
    brand_name: str = Form(""),
    brand_domains: str = Form(""),
    brand_logo_url: str = Form(""),
    brand_favicon_url: str = Form(""),
    brand_logo_file: UploadFile | None = File(None),
    brand_favicon_file: UploadFile | None = File(None),
    remove_brand_logo: Optional[str] = Form(None),
    remove_brand_favicon: Optional[str] = Form(None),
    brand_login_mode: str = Form("manual"),
    brand_auto_user_id: int = Form(0),
    brand_auto_user_token: str = Form(""),
    brand_show_user_info: Optional[str] = Form(None),
    brand_show_connection_info: Optional[str] = Form(None),
    brand_download_file: UploadFile | None = File(None),
    remove_brand_download: Optional[str] = Form(None),
    brand_android_version_name: str = Form(""),
    brand_android_description: str = Form(""),
    brand_page_color: str = Form("#04080d"),
    brand_page_alpha: int = Form(100),
    brand_panel_color: str = Form("#071019"),
    brand_panel_alpha: int = Form(100),
    brand_accent_color: str = Form("#ff2020"),
    brand_accent_alpha: int = Form(100),
    brand_text_color: str = Form("#f6fbff"),
    brand_text_alpha: int = Form(100),
    delete_brand: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    brands = webplayer_brands_for_node(db, node.id)
    previous_brands = [dict(item) for item in brands]
    # STREAMFORGE_WEBPLAYER_BRAND_ALIAS_LIFECYCLE_V124:
    # Brand URLs are auto-enrolled as Playlist/App authorities. Keep the old
    # brand URL set so edit/delete can retire aliases that no active profile
    # owns anymore instead of leaving a deleted brand domain live forever.
    previous_brand_urls = {
        str(url).strip().rstrip("/")
        for item in previous_brands
        for url in list(item.get("access_urls") or [])
        if str(url or "").strip()
    }
    previous_playlist_url = str(node.playlist_url or "")
    previous_playlist_urls = str(node.playlist_urls or "")
    clean_id = re.sub(r"[^a-z0-9_-]+", "", str(brand_id or "").strip().lower())[:40]
    existing = next((dict(item) for item in brands if str(item.get("id") or "") == clean_id), {})
    deferred_delete_download = ""
    new_uploaded_download = ""
    deleted_brand_id = ""
    try:
        if as_bool(delete_brand):
            deferred_delete_download = str(existing.get("download_stored_name") or "")
            deleted_brand_id = clean_id
            brands = [item for item in brands if str(item.get("id") or "") != clean_id]
            message = "Web Player brand deleted"
        else:
            base = webplayer_settings_for_node(db, node.id)
            uploaded_logo = save_branding_logo(brand_logo_file)
            uploaded_favicon = save_branding_favicon(brand_favicon_file)
            logo_value = "" if as_bool(remove_brand_logo) else (uploaded_logo or str(brand_logo_url or "").strip() or str(existing.get("logo_url") or ""))
            favicon_value = "" if as_bool(remove_brand_favicon) else (uploaded_favicon or str(brand_favicon_url or "").strip() or str(existing.get("favicon_url") or ""))
            download_name = Path(str(existing.get("download_name") or "")).name
            download_stored_name = Path(str(existing.get("download_stored_name") or "")).name
            # Do not delete the previous file until DB + listener/Remote sync
            # have both succeeded. This keeps rollback truly lossless.
            uploaded_download = save_webplayer_download(brand_download_file, node.id, "")
            if uploaded_download:
                old_stored_name = download_stored_name
                download_name, download_stored_name = uploaded_download
                new_uploaded_download = download_stored_name
                if old_stored_name and old_stored_name != download_stored_name:
                    deferred_delete_download = old_stored_name
            elif as_bool(remove_brand_download):
                deferred_delete_download = download_stored_name
                download_name, download_stored_name = "", ""
            # STREAMFORGE_WEBPLAYER_BRAND_SCHEME_INHERIT_V125:
            # A bare brand domain should follow the server's current public
            # Playlist/App scheme. v12.4 forced bare domains to HTTP, so a Node
            # whose normal WebPlayer is HTTPS could save the profile correctly
            # yet the brand authority was never usable on the expected HTTPS
            # URL. Explicit http:// or https:// values are preserved; bare IPs
            # stay HTTP because public CA certificates cannot be assumed for IPs.
            try:
                primary_scheme = (urlsplit(str(node.playlist_url or "").strip()).scheme or "http").lower()
            except ValueError:
                primary_scheme = "http"
            if primary_scheme not in {"http", "https"}:
                primary_scheme = "http"
            brand_access_values: list[str] = []
            for raw_brand_value in re.split(r"[\s,;]+", str(brand_domains or "").strip()):
                value = str(raw_brand_value or "").strip()
                if not value:
                    continue
                if "://" in value:
                    brand_access_values.append(value)
                    continue
                host = _webplayer_brand_domain(value)
                scheme = primary_scheme
                try:
                    if host and ipaddress.ip_address(host):
                        scheme = "http"
                except ValueError:
                    pass
                brand_access_values.append(f"{scheme}://{value}")
            candidate = _normalize_webplayer_brand({
                "id": clean_id or secrets.token_hex(6),
                "name": brand_name,
                "domains": brand_domains,
                "access_urls": brand_access_values,
                "logo_url": logo_value,
                "favicon_url": favicon_value,
                "download_name": download_name,
                "download_stored_name": download_stored_name,
                "android_version_name": str(brand_android_version_name or "").strip() or str(existing.get("android_version_name") or ""),
                "android_description": str(brand_android_description or "").strip() or str(existing.get("android_description") or ""),
                "show_user_info": bool(brand_show_user_info),
                "show_connection_info": bool(brand_show_connection_info),
                "login_mode": _webplayer_login_mode(brand_login_mode),
                "auto_user_id": max(0, int(brand_auto_user_id or 0)),
                "auto_user_token": str(brand_auto_user_token or "").strip(),
                "page_color": brand_page_color,
                "page_alpha": _webplayer_alpha(brand_page_alpha),
                "panel_color": brand_panel_color,
                "panel_alpha": _webplayer_alpha(brand_panel_alpha),
                "accent_color": brand_accent_color,
                "accent_alpha": _webplayer_alpha(brand_accent_alpha),
                "text_color": brand_text_color,
                "text_alpha": _webplayer_alpha(brand_text_alpha),
            }, base)
            if not candidate:
                raise ValueError("Brand name and at least one valid domain/IP are required")
            if candidate.get("android_version_name") and not str(candidate.get("download_name") or "").lower().endswith(".apk"):
                raise ValueError("Brand Android version requires an uploaded .apk Player download")
            other_hosts = {
                str(host)
                for item in brands if str(item.get("id") or "") != str(candidate.get("id") or "")
                for host in list(item.get("domains") or [])
            }
            duplicate = next((host for host in list(candidate.get("domains") or []) if host in other_hosts), "")
            if duplicate:
                raise ValueError(f"Domain {duplicate} is already assigned to another Web Player brand")
            replaced = False
            for idx, item in enumerate(brands):
                if str(item.get("id") or "") == str(candidate.get("id") or ""):
                    brands[idx] = candidate
                    replaced = True
                    break
            if not replaced:
                if len(brands) >= 16:
                    raise ValueError("Maximum 16 Web Player brands per server")
                brands.append(candidate)
            message = "Web Player brand saved"

        # STREAMFORGE_WEBPLAYER_BRAND_ALIAS_LIFECYCLE_V124:
        # Reconcile auto-enrolled authorities for both save and delete. URLs
        # previously owned by a brand are removed once no active brand owns
        # them; active brand URLs are enrolled immediately. This makes create,
        # domain edit and delete symmetric on Main and Remote Nodes.
        active_brand_urls = {
            str(url).strip().rstrip("/")
            for item in brands
            for url in list(item.get("access_urls") or [])
            if str(url or "").strip()
        }
        current_urls = [
            line.strip().rstrip("/")
            for line in str(node.playlist_urls or node.playlist_url or "").replace("\r", "").splitlines()
            if line.strip()
        ]
        # STREAMFORGE_WEBPLAYER_BRAND_HOST_RETIRE_V125:
        # Retire a removed brand by hostname, not only by the exact saved
        # scheme. This self-heals v12.4 profiles that enrolled http://brand while
        # the live listener/browser used https://brand. Keep the pre-existing
        # primary Playlist/App URL protected so deleting a profile can never
        # strand the server's canonical public address.
        previous_primary = str(node.playlist_url or "").strip().rstrip("/")
        previous_brand_hosts = {
            str(host).strip().lower().rstrip(".")
            for item in previous_brands
            for host in list(item.get("domains") or [])
            if str(host or "").strip()
        }
        active_brand_hosts = {
            str(host).strip().lower().rstrip(".")
            for item in brands
            for host in list(item.get("domains") or [])
            if str(host or "").strip()
        }
        filtered_urls: list[str] = []
        for url in current_urls:
            try:
                url_host = str(urlsplit(url).hostname or "").strip().lower().rstrip(".")
            except ValueError:
                url_host = ""
            retired_brand_host = bool(
                url_host and url_host in previous_brand_hosts and url_host not in active_brand_hosts
            )
            if retired_brand_host and url != previous_primary:
                continue
            if url in previous_brand_urls and url not in active_brand_urls and url != previous_primary:
                continue
            filtered_urls.append(url)
        current_urls = filtered_urls
        for brand_url in sorted(active_brand_urls):
            if brand_url not in current_urls:
                current_urls.append(brand_url)
        normalizer = normalize_main_access_urls if node.node_type == "local" else normalize_node_access_urls
        normalized_urls, normalized_slug = normalizer(
            "\n".join(current_urls), "Playlist/App", getattr(node, "playlist_access_slug", "")
        )
        if not normalized_urls:
            raise ValueError(
                "Cannot remove the last Playlist/App URL; add a base Playlist/App URL before deleting this brand"
            )
        node.playlist_urls = "\n".join(normalized_urls)
        node.playlist_url = previous_primary if previous_primary in normalized_urls else normalized_urls[0]
        node.playlist_access_slug = normalize_node_access_slug(urlsplit(str(node.playlist_url)).path.strip("/"))

        save_webplayer_brands(db, node.id, brands)
        db.commit()
        if node.node_type == "local":
            invalidate_main_host_policy_cache()
            apply_main_access_runtime()
            message += " · brand URL policy applied"
        else:
            web_settings = webplayer_settings_for_node(db, node.id)
            try:
                node_controller.sync_access_settings(node, _sync_main_base(request), webplayer_settings=web_settings)
                node_controller.sync_webplayer_brand_assets(node, web_settings)
                if deleted_brand_id:
                    node_controller.remove_webplayer_brand_assets(node, deleted_brand_id)
                message += " and synced to Remote Node"
            except NodeError as exc:
                # Preserve the existing response-first/offline-safe Node workflow:
                # Main remains authoritative and the normal pending-sync worker
                # will deliver the new aliases/profile/assets when Node recovers.
                node_controller.note_control_failure(node, exc)
                mark_node_sync_pending(db, node, "Web Player brand sync queued")
                db.commit()
                message += "; Remote Node sync queued until it is online"
        if deferred_delete_download:
            remove_webplayer_download(deferred_delete_download)
    except (ValueError, RuntimeError) as exc:
        # Restore both profile JSON and live access aliases on a failed listener/
        # Node sync. A brand save must never strand an already-working server.
        if new_uploaded_download:
            remove_webplayer_download(new_uploaded_download)
        save_webplayer_brands(db, node.id, previous_brands)
        node.playlist_url = previous_playlist_url
        node.playlist_urls = previous_playlist_urls
        db.commit()
        if node.node_type == "local":
            invalidate_main_host_policy_cache()
            try:
                apply_main_access_runtime()
            except RuntimeError:
                pass
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)
    admin = current_admin(request, db)
    log_event(
        "Web Player brand profiles updated", scope="node", node_id=node.id,
        actor=admin.username if admin else None,
        details={"brand_count": len(brands)}, db=db,
    )
    query = urlencode({"message": message})
    return RedirectResponse(f"/nodes/{node.id}/web-player?{query}", status_code=303)


@app.get("/nodes/{node_id}/asn", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.view"))])
def node_asn_page(
    node_id: int,
    request: Request,
    ip: str = "",
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    result = None
    try:
        status = asn_database_status() if node.node_type == "local" else node_controller.asn_status(node)
        geo_settings = load_geoip_settings() if node.node_type == "local" else node_controller.geo_settings_status(node)
        if ip.strip():
            result = node_controller.asn_lookup(node, ip.strip())
    except (NodeError, ValueError) as exc:
        status = {"loaded": False, "error": str(exc), "path": "/opt/streamforge/GeoLite2-ASN.mmdb"}
        geo_settings = {"provider": "auto", "auto_update": False, "maxmind_configured": False, "ipinfo_configured": False}
        error = str(exc)
    return render(request, "node_asn.html", db, node=node, status=status, geo_settings=geo_settings, result=result, test_ip=ip, error=error, message=message)


@app.post("/nodes/{node_id}/geoip/settings", dependencies=[Depends(permission_required("nodes.view"))])
def node_geoip_settings_save(
    node_id: int, request: Request, provider: str = Form("auto"), auto_update: Optional[str] = Form(None),
    maxmind_account_id: str = Form(""), maxmind_license_key: str = Form(""),
    ipinfo_token: str = Form(""), remove_maxmind_key: Optional[str] = Form(None),
    remove_ipinfo_token: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    try:
        payload = {
            "provider": provider, "auto_update": bool(auto_update),
            "maxmind_account_id": maxmind_account_id.strip(),
            "maxmind_license_key": maxmind_license_key.strip(),
            "ipinfo_token": ipinfo_token.strip(),
            "remove_maxmind_key": bool(remove_maxmind_key),
            "remove_ipinfo_token": bool(remove_ipinfo_token),
        }
        if node.node_type == "local":
            save_geoip_settings(**payload)
            clear_geo_cache()
        else:
            node_controller.save_geo_settings(node, payload)
        return RedirectResponse(f"/nodes/{node.id}/asn?message=GeoIP+settings+saved", status_code=303)
    except (NodeError, ValueError) as exc:
        return RedirectResponse(f"/nodes/{node.id}/asn?error={urllib.parse.quote(str(exc))}", status_code=303)


@app.post("/nodes/{node_id}/geoip/update", dependencies=[Depends(permission_required("nodes.view"))])
def node_geoip_update_now(node_id: int, request: Request, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    try:
        geo_settings = load_geoip_settings() if node.node_type == "local" else node_controller.geo_settings_status(node)
        if str(geo_settings.get("provider") or "auto").lower() == "ipinfo":
            output = "IPinfo is a live lookup API. No database update is required; use Test an IP below to verify the saved token."
        elif node.node_type == "local":
            ok, output = run_maxmind_update()
            if not ok:
                raise NodeError(output)
            clear_geo_cache()
        else:
            result = node_controller.run_geo_update(node)
            output = str(result.get("output") or "Update completed")
        return RedirectResponse(f"/nodes/{node.id}/asn?message={urllib.parse.quote(output[-500:])}", status_code=303)
    except (NodeError, ValueError) as exc:
        return RedirectResponse(f"/nodes/{node.id}/asn?error={urllib.parse.quote(str(exc))}", status_code=303)


@app.get("/nodes/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.create"))])
def node_new_page(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    return render(request, "node_form.html", db, node=None, error=None, suggested_token=secrets.token_urlsafe(32))


@app.post("/nodes/new", dependencies=[Depends(permission_required("nodes.create"))])
def node_create(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    api_url: str = Form(...),
    access_slug: str = Form(""),
    playlist_access_slug: str = Form(""),
    api_token: str = Form(...),
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    dns_name: str = Form(""),
    dns_scheme: str = Form("http"),
    dns_only: Optional[str] = Form(None),
    playlist_url: str = Form(""),
    playlist_port: int = Form(0),
    playlist_dns_only: Optional[str] = Form(None),
    local_channel_limit: int = Form(0),
    total_max_connections: int = Form(0),
    node_viewer_session_timeout_seconds: int = Form(5),
    node_hide_panel_hover_urls: Optional[str] = Form(None),
    sync_main_users: Optional[str] = Form(None),
    client_prefixes: str = Form(""),
    panel_ip_whitelist: str = Form(""),
    panel_ip_blacklist: str = Form(""),
    panel_asn_whitelist: str = Form(""),
    panel_asn_blacklist: str = Form(""),
    ip_whitelist: str = Form(""),
    ip_blacklist: str = Form(""),
    asn_whitelist: str = Form(""),
    asn_blacklist: str = Form(""),
    enabled: Optional[str] = Form(None),
    verify_tls: Optional[str] = Form(None),
    ssh_host: str = Form(""),
    ssh_port: int = Form(22),
    ssh_user: str = Form("root"),
    ssh_password: str = Form(""),
    save_ssh_password: Optional[str] = Form(None),
    agent_port: int = Form(0),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    cleaned_name = name.strip()[:120]
    token = api_token.strip()
    if not cleaned_name or not token:
        return render(request, "node_form.html", db, node=None, error="Name and API token are required", suggested_token=secrets.token_urlsafe(32))
    if db.scalar(select(Node).where(func.lower(Node.name) == cleaned_name.lower())):
        return render(request, "node_form.html", db, node=None, error="Node name already exists", suggested_token=token)
    panel_listener_port = max(1, min(65535, int(agent_port or 80)))
    playlist_listener_port = max(1, min(65535, int(playlist_port or panel_listener_port)))
    try:
        normalized_api_urls, normalized_access_slug = normalize_node_access_urls(
            api_url, "Node Panel/API access URLs", access_slug, panel_listener_port
        )
        normalized_playlist_urls, normalized_playlist_access_slug = normalize_node_access_urls(
            playlist_url, "Playlist/App access URLs", playlist_access_slug, playlist_listener_port
        )
        if not normalized_api_urls:
            raise ValueError("At least one Node Panel/API access URL is required")
        if not normalized_playlist_urls:
            raise ValueError("At least one Playlist/App access URL is required")
        cleaned_url = normalized_api_urls[0]
        normalized_playlist_url = normalized_playlist_urls[0]
        normalized_logo = normalize_node_logo_url(logo_url)
        normalized_dns = urlsplit(cleaned_url).hostname or None
        normalized_scheme = urlsplit(cleaned_url).scheme or "http"
        normalized_prefixes = normalize_prefixes(client_prefixes)
        normalized_panel_ip_whitelist = normalize_ip_rules(panel_ip_whitelist)
        normalized_panel_ip_blacklist = normalize_ip_rules(panel_ip_blacklist)
        normalized_panel_asn_whitelist = normalize_asn_rules(panel_asn_whitelist)
        normalized_panel_asn_blacklist = normalize_asn_rules(panel_asn_blacklist)
        normalized_ip_whitelist = normalize_ip_rules(ip_whitelist)
        normalized_ip_blacklist = normalize_ip_rules(ip_blacklist)
        normalized_asn_whitelist = normalize_asn_rules(asn_whitelist)
        normalized_asn_blacklist = normalize_asn_rules(asn_blacklist)
        conflict = prefix_conflict(db, normalized_prefixes)
        if conflict:
            raise ValueError(conflict)
    except ValueError as exc:
        draft = type("NodeDraft", (), {"name": cleaned_name, "api_url": api_url, "client_prefixes": client_prefixes, "panel_ip_whitelist": panel_ip_whitelist, "panel_ip_blacklist": panel_ip_blacklist, "panel_asn_whitelist": panel_asn_whitelist, "panel_asn_blacklist": panel_asn_blacklist, "enabled": as_bool(enabled), "verify_tls": as_bool(verify_tls), "node_type": "remote", "id": None, "dns_name": dns_name, "dns_scheme": dns_scheme, "dns_only": True, "playlist_url": playlist_url, "playlist_dns_only": True, "api_urls": api_url, "playlist_urls": playlist_url, "access_slug": access_slug, "playlist_access_slug": playlist_access_slug, "local_channel_limit": max(0, int(local_channel_limit or 0)), "total_max_connections": max(0, int(total_max_connections or 0)), "node_viewer_session_timeout_seconds": max(5, min(3600, int(node_viewer_session_timeout_seconds or 5))), "node_hide_panel_hover_urls": as_bool(node_hide_panel_hover_urls), "sync_main_users": as_bool(sync_main_users), "logo_url": logo_url})()
        return render(request, "node_form.html", db, node=draft, error=str(exc), suggested_token=token)
    try:
        uploaded_logo = save_node_logo(logo_file)
    except ValueError as exc:
        draft = type("NodeDraft", (), {"name": cleaned_name, "api_url": api_url, "client_prefixes": client_prefixes, "panel_ip_whitelist": panel_ip_whitelist, "panel_ip_blacklist": panel_ip_blacklist, "panel_asn_whitelist": panel_asn_whitelist, "panel_asn_blacklist": panel_asn_blacklist, "enabled": as_bool(enabled), "verify_tls": as_bool(verify_tls), "node_type": "remote", "id": None, "dns_name": dns_name, "dns_scheme": dns_scheme, "dns_only": True, "playlist_url": playlist_url, "playlist_dns_only": True, "api_urls": api_url, "playlist_urls": playlist_url, "access_slug": access_slug, "playlist_access_slug": playlist_access_slug, "local_channel_limit": max(0, int(local_channel_limit or 0)), "total_max_connections": max(0, int(total_max_connections or 0)), "node_viewer_session_timeout_seconds": max(5, min(3600, int(node_viewer_session_timeout_seconds or 5))), "node_hide_panel_hover_urls": as_bool(node_hide_panel_hover_urls), "sync_main_users": as_bool(sync_main_users), "logo_url": logo_url})()
        return render(request, "node_form.html", db, node=draft, error=str(exc), suggested_token=token)
    node = Node(
        name=cleaned_name,
        slug=unique_node_slug(db, cleaned_name),
        node_type="remote",
        api_url=cleaned_url,
        api_urls="\n".join(normalized_api_urls),
        playlist_urls="\n".join(normalized_playlist_urls),
        access_slug=normalized_access_slug,
        playlist_access_slug=normalized_playlist_access_slug,
        api_token=token,
        enabled=as_bool(enabled),
        verify_tls=as_bool(verify_tls),
        client_prefixes=normalized_prefixes,
        panel_ip_whitelist=normalized_panel_ip_whitelist or None,
        panel_ip_blacklist=normalized_panel_ip_blacklist or None,
        panel_asn_whitelist=normalized_panel_asn_whitelist or None,
        panel_asn_blacklist=normalized_panel_asn_blacklist or None,
        ip_whitelist=normalized_ip_whitelist or None,
        ip_blacklist=normalized_ip_blacklist or None,
        asn_whitelist=normalized_asn_whitelist or None,
        asn_blacklist=normalized_asn_blacklist or None,
        dns_name=normalized_dns,
        dns_scheme=normalized_scheme,
        dns_only=True,
        playlist_url=normalized_playlist_url,
        playlist_port=max(1, min(65535, int(urlsplit(normalized_playlist_url).port or playlist_listener_port))),
        playlist_dns_only=True,
        local_channel_limit=max(0, min(100000, int(local_channel_limit or 0))),
        total_max_connections=max(0, min(1000000, int(total_max_connections or 0))),
        sync_main_users=as_bool(sync_main_users),
        logo_url=uploaded_logo or normalized_logo,
        ssh_host=ssh_host.strip() or None,
        ssh_port=max(1, min(65535, int(ssh_port or 22))),
        ssh_user=ssh_user.strip() or None,
        ssh_password_enc=encrypt_secret(ssh_password) if as_bool(save_ssh_password) else None,
        agent_port=panel_listener_port,
        status="unknown",
    )
    db.add(node)
    db.flush()
    # STREAMFORGE_MANUAL_ADD_VIEWER_TTL_SAVE_V2292:
    node_viewer_ttl = max(5, min(3600, int(node_viewer_session_timeout_seconds or 5)))
    save_app_setting(db, _node_viewer_ttl_setting_key(node.id), node_viewer_ttl)
    # STREAMFORGE_MANUAL_ADD_HIDE_HOVER_SAVE_V2296:
    save_app_setting(db, _node_hide_hover_setting_key(node.id), "1" if as_bool(node_hide_panel_hover_urls) else "0")
    db.commit()
    background_tasks.add_task(sync_node_registries_background, node.id, main_panel_public_base(db, request))
    return RedirectResponse("/nodes?message=Remote+node+created+%C2%B7+synchronization+started", status_code=303)


@app.get("/nodes/install", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.install"))])
def node_install_page(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    return render(
        request,
        "node_install.html",
        db,
        error=None,
        suggested_token=secrets.token_urlsafe(32),
        install_job_id=secrets.token_urlsafe(18),
        form_data={
            "enabled": True,
            "verify_tls": True,
            "api_url": "",
            "playlist_url": "",
            "node_viewer_session_timeout_seconds": 5,
            "node_hide_panel_hover_urls": True,
        },
    )


def _set_node_install_job(job_id: str, **values: object) -> None:
    with _NODE_INSTALL_LOCK:
        current = dict(_NODE_INSTALL_JOBS.get(job_id) or {})
        current.update(values)
        current.setdefault("progress", 0)
        current.setdefault("phase", str(current.get("state") or "idle"))
        current.setdefault("logs", [])
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        _NODE_INSTALL_JOBS[job_id] = current
        if len(_NODE_INSTALL_JOBS) > 60:
            finished = [
                key for key, item in _NODE_INSTALL_JOBS.items()
                if key != job_id and item.get("state") not in {"queued", "running"}
            ]
            for key in finished[: max(0, len(_NODE_INSTALL_JOBS) - 50)]:
                _NODE_INSTALL_JOBS.pop(key, None)


def _append_node_install_log(job_id: str, line: object) -> None:
    cleaned = str(line or "").strip()
    if not cleaned:
        return
    with _NODE_INSTALL_LOCK:
        current = dict(_NODE_INSTALL_JOBS.get(job_id) or {})
        logs = [str(item) for item in (current.get("logs") or []) if str(item).strip()]
        for item in cleaned.replace("\r", "\n").split("\n"):
            item = item.strip()
            if item and (not logs or logs[-1] != item):
                logs.append(item[-900:])
        current["logs"] = logs[-120:]
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        _NODE_INSTALL_JOBS[job_id] = current


def _node_install_progress(job_id: str, phase: str, message: str, progress: int, detail: str = "") -> None:
    _set_node_install_job(
        job_id,
        state="running",
        phase=str(phase or "running"),
        message=str(message or "Node installation is running"),
        progress=max(0, min(99, int(progress or 0))),
        error="",
    )
    if detail:
        _append_node_install_log(job_id, detail)


@app.get("/nodes/install/status/{job_id}", dependencies=[Depends(permission_required("nodes.install"))])
def node_install_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401)
    cleaned_job_id = str(job_id or "").strip()
    with _NODE_INSTALL_LOCK:
        job = dict(_NODE_INSTALL_JOBS.get(cleaned_job_id) or {
            "state": "idle", "phase": "idle", "progress": 0,
            "message": "Waiting for Auto Install to start.", "error": "", "logs": [],
        })
    return JSONResponse(job, headers={"Cache-Control": "no-store"})


@app.post("/nodes/install", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.install"))])
def node_install_submit(
    request: Request,
    name: str = Form(...),
    ssh_host: str = Form(...),
    ssh_port: int = Form(22),
    ssh_user: str = Form("root"),
    ssh_password: str = Form(...),
    api_url: str = Form(...),
    api_token: str = Form(""),
    enabled: Optional[str] = Form(None),
    verify_tls: Optional[str] = Form(None),
    playlist_url: str = Form(...),
    local_channel_limit: int = Form(0),
    total_max_connections: int = Form(0),
    node_viewer_session_timeout_seconds: int = Form(5),
    node_hide_panel_hover_urls: Optional[str] = Form(None),
    sync_main_users: Optional[str] = Form(None),
    client_prefixes: str = Form(""),
    panel_ip_whitelist: str = Form(""),
    panel_ip_blacklist: str = Form(""),
    panel_asn_whitelist: str = Form(""),
    panel_asn_blacklist: str = Form(""),
    ip_whitelist: str = Form(""),
    ip_blacklist: str = Form(""),
    asn_whitelist: str = Form(""),
    asn_blacklist: str = Form(""),
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    save_ssh_password: Optional[str] = Form(None),
    install_job_id: str = Form(""),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    cleaned_name = name.strip()[:120]
    job_id = str(install_job_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", job_id):
        job_id = secrets.token_urlsafe(18)
    _set_node_install_job(
        job_id,
        state="running",
        phase="validating",
        progress=3,
        message="Validating Node settings",
        error="",
        logs=[f"Auto Install started for {cleaned_name or 'new Node'}"],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    form_data = {
        "name": cleaned_name,
        "ssh_host": ssh_host.strip(),
        "ssh_port": ssh_port,
        "api_url": api_url,
        "api_token": api_token.strip(),
        "enabled": as_bool(enabled),
        "verify_tls": as_bool(verify_tls),
        "playlist_url": playlist_url,
        "local_channel_limit": max(0, int(local_channel_limit or 0)),
        "total_max_connections": max(0, int(total_max_connections or 0)),
        "node_viewer_session_timeout_seconds": max(5, min(3600, int(node_viewer_session_timeout_seconds or 5))),
        "node_hide_panel_hover_urls": as_bool(node_hide_panel_hover_urls),
        "sync_main_users": as_bool(sync_main_users),
        "client_prefixes": client_prefixes,
        "panel_ip_whitelist": panel_ip_whitelist,
        "panel_ip_blacklist": panel_ip_blacklist,
        "panel_asn_whitelist": panel_asn_whitelist,
        "panel_asn_blacklist": panel_asn_blacklist,
        "ip_whitelist": ip_whitelist,
        "ip_blacklist": ip_blacklist,
        "asn_whitelist": asn_whitelist,
        "asn_blacklist": asn_blacklist,
        "logo_url": logo_url.strip(),
        "save_ssh_password": as_bool(save_ssh_password),
    }
    try:
        if not cleaned_name:
            raise ValueError("Node name is required")
        if db.scalar(select(Node).where(func.lower(Node.name) == cleaned_name.lower())):
            raise ValueError("Node name already exists")

        token = api_token.strip()
        if not token:
            raise ValueError("API token is required")

        normalized_api_urls, normalized_access_slug = normalize_node_access_urls(
            api_url, "Node Panel/API access URLs", "", 80
        )
        normalized_playlist_urls, normalized_playlist_access_slug = normalize_node_access_urls(
            playlist_url, "Playlist/App access URLs", "", 80
        )
        if not normalized_api_urls:
            raise ValueError("At least one Node Panel/API access URL is required")
        if not normalized_playlist_urls:
            raise ValueError("At least one Playlist/App access URL is required")

        panel_first = urlsplit(normalized_api_urls[0])
        playlist_first = urlsplit(normalized_playlist_urls[0])
        panel_host = panel_first.hostname or ""
        playlist_host = playlist_first.hostname or ""
        if not panel_host:
            raise ValueError("First Node Panel/API URL must contain a hostname or IP")
        if not playlist_host:
            raise ValueError("First Playlist/App URL must contain a hostname or IP")

        panel_port = int(panel_first.port or (443 if panel_first.scheme == "https" else 80))
        playlist_port = int(playlist_first.port or (443 if playlist_first.scheme == "https" else 80))
        if panel_first.scheme == "https":
            raise ValueError("Auto install requires the first Node Panel/API URL to use HTTP. HTTPS can be added after TLS/reverse proxy is configured.")
        if playlist_first.scheme == "https":
            raise ValueError("Auto install requires the first Playlist/App URL to use HTTP. HTTPS can be added after TLS/reverse proxy is configured.")

        normalized_prefixes = normalize_prefixes(client_prefixes)
        normalized_panel_ip_whitelist = normalize_ip_rules(panel_ip_whitelist)
        normalized_panel_ip_blacklist = normalize_ip_rules(panel_ip_blacklist)
        normalized_panel_asn_whitelist = normalize_asn_rules(panel_asn_whitelist)
        normalized_panel_asn_blacklist = normalize_asn_rules(panel_asn_blacklist)
        normalized_ip_whitelist = normalize_ip_rules(ip_whitelist)
        normalized_ip_blacklist = normalize_ip_rules(ip_blacklist)
        normalized_asn_whitelist = normalize_asn_rules(asn_whitelist)
        normalized_asn_blacklist = normalize_asn_rules(asn_blacklist)
        normalized_logo = normalize_node_logo_url(logo_url)
        conflict = prefix_conflict(db, normalized_prefixes)
        if conflict:
            raise ValueError(conflict)

        uploaded_logo = save_node_logo(logo_file)
        install_slug = unique_node_slug(db, cleaned_name)

        # Install against the first Manual-style URL. Additional aliases are
        # pushed immediately afterwards through the normal registry sync.
        result = install_node_over_ssh(
            app_root=APP_ROOT,
            host=ssh_host,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            agent_port=panel_port,
            public_port=playlist_port,
            token=token,
            dns_name=panel_host,
            dns_only=True,
            playlist_host=playlist_host,
            playlist_dns_only=True,
            panel_url=main_panel_public_base(db, request),
            node_slug=install_slug,
            progress=lambda phase, message, percent, detail: _node_install_progress(job_id, phase, message, percent, detail),
        )
    except (ValueError, SSHInstallError) as exc:
        _set_node_install_job(
            job_id,
            state="error",
            progress=100,
            message="Auto Install failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        _append_node_install_log(job_id, f"ERROR: {exc}")
        return render(
            request,
            "node_install.html",
            db,
            error=str(exc),
            suggested_token=api_token.strip() or secrets.token_urlsafe(32),
            install_job_id=secrets.token_urlsafe(18),
            form_data=form_data,
        )

    _node_install_progress(job_id, "saving", "Saving the Node in Main Server", 84)
    node = Node(
        name=cleaned_name,
        slug=install_slug,
        node_type="remote",
        api_url=normalized_api_urls[0],
        api_urls="\n".join(normalized_api_urls),
        playlist_url=normalized_playlist_urls[0],
        playlist_urls="\n".join(normalized_playlist_urls),
        access_slug=normalized_access_slug or None,
        playlist_access_slug=normalized_playlist_access_slug or None,
        api_token=token,
        enabled=as_bool(enabled),
        verify_tls=as_bool(verify_tls),
        status="unknown",
        client_prefixes=normalized_prefixes,
        panel_ip_whitelist=normalized_panel_ip_whitelist or None,
        panel_ip_blacklist=normalized_panel_ip_blacklist or None,
        panel_asn_whitelist=normalized_panel_asn_whitelist or None,
        panel_asn_blacklist=normalized_panel_asn_blacklist or None,
        ip_whitelist=normalized_ip_whitelist or None,
        ip_blacklist=normalized_ip_blacklist or None,
        asn_whitelist=normalized_asn_whitelist or None,
        asn_blacklist=normalized_asn_blacklist or None,
        dns_name=panel_host,
        dns_scheme=panel_first.scheme or "http",
        dns_only=True,
        ssh_host=ssh_host.strip() or None,
        ssh_port=max(1, min(65535, int(ssh_port or 22))),
        ssh_user=ssh_user.strip() or None,
        ssh_password_enc=encrypt_secret(ssh_password) if as_bool(save_ssh_password) else None,
        agent_port=panel_port,
        playlist_port=playlist_port,
        playlist_dns_only=True,
        local_channel_limit=max(0, min(100000, int(local_channel_limit or 0))),
        total_max_connections=max(0, min(1000000, int(total_max_connections or 0))),
        sync_main_users=as_bool(sync_main_users),
        logo_url=uploaded_logo or normalized_logo,
    )
    db.add(node)
    db.flush()
    # STREAMFORGE_NEW_NODE_FAVICON_STATE_RESET_V3048:
    # SQLite can reuse the primary key of the most recently deleted Node. A
    # legacy favicon setting from that deleted Node must not be inherited by
    # the newly installed Node.
    stale_favicon = db.get(AppSetting, _node_favicon_setting_key(node.id))
    if stale_favicon is not None:
        db.delete(stale_favicon)
    # STREAMFORGE_AUTO_INSTALL_VIEWER_TTL_SAVE_V2292:
    node_viewer_ttl = max(5, min(3600, int(node_viewer_session_timeout_seconds or 5)))
    save_app_setting(db, _node_viewer_ttl_setting_key(node.id), node_viewer_ttl)
    # STREAMFORGE_AUTO_INSTALL_HIDE_HOVER_SAVE_V2296:
    save_app_setting(db, _node_hide_hover_setting_key(node.id), "1" if as_bool(node_hide_panel_hover_urls) else "0")
    db.commit()
    _append_node_install_log(job_id, f"Node record saved: {node.name}")
    _node_install_progress(job_id, "syncing", "Synchronizing settings, users and channel catalogue", 92)
    sync_messages, sync_errors = sync_node_registries(
        node,
        request,
        access_connect_urls=[result.api_url],
    )
    message_text = f"Node installed. SSH host-key fingerprint: {result.host_key_fingerprint}"
    if sync_messages:
        message_text += " · " + " · ".join(sync_messages)
    values = {"message": message_text}
    if sync_errors:
        values["error"] = " · ".join(sync_errors)
    _set_node_install_job(
        job_id,
        state="done" if not sync_errors else "warning",
        phase="done",
        progress=100,
        message="Node installed and added" if not sync_errors else "Node installed with synchronization warnings",
        error=" · ".join(sync_errors),
        node_id=node.id,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    _append_node_install_log(job_id, "Auto Install completed" if not sync_errors else "Auto Install completed with warnings")
    return RedirectResponse(f"/nodes?{urlencode(values)}", status_code=303)


@app.get("/nodes/{node_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.view"))])
def node_edit_page(node_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    return render(
        request,
        "node_form.html",
        db,
        node=node,
        error=None,
        suggested_token="",
        node_favicon_url=node_favicon_url(db, node.id),
        node_viewer_session_timeout_seconds=max(
            5,
            min(
                3600,
                app_setting_int(
                    db,
                    _node_viewer_ttl_setting_key(node.id),
                    app_setting_int(db, VIEWER_SESSION_TIMEOUT_KEY, 5),
                ),
            ),
        ),
        node_hide_panel_hover_urls=app_setting_str(
            db,
            _node_hide_hover_setting_key(node.id),
            app_setting_str(db, HIDE_PANEL_HOVER_URLS_KEY, "1"),
        ).lower() not in {"0", "false", "off", "no"},
        managed_tls=managed_tls_status_for_node(node),
    )


# STREAMFORGE_DNS01_CARD_ACTIONS_API_V42: AJAX actions used by the DNS-01
# certificate cards. They deliberately do not submit the surrounding node form.
@app.post("/nodes/{node_id}/dns01/test", dependencies=[Depends(permission_required("nodes.view"))])
async def node_dns01_test(node_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401, "Login required")
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    enforce_node_editor_permission(request, db, node)
    # Do not hold a pooled Main DB connection while a Remote Node/DNS lookup runs.
    db.expunge(node)
    db.close()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    host = str(payload.get("host") or "") if isinstance(payload, dict) else ""
    # DNS/Remote-Node I/O must not block the single Main control worker.
    result = await asyncio.to_thread(dns01_cname_test_for_node, node, host)
    # STREAMFORGE_MAIN_DNS01_TEST_AUTO_ISSUE_V1126: a successful CNAME test is
    # enough to start issuance immediately; do not leave the operator waiting
    # for the periodic TLS timer or a second Retry click.
    if isinstance(result, dict) and result.get("ok") and not result.get("certificate_ready"):
        result = dict(result)
        try:
            if node.node_type == "local":
                _write_main_access_request(f"dns01-test-{int(time.time() * 1000)}-{secrets.token_hex(4)}")
            elif not result.get("issuance_queued"):
                await asyncio.to_thread(node_controller.request_tls_reconcile, node, host)
            result["issuance_queued"] = True
            result["message"] = "CNAME is correct and publicly visible. Certificate issuance queued automatically."
        except Exception as exc:
            result["issuance_queued"] = False
            result["message"] = f"CNAME is correct, but certificate issuance could not be queued yet: {exc}"
    return JSONResponse(result)


# STREAMFORGE_DNS01_RETRY_API_V42: queue certificate reconciliation without
# blocking the web worker for a potentially slow ACME transaction.
@app.post("/nodes/{node_id}/dns01/retry", dependencies=[Depends(permission_required("nodes.view"))])
async def node_dns01_retry(node_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401, "Login required")
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    enforce_node_editor_permission(request, db, node)
    # Keep the TLS retry path outside the Main SQLAlchemy checkout window.
    db.expunge(node)
    db.close()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    host = str(payload.get("host") or "").strip().lower().rstrip(".") if isinstance(payload, dict) else ""
    # STREAMFORGE_MAIN_NODE_DNS01_RETRY_V82: do not preflight a Remote Node
    # retry against Main's cached/secondary TLS status. A stale/missed status
    # used to raise 404, and strict Nginx converted it to 444 => Failed to fetch.
    # The Remote Node now validates the selected hostname itself.
    if node.node_type == "local":
        tls = managed_tls_status_for_node(node)
        delegations = tls.get("delegations", {}) if isinstance(tls, dict) else {}
        if host and (not isinstance(delegations, dict) or host not in delegations):
            raise HTTPException(409, "DNS-01 delegation is not registered for this hostname")
        request_id = f"dns01-{int(time.time() * 1000)}-{secrets.token_hex(6)}"
        try:
            _write_main_access_request(request_id)
        except OSError as exc:
            raise HTTPException(500, f"Could not queue Main TLS retry: {exc}") from exc
        result = {"ok": True, "queued": True, "message": "Main certificate retry queued"}
    else:
        try:
            result = await asyncio.to_thread(node_controller.request_tls_reconcile, node, host)
        except NodeError as exc:
            raise HTTPException(502, str(exc)) from exc
        if not isinstance(result, dict):
            result = {"ok": True, "queued": True}
    result = dict(result)
    result.setdefault("ok", True)
    result.setdefault("queued", True)
    result.setdefault("message", "Certificate retry queued")
    if host:
        result["host"] = host
    return JSONResponse(result)


@app.post("/nodes/{node_id}/edit", dependencies=[Depends(permission_required("nodes.view"))])
def node_update(
    node_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    api_url: str = Form(...),
    access_slug: str = Form(""),
    playlist_access_slug: str = Form(""),
    api_token: str = Form(""),
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    remove_logo: Optional[str] = Form(None),
    favicon_file: UploadFile | None = File(None),
    remove_favicon: Optional[str] = Form(None),
    dns_name: str = Form(""),
    dns_scheme: str = Form("http"),
    dns_only: Optional[str] = Form(None),
    playlist_url: str = Form(""),
    playlist_port: int = Form(0),
    playlist_dns_only: Optional[str] = Form(None),
    local_channel_limit: int = Form(0),
    total_max_connections: int = Form(0),
    node_viewer_session_timeout_seconds: int = Form(5),
    node_hide_panel_hover_urls: Optional[str] = Form(None),
    sync_main_users: Optional[str] = Form(None),
    client_prefixes: str = Form(""),
    panel_ip_whitelist: str = Form(""),
    panel_ip_blacklist: str = Form(""),
    panel_asn_whitelist: str = Form(""),
    panel_asn_blacklist: str = Form(""),
    ip_whitelist: str = Form(""),
    ip_blacklist: str = Form(""),
    asn_whitelist: str = Form(""),
    asn_blacklist: str = Form(""),
    enabled: Optional[str] = Form(None),
    verify_tls: Optional[str] = Form(None),
    ssh_host: str = Form(""),
    ssh_port: int = Form(22),
    ssh_user: str = Form("root"),
    ssh_password: str = Form(""),
    save_ssh_password: Optional[str] = Form(None),
    clear_ssh_password: Optional[str] = Form(None),
    agent_port: int = Form(0),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    enforce_node_editor_permission(request, db, node)
    local_snapshot = None
    if node.node_type == "local":
        local_snapshot = {
            field: getattr(node, field)
            for field in (
                "name", "api_url", "api_urls", "playlist_url", "playlist_urls",
                "access_slug", "playlist_access_slug", "logo_url", "enabled",
                "verify_tls", "client_prefixes", "panel_ip_whitelist", "panel_ip_blacklist",
                "panel_asn_whitelist", "panel_asn_blacklist", "ip_whitelist", "ip_blacklist",
                "asn_whitelist", "asn_blacklist", "dns_name", "dns_scheme",
                "dns_only", "playlist_dns_only", "agent_port", "playlist_port",
                "status", "last_error",
            )
        }
    cleaned_name = name.strip()[:120] or node.name
    previous_access_urls = [
        item.strip().rstrip("/")
        for item in str(getattr(node, "api_urls", None) or node.api_url or "").splitlines()
        if item.strip()
    ]
    duplicate = db.scalar(select(Node).where(func.lower(Node.name) == cleaned_name.lower(), Node.id != node_id))
    if duplicate:
        return render(request, "node_form.html", db, node=node, error="Node name already exists", suggested_token="")
    panel_listener_port = max(1, min(65535, int(agent_port or normalized_native_control_port(node.agent_port))))
    # A Playlist/App URL without an explicit port shares Panel/API by default.
    # Keep a separate listener only when its URL explicitly includes that port.
    playlist_listener_port = max(1, min(65535, int(playlist_port or panel_listener_port)))
    try:
        normalized_prefixes = normalize_prefixes(client_prefixes)
        normalized_panel_ip_whitelist = normalize_ip_rules(panel_ip_whitelist)
        normalized_panel_ip_blacklist = normalize_ip_rules(panel_ip_blacklist)
        normalized_panel_asn_whitelist = normalize_asn_rules(panel_asn_whitelist)
        normalized_panel_asn_blacklist = normalize_asn_rules(panel_asn_blacklist)
        normalized_ip_whitelist = normalize_ip_rules(ip_whitelist)
        normalized_ip_blacklist = normalize_ip_rules(ip_blacklist)
        normalized_asn_whitelist = normalize_asn_rules(asn_whitelist)
        normalized_asn_blacklist = normalize_asn_rules(asn_blacklist)
        normalized_logo = normalize_node_logo_url(logo_url)
        if node.node_type == "local":
            normalized_api_urls, normalized_access_slug = normalize_main_access_urls(
                api_url, "Main Panel/API access URLs", access_slug, None
            )
            normalized_playlist_urls, normalized_playlist_access_slug = normalize_main_access_urls(
                playlist_url, "Main Playlist/App access URLs", playlist_access_slug, None
            )
        else:
            normalized_api_urls, normalized_access_slug = normalize_node_access_urls(
                api_url, "Node Panel/API access URLs", access_slug, panel_listener_port
            )
            normalized_playlist_urls, normalized_playlist_access_slug = normalize_node_access_urls(
                playlist_url, "Playlist/App access URLs", playlist_access_slug, playlist_listener_port
            )
        if not normalized_api_urls:
            raise ValueError("At least one Panel/API access URL is required")
        if not normalized_playlist_urls:
            raise ValueError("At least one Playlist/App access URL is required")
        normalized_playlist_url = normalized_playlist_urls[0]
        if node.node_type == "local":
            # STREAMFORGE_MAIN_AUTOMATIC_RELAY_ACCESS_V3031: Main relay
            # authority follows the canonical Panel/API URL. Exact hostname,
            # port, access-path and URL-role enforcement is mandatory for Main
            # and is no longer controlled by three duplicate form options.
            primary_panel = urlsplit(normalized_api_urls[0])
            normalized_dns = str(primary_panel.hostname or "").strip().lower().rstrip(".")
            normalized_scheme = str(primary_panel.scheme or "http").lower()
        else:
            normalized_dns = urlsplit(normalized_api_urls[0]).hostname
            normalized_scheme = urlsplit(normalized_api_urls[0]).scheme
        conflict = prefix_conflict(db, normalized_prefixes, node.id)
        if conflict:
            raise ValueError(conflict)
        node.api_url = normalized_api_urls[0]
        node.api_urls = "\n".join(normalized_api_urls)
        node.playlist_url = normalized_playlist_url
        node.playlist_urls = "\n".join(normalized_playlist_urls)
        node.access_slug = normalized_access_slug or None
        node.playlist_access_slug = normalized_playlist_access_slug or None
    except ValueError as exc:
        return render(request, "node_form.html", db, node=node, error=str(exc), suggested_token="", node_favicon_url=node_favicon_url(db, node.id))
    old_logo = node.logo_url
    old_favicon = node_favicon_url(db, node.id)
    try:
        uploaded_logo = save_node_logo(logo_file)
    except ValueError as exc:
        return render(request, "node_form.html", db, node=node, error=str(exc), suggested_token="", node_favicon_url=node_favicon_url(db, node.id))
    if as_bool(remove_logo):
        node.logo_url = None
    elif uploaded_logo:
        node.logo_url = uploaded_logo
    elif normalized_logo:
        node.logo_url = normalized_logo
    try:
        uploaded_favicon = save_node_favicon(favicon_file, node.id)
    except ValueError as exc:
        return render(request, "node_form.html", db, node=node, error=str(exc), suggested_token="", node_favicon_url=node_favicon_url(db, node.id))
    new_favicon = old_favicon
    if uploaded_favicon:
        new_favicon = uploaded_favicon
        save_app_setting(db, _node_favicon_setting_key(node.id), uploaded_favicon)
    elif as_bool(remove_favicon):
        new_favicon = ""
        save_app_setting(db, _node_favicon_setting_key(node.id), "")
    node.name = cleaned_name
    if not node.slug:
        node.slug = unique_node_slug(db, cleaned_name, node.id)
    if node.node_type != "local" and api_token.strip():
        node.api_token = api_token.strip()
    node.enabled = True if node.node_type == "local" else as_bool(enabled)
    node.verify_tls = as_bool(verify_tls) if node.node_type != "local" else True
    node.client_prefixes = normalized_prefixes
    node.panel_ip_whitelist = normalized_panel_ip_whitelist or None
    node.panel_ip_blacklist = normalized_panel_ip_blacklist or None
    node.panel_asn_whitelist = normalized_panel_asn_whitelist or None
    node.panel_asn_blacklist = normalized_panel_asn_blacklist or None
    node.ip_whitelist = normalized_ip_whitelist or None
    node.ip_blacklist = normalized_ip_blacklist or None
    node.asn_whitelist = normalized_asn_whitelist or None
    node.asn_blacklist = normalized_asn_blacklist or None
    node.dns_name = normalized_dns
    node.dns_scheme = normalized_scheme
    node.dns_only = True
    panel_parsed = urlsplit(node.api_url or normalized_api_urls[0])
    playlist_parsed = urlsplit(normalized_playlist_url)
    panel_public_port = int(panel_parsed.port or (443 if panel_parsed.scheme == "https" else 80))
    playlist_public_port = int(playlist_parsed.port or (443 if playlist_parsed.scheme == "https" else 80))
    # STREAMFORGE_REMOTE_PUBLIC_INTERNAL_PORT_SPLIT_V36:
    # Remote public URL ports describe the browser-facing authority only.
    # The Node Agent/Playlist backend listener remains the separately resolved
    # internal listener (normally 80) so an HTTPS reverse proxy can terminate
    # TLS on 443 and forward plaintext HTTP internally without rewriting the
    # saved public URL to :80.
    node.playlist_port = max(1, min(65535, playlist_listener_port if node.node_type != "local" else playlist_public_port))
    node.playlist_dns_only = True
    node.local_channel_limit = max(0, min(100000, int(local_channel_limit or 0))) if node.node_type != "local" else 0
    node.total_max_connections = max(0, min(1000000, int(total_max_connections or 0))) if node.node_type != "local" else 0
    if node.node_type != "local":
        node_viewer_ttl = max(5, min(3600, int(node_viewer_session_timeout_seconds or 5)))
        save_app_setting(db, _node_viewer_ttl_setting_key(node.id), node_viewer_ttl)
        # STREAMFORGE_NODE_HIDE_HOVER_SAVE_V2293:
        save_app_setting(db, _node_hide_hover_setting_key(node.id), "1" if as_bool(node_hide_panel_hover_urls) else "0")
    node.sync_main_users = as_bool(sync_main_users) if node.node_type != "local" else False
    if node.node_type != "local":
        node.ssh_host = ssh_host.strip() or node.ssh_host
        node.ssh_port = max(1, min(65535, int(ssh_port or node.ssh_port or 22)))
        node.ssh_user = ssh_user.strip() or node.ssh_user
        node.agent_port = panel_listener_port
        if as_bool(clear_ssh_password):
            node.ssh_password_enc = None
        elif as_bool(save_ssh_password) and ssh_password:
            node.ssh_password_enc = encrypt_secret(ssh_password)
    else:
        node.agent_port = max(1, min(65535, panel_public_port))
    node.status = "online" if node.node_type == "local" else "unknown"
    node.last_error = None
    db.commit()
    if node.node_type == "local":
        invalidate_main_host_policy_cache()
        try:
            apply_main_access_runtime()
        except RuntimeError as exc:
            if local_snapshot is not None:
                for field, previous_value in local_snapshot.items():
                    setattr(node, field, previous_value)
                db.commit()
                invalidate_main_host_policy_cache()
                try:
                    apply_main_access_runtime()
                except RuntimeError:
                    pass
            if uploaded_logo and uploaded_logo != (local_snapshot or {}).get("logo_url"):
                cleanup_node_logo_if_unused(db, uploaded_logo)
            return render(
                request,
                "node_form.html",
                db,
                node=node,
                error=f"Main access URL was not applied; previous working settings were restored. {exc}",
                suggested_token="",
            )
    if old_logo and old_logo != node.logo_url:
        cleanup_node_logo_if_unused(db, old_logo)
    if old_favicon and old_favicon != new_favicon:
        cleanup_node_favicon_file(old_favicon)
    if node.node_type == "remote":
        background_tasks.add_task(
            sync_node_registries_background,
            node.id,
            main_panel_public_base(db, request),
            previous_access_urls,
        )
        message = "Node updated · synchronization started"
    else:
        message = "Main/Local Panel/API, Playlist/App, listener ports and access rules updated"
        canonical_panel = str(normalized_api_urls[0]).rstrip("/")
        return RedirectResponse(
            f"{canonical_panel}/nodes?message={quote_plus(message)}",
            status_code=303,
        )
    return RedirectResponse(f"/nodes?message={quote_plus(message)}", status_code=303)


def _main_server_admin_action(node_id: int, action: str, request: Request, db: Session) -> RedirectResponse:
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    node = db.get(Node, node_id)
    if not node or node.node_type != "local":
        raise HTTPException(404)
    label = "Main service restart" if action == "restart_service" else "Main Server reboot"
    try:
        request_id = _write_main_system_request(action, admin)
    except (OSError, ValueError) as exc:
        return RedirectResponse(f"/nodes?error={quote_plus(label + ' could not be queued: ' + str(exc))}", status_code=303)
    log_event(
        label + " requested", scope="system", level="warning", node_id=node.id,
        actor=admin.username, details={"request_id": request_id},
    )
    return RedirectResponse(f"/nodes?message={quote_plus(label + ' scheduled')}", status_code=303)


@app.post("/nodes/{node_id}/main-service-restart", dependencies=[Depends(permission_required("main_system.service_restart"))])
def main_service_restart(node_id: int, request: Request, db: Session = Depends(get_db)):
    return _main_server_admin_action(node_id, "restart_service", request, db)


@app.post("/nodes/{node_id}/main-reboot", dependencies=[Depends(permission_required("main_system.reboot"))])
def main_system_reboot(node_id: int, request: Request, db: Session = Depends(get_db)):
    return _main_server_admin_action(node_id, "reboot_server", request, db)


def _node_ssh_admin_action(node_id: int, action: str, request: Request, db: Session) -> RedirectResponse:
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    node = db.get(Node, node_id)
    if not node or node.node_type != "remote":
        raise HTTPException(404)
    password = decrypt_secret(node.ssh_password_enc) if node.ssh_password_enc else ""
    host = (node.ssh_host or urlsplit(node.api_url or "").hostname or "").strip()
    username = (node.ssh_user or "root").strip()
    if not host or not password:
        return RedirectResponse(
            f"/nodes?error={quote_plus(node.name + ': save SSH host and password from Edit Node first')}",
            status_code=303,
        )
    label = "Node service restart" if action == "service_restart" else "Node reboot"
    try:
        result = run_node_admin_command_over_ssh(
            host=host, port=int(node.ssh_port or 22), username=username, password=password, action=action
        )
        node.status = "unknown"
        node.last_error = None
        db.commit()
        log_event(
            label + " requested", scope="node", level="warning", node_id=node.id,
            actor=admin.username, details={"host_key": result.host_key_fingerprint},
        )
        return RedirectResponse(
            f"/nodes?message={quote_plus(label + ' requested for ' + node.name)}", status_code=303
        )
    except SSHInstallError as exc:
        node.last_error = str(exc)
        db.commit()
        return RedirectResponse(f"/nodes?error={quote_plus(node.name + ': ' + str(exc))}", status_code=303)


@app.post("/nodes/{node_id}/service-restart", dependencies=[Depends(permission_required("nodes.service_restart"))])
def node_service_restart(node_id: int, request: Request, db: Session = Depends(get_db)):
    return _node_ssh_admin_action(node_id, "service_restart", request, db)


@app.post("/nodes/{node_id}/reboot", dependencies=[Depends(permission_required("nodes.reboot"))])
def node_system_reboot(node_id: int, request: Request, db: Session = Depends(get_db)):
    return _node_ssh_admin_action(node_id, "reboot", request, db)


# STREAMFORGE_NODE_TEST_SYNC_BACKGROUND_V60R2:
def _node_test_sync_background(node_id: int, main_base_url: str) -> None:
    """Run Test & Sync outside the browser request.

    Full Node health + catalogue reconciliation can take many seconds. Keeping
    that work inside the POST made the browser spinner stay active and caused
    unrelated Nodes-page status fetches to time out. The browser now returns
    immediately while this worker performs the same fail-closed test/sync.
    """
    try:
        with SessionLocal() as work_db:
            node = work_db.get(Node, int(node_id))
            if not node:
                return
            node_name = str(node.name or f"Node {node_id}")
            node_type = str(node.node_type or "remote")
            # End the initial read transaction before any network wait.
            work_db.commit()
            try:
                result = node_controller.test(node)
            except NodeError as exc:
                current = work_db.get(Node, int(node_id))
                if current:
                    node_controller.note_control_failure(current, exc)
                    if current.node_type == "remote":
                        mark_node_sync_pending(work_db, current, "Manual Test & Sync retry: " + str(exc))
                    work_db.commit()
                log_event(
                    "Node Test & Sync failed", scope="node", level="warning",
                    node_id=int(node_id), details={"error": str(exc)},
                )
                return

            reported_version = str(result.get("version") or "").strip()
            current = work_db.get(Node, int(node_id))
            if current:
                if reported_version:
                    current.agent_version = reported_version[:64]
                current.status = "online"
                current.last_seen_at = datetime.now(timezone.utc)
                current.last_error = None
                if current.node_type == "remote":
                    clear_node_sync_pending(work_db, current.id)
                work_db.commit()

        messages: list[str] = []
        errors: list[str] = []
        if node_type == "remote":
            with SessionLocal() as sync_db:
                current = sync_db.get(Node, int(node_id))
                if current:
                    # Release SQLite before the long network reconciliation.
                    sync_db.commit()
                    messages, errors = sync_node_registries(current, main_base_url)
        if not errors:
            _reset_node_reconcile_backoff(int(node_id))
            node_controller.clear_transport_backoff(int(node_id))
        log_event(
            "Node Test & Sync completed" if not errors else "Node Test & Sync completed with warnings",
            scope="node", level="info" if not errors else "warning", node_id=int(node_id),
            details={"node": node_name, "messages": messages, "errors": errors},
        )
    except Exception as exc:
        log_event(
            "Node Test & Sync background worker failed", scope="node", level="error",
            node_id=int(node_id), details={"error": str(exc)},
        )


@app.post("/nodes/{node_id}/test", dependencies=[Depends(permission_required("nodes.test"))])
def node_test(
    node_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    if not current_admin(request, db):
        return redirect_login()
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)
    node_name = str(node.name or f"Node {node_id}")
    main_base = main_panel_public_base(db, request)
    if node.node_type == "remote":
        mark_node_sync_pending(db, node, "Manual Test & Sync queued")
    db.commit()
    background_tasks.add_task(_node_test_sync_background, int(node.id), main_base)
    return RedirectResponse(
        f"/nodes?message={urllib.parse.quote_plus(node_name + ': Test & Sync started in background')}",
        status_code=303,
    )


@app.post("/api/v1/node-access-back-sync/{node_slug}")
async def node_access_back_sync(
    node_slug: str,
    request: Request,
    x_node_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    node = db.scalar(select(Node).where(Node.slug == node_slug, Node.enabled.is_(True)))
    if not node or not node.api_token or not x_node_token or not hmac.compare_digest(node.api_token, x_node_token):
        raise HTTPException(401, "Invalid node settings sync")
    try:
        payload = await request.json()
        panel_urls, access_slug = normalize_node_access_urls(
            "\n".join(str(item or "") for item in (payload.get("panel_urls") or [])),
            "Node Panel/API access URLs",
            str(payload.get("access_slug") or ""),
            int(node.agent_port or 80),
        )
        api_primary = urlsplit(panel_urls[0])
        shared_listener_port = int(
            api_primary.port
            or payload.get("control_port")
            or node.agent_port
            or (443 if api_primary.scheme == "https" else 80)
        )
        stream_urls, playlist_access_slug = normalize_node_access_urls(
            "\n".join(str(item or "") for item in (payload.get("stream_urls") or [])),
            "Playlist/App access URLs",
            str(payload.get("stream_slug") or payload.get("playlist_access_slug") or ""),
            int(payload.get("stream_port") or shared_listener_port),
        )
        if not panel_urls or not stream_urls:
            raise ValueError("Both Node Panel/API and Playlist/App access URLs are required")
        api_parsed = urlsplit(panel_urls[0])
        playlist_parsed = urlsplit(stream_urls[0])
        node.api_url = panel_urls[0]
        node.api_urls = "\n".join(panel_urls)
        node.playlist_url = stream_urls[0]
        node.playlist_urls = "\n".join(stream_urls)
        node.access_slug = access_slug or None
        node.playlist_access_slug = playlist_access_slug or None
        node.dns_name = api_parsed.hostname
        node.dns_scheme = api_parsed.scheme or "http"
        node.dns_only = True
        node.playlist_dns_only = True
        node.agent_port = normalized_native_control_port(node.agent_port, payload.get("control_port"))
        node.playlist_port = max(1, min(65535, int(
            playlist_parsed.port
            or payload.get("stream_port")
            or (443 if playlist_parsed.scheme == "https" else shared_listener_port)
        )))
        if "total_max_connections" in payload:
            node.total_max_connections = max(0, min(1000000, int(payload.get("total_max_connections") or 0)))
        for field, normalizer in (("panel_ip_whitelist", normalize_ip_rules), ("panel_ip_blacklist", normalize_ip_rules), ("panel_asn_whitelist", normalize_asn_rules), ("panel_asn_blacklist", normalize_asn_rules), ("ip_whitelist", normalize_ip_rules), ("ip_blacklist", normalize_ip_rules), ("asn_whitelist", normalize_asn_rules), ("asn_blacklist", normalize_asn_rules)):
            if field in payload:
                setattr(node, field, normalizer(str(payload.get(field) or "")) or None)
        node.status = "online"
        node.last_seen_at = datetime.now(timezone.utc)
        node.last_error = None
        db.commit()
        return {"ok": True, "node": node.slug, "api_url": node.api_url, "playlist_url": node.playlist_url, "access_slug": node.access_slug or "", "stream_slug": node.playlist_access_slug or ""}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc




def _panel_live_auth_key(node_token: str) -> bytes:
    return hashlib.sha256(b"streamforge-live-panel-auth-v1\0" + node_token.encode("utf-8")).digest()


def _panel_live_auth_aad(node_slug: str) -> bytes:
    return f"streamforge-live-panel-auth-v1|{node_slug}".encode("utf-8")


def _decrypt_panel_live_auth_envelope(node: Node, encoded: object) -> dict[str, object]:
    raw_encoded = str(encoded or "").strip()
    if not node.api_token or not raw_encoded:
        raise HTTPException(400, "Invalid live-auth envelope")
    try:
        raw = base64.urlsafe_b64decode(raw_encoded.encode("ascii"))
        if len(raw) < 29:
            raise ValueError("short envelope")
        nonce, ciphertext = raw[:12], raw[12:]
        plaintext = AESGCM(_panel_live_auth_key(node.api_token)).decrypt(
            nonce, ciphertext, _panel_live_auth_aad(node.slug)
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        issued_at = int(payload.get("issued_at") or 0)
        if abs(int(time.time()) - issued_at) > 45:
            raise ValueError("expired envelope")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(400, "Invalid or expired live-auth envelope") from exc
    except Exception as exc:
        raise HTTPException(400, "Invalid live-auth envelope") from exc


def _encrypt_panel_live_auth_envelope(node: Node, payload: dict[str, object]) -> str:
    if not node.api_token:
        raise HTTPException(503, "Node authentication is not configured")
    nonce = secrets.token_bytes(12)
    body = dict(payload)
    body["issued_at"] = int(time.time())
    plaintext = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(_panel_live_auth_key(node.api_token)).encrypt(
        nonce, plaintext, _panel_live_auth_aad(node.slug)
    )
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _panel_admin_auth_version(admin: AdminUser) -> str:
    # Password changes invalidate every active Node session without storing a
    # second session table on the Main Server.
    return hashlib.sha256(str(admin.password_hash or "").encode("utf-8")).hexdigest()[:24]


@app.post("/api/v1/node-panel-live-auth/{node_slug}")
async def node_panel_live_auth(
    node_slug: str,
    request: Request,
    x_node_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Fail-closed live authorization for every Remote Node panel session.

    Passwords and authorization replies are encrypted with a key derived from
    the per-node API token, so credentials are not exposed even when the
    private Main-to-Node transport is plain HTTP.
    """
    node = db.scalar(
        select(Node).where(
            Node.slug == node_slug,
            Node.node_type == "remote",
            Node.enabled.is_(True),
        )
    )
    if not node or not node.api_token or not x_node_token or not hmac.compare_digest(node.api_token, x_node_token):
        raise HTTPException(401, "Invalid node authentication")
    try:
        outer = await request.json()
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid live-auth request") from exc
    payload = _decrypt_panel_live_auth_envelope(node, (outer or {}).get("envelope") if isinstance(outer, dict) else None)
    action = str(payload.get("action") or "").strip().lower()
    username = str(payload.get("username") or "").strip()
    # STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73: the authenticated Node forwards the
    # browser IP inside the encrypted envelope so Main does not log the Node's
    # transport address as the user address.
    submitted_client_ip = str(payload.get("client_ip") or "").strip()[:120]
    if action not in {"login", "session", "change_password"} or not username:
        raise HTTPException(400, "Invalid live-auth action")

    admin = db.scalar(
        select(AdminUser)
        .options(selectinload(AdminUser.role))
        .where(
            AdminUser.username == username,
            AdminUser.is_active.is_(True),
            AdminUser.nodes.any(Node.id == node.id),
        )
    )
    if not admin:
        if action == "login":
            log_event(
                f"Failed live Node panel login for {username or 'unknown'} on {node.name}",
                scope="auth", level="warning", node_id=node.id, actor=username or None, details={"ip": submitted_client_ip} if submitted_client_ip else None,
            )
        raise HTTPException(401, "Invalid username or password")

    auth_version = _panel_admin_auth_version(admin)
    if action == "login":
        password = str(payload.get("password") or "")
        if not verify_password(password, admin.password_hash):
            log_event(
                f"Failed live Node panel login for {username or 'unknown'} on {node.name}",
                scope="auth", level="warning", node_id=node.id, actor=username or None, details={"ip": submitted_client_ip} if submitted_client_ip else None,
            )
            raise HTTPException(401, "Invalid username or password")
        log_event(
            f"Live Node panel login succeeded on {node.name}",
            scope="auth", node_id=node.id, actor=admin.username, details={"ip": submitted_client_ip} if submitted_client_ip else None,
        )
    elif action == "change_password":
        # STREAMFORGE_NODE_PANEL_SELF_PASSWORD_V2255:
        current_password = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or "")
        if not verify_password(current_password, admin.password_hash):
            raise HTTPException(401, "Current password is incorrect")
        if len(new_password) < 8:
            raise HTTPException(400, "New password must contain at least 8 characters")
        if verify_password(new_password, admin.password_hash):
            raise HTTPException(400, "New password must be different from the current password")
        admin.password_hash = hash_password(new_password)
        db.commit()
        db.refresh(admin)
        auth_version = _panel_admin_auth_version(admin)
        log_event(
            f"Panel user changed own password from Node {node.name}",
            scope="auth", node_id=node.id, actor=admin.username, details={"ip": submitted_client_ip} if submitted_client_ip else None,
        )
    else:
        submitted_version = str(payload.get("auth_version") or "")
        if not submitted_version or not hmac.compare_digest(submitted_version, auth_version):
            raise HTTPException(401, "Node panel session was revoked")

    response_payload = {
        "ok": True,
        "username": admin.username,
        "permissions": sorted(role_permission_set(admin.role)),
        "auth_version": auth_version,
        "node": node.slug,
        "node_name": node.name,
    }
    return {"ok": True, "envelope": _encrypt_panel_live_auth_envelope(node, response_payload)}


@app.get("/api/v1/node-heartbeat/{node_slug}")
def node_heartbeat(
    node_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_node_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    node = db.scalar(select(Node).where(Node.slug == node_slug, Node.enabled.is_(True)))
    if not node or not node.api_token or not x_node_token or not hmac.compare_digest(node.api_token, x_node_token):
        raise HTTPException(401, "Invalid node heartbeat")
    user_agent = str(request.headers.get("user-agent") or "")
    version_match = re.search(r"(?:^|\s)StreamForge-Node/([A-Za-z0-9._+-]{1,64})", user_agent)
    if version_match:
        node.agent_version = version_match.group(1)
    # STREAMFORGE_NODE_HEARTBEAT_NO_FULL_SWEEP_V1127:
    # Heartbeat is liveness/metrics, not a hidden full-catalogue trigger. Only
    # an explicit Manual/Full reconcile request may run sync_node_registries().
    # Per-channel failures are upgraded/retried through their targeted queue and
    # retried through their dedicated targeted queue.
    pending_reason = node_sync_pending_reason(db, node.id)
    pending_lower = pending_reason.lower()
    # STREAMFORGE_NODE_LEGACY_GENERIC_PENDING_MIGRATION_V1129:
    # Older/in-flight workers may still leave generic channel config/catalogue
    # or logo reasons. Convert them into exact per-channel queues before removing
    # the generic key. This preserves retry work without permitting heartbeat to
    # launch a hidden full Node reconcile.
    # STREAMFORGE_NODE_STALE_CATALOGUE_PENDING_CLEANUP_V1128:
    # Legacy generic catalogue/mode pending reasons are migrated or cleared here;
    # heartbeat must never treat these stale reasons as a hidden full reconcile.
    legacy_target_pending_migrated = False
    for legacy_prefix in (
        "channel settings queued: ",
        "channel settings queued after connection failure: ",
        "channel catalogue queued after control connection failure: ",
    ):
        if pending_lower.startswith(legacy_prefix):
            legacy_name = pending_reason[len(legacy_prefix):].strip()
            legacy_ids = [
                int(item) for item in db.scalars(
                    select(Channel.id).where(Channel.name == legacy_name).limit(2)
                ).all()
            ]
            if len(legacy_ids) == 1:
                mark_node_channel_sync_pending(
                    db, node, legacy_ids[0], f"Migrated legacy pending: {pending_reason}"
                )
                legacy_target_pending_migrated = True
            break
    if not legacy_target_pending_migrated:
        for legacy_prefix in (
            "channel logo queued: ",
            "channel logo queued after connection failure: ",
        ):
            if pending_lower.startswith(legacy_prefix):
                legacy_name = pending_reason[len(legacy_prefix):].strip()
                legacy_ids = [
                    int(item) for item in db.scalars(
                        select(Channel.id).where(Channel.name == legacy_name).limit(2)
                    ).all()
                ]
                if len(legacy_ids) == 1:
                    mark_node_channel_logo_pending(
                        db, node, legacy_ids[0], f"Migrated legacy pending: {pending_reason}"
                    )
                    legacy_target_pending_migrated = True
                break
    stale_mode_pending = pending_lower.startswith("mode reconcile queued after control connection failure")
    if stale_mode_pending:
        clear_node_sync_pending(db, node.id)
        pending_reason = ""
        pending_lower = ""
    pending_channel_ids = node_channel_sync_pending_ids(db, node.id, limit=500)
    pending_logo_ids = node_channel_logo_pending_ids(db, node.id, limit=500)
    full_reconcile_requested = bool(
        pending_reason and (
            pending_lower.startswith("manual test & sync queued")
            or pending_lower.startswith("manual test & sync retry:")
            or pending_lower.startswith("full reconcile retry:")
        )
    )
    targeted_retry_requested = bool(pending_channel_ids or pending_logo_ids) and not full_reconcile_requested
    full_reconcile_claimed = bool(
        full_reconcile_requested and _claim_node_reconcile(int(node.id))
    )
    targeted_retry_claimed = bool(
        targeted_retry_requested and _claim_node_targeted_retry(int(node.id))
    )
    heartbeat_metrics = {
        "cpu_percent": request.headers.get("x-node-cpu-percent"),
        "memory_percent": request.headers.get("x-node-memory-percent"),
        "network_download_mbps": request.headers.get("x-node-download-mbps"),
        "network_upload_mbps": request.headers.get("x-node-upload-mbps"),
        "uptime_seconds": request.headers.get("x-node-uptime-seconds"),
    }
    cache_node_metrics(node.id, heartbeat_metrics)
    active_connections_hint = max(0, int(request.headers.get("x-node-active-connections") or 0))
    cache_node_live_summary(node.id, {
        "up": request.headers.get("x-node-up-channels"),
        "waiting": request.headers.get("x-node-waiting-channels"),
        "down": request.headers.get("x-node-down-channels"),
        "active_connections": active_connections_hint,
    })
    node.status = "online"
    node.last_seen_at = datetime.now(timezone.utc)
    node.last_error = None
    if full_reconcile_requested and full_reconcile_claimed:
        # Explicit full work owns the legacy key while it runs; failures restore
        # it with the Full reconcile retry prefix.
        clear_node_sync_pending(db, node.id)
    elif legacy_target_pending_migrated:
        # The exact config/logo queue now owns this retry; remove the migrated
        # legacy generic key so only explicit Manual/Full reconcile may remain
        # under node_<id>_sync_pending.
        clear_node_sync_pending(db, node.id)
    db.commit()
    if full_reconcile_claimed:
        background_tasks.add_task(
            sync_node_registries_background,
            int(node.id),
            main_panel_public_base(db, request),
        )
    elif targeted_retry_claimed:
        background_tasks.add_task(sync_node_pending_channels_background, int(node.id))
    # Keep detailed direct-session rows warm independently of the browser.
    # This is a cheap scheduler call; Remote HTTP runs on the dedicated executor.
    request_remote_viewer_node_refresh(int(node.id), active_hint=active_connections_hint)
    return {
        "ok": True,
        "node": node.slug,
        "node_name": node.name,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "viewer_sessions": central_viewer_session_details(db, node_id=node.id, include_geo=False),
        "viewer_ttl_seconds": viewer_tracker.ttl_seconds,
        "panel_url": main_panel_public_base(db, request),
    }


@app.get("/nodes/{node_id}/status.json", dependencies=[Depends(permission_required("nodes.view"))])
def node_status_json(node_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401)
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404)

    if node.node_type == "local":
        result = {"ok": True, "status": "online", "version": APP_VERSION, "metrics": system_metrics.snapshot()}
    else:
        stored_version = str(getattr(node, "agent_version", None) or "unknown")
        # STREAMFORGE_NODE_STATUS_HEARTBEAT_FIRST_V60R2:
        # The authenticated Node heartbeat already gives Main fresh liveness and
        # metrics. Do not make every browser card perform another Remote Node
        # HTTP request while Test & Sync or a large catalogue reconciliation is
        # running. This removes false "Node status request timed out" cards and
        # keeps the Nodes page independent from slow Node control requests.
        # STREAMFORGE_HEARTBEAT_AUTHORITATIVE_LIVENESS_V1132:
        # Match the controller's 90s heartbeat grace. A reverse control timeout
        # must not force the Nodes card onto a direct-probe/offline path while a
        # recent authenticated heartbeat still proves liveness.
        recently_seen = node_controller.node_liveness_recent(node)
        if recently_seen:
            live_summary = cached_node_live_summary(node.id)
            if live_summary:
                result = {
                    "ok": True,
                    "status": "online",
                    "version": stored_version,
                    "metrics": cached_node_metrics(node.id),
                    "heartbeat_cached": True,
                    "up_channels": int(live_summary.get("up", 0)),
                    "waiting_channels": int(live_summary.get("waiting", 0)),
                    "down_channels": int(live_summary.get("down", 0)),
                    "active_connections": int(live_summary.get("active_connections", 0)),
                    "viewer_stats": {"total_users": int(live_summary.get("active_connections", 0))},
                }
            else:
                # Main may have restarted after the Node's last persisted
                # heartbeat, leaving only the in-memory live-summary cache
                # empty.  Seed it once from the lightweight quick endpoint.
                try:
                    db.commit()
                    result = node_controller.quick_status(node, refresh=False)
                    cache_node_metrics(node.id, result.get("metrics"))
                    cache_node_live_summary(node.id, {
                        "up": result.get("up_channels"),
                        "waiting": result.get("waiting_channels"),
                        "down": result.get("down_channels"),
                        "active_connections": (result.get("viewer_stats") or {}).get("total_users", result.get("active_connections")),
                    })
                    result["heartbeat_cache_seeded"] = True
                except NodeError as exc:
                    db.rollback()
                    result = {
                        "ok": True,
                        "status": "online",
                        "version": stored_version,
                        "metrics": cached_node_metrics(node.id),
                        "heartbeat_cached": True,
                        "live_summary_pending": True,
                        "error": str(exc),
                    }
        else:
            try:
                # STREAMFORGE_NODE_STATUS_RELEASE_DB_V32:
                # Only stale heartbeats fall back to a bounded direct probe.
                db.commit()
                result = node_controller.quick_status(node, refresh=False)
                cache_node_metrics(node.id, result.get("metrics"))
                cache_node_live_summary(node.id, {
                    "up": result.get("up_channels"),
                    "waiting": result.get("waiting_channels"),
                    "down": result.get("down_channels"),
                    "active_connections": (result.get("viewer_stats") or {}).get("total_users", result.get("active_connections")),
                })
                connected_base_url = str(result.pop("_connected_base_url", "") or "").strip().rstrip("/")
                reported_version = str(result.get("version") or stored_version or "unknown")
                if reported_version and reported_version != "unknown" and reported_version != stored_version:
                    node.agent_version = reported_version[:64]
                try:
                    reported_control_port = int(result.get("control_port") or 0)
                except (TypeError, ValueError):
                    reported_control_port = 0
                if reported_control_port:
                    resolved_control_port = normalized_native_control_port(node.agent_port, reported_control_port)
                    if int(node.agent_port or 0) != resolved_control_port:
                        node.agent_port = resolved_control_port
                node.status = "online"
                node.last_seen_at = datetime.now(timezone.utc)
                node.last_error = None
                db.commit()
                desired_panel_urls = [
                    item.strip().rstrip("/")
                    for item in str(node.api_urls or node.api_url or "").splitlines()
                    if item.strip()
                ]
                reported_panel_urls = [
                    str(item or "").strip().rstrip("/")
                    for item in (result.get("panel_urls") or [])
                    if str(item or "").strip()
                ]
                desired_stream_urls = [
                    item.strip().rstrip("/")
                    for item in str(node.playlist_urls or node.playlist_url or "").splitlines()
                    if item.strip()
                ]
                reported_stream_urls = [
                    str(item or "").strip().rstrip("/")
                    for item in (result.get("stream_urls") or [])
                    if str(item or "").strip()
                ]
                if desired_panel_urls and reported_panel_urls != desired_panel_urls:
                    result["panel_access_sync_pending"] = True
                if desired_stream_urls and reported_stream_urls != desired_stream_urls:
                    result["stream_access_sync_pending"] = True
                if connected_base_url:
                    result["connected_base_url"] = connected_base_url
            except NodeError as exc:
                db.rollback()
                result = {
                    "ok": False,
                    "status": "offline",
                    "version": stored_version,
                    "metrics": cached_node_metrics(node.id),
                    "quick_status_pending": True,
                    "error": str(exc),
                }

    direct_total = int(((result.get("viewer_stats") or {}).get("total_users") or result.get("active_connections") or 0))
    if node.node_type == "local":
        central_total = sum(1 for item in viewer_tracker.snapshot().sessions if item.node_id == node.id)
        result["online_users"] = central_total
    else:
        result["online_users"] = direct_total
    counts = fast_node_channel_counts(db).get(node.id, {"up": 0, "waiting": 0, "down": 0, "total": 0})
    if node.node_type == "local":
        counts = main_local_delivery_counts(node)
    else:
        counts["up"] = int(result.get("up_channels") if result.get("up_channels") is not None else counts.get("up") or 0)
        counts["waiting"] = int(result.get("waiting_channels") if result.get("waiting_channels") is not None else counts.get("waiting") or 0)
        counts["down"] = int(result.get("down_channels") if result.get("down_channels") is not None else counts.get("down") or 0)
    result["channel_counts"] = node_card_display_counts(counts)
    result["version"] = APP_VERSION if node.node_type == "local" else str(result.get("version") or getattr(node, "agent_version", None) or "unknown")
    result["total_max_connections"] = int(getattr(node, "total_max_connections", 0) or 0)
    return result


@app.get("/nodes/{node_id}/update", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.update"))])
def node_update_page(node_id: int, request: Request, error: str = "", message: str = "", db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node or node.node_type == "local":
        raise HTTPException(404)
    parsed = urlsplit(node.api_url or "")
    return render(
        request,
        "node_update.html",
        db,
        node=node,
        error=error,
        message=message,
        ssh_host=node.ssh_host or parsed.hostname or "",
        ssh_port=node.ssh_port or 22,
        ssh_user=node.ssh_user or "root",
        ssh_password_saved=bool(node.ssh_password_enc),
        agent_port=normalized_native_control_port(node.agent_port),
        update_job=dict(_NODE_UPDATE_JOBS.get(node.id) or {}),
    )


def node_update_redirect(node_id: int, *, error: str = "", message: str = "") -> RedirectResponse:
    values: dict[str, str] = {}
    if error:
        values["error"] = error
    if message:
        values["message"] = message
    suffix = f"?{urlencode(values)}" if values else ""
    return RedirectResponse(f"/nodes/{node_id}/update{suffix}", status_code=303)


def _set_node_update_job(node_id: int, **values: object) -> None:
    with _NODE_UPDATE_LOCK:
        current = dict(_NODE_UPDATE_JOBS.get(node_id) or {})
        current.update(values)
        current.setdefault("progress", 0)
        current.setdefault("phase", str(current.get("state") or "idle"))
        current.setdefault("logs", [])
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        _NODE_UPDATE_JOBS[node_id] = current


def _append_node_update_log(node_id: int, line: object) -> None:
    cleaned = str(line or "").strip()
    if not cleaned:
        return
    with _NODE_UPDATE_LOCK:
        current = dict(_NODE_UPDATE_JOBS.get(node_id) or {})
        logs = [str(item) for item in (current.get("logs") or []) if str(item).strip()]
        for item in cleaned.replace("\r", "\n").split("\n"):
            item = item.strip()
            if item and (not logs or logs[-1] != item):
                logs.append(item[-900:])
        current["logs"] = logs[-120:]
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        _NODE_UPDATE_JOBS[node_id] = current


def _node_update_progress(node_id: int, phase: str, message: str, progress: int, detail: str = "") -> None:
    _set_node_update_job(
        node_id,
        state="running",
        phase=str(phase or "running"),
        message=str(message or "Node update is running"),
        progress=max(0, min(99, int(progress or 0))),
        error="",
    )
    if detail:
        _append_node_update_log(node_id, detail)


def _sync_node_after_update(node_id: int, panel_url: str) -> tuple[list[str], list[str]]:
    """Reconcile one updated Node without occupying Main Web/DB hot paths.

    STREAMFORGE_NODE_UPDATE_MAIN_WEB_ISOLATION_V57: older builds kept one Main
    SQLite Session checked out across restart sleeps, health network waits and
    the complete channel sync. The update already runs in a background thread,
    so keep every DB interaction short as well. This prevents Node maintenance
    from delaying Main Web Player/Channels requests.
    """
    messages: list[str] = []
    errors: list[str] = []

    def current_node() -> Node:
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            if not node:
                raise RuntimeError("Node record was deleted while the update was running")
            # expire_on_commit=False leaves scalar connection settings usable
            # after this very short Session is closed.
            return node

    with SessionLocal() as db:
        node = db.get(Node, node_id)
        if not node:
            raise RuntimeError("Node record was deleted while the update was running")
        node.status = "updating"
        node.last_error = None
        db.commit()

    _node_update_progress(node_id, "restarting", "Waiting for the updated Node Agent to restart", 82)
    healthy = False
    last_health_error = ""
    for index, delay in enumerate((2, 3, 5, 8, 12), 1):
        time.sleep(delay)
        node = current_node()
        try:
            health = node_controller.health(node, refresh=True)
            running_version = str(health.get("version") or "unknown")
            _append_node_update_log(node_id, f"Health check {index}: Node Agent v{running_version} is online")
            with SessionLocal() as db:
                live_node = db.get(Node, node_id)
                if live_node:
                    if running_version and running_version != "unknown":
                        live_node.agent_version = running_version[:64]
                    if running_version == APP_VERSION:
                        live_node.status = "online"
                        live_node.last_seen_at = datetime.now(timezone.utc)
                        live_node.last_error = None
                    db.commit()
            if running_version == APP_VERSION:
                healthy = True
                break
            last_health_error = f"Node returned version {running_version}"
        except NodeError as exc:
            last_health_error = str(exc)
            _append_node_update_log(node_id, f"Health check {index}: {exc}")
    if not healthy and last_health_error:
        errors.append(f"health: {last_health_error}")

    _node_update_progress(node_id, "syncing", "Synchronizing Node settings and catalogue", 91)
    node = current_node()
    if bool(getattr(node, "sync_main_users", False)):
        messages.append("independent access policy preserved")
    else:
        try:
            with SessionLocal() as db:
                live_node = db.get(Node, node_id)
                if not live_node:
                    raise RuntimeError("Node record was deleted while the update was running")
                web_settings = webplayer_settings_for_node(db, int(live_node.id))
            node_controller.sync_access_settings(
                node,
                panel_url,
                webplayer_settings=web_settings,
            )
            messages.append("access policy + Web Player theme synced")
            _append_node_update_log(node_id, "Access policy synchronized")
        except NodeError as exc:
            errors.append(f"access policy: {exc}")
    try:
        result_users = node_controller.sync_stream_users(node, panel_url)
        messages.append(f"{int(result_users.get('users') or 0)} playlist user(s) synced")
    except NodeError as exc:
        errors.append(f"playlist users: {exc}")
    try:
        result_panel = node_controller.sync_panel_users(node, panel_url)
        messages.append(f"{int(result_panel.get('users') or 0)} panel user(s) synced")
    except NodeError as exc:
        errors.append(f"panel users: {exc}")
    # STREAMFORGE_NODE_UPDATE_LIGHTWEIGHT_CATALOG_SYNC_V57: the Node installer
    # preserves channel logo storage, so an update only needs config/catalogue
    # reconciliation. This avoids a large burst of redundant logo uploads from
    # Main while local HLS/Web Player clients are active.
    channel_errors = node_controller.sync_node_channels(node, sync_logos=False)
    errors.extend(f"channel: {item}" for item in channel_errors)
    if not channel_errors:
        _append_node_update_log(node_id, "Channel catalogue synchronized")

    with SessionLocal() as db:
        live_node = db.get(Node, node_id)
        if live_node:
            if healthy:
                live_node.status = "online"
                live_node.last_seen_at = datetime.now(timezone.utc)
                live_node.last_error = None
            elif live_node.status == "updating":
                live_node.status = "unknown"
            db.commit()
    return messages, errors


# STREAMFORGE_NODE_UPDATE_CONCISE_ERROR_V65R3: the status card is a summary;
# complete SSH/API output belongs only in the collapsible live log.
def _node_update_error_summary(exc: Exception) -> str:
    text = str(exc or "").replace("\r", "\n").strip()
    first = next((line.strip() for line in text.splitlines() if line.strip()), "Node update failed")
    if len(first) > 240:
        first = first[:237].rstrip() + "..."
    if "Remote installer exited with code" in first:
        return f"{first} See Live update log for details."
    return first


def _run_node_ssh_update(node_id: int, options: dict[str, object]) -> None:
    _set_node_update_job(
        node_id,
        state="running",
        phase="connecting",
        progress=4,
        message="Starting SSH Node update",
        error="",
        logs=[f"Target version: {APP_VERSION}", "SSH update job started"],
        target_version=APP_VERSION,
        method="ssh",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        result = install_node_over_ssh(
            app_root=APP_ROOT,
            host=str(options["host"]),
            port=int(options["port"]),
            username=str(options["username"]),
            password=str(options["password"]),
            agent_port=int(options["agent_port"]),
            public_port=int(options["public_port"]),
            token=str(options["token"]),
            dns_name=str(options.get("dns_name") or "") or None,
            dns_only=bool(options.get("dns_only")),
            playlist_host=str(options.get("playlist_host") or "") or None,
            playlist_dns_only=bool(options.get("playlist_dns_only")),
            panel_url=str(options.get("panel_url") or "") or None,
            node_slug=str(options.get("node_slug") or "") or None,
            progress=lambda phase, message, percent, detail: _node_update_progress(node_id, phase, message, percent, detail),
        )
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            if not node:
                raise RuntimeError("Node record was deleted while the update was running")
            node.api_url = result.api_url
            node.status = "unknown"
            node.last_error = None
            db.commit()
        _append_node_update_log(node_id, f"SSH host fingerprint: {result.host_key_fingerprint}")
        messages, errors = _sync_node_after_update(node_id, str(options.get("panel_url") or ""))
        message = f"Node updated over SSH. Host fingerprint: {result.host_key_fingerprint}."
        if messages:
            message += " " + "; ".join(messages) + "."
        _set_node_update_job(
            node_id,
            state="done" if not errors else "warning",
            phase="done",
            progress=100,
            message=message,
            error="; ".join(errors),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        _append_node_update_log(node_id, "Update completed" if not errors else "Update completed with warnings")
        log_event("Node SSH update completed", scope="update", node_id=node_id, details={"messages": messages, "errors": errors})
    except Exception as exc:
        summary = _node_update_error_summary(exc)
        _set_node_update_job(
            node_id,
            state="error",
            phase="error",
            progress=100,
            message="Node SSH update failed",
            error=summary,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        # The remote output was already streamed into the live log by the SSH
        # progress callback.  Append only the concise terminal error so the log
        # does not repeat the same multi-kilobyte tail a second time.
        _append_node_update_log(node_id, f"ERROR: {summary}")
        log_event("Node SSH update failed", scope="update", level="error", node_id=node_id, details=str(exc))


def _run_node_api_update(node_id: int, panel_url: str) -> None:
    _set_node_update_job(
        node_id,
        state="running",
        phase="connecting",
        progress=8,
        message="Connecting to the Node API",
        error="",
        logs=[f"Target version: {APP_VERSION}", "API update job started"],
        target_version=APP_VERSION,
        method="api",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        # Moving an already-installed systemd service from legacy 8810 to the
        # privileged default port 80 requires rewriting its environment/unit
        # and restarting it. Prefer saved SSH automatically when available;
        # an API-only code upload cannot change /etc/streamforge-node.env.
        migration_options: dict[str, object] | None = None
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            if not node:
                raise RuntimeError("Node not found")
            saved_password = decrypt_secret(node.ssh_password_enc) if node.ssh_password_enc else ""
            # v1.11.125 changes the root-owned systemd ExecStart to
            # /usr/bin/gunicorn. An unprivileged Node API upload cannot modify
            # that unit, so use the saved SSH path automatically once.
            # STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1030: v10.30 carries the
            # root-owned TLS/Certbot repair, so a saved SSH credential must win over API-only update.
            # STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1031: v10.31 preserves that root-required repair.
            # STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1032: v10.32 repairs the Node Nginx HTTP front before TLS reconciliation.
            # STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1033: v10.33 repairs managed TLS on Nginx builds older than 1.19.4.
            # STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1034: v10.34 repairs private Nginx media-auth redirects and must deploy the Node root/TLS stack over saved SSH.
            if APP_VERSION in {"1.11.127", "1.11.128", "1.11.129", "2.0.0", "2.0.1", "2.0.2", "2.0.3", "2.0.4", "2.0.5", "2.0.6", "2.0.7", "2.0.8", "2.0.9", "2.0.10", "2.0.11", "2.0.12", "2.0.13", "2.0.14", "2.0.15", "2.0.16", "2.1.0", "2.1.1", "2.1.2", "2.1.3", "2.1.4", "2.1.5", "2.1.6", "2.1.7", "2.1.8", "2.1.9", "2.1.10", "2.1.11", "2.1.12", "2.1.13", "2.1.14", "2.1.15", "2.1.16", "2.1.17", "2.1.18", "2.1.19", "2.1.20", "2.1.21", "2.1.22", "2.1.23", "2.1.24", "2.1.25", "2.1.26", "2.1.27", "2.1.28", "2.1.29", "2.1.30", "2.1.31", "2.1.32", "2.1.33", "2.1.34", "2.1.35", "2.1.36", "2.1.37", "2.1.38", "2.1.39", "2.1.40", "2.1.41", "2.1.42", "2.1.43", "2.1.44", "2.1.45", "2.1.46", "2.1.47", "2.1.48", "2.1.49", "2.1.50", "2.1.51", "2.1.52", "2.1.53", "2.1.54", "2.1.55", "2.1.56", "2.1.57", "2.1.58", "2.1.59", "2.1.60", "2.1.61", "2.1.62", "2.1.63", "2.1.64", "2.1.65", "2.1.66", "2.1.67", "2.1.68", "2.1.69", "2.1.70", "2.1.71", "2.1.72", "2.1.73", "2.1.74", "2.1.79", "2.1.80", "2.1.81", "2.1.82", "2.1.83", "2.1.84", "2.1.85", "2.1.86", "2.1.87", "2.1.88", "2.1.89", "2.1.90", "2.1.91", "2.1.92", "2.1.93", "2.1.94", "2.1.95", "2.1.96", "2.1.97", "2.1.98", "2.1.99", "2.1.100", "2.1.101", "2.1.102", "2.1.103", "2.1.104", "2.1.105", "2.1.106", "2.1.107", "2.1.108", "2.1.109", "2.1.110", "2.1.111", "2.1.112", "2.1.113", "2.1.114", "2.1.115", "2.1.116", "2.1.117", "2.1.118", "2.1.119", "2.1.120", "2.1.121", "2.1.122", "2.1.123", "2.1.124", "2.1.125", "2.1.126", "2.1.127", "2.1.128", "2.1.129", "2.1.130", "2.1.131", "2.1.132", "2.1.133", "2.1.134", "2.1.135", "2.1.136", "2.1.137", "2.1.138", "2.1.139", "2.1.140", "2.1.141", "2.1.142", "2.1.143", "2.1.144", "2.1.145", "2.1.146", "2.1.147", "2.1.148", "2.1.149", "2.1.150", "2.1.151", "2.1.152", "2.1.153", "2.1.154", "2.1.155", "2.1.156", "2.1.157", "2.1.158", "2.1.159", "2.1.160", "2.1.161", "2.1.162", "2.1.163", "2.1.164", "2.1.165", "2.1.166", "2.1.167", "2.1.168", "2.1.169", "2.1.170", "2.1.171", "2.1.172", "2.1.173", "2.1.174", "2.1.175", "2.1.176", "2.1.177", "2.1.178", "2.1.179", "2.1.180", "2.1.181", "2.1.182", "2.1.183", "2.1.184", "2.1.185", "2.1.186", "2.1.187", "2.1.188", "2.1.189", "2.1.190", "2.1.191", "2.1.192", "2.1.193", "2.1.194", "2.1.195", "2.1.196", "2.1.197", "2.1.198", "2.1.199", "2.1.200", "2.1.201", "2.1.202", "2.1.203", "2.1.204", "2.1.205", "2.1.206", "2.1.208", "2.1.209", "2.1.210", "2.1.211", "2.1.212", "2.1.213", "2.1.214", "2.1.215", "2.1.216", "2.1.217", "2.1.218", "2.1.219", "2.1.220", "2.1.221", "2.1.222", "2.1.223", "2.1.224", "2.1.225", "2.1.226", "2.1.227", "2.1.228", "2.1.229", "2.1.231", "2.1.232", "2.1.233", "2.1.234", "2.1.235", "2.1.236", "2.1.237", "2.1.238", "2.1.239", "2.1.240", "2.1.241", "2.1.242", "2.1.243", "2.1.244", "2.1.245", "2.1.246", "2.1.247", "2.1.248", "2.1.249", "2.1.250", "2.1.251", "2.1.252", "2.1.253", "2.1.254", "2.1.255", "2.1.256", "2.1.257", "2.1.258", "2.1.259", "2.1.260", "2.1.261", "2.1.262", "2.1.263", "2.1.264", "2.1.265", "2.1.266", "2.1.267", "2.1.268", "2.1.269", "2.1.270", "2.1.271", "2.1.272", "2.1.273", "2.1.274", "2.1.275", "2.1.276", "2.1.277", "2.1.278", "2.1.279", "2.1.280", "2.1.281", "2.1.282", "2.1.283", "2.1.284", "2.1.285", "2.1.286", "2.1.287", "2.1.288", "2.1.289", "2.1.290", "2.1.291", "2.1.292", "2.1.293", "2.1.294", "2.1.295", "2.1.296", "2.1.297", "2.1.298", "2.1.299", "2.1.300", "2.1.301", "2.1.302", "2.1.303", "2.1.304", "2.1.305", "2.1.306", "2.1.307", "3.0", "3.0.1", "3.0.2", "3.0.3", "3.0.4", "3.0.5", "3.0.6", "3.0.7", "3.0.8", "3.0.9", "3.0.10", "3.0.11", "3.0.12", "3.0.13", "3.0.14", "3.0.15", "3.0.16", "3.0.17", "3.0.18", "3.0.19", "3.0.20", "3.0.21", "3.0.22", "3.0.23", "3.0.24", "3.0.25", "3.0.26", "3.0.27", "3.0.28", "3.0.29", "3.0.30", "3.0.31", "3.0.32", "3.0.33", "3.0.34", "3.0.35", "3.0.36", "3.0.37", "3.0.38", "3.0.39", "3.0.40", "3.0.41", "3.0.42", "3.0.43", "3.0.44", "3.0.45", "3.0.46", "3.0.47", "3.0.48", "3.0.49", "3.0.50", "3.0.51", "3.0.52", "3.0.53", "3.0.54", "3.0.55", "3.0.56", "3.0.57", "3.0.58", "3.0.59", "3.0.60", "3.2", "3.9", "4.0", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "5.0", "5.1", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "6.0", "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "7.0", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "9.0", "9.1", "9.2", "9.3", "9.4", "10.28", "10.29", "10.30", "10.31", "10.32", "10.33", "10.34", "12.3"} and saved_password and node.ssh_host and node.ssh_user:
                playlist_host = urlsplit((node.playlist_url or "").strip()).hostname or node.dns_name or ""
                migration_options = {
                    "host": node.ssh_host,
                    "port": node.ssh_port or 22,
                    "username": node.ssh_user,
                    "password": saved_password,
                    "agent_port": node.agent_port or 80,
                    "public_port": node.playlist_port or 80,
                    "token": node.api_token,
                    "dns_name": node.dns_name or "",
                    "dns_only": bool(node.dns_only),
                    "playlist_host": playlist_host,
                    "playlist_dns_only": bool(node.playlist_dns_only),
                    "panel_url": panel_url,
                    "node_slug": node.slug,
                }
            needs_port80_migration = normalized_native_control_port(node.agent_port) == 80
            if needs_port80_migration:
                try:
                    health = node_controller.health(node, refresh=True)
                    reported_port = int(health.get("control_port") or 0)
                    connected = urlsplit(str(health.get("_connected_base_url") or ""))
                    connected_port = int(connected.port or (80 if connected.scheme == "http" else 443 if connected.scheme == "https" else 0))
                    needs_port80_migration = reported_port != 80 or connected_port != 80
                except (NodeError, TypeError, ValueError):
                    needs_port80_migration = True
            if migration_options is None and needs_port80_migration and saved_password and node.ssh_host and node.ssh_user:
                playlist_host = urlsplit((node.playlist_url or "").strip()).hostname or node.dns_name or ""
                migration_options = {
                    "host": node.ssh_host,
                    "port": node.ssh_port or 22,
                    "username": node.ssh_user,
                    "password": saved_password,
                    "agent_port": 80,
                    "public_port": node.playlist_port or 80,
                    "token": node.api_token,
                    "dns_name": node.dns_name or "",
                    "dns_only": bool(node.dns_only),
                    "playlist_host": playlist_host,
                    "playlist_dns_only": bool(node.playlist_dns_only),
                    "panel_url": panel_url,
                    "node_slug": node.slug,
                }
        if migration_options:
            if APP_VERSION == "6.5":
                _append_node_update_log(node_id, "Using saved SSH to install v6.5 Node Redis, public-worker, and Nginx media fast-path components")
            else:
                _append_node_update_log(node_id, "Using saved SSH to install the version-required Node system runtime")
            _run_node_ssh_update(node_id, migration_options)
            return
        if APP_VERSION == "6.5":
            raise RuntimeError(
                "v6.5 Node high-concurrency upgrade requires saved SSH credentials (or one manual Node installer run) "
                "because Redis, the public worker pool, and Nginx media fast-path require root-owned system changes."
            )
        if APP_VERSION in {"10.28", "10.29", "10.30", "10.31", "10.32", "10.33", "10.34"}:
            raise RuntimeError(
                f"v{APP_VERSION} Node HTTPS/Certbot repair requires one saved-SSH or manual root Node update "
                "because the root TLS helper and Python dependency compatibility must be repaired together."
            )
        if APP_VERSION == "12.3":
            raise RuntimeError(
                "v12.3 Node runtime consistency update requires saved SSH credentials (or one manual Node installer run) "
                "because public_start.py worker sizing is owned by the systemd Node runtime."
            )
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            if not node:
                raise RuntimeError("Node not found")
            _node_update_progress(node_id, "uploading", "Uploading Node Agent package through the Node API", 28)
            result = node_controller.update_agent(node, APP_ROOT, APP_VERSION)
            _append_node_update_log(node_id, f"Node API accepted package: {result}")
        _node_update_progress(node_id, "installing", "Node accepted the package and is installing it", 63)
        messages, errors = _sync_node_after_update(node_id, panel_url)
        with SessionLocal() as db:
            current = db.get(Node, node_id)
            if current and normalized_native_control_port(current.agent_port) == 80 and not current.ssh_password_enc and errors:
                errors.append("port 80 requires one SSH update to install/restart the low-port systemd listener")
        message = "Node updated through the Node API."
        if messages:
            message += " " + "; ".join(messages) + "."
        _set_node_update_job(
            node_id,
            state="done" if not errors else "warning",
            phase="done",
            progress=100,
            message=message,
            error="; ".join(errors),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        _append_node_update_log(node_id, "API update completed" if not errors else "API update completed with warnings")
        log_event("Node API update completed", scope="node", node_id=node_id, details={"messages": messages, "errors": errors})
    except Exception as exc:
        _append_node_update_log(node_id, f"API update failed: {exc}")
        # First-upgrade fallback: reuse the saved SSH credential if available.
        with SessionLocal() as db:
            node = db.get(Node, node_id)
            saved_password = decrypt_secret(node.ssh_password_enc) if node and node.ssh_password_enc else ""
            if node and saved_password and node.ssh_host and node.ssh_user:
                playlist_host = urlsplit((node.playlist_url or "").strip()).hostname or node.dns_name or ""
                options = {
                    "host": node.ssh_host, "port": node.ssh_port or 22,
                    "username": node.ssh_user, "password": saved_password,
                    "agent_port": node.agent_port or 80,
                    "public_port": node.playlist_port or 80,
                    "token": node.api_token, "dns_name": node.dns_name or "",
                    "dns_only": bool(node.dns_only), "playlist_host": playlist_host,
                    "playlist_dns_only": bool(node.playlist_dns_only),
                    "panel_url": panel_url, "node_slug": node.slug,
                }
            else:
                options = None
        if options:
            _append_node_update_log(node_id, "Falling back to the saved SSH connection")
            _run_node_ssh_update(node_id, options)
            return
        port80_hint = ""
        if node and normalized_native_control_port(getattr(node, "agent_port", 0)) == 80:
            port80_hint = "; use SSH update once to install the port-80 systemd listener"
        _set_node_update_job(
            node_id,
            state="error",
            phase="error",
            progress=100,
            message="Node API update failed",
            error=str(exc) + port80_hint,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        log_event("Node API update failed", scope="node", level="error", node_id=node_id, details=str(exc))


def _node_agent_supports_seamless_code_update(version: str | None) -> bool:
    # STREAMFORGE_MAIN_NODE_SEAMLESS_UPDATE_LEASE_V1083:
    # v10.83+ Remote Nodes preserve/adopt FFmpeg across API control-worker
    # reloads, so Main must not take an otherwise healthy Node out of playback
    # rotation merely because its code package is being refreshed.
    parts = [int(item) for item in re.findall(r"\d+", str(version or ""))[:3]]
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:2]) >= (10, 83)


def _start_node_update_job(node_id: int, *, method: str, options: dict[str, object] | None = None, panel_url: str = "") -> bool:
    with _NODE_UPDATE_LOCK:
        existing = _NODE_UPDATE_JOBS.get(node_id) or {}
        if existing.get("state") in {"queued", "running"}:
            return False
        _NODE_UPDATE_JOBS[node_id] = {
            "state": "queued",
            "phase": "queued",
            "progress": 1,
            "message": f"{method.upper()} Node update queued",
            "error": "",
            "logs": [f"Queued {method.upper()} update to v{APP_VERSION}"],
            "method": method,
            "target_version": APP_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    if method == "ssh":
        target = _run_node_ssh_update
        args = (node_id, dict(options or {}))
    else:
        target = _run_node_api_update
        args = (node_id, panel_url)

    # STREAMFORGE_NODE_UPDATE_MAINTENANCE_LEASE_V57: pre-v10.83 API updates
    # and every SSH update can still restart the Node service/encoders, so keep
    # the historical maintenance lease for those paths.  v10.83+ API updates
    # preserve/adopt FFmpeg and stay in playback rotation during the code reload.
    seamless_api_update = False
    with SessionLocal() as db:
        updating_node = db.get(Node, node_id)
        if updating_node:
            seamless_api_update = bool(
                method == "api" and _node_agent_supports_seamless_code_update(updating_node.agent_version)
            )
            updating_node.last_error = None
            if not seamless_api_update:
                updating_node.status = "updating"
            db.commit()
    if not seamless_api_update:
        set_node_maintenance(node_id, True, ttl_seconds=1800)
    else:
        _append_node_update_log(node_id, "Seamless API update: playback routing remains online")

    def run_isolated() -> None:
        try:
            target(*args)
        finally:
            set_node_maintenance(node_id, False)
            with SessionLocal() as db:
                finished_node = db.get(Node, node_id)
                if finished_node and str(finished_node.status or "").strip().lower() == "updating":
                    finished_node.status = "unknown"
                    db.commit()

    threading.Thread(target=run_isolated, daemon=True, name=f"node-{method}-update-{node_id}").start()
    return True


def _start_node_ssh_update(node_id: int, options: dict[str, object]) -> bool:
    return _start_node_update_job(node_id, method="ssh", options=options)


@app.get("/nodes/{node_id}/update/status", dependencies=[Depends(permission_required("nodes.update"))])
def node_update_status(node_id: int, request: Request, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node or node.node_type == "local":
        raise HTTPException(404)
    with _NODE_UPDATE_LOCK:
        job = dict(_NODE_UPDATE_JOBS.get(node_id) or {
            "state": "idle", "phase": "idle", "progress": 0,
            "message": "No update is running.", "error": "", "logs": [],
            "target_version": APP_VERSION,
        })
    return JSONResponse(job, headers={"Cache-Control": "no-store"})


@app.post("/nodes/{node_id}/update/api", dependencies=[Depends(permission_required("nodes.update"))])
def node_update_via_api(node_id: int, request: Request, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node or node.node_type == "local":
        raise HTTPException(404)
    if not _start_node_update_job(node.id, method="api", panel_url=main_panel_public_base(db, request)):
        return node_update_redirect(node_id, error="A Node update is already running.")
    log_event("Node API update queued", scope="node", node_id=node.id, actor=current_admin(request, db).username if current_admin(request, db) else None)
    return node_update_redirect(node_id, message="API update started. Live progress is shown above.")


@app.post("/nodes/{node_id}/update/ssh", dependencies=[Depends(permission_required("nodes.update"))])
def node_update_via_ssh(
    node_id: int,
    request: Request,
    ssh_host: str = Form(""),
    ssh_port: int = Form(22),
    ssh_user: str = Form("root"),
    ssh_password: str = Form(""),
    use_saved_password: Optional[str] = Form(None),
    save_ssh_password: Optional[str] = Form(None),
    agent_port: int = Form(80),
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if not node or node.node_type == "local" or not node.api_token:
        raise HTTPException(404)
    resolved_password = decrypt_secret(node.ssh_password_enc) if as_bool(use_saved_password) else ssh_password
    if not resolved_password:
        return node_update_redirect(node_id, error="Enter an SSH password or select the saved password option.")
    host_value = ssh_host.strip() or node.ssh_host or ""
    user_value = ssh_user.strip() or node.ssh_user or "root"
    node.ssh_host = host_value
    node.ssh_port = ssh_port or node.ssh_port or 22
    node.ssh_user = user_value
    node.agent_port = agent_port or node.agent_port or 80
    if as_bool(save_ssh_password) and ssh_password:
        node.ssh_password_enc = encrypt_secret(ssh_password)
    db.commit()
    playlist_host = urlsplit((node.playlist_url or "").strip()).hostname or node.dns_name or ""
    options: dict[str, object] = {
        "host": host_value,
        "port": node.ssh_port or 22,
        "username": user_value,
        "password": resolved_password,
        "agent_port": node.agent_port or 80,
        "public_port": node.playlist_port or 80,
        "token": node.api_token,
        "dns_name": node.dns_name or "",
        "dns_only": bool(node.dns_only),
        "playlist_host": playlist_host,
        "playlist_dns_only": bool(node.playlist_dns_only),
        "panel_url": main_panel_public_base(db, request),
        "node_slug": node.slug,
    }
    if not _start_node_ssh_update(node.id, options):
        return node_update_redirect(node_id, error="A Node update is already running.")
    return node_update_redirect(node_id, message="SSH update started in the background. This page will show completion or errors without causing an Nginx 502 timeout.")


@app.post("/nodes/{node_id}/delete", dependencies=[Depends(permission_required("nodes.delete"))])
def node_delete(node_id: int, request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    node = db.get(Node, node_id)
    if not node or node.node_type == "local":
        raise HTTPException(404)

    # STREAMFORGE_OFFLINE_NODE_LOCAL_DELETE_V3043:
    # Deleting a Node is first and foremost removal of its Main registration.
    # Remote cleanup is best-effort: an offline/dead Node or missing saved SSH
    # credentials must never make the database record impossible to remove.
    saved_password = decrypt_secret(node.ssh_password_enc) if node.ssh_password_enc else ""
    ssh_host = (node.ssh_host or "").strip()
    ssh_user = (node.ssh_user or "root").strip()
    last_seen = node.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    recently_online = bool(
        str(node.status or "").strip().lower() == "online"
        and last_seen is not None
        and last_seen >= datetime.now(timezone.utc) - timedelta(seconds=90)
    )
    can_clean_remote = bool(recently_online and ssh_host and ssh_user and saved_password)
    cleanup_status = "skipped"
    cleanup_detail = "Node is offline or saved SSH credentials are unavailable"

    assigned = list(node.channels)
    if can_clean_remote:
        # Stop/forget remote channel processes only while the Node is recently
        # online. Offline deletion must not wait on one timeout per channel.
        for channel in assigned:
            try:
                node_controller.stop_on_node(channel.id, node.id)
                node_controller.forget_on_node(channel.id, node.id)
            except Exception:
                pass
        try:
            uninstall_node_over_ssh(
                app_root=APP_ROOT,
                host=ssh_host,
                port=int(node.ssh_port or 22),
                username=ssh_user,
                password=saved_password,
            )
            cleanup_status = "completed"
            cleanup_detail = "Remote Node files and services were removed"
        except (SSHInstallError, OSError, ValueError) as exc:
            cleanup_status = "failed"
            cleanup_detail = str(exc)

    local = ensure_local_node(db)
    for channel in assigned:
        remaining = [item for item in channel.nodes if item.id != node.id]
        if not remaining:
            remaining = [local]
        set_channel_nodes(channel, remaining)
        if channel.node_id == node.id:
            channel.node = remaining[0]

    # Preserve unified streaming accounts if their direct node is removed.
    for stream_user in list(node.direct_stream_users):
        stream_user.delivery_mode = "central"
        stream_user.direct_node = None
        stream_user.load_balance_enabled = True

    old_logo = node.logo_url
    old_favicon = node_favicon_url(db, node.id)
    # STREAMFORGE_NODE_DELETE_FAVICON_STATE_CLEANUP_V3048:
    # Remove both the file and its database reference. Otherwise a later Node
    # that receives the same SQLite id can inherit a path to a deleted file.
    favicon_setting = db.get(AppSetting, _node_favicon_setting_key(node.id))
    if favicon_setting is not None:
        db.delete(favicon_setting)
    deleted_node_name = node.name
    log_event(
        "Node deleted",
        scope="node",
        level="warning",
        actor=admin.username,
        details={
            "node_name": deleted_node_name,
            "remote_cleanup": cleanup_status,
            "remote_cleanup_detail": cleanup_detail,
        },
        db=db,
    )
    db.delete(node)
    db.commit()
    cleanup_node_logo_if_unused(db, old_logo)
    cleanup_node_favicon_file(old_favicon)
    if cleanup_status == "completed":
        message = "Node deleted from Main and Remote Node server cleaned"
    elif cleanup_status == "failed":
        message = "Node deleted from Main; Remote Node cleanup failed, so remote files may remain"
    else:
        message = "Node deleted from Main; Remote Node cleanup was skipped (offline or SSH unavailable)"
    values = urlencode({"message": message})
    return RedirectResponse(f"/nodes?{values}", status_code=303)


@app.get("/channels", response_class=HTMLResponse, dependencies=[Depends(permission_required("channels.view"))])
def channels_page(
    request: Request,
    category: str = "",
    node: str = "",
    status: str = "",
    q: str = "",
    limit: str = "10",
    page: int = 1,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    if local_node_for_public_urls(db) is None:
        ensure_local_node(db)
        db.commit()

    selected_category = category.strip()
    selected_node = node.strip()
    selected_status = status.strip().lower()
    if selected_status not in {"", "up", "waiting", "down"}:
        selected_status = ""
    search_query = q.strip()[:160]
    # STREAMFORGE_CHANNEL_LIST_LIMIT_SELECTOR_V85:
    # v11.22 restores the original zero-navigation search UX: the catalogue is
    # rendered once and Show N controls only client-side visibility/pagination.
    channel_limit_options = {"10": 10, "20": 20, "50": 50, "100": 100, "200": 200, "500": 500, "all": 0}
    selected_channel_limit = str(limit or "10").strip().lower()
    if selected_channel_limit not in channel_limit_options:
        selected_channel_limit = "10"

    if selected_category not in {"", "uncategorized"}:
        try:
            int(selected_category)
        except ValueError:
            selected_category = ""
    if selected_node:
        try:
            int(selected_node)
        except ValueError:
            selected_node = ""

    # STREAMFORGE_MAIN_CHANNELS_SERVER_PAGINATION_V117:
    # Historical compatibility marker retained. v11.22 supersedes server-side
    # Channels pagination with the in-page live catalogue below so typing never
    # navigates/reloads; Users & Playlists remains server-paginated.
    # STREAMFORGE_MAIN_CHANNEL_LIVE_CATALOGUE_V1122:
    # Keep every channel row in the already-open page so search, category,
    # server, status, Show and pagination can update instantly without a GET,
    # document reload or focus loss. The query remains bounded to one ORM load
    # with relationship select-in batches and performs no Remote Node probes.
    catalogue_order = channel_catalogue_sql_order()
    statement = (
        select(Channel)
        .options(
            selectinload(Channel.category),
            selectinload(Channel.categories),
            selectinload(Channel.nodes),
            selectinload(Channel.node),
        )
        .order_by(*catalogue_order)
    )
    channels = list(db.scalars(statement).all())
    total_channels = len(channels)

    # STREAMFORGE_MAIN_CHANNELS_CATALOGUE_SQL_ORDER_V1111 remains authoritative:
    # contiguous display IDs are generated from the same catalogue order used by
    # playlists, and no per-keystroke SQL query is required.
    # STREAMFORGE_MAIN_CHANNEL_LIST_REUSE_V48:
    # Reuse the single already-loaded catalogue for contiguous public display IDs.
    channel_display_ids = {int(channel.id): 101 + index for index, channel in enumerate(channels)}

    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    nodes = db.scalars(select(Node).order_by(Node.node_type, Node.name)).all()
    category_counts = {
        int(category_id_value): int(count)
        for category_id_value, count in db.execute(
            select(channel_category_links.c.category_id, func.count(func.distinct(channel_category_links.c.channel_id)))
            .group_by(channel_category_links.c.category_id)
        ).all()
    }
    node_counts = {
        int(node_id_value): int(count)
        for node_id_value, count in db.execute(
            select(channel_nodes.c.node_id, func.count(channel_nodes.c.channel_id)).group_by(channel_nodes.c.node_id)
        ).all()
    }

    playlist_public_base = main_playlist_public_base(db, request)
    http_outputs = {
        channel.id: channel_http_urls(channel, request, db, public_base=playlist_public_base)
        for channel in channels
        if channel.output_type == "hls"
    }
    # STREAMFORGE_MAIN_CHANNEL_INITIAL_UPTIME_V60R2:
    # All rows still use only local in-memory supervisor snapshots at first paint;
    # Remote Node/HLS/viewer work stays outside this render path.
    # STREAMFORGE_CHANNELS_FAST_OFFLINE_RENDER_V3044:
    # STREAMFORGE_CHANNELS_NO_BLOCKING_VIEWER_FETCH_V3044:
    initial_channel_runtime: dict[int, dict[str, object]] = {}
    for channel in channels:
        assigned_local = any(item.node_type == "local" for item in node_controller.assigned_nodes(channel))
        snapshot = dict(stream_manager.runtime_snapshot(int(channel.id))) if assigned_local else {}
        alive = bool(snapshot.get("alive"))
        process_status = str(channel.status or "").strip().lower()
        if alive:
            initial_status = "up"
        elif bool(channel.enabled) and (bool(channel.desired_running) or process_status in {"running", "starting", "restarting", "degraded"}):
            initial_status = "waiting"
        else:
            initial_status = "down"
        initial_channel_runtime[int(channel.id)] = {
            "status": initial_status,
            "process_status": "running" if alive else process_status,
            "uptime_seconds": int(snapshot.get("uptime_seconds") or 0),
        }

    return render(
        request,
        "channels.html",
        db,
        channels=channels,
        channel_display_ids=channel_display_ids,
        categories=categories,
        nodes=nodes,
        selected_category=selected_category,
        selected_node=selected_node,
        selected_status=selected_status,
        search_query=search_query,
        selected_channel_limit=selected_channel_limit,
        channel_limit_options=list(channel_limit_options.keys()),
        http_outputs=http_outputs,
        http_ready={},
        initial_channel_runtime=initial_channel_runtime,
        category_counts=category_counts,
        node_counts=node_counts,
        viewer_by_channel={},
        total_channels=total_channels,
        message=message,
        error=error,
    )


@app.get("/channels/{channel_id}/info", response_class=HTMLResponse, dependencies=[Depends(permission_required("channels.info"))])
def channel_info_page(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    runtime = main_channel_runtime(channel)
    encoder = channel_encoder_details(channel)
    output_ready = channel_output_probe_target(channel) is not None
    replica_nodes = channel_info_replica_seed(channel)
    return render(
        request,
        "channel_info.html",
        db,
        channel=channel,
        runtime=runtime,
        encoder=encoder,
        output_ready=output_ready,
        replica_nodes=replica_nodes,
        http_output=channel_http_urls(channel, request, db) if channel.output_type == "hls" else None,
    )


@app.get("/channels/{channel_id}/replicas.json", dependencies=[Depends(permission_required("channels.info"))])
def channel_replicas_json(channel_id: int, request: Request, db: Session = Depends(get_db)):
    """Live-check only this channel's assigned nodes when Stream Info requests it.

    There is intentionally no scheduler/background poll behind this endpoint.
    Remote Node requests run concurrently, so one offline Node costs at most one
    timeout window rather than one timeout per selected Node.
    """
    if not current_admin(request, db):
        raise HTTPException(401)
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    assigned_nodes = list(node_controller.assigned_nodes(channel))
    node_ids = [int(node.id) for node in assigned_nodes]
    node_types = {int(node.id): str(node.node_type or "remote") for node in assigned_nodes}
    db.close()
    if not node_ids:
        return {"ok": True, "channel_id": channel_id, "replicas": [], "captured_at": datetime.now(timezone.utc).isoformat()}

    snapshots: dict[int, dict[str, object]] = {}

    def read_one(node_id: int) -> tuple[int, dict[str, object]]:
        payload = dict(node_controller.runtime_snapshot_on_node(channel_id, node_id, refresh=True))
        # STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7:
        # v6.3-r7 Nodes return host uptime in the same channel-status request.
        # Older Nodes get one lightweight quick-status fallback here only while
        # Stream Info is opened/refreshed; there is still no background polling.
        if (
            node_types.get(node_id) != "local"
            and bool(payload.get("node_online"))
            and int(payload.get("node_uptime_seconds") or 0) <= 0
        ):
            try:
                with SessionLocal() as status_db:
                    current_node = status_db.get(Node, node_id)
                    if current_node is not None:
                        quick = node_controller.quick_status(current_node, refresh=True)
                        metrics = quick.get("metrics") if isinstance(quick, dict) else {}
                        if isinstance(metrics, dict):
                            payload["node_uptime_seconds"] = int(metrics.get("uptime_seconds") or 0)
            except Exception:
                pass
        return node_id, payload

    workers = min(12, max(1, len(node_ids)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sf-info-node") as executor:
        futures = [executor.submit(read_one, node_id) for node_id in node_ids]
        for future in as_completed(futures):
            try:
                node_id, payload = future.result()
            except Exception as exc:
                node_id = 0
                payload = {
                    "node_id": 0, "node_name": "Node", "node_online": False,
                    "alive": False, "status": "offline", "last_error": str(exc),
                }
            snapshots[int(node_id)] = payload

    replicas = [normalize_channel_info_replica(snapshots.get(node_id, {
        "node_id": node_id, "node_name": f"Node {node_id}", "node_online": False,
        "alive": False, "status": "offline", "last_error": "Node status unavailable",
    })) for node_id in node_ids]
    return {
        "ok": True,
        "channel_id": channel_id,
        "replicas": replicas,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/channels/{channel_id}/probe.json", dependencies=[Depends(permission_required("channels.info"))])
def channel_probe_json(
    channel_id: int,
    request: Request,
    target: str = "input",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        raise HTTPException(401)
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    if target not in {"input", "output"}:
        raise HTTPException(400, "Target must be input or output")

    if target == "input":
        inputs = channel_input_urls(channel)
        index = max(0, min(int(channel.active_input_index or 0), len(inputs) - 1)) if inputs else 0
        probe_target: str | Path = inputs[index] if inputs else channel.input_url
    else:
        if channel.output_type != "hls":
            return {
                "ok": False,
                "target": "output",
                "error": "Live output probing is available for local HTTP HLS output. Remote HTTP/UDP/SRT output details are shown from the channel configuration.",
            }
        output_path = channel_output_probe_target(channel)
        if output_path is None:
            return {
                "ok": False,
                "target": "output",
                "error": "HLS output is not ready. Start the channel and wait for the first segments.",
            }
        probe_target = output_path

    configured_program_id = (lambda values, idx: values[idx] if values and 0 <= idx < len(values) else None)(channel_source_program_ids(channel), max(0, min(int(channel.active_input_index or 0), len(channel_input_urls(channel)) - 1))) if channel_input_urls(channel) else None
    source_program_ids = channel_source_program_ids(channel)
    runtime = main_channel_runtime(channel)
    channel_name = channel.name
    channel_status = channel.status
    live_bitrate_kbps = channel.live_bitrate_kbps
    # STREAMFORGE_STREAM_INFO_PROBE_RELEASE_DB_V63R6: ffprobe can wait for a
    # network timeout; never hold the Main SQLite read transaction while it does.
    db.close()
    result = probe_stream(probe_target)
    result.update(
        {
            "target": target,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "configured_program_id": configured_program_id,
            "source_program_ids": source_program_ids,
            "runtime": runtime,
            "channel_status": channel_status,
            "live_bitrate_kbps": live_bitrate_kbps,
        }
    )
    return result


@app.post("/channels/source-scan.json")
async def channel_source_scan(request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        raise HTTPException(401)
    if not any(has_permission(admin, key) for key in ("channels.create", "channels.edit", "channels.info")):
        raise HTTPException(403, "Source scan permission denied")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON payload") from exc
    target = str(payload.get("input_url") or "").strip()
    if not target:
        raise HTTPException(400, "Input URL is required")
    if len(target) > 8000:
        raise HTTPException(400, "Input URL is too long")
    # STREAMFORGE_NONBLOCKING_SOURCE_SCAN_V3061:
    # ffprobe is blocking and may wait for the configured probe timeout. Running
    # it directly in this async endpoint stalls the ASGI event loop, making the
    # Panel, Web Player, Node heartbeats and APIs appear offline during Scan.
    # STREAMFORGE_SCAN_RELEASE_DB_V32:
    # Authentication above has finished. Do not reserve a pooled DB connection
    # while ffprobe waits on an unreachable/slow source. The dependency cleanup
    # may safely close this Session again after the response.
    db.close()
    result = await asyncio.to_thread(probe_stream, target)
    result.update({"target": "source-scan", "configured_program_id": None})
    return result


@app.get("/channels/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("channels.create"))])
def channel_new_page(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    ensure_local_node(db)
    db.commit()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    return render(
        request, "channel_form.html", db, channel=None, categories=categories, nodes=nodes,
        node_input_modes={}, node_encoding_profiles={}, error=None, **encoder_template_context()
    )


@app.post("/channels/new", dependencies=[Depends(permission_required("channels.create"))])
def channel_create(
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    input_url: str = Form(""),
    backup_inputs: str = Form(""),
    source_urls: list[str] = Form(default=[]),
    source_program_ids: list[str] = Form(default=[]),
    failback_enabled: Optional[str] = Form(None),
    failback_interval: int = Form(30),
    program_id: str = Form(""),
    category_id: str = Form(""),
    category_ids: list[int] = Form(default=[]),
    node_ids: list[int] = Form(default=[]),
    node_input_modes: list[str] = Form(default=[]),
    node_profile_node_ids: list[int] = Form(default=[]),
    node_video_codecs: list[str] = Form(default=[]),
    node_video_bitrates: list[str] = Form(default=[]),
    node_resolutions: list[str] = Form(default=[]),
    node_audio_codecs: list[str] = Form(default=[]),
    node_audio_bitrates: list[str] = Form(default=[]),
    node_hls_segment_times: list[str] = Form(default=[]),
    remote_input_mode: str = Form("source"),
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    enabled: Optional[str] = Form(None),
    auto_restart: Optional[str] = Form(None),
    video_codec: str = Form("copy"),
    video_bitrate: str = Form("2500k"),
    width: str = Form(""),
    height: str = Form(""),
    fps: str = Form(""),
    preset: str = Form("ultrafast"),
    audio_codec: str = Form("copy"),
    audio_bitrate: str = Form("128k"),
    output_type: str = Form("hls"),
    output_url: str = Form(""),
    output_urls: list[str] = Form(default=[]),
    hls_segment_time: int = Form(1),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    try:
        input_url, backup_inputs, normalized_program_ids = normalize_source_configs(
            source_urls, source_program_ids, input_url, backup_inputs, program_id
        )
    except ValueError as exc:
        categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
        nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
        return render(request, "channel_form.html", db, channel=None, categories=categories, nodes=nodes,
                      node_input_modes={}, node_encoding_profiles={}, error=str(exc), **encoder_template_context())
    final_slug = slugify(slug or name)
    if db.scalar(select(Channel).where(Channel.slug == final_slug)):
        categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
        nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
        return render(request, "channel_form.html", db, channel=None, categories=categories, nodes=nodes,
                      node_input_modes={}, node_encoding_profiles={}, error="Slug already exists", **encoder_template_context())
    selected_categories = selected_channel_categories(db, category_ids, category_id)
    category = selected_categories[0] if selected_categories else None
    assigned_nodes = selected_nodes(db, node_ids) if has_permission(admin, "channels.assign_node") else [ensure_local_node(db)]
    try:
        selected_modes = parse_node_input_modes(node_input_modes, assigned_nodes, remote_input_mode)
        selected_profiles = parse_node_encoding_profiles(
            node_profile_node_ids, node_video_codecs, node_video_bitrates,
            node_resolutions, node_audio_codecs, node_audio_bitrates,
            node_hls_segment_times, assigned_nodes,
        )
        normalized_output = normalize_output_type(output_type)
        normalized_remote_outputs = normalize_remote_output_urls(output_urls, output_url)
        if normalized_output != "hls" and not normalized_remote_outputs:
            raise ValueError("Add at least one Remote HTTP/UDP/SRT output URL")
        validate_remote_relay_selection(selected_modes, assigned_nodes, normalized_output)
        uploaded_logo = save_channel_logo(logo_file, final_slug)
        final_logo = uploaded_logo or normalize_channel_logo_url(logo_url)
    except ValueError as exc:
        categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
        nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
        return render(request, "channel_form.html", db, channel=None, categories=categories, nodes=nodes,
                      node_input_modes={}, node_encoding_profiles={}, error=str(exc), **encoder_template_context())
    channel = Channel(
        name=name.strip(), slug=final_slug, input_url=input_url.strip(), backup_inputs=backup_inputs.strip() or None, active_input_index=0, failback_enabled=as_bool(failback_enabled), failback_interval=max(10, min(3600, int(failback_interval or 30))), logo_url=final_logo, program_id=normalized_program_ids[0] if normalized_program_ids else None, source_program_ids=json.dumps(normalized_program_ids), category=category, sort_order=next_channel_sort_order(db, category.id if category else None), node=assigned_nodes[0], nodes=assigned_nodes, enabled=as_bool(enabled),
        auto_restart=as_bool(auto_restart), remote_input_mode="source",
        video_codec=normalize_video_codec(video_codec), video_bitrate=video_bitrate.strip(), width=to_int(width),
        height=to_int(height), fps=to_int(fps), preset=preset, audio_codec=audio_codec,
        audio_bitrate=audio_bitrate.strip(), output_type=normalized_output,
        output_url=normalized_remote_outputs or None, hls_segment_time=max(1, hls_segment_time),
    )
    db.add(channel)
    db.flush()
    assign_channel_categories(channel, selected_categories)
    set_channel_node_input_modes(db, channel, selected_modes)
    set_channel_node_encoding_profiles(db, channel, selected_profiles)
    db.commit()
    # STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118: do not make the browser wait
    # for one or more Remote Node HTTP timeouts. The committed channel is
    # authoritative immediately; propagation continues in the ordered worker.
    queue_channel_save_sync(
        int(channel.id),
        {int(item.id) for item in assigned_nodes if item.node_type == "remote"},
        _sync_main_base(request),
        sync_logo=bool(final_logo and str(final_logo).startswith("/channel-logos/")),
    )
    log_event("Channel created", scope="channel", channel_id=channel.id, actor=admin.username, details={"name": channel.name, "inputs": channel_input_urls(channel)})
    return RedirectResponse("/channels", status_code=303)


@app.get("/channels/{channel_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("channels.edit"))])
def channel_edit_page(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    ensure_local_node(db)
    db.commit()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    return render(
        request, "channel_form.html", db, channel=channel, categories=categories, nodes=nodes,
        node_input_modes=channel_node_input_modes(db, channel),
        node_encoding_profiles=channel_node_encoding_profiles(db, channel),
        error=None, **encoder_template_context()
    )


@app.post("/channels/{channel_id}/edit", dependencies=[Depends(permission_required("channels.edit"))])
def channel_update(
    channel_id: int,
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    input_url: str = Form(""),
    backup_inputs: str = Form(""),
    source_urls: list[str] = Form(default=[]),
    source_program_ids: list[str] = Form(default=[]),
    failback_enabled: Optional[str] = Form(None),
    failback_interval: int = Form(30),
    program_id: str = Form(""),
    category_id: str = Form(""),
    category_ids: list[int] = Form(default=[]),
    node_ids: list[int] = Form(default=[]),
    node_input_modes: list[str] = Form(default=[]),
    node_profile_node_ids: list[int] = Form(default=[]),
    node_video_codecs: list[str] = Form(default=[]),
    node_video_bitrates: list[str] = Form(default=[]),
    node_resolutions: list[str] = Form(default=[]),
    node_audio_codecs: list[str] = Form(default=[]),
    node_audio_bitrates: list[str] = Form(default=[]),
    node_hls_segment_times: list[str] = Form(default=[]),
    remote_input_mode: str = Form("source"),
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    remove_logo: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    auto_restart: Optional[str] = Form(None),
    video_codec: str = Form("copy"),
    video_bitrate: str = Form("2500k"),
    width: str = Form(""),
    height: str = Form(""),
    fps: str = Form(""),
    preset: str = Form("ultrafast"),
    audio_codec: str = Form("copy"),
    audio_bitrate: str = Form("128k"),
    output_type: str = Form("hls"),
    output_url: str = Form(""),
    output_urls: list[str] = Form(default=[]),
    hls_segment_time: int = Form(1),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    try:
        input_url, backup_inputs, normalized_program_ids = normalize_source_configs(
            source_urls, source_program_ids, input_url, backup_inputs, program_id
        )
    except ValueError as exc:
        input_url = channel.input_url
        backup_inputs = channel.backup_inputs or ""
        source_error = str(exc)
    else:
        source_error = ""

    def render_edit_error(message: str):
        db.rollback()
        current = db.get(Channel, channel_id)
        categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
        nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
        modes = channel_node_input_modes(db, current) if current else {}
        return render(
            request,
            "channel_form.html",
            db,
            channel=current,
            categories=categories,
            nodes=nodes,
            node_input_modes=modes,
            node_encoding_profiles=channel_node_encoding_profiles(db, current),
            error=message,
            **encoder_template_context(),
        )

    if source_error:
        return render_edit_error(source_error)

    final_slug = slugify(slug or name)
    duplicate = db.scalar(select(Channel).where(Channel.slug == final_slug, Channel.id != channel_id))
    if duplicate:
        return render_edit_error("Slug already exists")

    old_logo = channel.logo_url
    old_node_ids = {item.id for item in node_controller.assigned_nodes(channel)}
    try:
        uploaded_logo = save_channel_logo(logo_file, final_slug)
        if uploaded_logo:
            final_logo = uploaded_logo
        elif as_bool(remove_logo):
            final_logo = None
        elif logo_url.strip():
            final_logo = normalize_channel_logo_url(logo_url)
        else:
            final_logo = old_logo
    except ValueError as exc:
        return render_edit_error(str(exc))

    requested_nodes = selected_nodes(db, node_ids) if has_permission(admin, "channels.assign_node") else node_controller.assigned_nodes(channel)
    requested_node_ids = [item.id for item in requested_nodes]
    try:
        selected_modes = parse_node_input_modes(node_input_modes, requested_nodes, remote_input_mode)
        selected_profiles = (
            parse_node_encoding_profiles(
                node_profile_node_ids, node_video_codecs, node_video_bitrates,
                node_resolutions, node_audio_codecs, node_audio_bitrates,
                node_hls_segment_times, requested_nodes,
            )
            if has_permission(admin, "channels.assign_node")
            else channel_node_encoding_profiles(db, channel)
        )
        normalized_output = normalize_output_type(output_type)
        normalized_remote_outputs = normalize_remote_output_urls(output_urls, output_url)
        if normalized_output != "hls" and not normalized_remote_outputs:
            raise ValueError("Add at least one Remote HTTP/UDP/SRT output URL")
        validate_remote_relay_selection(selected_modes, requested_nodes, normalized_output)
    except ValueError as exc:
        return render_edit_error(str(exc))

    # STREAMFORGE_OFFLINE_CHANNEL_EDIT_SAVE_V3045:
    # Save the desired configuration first. Do not stop, probe, or start an
    # offline Node inside this request. Shared Node config is synchronized
    # without changing that Node's process state; offline Nodes receive it later.
    db.commit()

    operation_errors: list[str] = []
    db.expire_all()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    requested_nodes = selected_nodes(db, requested_node_ids) if has_permission(admin, "channels.assign_node") else node_controller.assigned_nodes(channel)

    try:
        channel.name = name.strip()
        channel.slug = final_slug
        channel.input_url = input_url.strip()
        channel.backup_inputs = backup_inputs.strip() or None
        channel.active_input_index = 0
        channel.failback_enabled = as_bool(failback_enabled)
        channel.failback_interval = max(10, min(3600, int(failback_interval or 30)))
        channel.logo_url = final_logo
        channel.source_program_ids = json.dumps(normalized_program_ids)
        channel.program_id = normalized_program_ids[0] if normalized_program_ids else None
        selected_categories = selected_channel_categories(db, category_ids, category_id)
        old_category_id = channel.category_id
        if old_category_id and any(item.id == old_category_id for item in selected_categories):
            selected_categories.sort(key=lambda item: 0 if item.id == old_category_id else 1)
        assign_channel_categories(channel, selected_categories)
        if old_category_id != channel.category_id:
            channel.sort_order = next_channel_sort_order(db, channel.category_id)
        set_channel_nodes(channel, requested_nodes)
        channel.enabled = as_bool(enabled)
        channel.auto_restart = as_bool(auto_restart)
        channel.video_codec = normalize_video_codec(video_codec)
        channel.video_bitrate = video_bitrate.strip()
        channel.width = to_int(width)
        channel.height = to_int(height)
        channel.fps = to_int(fps)
        channel.preset = preset
        channel.audio_codec = audio_codec
        channel.audio_bitrate = audio_bitrate.strip()
        channel.output_type = normalized_output
        channel.output_url = normalized_remote_outputs or None
        channel.hls_segment_time = max(1, hls_segment_time)
        db.flush()
        set_channel_node_input_modes(db, channel, selected_modes)
        set_channel_node_encoding_profiles(db, channel, selected_profiles)
        db.commit()
    except Exception as exc:
        db.rollback()
        log_event(
            "Channel update failed",
            scope="channel",
            level="error",
            channel_id=channel_id,
            actor=admin.username,
            details=str(exc),
        )
        return render_edit_error(f"Could not save channel: {exc}")

    # STREAMFORGE_MAIN_CONFIG_SAVE_NO_PROCESS_RESTART_V33:
    # A Main Panel channel edit is a configuration save only. Do not restart
    # Main Local FFmpeg.
    # STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118:
    # Remote Node/config/logo propagation is intentionally queued after the DB
    # commit. It must never hold this form POST open for 12-20 seconds per Node.
    new_node_ids = {item.id for item in requested_nodes}
    if old_node_ids != new_node_ids:
        node_controller._invalidate(channel_id)
    if old_logo != final_logo:
        try:
            cleanup_logo_if_unused(db, old_logo)
            db.commit()
        except Exception as exc:
            db.rollback()
            operation_errors.append(f"Old logo cleanup: {exc}")

    log_event(
        "Channel updated",
        scope="channel",
        channel_id=channel_id,
        actor=admin.username,
        details={"inputs": [input_url.strip()] + [item.strip() for item in backup_inputs.splitlines() if item.strip()], "node_ids": requested_node_ids},
    )

    # Release any read transaction opened by cleanup before queueing remote work.
    db.commit()
    queue_channel_save_sync(
        int(channel_id),
        {int(item.id) for item in requested_nodes if item.node_type == "remote"},
        _sync_main_base(request),
        removed_node_ids={int(item) for item in (old_node_ids - new_node_ids)},
        sync_logo=bool(
            old_logo != final_logo
            and final_logo
            and str(final_logo).startswith("/channel-logos/")
        ),
    )

    values = {"message": "Channel saved successfully"}
    if operation_errors:
        values["error"] = "; ".join(str(item) for item in operation_errors if item)[-3000:]
    return RedirectResponse(f"/channels?{urlencode(values)}", status_code=303)


@app.post("/channels/{channel_id}/start", dependencies=[Depends(permission_required("channels.start"))])
def channel_start(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    if not channel.enabled:
        channel.last_error = "Channel is disabled"
        db.commit()
    else:
        try:
            node_controller.start(channel_id)
            log_event("Channel start requested", scope="channel", channel_id=channel_id, actor=current_admin(request, db).username if current_admin(request, db) else None)
        except RuntimeError as exc:
            channel.last_error = str(exc)
            channel.status = "error"
            db.commit()
    # STREAMFORGE_MAIN_CHANNEL_ACTION_AJAX_V2250:
    if request.headers.get("X-StreamForge-Ajax") == "1":
        return JSONResponse({"ok": True, "action": "start", "channel_id": channel_id})
    return RedirectResponse(request.headers.get("referer", "/channels"), status_code=303)


@app.post("/channels/{channel_id}/stop", dependencies=[Depends(permission_required("channels.stop"))])
def channel_stop(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    node_controller.stop(channel_id)
    log_event("Channel stopped", scope="channel", channel_id=channel_id, actor=current_admin(request, db).username if current_admin(request, db) else None)
    if request.headers.get("X-StreamForge-Ajax") == "1":
        return JSONResponse({"ok": True, "action": "stop", "channel_id": channel_id})
    return RedirectResponse(request.headers.get("referer", "/channels"), status_code=303)


@app.post("/channels/{channel_id}/delete", dependencies=[Depends(permission_required("channels.delete"))])
def channel_delete(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    node_controller.stop(channel_id)
    channel = db.get(Channel, channel_id)
    if channel:
        old_logo = channel.logo_url
        node_controller.forget(channel_id)
        db.delete(channel)
        db.commit()
        cleanup_logo_if_unused(db, old_logo)
    return RedirectResponse("/channels", status_code=303)


@app.post("/channels/{channel_id}/restart", dependencies=[Depends(permission_required("channels.restart"))])
def channel_restart(channel_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    if not channel.enabled:
        channel.last_error = "Channel is disabled"
        db.commit()
    else:
        try:
            node_controller.restart(channel_id)
        except RuntimeError as exc:
            channel = db.get(Channel, channel_id)
            if channel:
                channel.last_error = str(exc)
                channel.status = "error"
                db.commit()
    if request.headers.get("X-StreamForge-Ajax") == "1":
        return JSONResponse({"ok": True, "action": "restart", "channel_id": channel_id})
    return RedirectResponse(request.headers.get("referer", "/channels"), status_code=303)


@app.post("/channels/bulk", dependencies=[Depends(permission_required("channels.view"))])
def channels_bulk_action(
    request: Request,
    action: str = Form(...),
    channel_ids: list[int] = Form(default=[]),
    bulk_node_ids: list[int] = Form(default=[]),
    bulk_profile_node_ids: list[int] = Form(default=[]),
    bulk_video_codec: str = Form("keep"),
    bulk_video_bitrate: str = Form(""),
    bulk_resolution: str = Form("keep"),
    bulk_audio_codec: str = Form("keep"),
    bulk_audio_bitrate: str = Form(""),
    bulk_hls_segment_time: str = Form("keep"),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()

    action_permissions = {
        "start_all": "channels.start",
        "start_selected": "channels.start",
        "restart_all": "channels.restart",
        "restart_selected": "channels.restart",
        "stop_all": "channels.stop",
        "stop_selected": "channels.stop",
        "delete_selected": "channels.delete",
        "nodes_add": "channels.assign_node",
        "nodes_remove": "channels.assign_node",
        "nodes_set": "channels.assign_node",
        "relay_set": "channels.assign_node",
        "relay_clear": "channels.assign_node",
        "profile_update": "channels.edit",
    }
    permission = action_permissions.get(action)
    if permission is None:
        raise HTTPException(400, "Unknown channel action")
    enforce_permission(request, db, permission)

    # STREAMFORGE_BULK_SELECTED_EAGER_V1124:
    # Non-node bulk actions still batch-load selected relationships.
    # STREAMFORGE_BULK_NODE_NO_ORM_HYDRATION_V1125:
    # Node assignment itself uses a direct association-table transaction below,
    # so do not hydrate 50-500 Channel.nodes collections only to diff them again.
    selected_ids = {int(item) for item in channel_ids}
    node_assignment_action = action in {"nodes_add", "nodes_remove", "nodes_set"}
    selected_channels = (
        db.scalars(
            select(Channel)
            .where(Channel.id.in_(selected_ids))
            .options(selectinload(Channel.nodes), selectinload(Channel.node))
            .order_by(Channel.id)
        ).all()
        if selected_ids and not node_assignment_action
        else []
    )
    all_channels = (
        db.scalars(select(Channel).order_by(Channel.id)).all()
        if action in {"start_all", "restart_all", "stop_all"}
        else []
    )

    if action == "delete_selected":
        if not selected_channels:
            return RedirectResponse("/channels?error=Select+at+least+one+channel", status_code=303)
        deleted = 0
        errors: list[str] = []
        # Run the same proven sequence as single-channel delete and commit each
        # channel independently. A failed Node cleanup can no longer abort the
        # entire bulk request or leave the browser on an HTTP 500 page.
        for selected in selected_channels:
            channel_id = int(selected.id)
            channel_name = str(selected.name)
            old_logo = selected.logo_url
            try:
                node_controller.stop(channel_id)
                channel = db.get(Channel, channel_id)
                if not channel:
                    continue
                node_controller.forget(channel_id)
                db.delete(channel)
                db.commit()
                cleanup_logo_if_unused(db, old_logo)
                deleted += 1
            except Exception as exc:
                db.rollback()
                errors.append(f"{channel_name}: {exc}")
        query = {"message": f"Deleted {deleted} channel(s)"}
        if errors:
            query["error"] = "; ".join(errors)[:3500]
        return RedirectResponse(f"/channels?{urlencode(query)}", status_code=303)

    if action == "profile_update":
        if not selected_channels:
            return RedirectResponse("/channels?error=Select+at+least+one+channel", status_code=303)
        video_codec = str(bulk_video_codec or "keep").strip().lower()
        audio_codec = str(bulk_audio_codec or "keep").strip().lower()
        resolution = str(bulk_resolution or "keep").strip().lower()
        video_bitrate = str(bulk_video_bitrate or "").strip()
        audio_bitrate = str(bulk_audio_bitrate or "").strip()
        hls_segment = str(bulk_hls_segment_time or "keep").strip().lower()
        if video_codec not in {"keep", "copy", "auto", "auto_hevc", "h264_nvenc"}:
            return RedirectResponse("/channels?error=Invalid+bulk+video+codec", status_code=303)
        if audio_codec not in (_NODE_AUDIO_CODECS - {"inherit"}) | {"keep"}:
            return RedirectResponse("/channels?error=Invalid+bulk+audio+codec", status_code=303)
        if resolution not in (_NODE_RESOLUTIONS - {"inherit"}) | {"keep"}:
            return RedirectResponse("/channels?error=Invalid+bulk+resolution", status_code=303)
        if video_bitrate and not _BITRATE_VALUE_RE.fullmatch(video_bitrate):
            return RedirectResponse("/channels?error=Video+bitrate+must+look+like+2500k+or+5M", status_code=303)
        if audio_bitrate and not _BITRATE_VALUE_RE.fullmatch(audio_bitrate):
            return RedirectResponse("/channels?error=Audio+bitrate+must+look+like+128k+or+1M", status_code=303)
        target_node_ids = {int(item) for item in bulk_profile_node_ids}
        if hls_segment == "inherit":
            if not target_node_ids:
                return RedirectResponse("/channels?error=Select+at+least+one+encoding+target+node", status_code=303)
            hls_segment_seconds = None
        elif hls_segment != "keep":
            try:
                hls_segment_seconds = int(hls_segment)
            except ValueError:
                return RedirectResponse("/channels?error=Invalid+HLS+segment+seconds", status_code=303)
            if not 1 <= hls_segment_seconds <= 20:
                return RedirectResponse("/channels?error=HLS+segment+seconds+must+be+between+1+and+20", status_code=303)
        else:
            hls_segment_seconds = None
        if all(value in {"", "keep"} for value in (video_codec, video_bitrate, resolution, audio_codec, audio_bitrate, hls_segment)):
            return RedirectResponse("/channels?error=Choose+at+least+one+profile+change", status_code=303)
        # STREAMFORGE_BULK_PROFILE_500_GUARD_V3046:
        # Keep the database write and Node reconciliation as separate phases.
        # SQLAlchemy expires ORM rows after commit; retaining only primitive
        # IDs/names prevents an offline Node sync failure from triggering an
        # expired-row reload (and a second exception) in the error handler.
        # STREAMFORGE_BULK_PROFILE_NODE_SCOPE_V1027:
        # The Encoding target selector scopes the entire profile, not just HLS.
        # With no target selected we update the channel defaults (existing bulk
        # behavior). With one or more targets selected we write only those
        # channel_nodes overrides, so changing a Remote Node can never mutate or
        # restart the Main Server unless the Local/Main node itself is selected.
        selected_targets = [(int(channel.id), str(channel.name)) for channel in selected_channels]
        sync_targets: dict[int, set[int] | None] = {}
        changed_target_links = 0
        try:
            for channel in selected_channels:
                assigned_node_ids = {int(node.id) for node in node_controller.assigned_nodes(channel)}
                scoped_node_ids = target_node_ids & assigned_node_ids
                if target_node_ids:
                    if not scoped_node_ids:
                        sync_targets[int(channel.id)] = set()
                        continue
                    set_parts: list[str] = []
                    params: dict[str, object] = {"channel_id": int(channel.id)}
                    if video_codec != "keep":
                        set_parts.append("video_codec=:video_codec")
                        params["video_codec"] = normalize_video_codec(video_codec)
                    if video_bitrate:
                        set_parts.append("video_bitrate=:video_bitrate")
                        params["video_bitrate"] = video_bitrate
                    if resolution != "keep":
                        set_parts.append("resolution=:resolution")
                        params["resolution"] = resolution
                    if audio_codec != "keep":
                        set_parts.append("audio_codec=:audio_codec")
                        params["audio_codec"] = audio_codec
                    if audio_bitrate:
                        set_parts.append("audio_bitrate=:audio_bitrate")
                        params["audio_bitrate"] = audio_bitrate
                    if hls_segment != "keep":
                        set_parts.append("hls_segment_time=:hls_segment_time")
                        params["hls_segment_time"] = hls_segment_seconds
                    if set_parts:
                        for target_node_id in sorted(scoped_node_ids):
                            node_params = dict(params)
                            node_params["node_id"] = target_node_id
                            db.execute(
                                text(
                                    f"UPDATE channel_nodes SET {', '.join(set_parts)} "
                                    "WHERE channel_id=:channel_id AND node_id=:node_id"
                                ),
                                node_params,
                            )
                            changed_target_links += 1
                    sync_targets[int(channel.id)] = set(scoped_node_ids)
                    continue

                # No node target: change only the channel-wide defaults.
                if video_codec != "keep":
                    channel.video_codec = normalize_video_codec(video_codec)
                if video_bitrate:
                    channel.video_bitrate = video_bitrate
                if resolution != "keep":
                    if resolution == "source":
                        channel.width = None
                        channel.height = None
                    else:
                        width_text, height_text = resolution.split("x", 1)
                        channel.width = int(width_text)
                        channel.height = int(height_text)
                if audio_codec != "keep":
                    channel.audio_codec = audio_codec
                if audio_bitrate:
                    channel.audio_bitrate = audio_bitrate
                if hls_segment != "keep" and hls_segment_seconds is not None:
                    channel.hls_segment_time = hls_segment_seconds
                sync_targets[int(channel.id)] = None
            db.commit()
        except Exception as exc:
            db.rollback()
            query = {"error": f"Bulk profile update could not be saved: {exc}"[:3500]}
            return RedirectResponse(f"/channels?{urlencode(query)}", status_code=303)
        # STREAMFORGE_BULK_PROFILE_ASYNC_APPLY_V1119:
        # The profile is already committed.  Build only primitive background
        # work descriptors and return immediately; Local FFmpeg restarts and
        # Remote Node API waits happen outside the browser request.
        background_targets: list[tuple[int, str, set[int] | None]] = []
        applied_channels = 0
        for channel_id, channel_name in selected_targets:
            node_scope = sync_targets.get(channel_id)
            if target_node_ids and not node_scope:
                continue
            applied_channels += 1
            background_targets.append(
                (
                    int(channel_id),
                    str(channel_name),
                    None if node_scope is None else {int(item) for item in node_scope},
                )
            )
        queued = queue_bulk_profile_sync(background_targets)
        if target_node_ids:
            query = {
                "message": (
                    f"Encoding profile saved on {changed_target_links} selected node assignment(s) "
                    f"across {applied_channels} channel(s); running replicas are applying it in background"
                )
            }
        else:
            query = {
                "message": (
                    f"Encoding profile defaults saved for {applied_channels} channel(s); "
                    "running replicas are applying them in background"
                )
            }
        if not queued:
            query["error"] = "Profile was saved, but background apply could not be queued; restart affected channels manually"
        return RedirectResponse(f"/channels?{urlencode(query)}", status_code=303)

    if action in {"relay_set", "relay_clear"}:
        if not selected_channels:
            return RedirectResponse("/channels?error=Select+at+least+one+channel", status_code=303)
        chosen_remote_nodes = db.scalars(
            select(Node).where(
                Node.id.in_(set(bulk_node_ids)),
                Node.enabled.is_(True),
                Node.node_type == "remote",
            ).order_by(Node.name)
        ).all() if bulk_node_ids else []
        if not chosen_remote_nodes:
            return RedirectResponse("/channels?error=Select+at+least+one+remote+node", status_code=303)
        local = ensure_local_node(db)
        chosen_remote_ids = {int(node.id) for node in chosen_remote_nodes}
        changed: list[int] = []
        errors: list[str] = []
        for channel in selected_channels:
            if action == "relay_set" and channel.output_type != "hls":
                errors.append(f"{channel.name}: Local Node relay requires HTTP HLS output")
                continue
            # STREAMFORGE_BULK_ACTION_DB_ONLY_LIVE_STATE_V3050:
            # STREAMFORGE_BULK_RELAY_DB_ONLY_LIVE_STATE_V1138:
            # Bulk relay/original-input changes must use committed DB state only.
            # Never probe Remote runtime from this browser POST; selected-node
            # delivery is persisted to the targeted queue below and applied later.
            old_nodes = node_controller.assigned_nodes(channel)
            old_modes = channel_node_input_modes(db, channel)
            if action == "relay_set":
                merged = {node.id: node for node in [*old_nodes, local, *chosen_remote_nodes]}
                new_nodes = sorted(merged.values(), key=lambda item: (0 if item.node_type == "local" else 1, item.name.lower()))
                channel.nodes = new_nodes
                channel.node = new_nodes[0]
                db.flush()
                modes = {node.id: old_modes.get(node.id, "source") for node in new_nodes if node.node_type == "remote"}
                for node_id in chosen_remote_ids:
                    modes[node_id] = "local_relay"
                validate_remote_relay_selection(modes, new_nodes, channel.output_type)
                set_channel_node_input_modes(db, channel, modes)
            else:
                assigned = node_controller.assigned_nodes(channel)
                modes = {node.id: old_modes.get(node.id, "source") for node in assigned if node.node_type == "remote"}
                touched = False
                for node_id in chosen_remote_ids:
                    if node_id in modes and modes[node_id] != "source":
                        modes[node_id] = "source"
                        touched = True
                if not touched:
                    continue
                set_channel_node_input_modes(db, channel, modes)

            # STREAMFORGE_BULK_RELAY_SELECTED_NODE_ONLY_V1137:
            # Local-relay/original-input is a per-Node input-mode setting. Queue
            # only the explicitly selected Remote Nodes. Never call the legacy
            # node_ids=None sync path here because that contacts every assigned
            # replica and turns one TS Live edit into BRO/Plex/Air Play timeouts.
            reason = (
                f"Local relay settings queued: {channel.name}"
                if action == "relay_set"
                else f"Original input settings queued: {channel.name}"
            )
            for chosen_node in chosen_remote_nodes:
                mark_node_channel_sync_pending(db, chosen_node, int(channel.id), reason)
            changed.append(int(channel.id))

        # STREAMFORGE_BULK_RELAY_RESPONSE_FIRST_V1137:
        # Persist mode + exact targeted delivery work together, then return. The
        # heartbeat targeted dispatcher applies configs in true batches. No
        # remote HTTP request or executor handoff is allowed in this POST.
        db.commit()
        label = "Local Node relay enabled" if action == "relay_set" else "Original input restored"
        query = {"message": f"{label} for {len(changed)} channel(s); selected node(s) applying in background"}
        if errors:
            query["error"] = "; ".join(errors)[:3500]
        return RedirectResponse(f"/channels?{urlencode(query)}", status_code=303)

    if action in {"nodes_add", "nodes_remove", "nodes_set"}:
        # STREAMFORGE_BULK_NODE_ASSIGNMENT_500_GUARD_V3048:
        # STREAMFORGE_BULK_NODE_ASSIGNMENT_FAST_RETURN_V1124:
        # STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_V1125:
        # Save node membership as one compact local DB transaction.  Remote
        # replica reconciliation is queued afterwards and the browser can use a
        # JSON response, so no full Channels document reload is required.
        wants_json = request.headers.get("X-StreamForge-Ajax") == "1" or "application/json" in str(request.headers.get("accept") or "").lower()
        if not selected_ids:
            if wants_json:
                return JSONResponse({"ok": False, "error": "Select at least one channel"}, status_code=400)
            return RedirectResponse("/channels?error=Select+at+least+one+channel", status_code=303)
        chosen_nodes = db.scalars(
            select(Node).where(Node.id.in_(set(bulk_node_ids)), Node.enabled.is_(True)).order_by(Node.node_type, Node.name)
        ).all() if bulk_node_ids else []
        if not chosen_nodes:
            if wants_json:
                return JSONResponse({"ok": False, "error": "Select at least one node"}, status_code=400)
            return RedirectResponse("/channels?error=Select+at+least+one+node", status_code=303)
        try:
            changes = _bulk_node_assignment_db_fast(db, action, selected_ids, list(chosen_nodes))
        except Exception as exc:
            db.rollback()
            error_text = f"Bulk Node assignment could not be saved: {exc}"[:3500]
            if wants_json:
                return JSONResponse({"ok": False, "error": error_text}, status_code=500)
            return RedirectResponse(f"/channels?{urlencode({'error': error_text})}", status_code=303)

        changed_targets = [
            (
                int(item["channel_id"]),
                str(item["channel_name"]),
                {int(value) for value in item["removed_ids"]},
                {int(value) for value in item["added_ids"]},
            )
            for item in changes
            if item["removed_ids"] or item["added_ids"]
        ]
        # STREAMFORGE_BULK_NODE_ASSIGNMENT_RESPONSE_FIRST_V1137:
        # New replicas were persisted to the targeted config/logo queue inside
        # _bulk_node_assignment_db_fast().  Do not submit add work to an executor
        # from the request thread: on busy Main hosts that handoff could keep the
        # AJAX fetch open even though the assignment transaction had committed.
        # Only removal cleanup still uses the historical best-effort worker.
        removal_targets = [
            (channel_id, channel_name, removed_ids, set())
            for channel_id, channel_name, removed_ids, _added_ids in changed_targets
            if removed_ids
        ]
        queued = queue_bulk_node_assignment_sync(removal_targets) if removal_targets else True
        message_text = (
            f"Node assignment saved for {len(changes)} channel(s); "
            "added replicas are queued durably and synchronize in background"
        )
        warning_text = "" if queued else "Node assignment was saved; removal cleanup could not be queued, but added replicas remain durably queued"
        if wants_json:
            return JSONResponse({
                "ok": True,
                "message": message_text,
                "warning": warning_text,
                "channels": [
                    {
                        "channel_id": int(item["channel_id"]),
                        "node_ids": [int(value) for value in item["node_ids"]],
                        "nodes": list(item["nodes"]),
                    }
                    for item in changes
                ],
            })
        query = {"message": message_text}
        if warning_text:
            query["error"] = warning_text
        return RedirectResponse(f"/channels?{urlencode(query)}", status_code=303)

    if action == "start_all":
        targets = [channel for channel in all_channels if channel.enabled]
        operation = "start"
    elif action == "restart_all":
        targets = [
            channel for channel in all_channels
            if channel.enabled and (
                bool(getattr(channel, "desired_running", False))
                or channel.status in {"starting", "running", "restarting", "degraded"}
            )
        ]
        operation = "restart"
    elif action == "stop_all":
        targets = all_channels
        operation = "stop"
    elif action == "start_selected":
        targets = selected_channels
        operation = "start"
    elif action == "restart_selected":
        targets = selected_channels
        operation = "restart"
    elif action == "stop_selected":
        targets = selected_channels
        operation = "stop"
    else:
        targets = selected_channels
        operation = "delete"

    for channel in targets:
        try:
            if operation == "start":
                if not channel.enabled:
                    channel.last_error = "Channel is disabled"
                    continue
                node_controller.start(channel.id)
            elif operation == "restart":
                node_controller.restart(channel.id)
            else:
                node_controller.stop(channel.id)
        except RuntimeError as exc:
            current = db.get(Channel, channel.id)
            if current:
                current.last_error = str(exc)
                current.status = "error"

    db.commit()
    return RedirectResponse("/channels", status_code=303)



def sync_category_catalog_to_remote_nodes(db: Session, *, channel_ids: set[int] | None = None) -> list[str]:
    errors: list[str] = []
    nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.name)).all()
    for node in nodes:
        try:
            node_controller.sync_node_mode(node)
        except NodeError as exc:
            errors.append(f"{node.name}: {exc}")
    for channel_id in sorted(channel_ids or set()):
        errors.extend(node_controller.sync_channel_to_nodes(channel_id))
    return errors


@app.get("/categories", response_class=HTMLResponse, dependencies=[Depends(permission_required("categories.view"))])
def categories_page(
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    category_counts = {
        int(category_id): int(count)
        for category_id, count in db.execute(
            select(channel_category_links.c.category_id, func.count(func.distinct(channel_category_links.c.channel_id)))
            .group_by(channel_category_links.c.category_id)
        ).all()
    }
    uncategorized_count = db.scalar(
        select(func.count(Channel.id)).where(~Channel.id.in_(select(channel_category_links.c.channel_id)))
    ) or 0
    return render(
        request,
        "categories.html",
        db,
        categories=categories,
        category_counts=category_counts,
        uncategorized_count=uncategorized_count,
        error=error,
        message=message,
    )


@app.get("/categories/{category_id}/channels", response_class=HTMLResponse, dependencies=[Depends(permission_required("categories.view"))])
def category_channels_page(
    category_id: int,
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    category = db.get(ChannelCategory, category_id)
    if not category:
        raise HTTPException(404)
    channels = ordered_category_channels(db, category)
    return render(
        request,
        "category_channels.html",
        db,
        category=category,
        channels=channels,
        error=error,
        message=message,
    )


@app.post("/categories/{category_id}/channels/reorder", dependencies=[Depends(permission_required("categories.reorder"))])
def category_channels_reorder(
    category_id: int,
    request: Request,
    channel_order: str = Form("[]"),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    category = db.get(ChannelCategory, category_id)
    if not category:
        raise HTTPException(404)
    channels = ordered_category_channels(db, category)
    valid = {int(item.id): item for item in channels}
    try:
        raw = json.loads(channel_order or "[]")
    except json.JSONDecodeError:
        return RedirectResponse(f"/categories/{category_id}/channels?error=Invalid+channel+order", status_code=303)
    ids: list[int] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value in valid and value not in ids:
            ids.append(value)
    ordered = [valid[item] for item in ids if item in valid]
    ordered.extend(item for item in channels if item.id not in {row.id for row in ordered})
    for index, channel in enumerate(ordered, 1):
        channel.sort_order = index * 10
    db.commit()
    sync_errors = []
    for channel in ordered:
        sync_errors.extend(node_controller.sync_channel_to_nodes(channel.id))
    suffix = "&error=" + quote_plus("; ".join(sync_errors)) if sync_errors else ""
    return RedirectResponse(f"/categories/{category_id}/channels?message=Channel+order+saved{suffix}", status_code=303)


@app.post("/categories/new", dependencies=[Depends(permission_required("categories.create"))])
def category_create(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    cleaned = name.strip()[:120]
    if not cleaned:
        return RedirectResponse("/categories?error=Category+name+is+required", status_code=303)
    if category_by_name(db, cleaned):
        return RedirectResponse("/categories?error=Category+already+exists", status_code=303)
    get_or_create_category(db, cleaned)
    db.commit()
    sync_errors = sync_category_catalog_to_remote_nodes(db)
    suffix = "&error=" + quote_plus("; ".join(sync_errors)) if sync_errors else ""
    return RedirectResponse("/categories?message=Category+created" + suffix, status_code=303)


@app.post("/categories/{category_id}/rename", dependencies=[Depends(permission_required("categories.edit"))])
def category_rename(
    category_id: int,
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    category = db.get(ChannelCategory, category_id)
    if not category:
        raise HTTPException(404)
    cleaned = name.strip()[:120]
    duplicate = category_by_name(db, cleaned) if cleaned else None
    if not cleaned:
        return RedirectResponse("/categories?error=Category+name+is+required", status_code=303)
    if duplicate and duplicate.id != category.id:
        return RedirectResponse("/categories?error=Category+already+exists", status_code=303)
    affected_ids = {channel.id for channel in category.linked_channels}
    category.name = cleaned
    category.slug = unique_category_slug(db, cleaned, category.id)
    db.commit()
    sync_errors = sync_category_catalog_to_remote_nodes(db, channel_ids=affected_ids)
    suffix = "&error=" + quote_plus("; ".join(sync_errors)) if sync_errors else ""
    return RedirectResponse("/categories?message=Category+updated" + suffix, status_code=303)


@app.post("/categories/reorder", dependencies=[Depends(permission_required("categories.reorder"))])
def category_reorder(
    request: Request,
    category_order: str = Form("[]"),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    try:
        raw = json.loads(category_order or "[]")
    except json.JSONDecodeError:
        return RedirectResponse("/categories?error=Invalid+category+order", status_code=303)
    ids: list[int] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    valid = {item.id: item for item in categories}
    ordered = [valid[item] for item in ids if item in valid]
    ordered.extend(item for item in categories if item.id not in {row.id for row in ordered})
    for index, category in enumerate(ordered, 1):
        category.sort_order = index * 10
    db.commit()
    sync_errors = sync_category_catalog_to_remote_nodes(db)
    suffix = "&error=" + quote_plus("; ".join(sync_errors)) if sync_errors else ""
    return RedirectResponse("/categories?message=Category+order+saved" + suffix, status_code=303)


@app.post("/categories/{category_id}/delete", dependencies=[Depends(permission_required("categories.delete"))])
def category_delete(category_id: int, request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    category = db.get(ChannelCategory, category_id)
    affected_ids: set[int] = set()
    if category:
        affected_ids = {channel.id for channel in category.linked_channels}
        for channel in list(category.linked_channels):
            remaining = [item for item in channel_category_list(channel) if item.id != category.id]
            assign_channel_categories(channel, remaining)
        db.delete(category)
        db.commit()
    sync_errors = sync_category_catalog_to_remote_nodes(db, channel_ids=affected_ids)
    suffix = "&error=" + quote_plus("; ".join(sync_errors)) if sync_errors else ""
    return RedirectResponse("/categories?message=Category+deleted" + suffix, status_code=303)


@app.get("/channels/import", response_class=HTMLResponse, dependencies=[Depends(permission_required("imports.view"))])
def channel_import_page(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    ensure_local_node(db)
    db.commit()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    return render(
        request,
        "channel_import.html",
        db,
        categories=categories,
        nodes=nodes,
        result=None,
        error=None,
        **encoder_template_context(),
    )


@app.post("/channels/import", response_class=HTMLResponse, dependencies=[Depends(permission_required("imports.execute"))])
async def channel_import(
    request: Request,
    playlist_file: UploadFile | None = File(None),
    playlist_text: str = Form(""),
    remote_url: str = Form(""),
    duplicate_policy: str = Form("skip"),
    category_mode: str = Form("source"),
    category_id: str = Form(""),
    node_ids: list[int] = Form(default=[]),
    create_missing_categories: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    auto_restart: Optional[str] = Form(None),
    start_after_import: Optional[str] = Form(None),
    video_codec: str = Form("copy"),
    video_bitrate: str = Form("2500k"),
    audio_codec: str = Form("copy"),
    audio_bitrate: str = Form("128k"),
    output_type: str = Form("hls"),
    hls_segment_time: int = Form(1),
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
    ensure_local_node(db)
    db.commit()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    max_bytes = 5 * 1024 * 1024
    content = ""
    source_name = "pasted text"

    try:
        if playlist_file and playlist_file.filename:
            raw = await playlist_file.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("Import file is larger than 5 MB")
            content = raw.decode("utf-8-sig", errors="replace")
            source_name = playlist_file.filename
        elif playlist_text.strip():
            content = playlist_text
        elif remote_url.strip():
            url = remote_url.strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("Remote playlist URL must start with http:// or https://")
            req = urllib.request.Request(url, headers={"User-Agent": settings.http_user_agent})
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    raw = response.read(max_bytes + 1)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ValueError(f"Could not download playlist: {exc}") from exc
            if len(raw) > max_bytes:
                raise ValueError("Remote playlist is larger than 5 MB")
            content = raw.decode("utf-8-sig", errors="replace")
            source_name = url
        else:
            raise ValueError("Upload an M3U/CSV file, paste playlist text, or enter a remote URL")

        import_format, items = detect_and_parse(content, source_name)
        if not items:
            raise ValueError("No valid stream URLs were found in the import source")
        if len(items) > 5000:
            raise ValueError("A maximum of 5000 streams can be imported at once")
        if duplicate_policy not in {"skip", "update"}:
            raise ValueError("Invalid duplicate policy")
        if category_mode not in {"source", "selected", "none"}:
            raise ValueError("Invalid category mode")

        fallback_category = selected_category(db, category_id)
        assigned_nodes = selected_nodes(db, node_ids) if has_permission(admin, "channels.assign_node") else [ensure_local_node(db)]
        create_categories = as_bool(create_missing_categories)
        default_enabled = as_bool(enabled)
        default_auto_restart = as_bool(auto_restart)
        start_imported = as_bool(start_after_import)
        default_video_codec = normalize_video_codec(video_codec)
        default_output_type = normalize_output_type(output_type)
        default_hls_segment_time = max(1, min(20, hls_segment_time))

        existing_channels = db.scalars(select(Channel).order_by(Channel.id)).all()
        by_url = {channel.input_url: channel for channel in existing_channels}
        by_slug = {channel.slug: channel for channel in existing_channels}
        used_slugs = set(by_slug)
        restart_after_commit: set[int] = set()
        start_after_commit: set[int] = set()
        created = 0
        updated = 0
        skipped = 0

        for item in items:
            item_url = item.input_url.strip()
            item_name = item.name.strip()[:150] or f"Imported stream {created + updated + skipped + 1}"
            base_slug = slugify(item_name)[:150]
            existing = by_url.get(item_url) or by_slug.get(base_slug)
            if existing and duplicate_policy == "skip":
                skipped += 1
                continue

            category = import_category_for_item(
                db, item, category_mode, fallback_category, create_categories
            )
            values = {
                "name": item_name,
                "input_url": item_url,
                "program_id": item.program_id,
                "logo_url": normalize_channel_logo_url(item.logo_url) if item.logo_url else None,
                "category": category,
                "enabled": default_enabled if item.enabled is None else item.enabled,
                "auto_restart": default_auto_restart if item.auto_restart is None else item.auto_restart,
                "video_codec": normalize_video_codec(item.video_codec or default_video_codec),
                "video_bitrate": clean_import_profile(item.video_bitrate, video_bitrate.strip() or "2500k"),
                "audio_codec": clean_import_profile(item.audio_codec, audio_codec.strip() or "auto"),
                "audio_bitrate": clean_import_profile(item.audio_bitrate, audio_bitrate.strip() or "128k"),
                "output_type": normalize_output_type(item.output_type or default_output_type),
                "output_url": (item.output_url or "").strip() or None,
                "hls_segment_time": default_hls_segment_time,
            }

            if existing:
                was_running = existing.status in {"starting", "running", "restarting", "degraded"} or bool(
                    main_channel_runtime(existing).get("alive")
                )
                old_node_ids = {item.id for item in node_controller.assigned_nodes(existing)}
                if was_running:
                    node_controller.stop(existing.id)
                    restart_after_commit.add(existing.id)
                new_node_ids = {item.id for item in assigned_nodes}
                for removed_node_id in sorted(old_node_ids - new_node_ids):
                    node_controller.forget_on_node(existing.id, removed_node_id)
                old_url = existing.input_url
                old_slug = existing.slug
                for key, value in values.items():
                    setattr(existing, key, value)
                set_channel_nodes(existing, assigned_nodes)
                by_url.pop(old_url, None)
                by_url[item_url] = existing
                by_slug.pop(old_slug, None)
                by_slug[existing.slug] = existing
                updated += 1
            else:
                slug = base_slug
                suffix = 2
                while slug in used_slugs:
                    tail = f"-{suffix}"
                    slug = f"{base_slug[:150-len(tail)]}{tail}"
                    suffix += 1
                used_slugs.add(slug)
                channel = Channel(
                    slug=slug,
                    width=None,
                    height=None,
                    fps=None,
                    preset="ultrafast",
                    sort_order=next_channel_sort_order(db, category.id if category else None),
                    node=assigned_nodes[0],
                    nodes=assigned_nodes,
                    **values,
                )
                db.add(channel)
                db.flush()
                by_url[item_url] = channel
                by_slug[slug] = channel
                created += 1
                if start_imported and channel.enabled:
                    start_after_commit.add(channel.id)

        db.commit()

        start_errors: list[str] = []
        for channel_id_to_restart in sorted(restart_after_commit):
            channel = db.get(Channel, channel_id_to_restart)
            if not channel or not channel.enabled:
                continue
            try:
                node_controller.restart(channel.id)
            except RuntimeError as exc:
                start_errors.append(f"{channel.name}: {exc}")
        for channel_id_to_start in sorted(start_after_commit - restart_after_commit):
            channel = db.get(Channel, channel_id_to_start)
            if not channel or not channel.enabled:
                continue
            try:
                node_controller.start(channel.id)
            except RuntimeError as exc:
                start_errors.append(f"{channel.name}: {exc}")

        categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
        result = {
            "format": import_format,
            "source": source_name,
            "total": len(items),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "start_errors": start_errors[:10],
        }
        return render(
            request,
            "channel_import.html",
            db,
            categories=categories,
            nodes=nodes,
            result=result,
            error=None,
            **encoder_template_context(),
        )
    except ValueError as exc:
        return render(
            request,
            "channel_import.html",
            db,
            categories=categories,
            nodes=nodes,
            result=None,
            error=str(exc),
            **encoder_template_context(),
        )



def role_is_unrestricted(role: Role | None) -> bool:
    return role is not None and SUPERUSER_PERMISSION in decode_permissions(role.permissions)


# STREAMFORGE_SUPER_ADMIN_RBAC_GUARD_V2257:
def admin_is_super_admin(admin: AdminUser | None) -> bool:
    return bool(admin and admin.is_active and role_is_unrestricted(admin.role))


# STREAMFORGE_RBAC_HIERARCHY_CEILING_V2270:
def role_is_within_actor_ceiling(actor: AdminUser | None, role: Role | None) -> bool:
    if not actor or not role:
        return False
    if admin_is_super_admin(actor):
        return True
    if role_is_unrestricted(role):
        return False
    actor_permissions = set(role_permission_set(actor.role))
    role_permissions = set(role_permission_set(role))
    return role_permissions.issubset(actor_permissions)


def panel_user_is_within_actor_ceiling(actor: AdminUser | None, user: AdminUser | None) -> bool:
    if not actor or not user:
        return False
    if admin_is_super_admin(actor):
        return True
    return role_is_within_actor_ceiling(actor, user.role)


def visible_panel_roles(db: Session, actor: AdminUser) -> list[Role]:
    roles = db.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)).all()
    if admin_is_super_admin(actor):
        return roles
    return [role for role in roles if role_is_within_actor_ceiling(actor, role)]


def panel_user_is_protected_super_admin(user: AdminUser | None) -> bool:
    return bool(user and role_is_unrestricted(user.role))


# STREAMFORGE_ROLE_PERMISSION_CEILING_V2259:
def role_assignable_permissions_for_admin(admin: AdminUser | None) -> set[str]:
    """Permissions this actor is allowed to grant to another role."""
    # STREAMFORGE_ROLE_PERMISSION_NAME_FIX_V2261:
    if not admin:
        return set()
    if admin_is_super_admin(admin):
        return set(ALL_PERMISSION_KEYS)

    # STREAMFORGE_ROLE_ADMIN_GRANT_WITH_HIERARCHY_V2270:
    # Users may grant only permissions they already own. Hierarchy rules block
    # any role/user above the current user's permission set.
    return {
        key
        for key in role_permission_set(admin.role)
        if key in ALL_PERMISSION_KEYS
    }


def permission_groups_for_role_editor(admin: AdminUser | None):
    allowed = role_assignable_permissions_for_admin(admin)
    # STREAMFORGE_ROLE_FORM_FILTERED_GROUPS_MAPPING_FIX_V2262:
    # role_form.html uses permission_groups.items(), so preserve mapping shape.
    filtered_groups: dict[str, list[tuple[str, str, str]]] = {}
    for group_name, group_items in PERMISSION_GROUPS.items():
        visible_items = [item for item in group_items if item[0] in allowed]
        if visible_items:
            filtered_groups[group_name] = visible_items
    return filtered_groups


def active_unrestricted_admins(db: Session) -> list[AdminUser]:
    admins = db.scalars(
        select(AdminUser).where(AdminUser.is_active.is_(True), AdminUser.main_panel_access.is_(True))
    ).all()
    return [admin for admin in admins if role_is_unrestricted(admin.role)]


def sync_panel_users_for_node_ids(node_ids: set[int], request: Request, db: Session) -> list[str]:
    errors: list[str] = []
    for node_id in sorted(node_ids):
        node = db.get(Node, node_id)
        if not node or node.node_type != "remote":
            continue
        ok, detail = sync_panel_user_registry(node, request)
        if not ok:
            errors.append(f"{node.name}: {detail}")
    return errors


def panel_admin_redirect(path: str, *, error: str = "", message: str = "") -> RedirectResponse:
    params = {}
    if error:
        params["error"] = error
    if message:
        params["message"] = message
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{path}{suffix}", status_code=303)



@app.get("/account/password", response_class=HTMLResponse)
def account_password_page(
    request: Request,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    return render(request, "account_password.html", db, message=message, error=error or None)


@app.post("/account/password", response_class=HTMLResponse)
def account_password_change(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    # STREAMFORGE_PANEL_USER_SELF_PASSWORD_V2254:
    admin = current_admin(request, db)
    if not admin:
        return redirect_login()
    error = ""
    if not verify_password(current_password, admin.password_hash):
        error = "Current password is incorrect."
    elif len(new_password) < 8:
        error = "New password must contain at least 8 characters."
    elif new_password != new_password_confirm:
        error = "New password confirmation does not match."
    elif verify_password(new_password, admin.password_hash):
        error = "New password must be different from the current password."
    if error:
        return render(request, "account_password.html", db, message="", error=error)

    admin.password_hash = hash_password(new_password)
    affected_node_ids = {item.id for item in admin.nodes}
    db.commit()
    sync_errors = sync_panel_users_for_node_ids(affected_node_ids, request, db)
    log_event("Panel user changed own password", scope="auth", actor=admin.username, details={"ip": client_ip(request)})
    message = "Password changed successfully."
    if sync_errors:
        message += " Node sync warning: " + "; ".join(sync_errors)
    # STREAMFORGE_MAIN_PASSWORD_PREFIX_FIX_V2255:
    root = str(request.scope.get("root_path") or "").rstrip("/")
    return RedirectResponse(root + "/account/password?message=" + quote_plus(message), status_code=303)


@app.get("/admin-users", response_class=HTMLResponse, dependencies=[Depends(permission_required("panel_users.view"))])
def admin_users_page(
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    actor = enforce_permission(request, db, "panel_users.view")
    panel_users = db.scalars(select(AdminUser).order_by(AdminUser.username)).unique().all()
    if not admin_is_super_admin(actor):
        panel_users = [
            user for user in panel_users
            if panel_user_is_within_actor_ceiling(actor, user)
        ]
    return render(
        request,
        "admin_users.html",
        db,
        panel_users=panel_users,
        error=error,
        message=message,
    )


@app.get("/admin-users/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("panel_users.create"))])
def admin_user_new_page(request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "panel_users.create")
    roles = visible_panel_roles(db, actor)
    nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.name)).all()
    return render(request, "admin_user_form.html", db, panel_user=None, roles=roles, nodes=nodes, error=None)


@app.post("/admin-users/new", dependencies=[Depends(permission_required("panel_users.create"))])
def admin_user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    role_id: int = Form(...),
    is_active: Optional[str] = Form(None),
    main_panel_access: Optional[str] = Form(None),
    node_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    actor = enforce_permission(request, db, "panel_users.create")
    cleaned_username = username.strip()
    roles = visible_panel_roles(db, actor)
    nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.name)).all()
    selected_nodes = [item for item in nodes if item.id in set(node_ids)]
    role = db.get(Role, role_id)
    error = None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", cleaned_username):
        error = "Username must be 3–80 characters using letters, numbers, dot, underscore or dash."
    elif db.scalar(select(AdminUser).where(func.lower(AdminUser.username) == cleaned_username.lower())):
        error = "Username already exists."
    elif not role:
        error = "Select a valid role."
    elif not role_is_within_actor_ceiling(actor, role):
        error = "You cannot assign a role above your own permission level."
    elif len(password) < 8:
        error = "Password must contain at least 8 characters."
    elif password != password_confirm:
        error = "Password confirmation does not match."
    elif not as_bool(main_panel_access) and not selected_nodes:
        error = "Select Main Panel access or at least one remote node."
    if error:
        draft = type("PanelUserDraft", (), {
            "username": cleaned_username,
            "role_id": role_id,
            "is_active": as_bool(is_active),
            "main_panel_access": as_bool(main_panel_access),
            "nodes": selected_nodes,
            "id": None,
        })()
        return render(request, "admin_user_form.html", db, panel_user=draft, roles=roles, nodes=nodes, error=error)

    panel_user = AdminUser(
        username=cleaned_username,
        password_hash=hash_password(password),
        is_active=as_bool(is_active),
        main_panel_access=as_bool(main_panel_access),
        role=role,
        nodes=selected_nodes,
    )
    db.add(panel_user)
    db.commit()
    sync_errors = sync_panel_users_for_node_ids({item.id for item in selected_nodes}, request, db)
    return panel_admin_redirect(
        "/admin-users",
        message="Panel user created",
        error="; ".join(sync_errors),
    )


@app.get("/admin-users/{admin_user_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("panel_users.edit"))])
def admin_user_edit_page(admin_user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "panel_users.edit")
    panel_user = db.get(AdminUser, admin_user_id)
    if not panel_user:
        raise HTTPException(404)
    if not panel_user_is_within_actor_ceiling(actor, panel_user):
        raise HTTPException(404)
    roles = visible_panel_roles(db, actor)
    nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.name)).all()
    return render(request, "admin_user_form.html", db, panel_user=panel_user, roles=roles, nodes=nodes, error=None)


@app.post("/admin-users/{admin_user_id}/edit", dependencies=[Depends(permission_required("panel_users.edit"))])
def admin_user_update(
    admin_user_id: int,
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    password_confirm: str = Form(""),
    role_id: int = Form(...),
    is_active: Optional[str] = Form(None),
    main_panel_access: Optional[str] = Form(None),
    node_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    actor = enforce_permission(request, db, "panel_users.edit")
    panel_user = db.get(AdminUser, admin_user_id)
    if not panel_user:
        raise HTTPException(404)
    if not panel_user_is_within_actor_ceiling(actor, panel_user):
        raise HTTPException(404)
    cleaned_username = username.strip()
    role = db.get(Role, role_id)
    roles = visible_panel_roles(db, actor)
    nodes = db.scalars(select(Node).where(Node.node_type == "remote", Node.enabled.is_(True)).order_by(Node.name)).all()
    selected_nodes = [item for item in nodes if item.id in set(node_ids)]
    old_node_ids = {item.id for item in panel_user.nodes}
    duplicate = db.scalar(
        select(AdminUser).where(
            func.lower(AdminUser.username) == cleaned_username.lower(),
            AdminUser.id != panel_user.id,
        )
    )
    new_active = as_bool(is_active)
    new_main_access = as_bool(main_panel_access)
    error = None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", cleaned_username):
        error = "Username must be 3–80 characters using letters, numbers, dot, underscore or dash."
    elif duplicate:
        error = "Username already exists."
    elif not role:
        error = "Select a valid role."
    elif not role_is_within_actor_ceiling(actor, role):
        error = "You cannot assign a role above your own permission level."
    elif password and len(password) < 8:
        error = "Password must contain at least 8 characters."
    elif password != password_confirm:
        error = "Password confirmation does not match."
    elif not new_main_access and not selected_nodes:
        error = "Select Main Panel access or at least one remote node."
    elif panel_user.id == actor.id and not new_active:
        error = "You cannot disable your own panel account."
    elif panel_user.id == actor.id and not new_main_access:
        error = "You cannot remove your own Main Panel access."
    elif role_is_unrestricted(panel_user.role) and (not new_active or not new_main_access or not role_is_unrestricted(role)):
        unrestricted = active_unrestricted_admins(db)
        if len(unrestricted) <= 1 and panel_user in unrestricted:
            error = "At least one active Super Admin account must remain."
    if error:
        panel_user.main_panel_access = new_main_access
        panel_user.nodes = selected_nodes
        return render(request, "admin_user_form.html", db, panel_user=panel_user, roles=roles, nodes=nodes, error=error)

    panel_user.username = cleaned_username
    panel_user.role = role
    panel_user.is_active = new_active
    panel_user.main_panel_access = new_main_access
    panel_user.nodes = selected_nodes
    if password:
        panel_user.password_hash = hash_password(password)
    db.commit()
    affected_node_ids = old_node_ids | {item.id for item in selected_nodes}
    sync_errors = sync_panel_users_for_node_ids(affected_node_ids, request, db)
    return panel_admin_redirect(
        "/admin-users",
        message="Panel user updated",
        error="; ".join(sync_errors),
    )


@app.post("/admin-users/{admin_user_id}/delete", dependencies=[Depends(permission_required("panel_users.delete"))])
def admin_user_delete(admin_user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "panel_users.delete")
    panel_user = db.get(AdminUser, admin_user_id)
    if not panel_user:
        return panel_admin_redirect("/admin-users")
    if not panel_user_is_within_actor_ceiling(actor, panel_user):
        raise HTTPException(404)
    # STREAMFORGE_BLOCK_OWN_PANEL_USER_DELETE_V2270:
    if panel_user.id == actor.id:
        return panel_admin_redirect("/admin-users", error="You cannot delete your own panel account.")
    if panel_user.is_active and role_is_unrestricted(panel_user.role) and len(active_unrestricted_admins(db)) <= 1:
        return panel_admin_redirect("/admin-users", error="At least one active Super Admin account must remain.")
    old_node_ids = {item.id for item in panel_user.nodes}
    db.delete(panel_user)
    db.commit()
    sync_errors = sync_panel_users_for_node_ids(old_node_ids, request, db)
    return panel_admin_redirect(
        "/admin-users",
        message="Panel user deleted",
        error="; ".join(sync_errors),
    )


@app.get("/roles", response_class=HTMLResponse, dependencies=[Depends(permission_required("roles.view"))])
def roles_page(
    request: Request,
    error: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    actor = enforce_permission(request, db, "roles.view")
    roles = visible_panel_roles(db, actor)
    role_rows = [
        {
            "role": role,
            "permissions": decode_permissions(role.permissions),
            "permission_count": len(ALL_PERMISSION_KEYS) if role_is_unrestricted(role) else len(decode_permissions(role.permissions)),
        }
        for role in roles
    ]
    return render(request, "roles.html", db, role_rows=role_rows, error=error, message=message)


@app.get("/roles/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("roles.create"))])
def role_new_page(request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "roles.create")
    return render(
        request,
        "role_form.html",
        db,
        role=None,
        selected_permissions=set(),
        permission_groups=permission_groups_for_role_editor(actor),
        error=None,
    )


@app.post("/roles/new", dependencies=[Depends(permission_required("roles.create"))])
def role_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    permissions: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    # STREAMFORGE_ROLE_CREATE_BY_PERMISSION_V2266:
    actor = enforce_permission(request, db, "roles.create")
    allowed_permissions = role_assignable_permissions_for_admin(actor)
    cleaned_name = name.strip()[:120]
    selected = set(permissions)
    forbidden = selected - allowed_permissions
    if forbidden:
        raise HTTPException(403, "You cannot grant permissions that you do not have.")
    error = None
    if len(cleaned_name) < 2:
        error = "Role name must contain at least 2 characters."
    elif db.scalar(select(Role).where(func.lower(Role.name) == cleaned_name.lower())):
        error = "Role name already exists."
    if error:
        draft = type("RoleDraft", (), {
            "name": cleaned_name,
            "description": description.strip(),
            "is_system": False,
            "id": None,
        })()
        return render(
            request,
            "role_form.html",
            db,
            role=draft,
            selected_permissions=selected & allowed_permissions,
            permission_groups=permission_groups_for_role_editor(actor),
            error=error,
        )

    role = Role(
        name=cleaned_name,
        description=description.strip() or None,
        permissions=encode_permissions(selected & allowed_permissions),
        is_system=False,
    )
    db.add(role)
    db.commit()
    return panel_admin_redirect("/roles", message="Role created")


@app.get("/roles/{role_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("roles.edit"))])
def role_edit_page(role_id: int, request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "roles.edit")
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404)
    if not role_is_within_actor_ceiling(actor, role):
        raise HTTPException(404)
    # STREAMFORGE_BLOCK_OWN_ASSIGNED_ROLE_EDIT_V2263:
    if actor.role_id == role.id and not admin_is_super_admin(actor):
        raise HTTPException(403, "You cannot edit the role currently assigned to your own account.")
    allowed_permissions = role_assignable_permissions_for_admin(actor)
    existing_permissions = set(decode_permissions(role.permissions))
    if not admin_is_super_admin(actor) and not existing_permissions.issubset(allowed_permissions):
        raise HTTPException(403, "This role contains permissions you do not have and cannot be edited by your account.")
    return render(
        request,
        "role_form.html",
        db,
        role=role,
        selected_permissions=existing_permissions & allowed_permissions,
        permission_groups=permission_groups_for_role_editor(actor),
        error=None,
    )


@app.post("/roles/{role_id}/edit", dependencies=[Depends(permission_required("roles.edit"))])
def role_update(
    role_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    permissions: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    actor = enforce_permission(request, db, "roles.edit")
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404)
    if not role_is_within_actor_ceiling(actor, role):
        raise HTTPException(404)
    if actor.role_id == role.id and not admin_is_super_admin(actor):
        raise HTTPException(403, "You cannot edit the role currently assigned to your own account.")
    allowed_permissions = role_assignable_permissions_for_admin(actor)
    existing_permissions = set(decode_permissions(role.permissions))
    if not admin_is_super_admin(actor) and not existing_permissions.issubset(allowed_permissions):
        raise HTTPException(403, "This role contains permissions you do not have and cannot be edited by your account.")
    submitted_permissions = set(permissions)
    forbidden = submitted_permissions - allowed_permissions
    if forbidden:
        raise HTTPException(403, "You cannot grant permissions that you do not have.")
    if role.is_system:
        return panel_admin_redirect("/roles", error="The built-in Super Admin role cannot be modified.")
    cleaned_name = name.strip()[:120]
    duplicate = db.scalar(
        select(Role).where(func.lower(Role.name) == cleaned_name.lower(), Role.id != role.id)
    )
    if len(cleaned_name) < 2:
        return render(
            request, "role_form.html", db, role=role,
            selected_permissions=submitted_permissions & allowed_permissions,
            permission_groups=permission_groups_for_role_editor(actor),
            error="Role name must contain at least 2 characters.",
        )
    if duplicate:
        return render(
            request, "role_form.html", db, role=role,
            selected_permissions=submitted_permissions & allowed_permissions,
            permission_groups=permission_groups_for_role_editor(actor),
            error="Role name already exists.",
        )
    role.name = cleaned_name
    role.description = description.strip() or None
    role.permissions = encode_permissions(submitted_permissions & allowed_permissions)
    db.commit()
    return panel_admin_redirect("/roles", message="Role updated")


@app.post("/roles/{role_id}/delete", dependencies=[Depends(permission_required("roles.delete"))])
def role_delete(role_id: int, request: Request, db: Session = Depends(get_db)):
    actor = enforce_permission(request, db, "roles.delete")
    role = db.get(Role, role_id)
    if not role:
        return panel_admin_redirect("/roles")
    if not role_is_within_actor_ceiling(actor, role):
        raise HTTPException(404)
    # STREAMFORGE_BLOCK_OWN_ASSIGNED_ROLE_DELETE_V2264:
    if actor.role_id == role.id and not admin_is_super_admin(actor):
        raise HTTPException(403, "You cannot delete the role currently assigned to your own account.")
    if role.is_system:
        return panel_admin_redirect("/roles", error="The built-in Super Admin role cannot be deleted.")
    if role.panel_users:
        return panel_admin_redirect("/roles", error="Move all panel users to another role before deleting this role.")
    db.delete(role)
    db.commit()
    return panel_admin_redirect("/roles", message="Role deleted")


def parse_playlist_order(raw: str | None, allowed_ids: set[int]) -> list[int]:
    return parse_channel_order(raw, allowed_ids)


def parse_playlist_category_order(raw: str | None, channels: list[Channel]) -> list[str]:
    return parse_category_order(raw, channels)


def _category_grouped_channels(
    channels: list[Channel],
    raw_order: str | None,
    raw_category_order: str | None = None,
) -> list[Channel]:
    return apply_playlist_order(channels, raw_order, raw_category_order)


def enabled_main_playlist_channels(db: Session) -> list[Channel]:
    return list(db.scalars(
        select(Channel).where(
            Channel.enabled.is_(True),
            func.lower(Channel.output_type) == "hls",
        ).order_by(Channel.name)
    ).all())


def playlist_profile_channels(playlist: PlaylistProfile) -> list[Channel]:
    """Return the profile's current source catalogue.

    Custom profiles use their saved relationship.  Dynamic profiles mirror all
    currently enabled Main HLS channels, so newly enabled channels appear
    without editing and disabled channels disappear automatically.
    """
    if bool(getattr(playlist, "all_enabled_channels", False)):
        session = object_session(playlist)
        if session is not None:
            return enabled_main_playlist_channels(session)
    return list(playlist.channels)


def ordered_playlist_channels(playlist: PlaylistProfile) -> list[Channel]:
    """Return the playlist profile's own category/channel hierarchy."""
    return _category_grouped_channels(
        playlist_profile_channels(playlist),
        playlist.channel_order,
        playlist.category_order,
    )


def ordered_user_channels(user: StreamUser) -> list[Channel]:
    if user.playlist:
        return ordered_playlist_channels(user.playlist)
    return _category_grouped_channels(list(user.channels), user.playlist_order)


def local_channel_process_alive(channel: Channel) -> bool:
    """Verify that the persisted Local FFmpeg PID is the live process for this channel.

    STREAMFORGE_PUBLIC_STRICT_LOCAL_UP_V63R3: Public workers intentionally do
    not own the control worker's in-memory StreamManager.  A persisted
    running/restarting status is therefore not sufficient: stale HLS files and
    a stale DB state must never advertise an offline channel.  Verify the
    persisted PID against /proc and ensure it is an FFmpeg process whose
    command line targets this channel's Local HLS directory.
    """
    try:
        pid = int(getattr(channel, "pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    proc = Path(f"/proc/{pid}")
    try:
        exe_name = (proc / "exe").resolve(strict=True).name.lower()
        if "ffmpeg" not in exe_name:
            return False
        cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except (OSError, RuntimeError):
        return False
    expected_hls_dir = str((settings.hls_root / channel.slug).resolve())
    return expected_hls_dir in cmdline


def filter_catalog_channels_for_request(
    user: StreamUser, channels: list[Channel], request: Request, db: Session
) -> list[Channel]:
    """Hide Main catalogue entries when no preferred/fallback Node permits the client.

    STREAMFORGE_MAIN_CATALOG_IP_WHITELIST_V97
    STREAMFORGE_MAIN_CATALOG_FULL_POLICY_V99R18
    STREAMFORGE_CATALOG_STATIC_STRICT_FALLBACK_V100: Strict-prefix and Static
    routes are preferences. Catalogue visibility remains available when the
    preferred target cannot be used but another assigned fallback Node permits
    the client. Readiness is checked separately by the online/playback path.
    """
    items = list(channels or [])
    if not items:
        return []
    ip_text = client_ip(request)
    try:
        pinned, _prefix = pinned_node_for_ip(db, ip_text)
    except NodeSelectionError:
        # Conflicting equal-length prefixes have no single preference; fallback
        # to the normal user route rather than hiding the whole catalogue.
        pinned = None

    policy_cache: dict[int, bool] = {}
    def allows(node: Node) -> bool:
        node_id = int(node.id)
        if node_id not in policy_cache:
            policy_cache[node_id] = node_playback_access_allows(node, request)
        return policy_cache[node_id]

    visible: list[Channel] = []
    for channel in items:
        candidates = playback_candidate_nodes(user, channel, pinned=pinned)
        if any(allows(node) for node in candidates):
            visible.append(channel)
    return visible


def online_user_channels(user: StreamUser) -> list[Channel]:
    """Return channels that are ready on a preferred or fallback playback Node.

    STREAMFORGE_MAIN_WEBPLAYER_LOCAL_FAST_PATH_V55
    STREAMFORGE_PUBLIC_CATALOG_LOCAL_READINESS_V62R2
    STREAMFORGE_PUBLIC_CATALOG_STOPPED_EXCLUSION_V63R2
    STREAMFORGE_MAIN_LOCAL_PLAYLIST_HLS_AUTHORITY_V100
    STREAMFORGE_MAIN_LOCAL_LB_HLS_AUTHORITY_V100
    STREAMFORGE_STATIC_ROUTE_CATALOG_FALLBACK_V100: a fixed/static user no
    longer disappears from the Main catalogue merely because its selected Node
    is down. Local and other assigned Nodes are considered as fallback targets.
    """
    candidates = [
        channel for channel in ordered_user_channels(user)
        if channel.enabled and channel.output_type == "hls"
    ]
    if not candidates:
        return []

    public_role = str(os.getenv("STREAMFORGE_PROCESS_ROLE", "control") or "control").strip().lower() == "public"
    ready: list[Channel] = []
    deferred: list[Channel] = []
    deferred_permitted: dict[int, set[int]] = {}

    for channel in candidates:
        permitted = playback_candidate_nodes(user, channel, pinned=None)
        if not permitted:
            continue

        local_nodes = [node for node in permitted if node.node_type == "local"]
        local_ready = False
        if public_role:
            local_ready = bool(channel.desired_running) and any(node_controller.hls_ready(channel, node) for node in local_nodes)
        else:
            runtime = stream_manager.runtime_snapshot(int(channel.id))
            local_ready = bool(runtime.get("alive")) and any(node_controller.hls_ready(channel, node) for node in local_nodes)
        if local_ready:
            ready.append(channel)
            continue

        remote_ids = {int(node.id) for node in permitted if node.node_type != "local"}
        if remote_ids:
            deferred.append(channel)
            deferred_permitted[int(channel.id)] = remote_ids

    if deferred:
        try:
            snapshots = node_controller.runtime_snapshots([channel.id for channel in deferred], persist=False)
        except Exception:
            snapshots = {}
        for channel in deferred:
            permitted_ids = deferred_permitted.get(int(channel.id), set())
            node_rows = list((snapshots.get(channel.id) or {}).get("nodes") or [])
            if any(
                int(item.get("node_id") or 0) in permitted_ids
                and bool(item.get("alive"))
                and bool(item.get("hls_ready"))
                for item in node_rows
            ):
                ready.append(channel)

    ready_ids = {int(channel.id) for channel in ready}
    return [channel for channel in candidates if int(channel.id) in ready_ids]


def _webplayer_prefetched_ordered_channels(user: StreamUser) -> list[Channel]:
    """Load the Web Player catalogue with bounded relationship queries.

    STREAMFORGE_MAIN_WEBPLAYER_PREFETCH_CATALOG_V1112:
    The old catalogue path lazily touched categories and assigned Nodes while
    iterating every channel, producing an N+1 SQLite pattern on larger lineups.
    Fetch channel/category/Node relationships in batches, then apply the exact
    saved playlist ordering in memory.
    """
    session = object_session(user)
    if session is None:
        return ordered_user_channels(user)

    options = (
        selectinload(Channel.category),
        selectinload(Channel.categories),
        selectinload(Channel.node),
        selectinload(Channel.nodes),
    )
    if user.playlist:
        playlist = user.playlist
        if bool(getattr(playlist, "all_enabled_channels", False)):
            channels = list(session.scalars(
                select(Channel).options(*options).where(
                    Channel.enabled.is_(True),
                    func.lower(Channel.output_type) == "hls",
                )
            ).all())
        else:
            channels = list(session.scalars(
                select(Channel)
                .join(
                    playlist_profile_channel_links,
                    playlist_profile_channel_links.c.channel_id == Channel.id,
                )
                .options(*options)
                .where(playlist_profile_channel_links.c.playlist_id == int(playlist.id))
            ).all())
        return _category_grouped_channels(channels, playlist.channel_order, playlist.category_order)

    channel_ids = [int(item) for item in session.scalars(
        select(user_channel_links.c.channel_id).where(user_channel_links.c.user_id == int(user.id))
    ).all()]
    if not channel_ids:
        return []
    channels = list(session.scalars(
        select(Channel).options(*options).where(Channel.id.in_(channel_ids))
    ).all())
    return _category_grouped_channels(channels, user.playlist_order)


def webplayer_authorized_channel(user: StreamUser, slug: str) -> Channel | None:
    """Resolve one Web Player channel directly from account membership.

    STREAMFORGE_MAIN_WEBPLAYER_SINGLE_CHANNEL_FAST_PATH_V55
    STREAMFORGE_MAIN_WEBPLAYER_DIRECT_MEMBERSHIP_LOOKUP_V1112: opening one
    channel must not sort/load the user's complete catalogue first.  Resolve the
    requested slug with one membership query; the HLS master request remains the
    authoritative readiness selector.
    """
    wanted = str(slug or "").strip()
    if not wanted:
        return None
    session = object_session(user)
    if session is None:
        return next((
            channel for channel in ordered_user_channels(user)
            if channel.enabled and channel.output_type == "hls" and channel.slug == wanted
        ), None)

    statement = select(Channel).options(
        selectinload(Channel.category),
        selectinload(Channel.categories),
        selectinload(Channel.node),
        selectinload(Channel.nodes),
    ).where(
        Channel.slug == wanted,
        Channel.enabled.is_(True),
        func.lower(Channel.output_type) == "hls",
    )
    if user.playlist:
        playlist = user.playlist
        if not bool(getattr(playlist, "all_enabled_channels", False)):
            statement = statement.join(
                playlist_profile_channel_links,
                playlist_profile_channel_links.c.channel_id == Channel.id,
            ).where(playlist_profile_channel_links.c.playlist_id == int(playlist.id))
    else:
        statement = statement.join(
            user_channel_links,
            user_channel_links.c.channel_id == Channel.id,
        ).where(user_channel_links.c.user_id == int(user.id))
    return session.scalar(statement.limit(1))


def webplayer_fast_catalog_channels(user: StreamUser) -> list[Channel]:
    """Return only authoritative Up/HLS-ready channels for the Main Web Player.

    STREAMFORGE_MAIN_WEBPLAYER_ZERO_NETWORK_CATALOG_V55
    STREAMFORGE_PUBLIC_CATALOG_LOCAL_STATE_V62R2
    STREAMFORGE_MAIN_WEBPLAYER_LOCAL_HLS_AUTHORITY_V100
    STREAMFORGE_MAIN_WEBPLAYER_LB_LOCAL_HLS_V100
    STREAMFORGE_WEBPLAYER_STATIC_FALLBACK_CATALOG_V100
    STREAMFORGE_MAIN_WEBPLAYER_ASYNC_LOCAL_READY_V1112
    Compatibility markers above are retained for package integrity checks; the
    stale status-only fallback they originally described is no longer used.

    STREAMFORGE_MAIN_WEBPLAYER_STRICT_ONLINE_CATALOG_V1220:
    The former zero-network fallback appended channels solely because their
    persisted status was running/starting/restarting.  That status can remain
    set while delivery is Waiting.  Reuse the same readiness resolver as M3U
    and Xtream so every Main catalogue requires an actually ready local or
    remote playback target.
    """
    return online_user_channels(user)

def playlist_category_rank(playlist: PlaylistProfile, channel: Channel) -> int:
    positions = category_position_map(playlist_profile_channels(playlist), playlist.category_order)
    return positions.get(channel_category_key(channel), 100000)


def playlist_channel_rank(playlist: PlaylistProfile, channel: Channel) -> int:
    positions = {item.id: index + 1 for index, item in enumerate(ordered_playlist_channels(playlist))}
    return positions.get(channel.id, 100000)


def stream_user_redirect(*, message: str = "", error: str = "") -> RedirectResponse:
    values: dict[str, str] = {}
    if message:
        values["message"] = message
    if error:
        values["error"] = error
    suffix = f"?{urlencode(values)}" if values else ""
    return RedirectResponse(f"/users{suffix}", status_code=303)


def sync_direct_users_for_node_ids(node_ids: set[int], request: Request, db: Session) -> list[str]:
    errors: list[str] = []
    for node_id in sorted(node_ids):
        node = db.get(Node, node_id)
        if not node or node.node_type != "remote":
            continue
        if node_controller.is_effectively_offline(node):
            mark_node_sync_pending(db, node, "Direct playlist users queued while Node is offline")
            db.commit()
            continue
        ok, detail = sync_stream_user_registry(node, request)
        if not ok:
            node_controller.note_control_failure(node, detail, transport_hint=True)
            mark_node_sync_pending(db, node, "Direct playlist user sync queued after connection failure")
            db.commit()
    return errors


def direct_playlist_url(user: StreamUser) -> str:
    if user.delivery_mode != "direct_node" or not user.direct_node:
        return ""
    base = node_public_base(user.direct_node)
    return f"{base}/playlist/{user.token}.m3u" if base else ""


def stream_user_playlist_url(user: StreamUser, base_url: str) -> str:
    """Return a copy-ready M3U URL without exposing the saved password."""
    direct = direct_playlist_url(user)
    if direct:
        return direct
    return f"{base_url.rstrip('/')}/playlist/{urllib.parse.quote(user.token)}.m3u"


def stream_user_server_url(user: StreamUser, base_url: str) -> str:
    if user.delivery_mode == "direct_node" and user.direct_node:
        return node_public_base(user.direct_node) or base_url.rstrip("/")
    return base_url.rstrip("/")


def stream_user_xtream_url(user: StreamUser, base_url: str) -> str:
    if not user.xtream_username or not user.xtream_password_enc:
        return ""
    password = decrypt_secret(user.xtream_password_enc)
    if not password:
        return ""
    base = stream_user_server_url(user, base_url).rstrip("/")
    return (
        f"{base}/get.php?username={urllib.parse.quote(user.xtream_username, safe='')}"
        f"&password={urllib.parse.quote(password, safe='')}&type=m3u_plus&output=m3u8"
    )


def playlist_redirect(*, message: str = "", error: str = "") -> RedirectResponse:
    return stream_user_redirect(message=message, error=error)


def playlist_form_context(db: Session, playlist: PlaylistProfile | None, error: str | None = None) -> dict[str, object]:
    channels = db.scalars(select(Channel)).all()
    channels = apply_playlist_order(channels, "[]", None)
    return {
        "playlist": playlist,
        "channels": channels,
        "error": error,
    }


def sync_main_channel_catalogue_order(
    db: Session,
    playlist_channels: list[Channel],
    saved_category_order: list[str],
    saved_channel_order: list[int],
) -> None:
    """Make the Main Channels page follow the hierarchy saved for a playlist.

    The public/display ID is derived from category/channel sort_order, while the
    DB primary key remains stable. Playlist members lead each category and any
    channels not present in the playlist retain their relative order afterward.
    """
    catalogue = list(db.scalars(
        select(Channel).options(
            selectinload(Channel.category),
            selectinload(Channel.categories),
        )
    ).all())
    catalogue.sort(key=channel_catalogue_order_key)

    playlist_by_id = {int(channel.id): channel for channel in playlist_channels}
    ordered_playlist = [
        playlist_by_id[channel_id]
        for channel_id in saved_channel_order
        if channel_id in playlist_by_id
    ]

    categories_by_key: dict[str, ChannelCategory] = {}
    for channel in catalogue:
        category = getattr(channel, "category", None)
        if category is not None:
            categories_by_key.setdefault(channel_category_key(channel), category)

    existing_category_keys: list[str] = []
    for channel in catalogue:
        key = channel_category_key(channel)
        if key not in existing_category_keys:
            existing_category_keys.append(key)
    final_category_keys = [
        key for key in saved_category_order if key in existing_category_keys
    ]
    final_category_keys.extend(
        key for key in existing_category_keys if key not in final_category_keys
    )
    for position, key in enumerate(final_category_keys, start=1):
        category = categories_by_key.get(key)
        if category is not None:
            category.sort_order = position * 10

    playlist_ids_by_category: dict[str, list[int]] = {}
    for channel in ordered_playlist:
        playlist_ids_by_category.setdefault(channel_category_key(channel), []).append(int(channel.id))

    catalogue_by_category: dict[str, list[Channel]] = {}
    for channel in catalogue:
        catalogue_by_category.setdefault(channel_category_key(channel), []).append(channel)
    for key, category_channels in catalogue_by_category.items():
        wanted_ids = playlist_ids_by_category.get(key, [])
        by_id = {int(channel.id): channel for channel in category_channels}
        final_ids = [channel_id for channel_id in wanted_ids if channel_id in by_id]
        final_ids.extend(
            int(channel.id) for channel in category_channels
            if int(channel.id) not in final_ids
        )
        for position, channel_id in enumerate(final_ids, start=1):
            by_id[channel_id].sort_order = position * 10


def playlist_hierarchy_context(
    playlist: PlaylistProfile,
    *,
    message: str = "",
    error: str = "",
) -> dict[str, object]:
    ordered = ordered_playlist_channels(playlist)
    category_order = parse_playlist_category_order(playlist.category_order, ordered)
    grouped: dict[str, dict[str, object]] = {}
    for channel in ordered:
        key = channel_category_key(channel)
        category = getattr(channel, "category", None)
        name = str(getattr(category, "name", "") or "").strip() or "Uncategorized"
        grouped.setdefault(key, {"key": key, "name": name, "channels": []})["channels"].append(channel)
    groups = [grouped[key] for key in category_order if key in grouped]
    return {
        "playlist": playlist,
        "groups": groups,
        "category_order_json": json.dumps([group["key"] for group in groups], separators=(",", ":")),
        "channel_order_json": json.dumps([channel.id for channel in ordered], separators=(",", ":")),
        "message": message,
        "error": error,
    }


@app.get("/playlists/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.create"))])
def playlist_new_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "playlist_form.html", db, **playlist_form_context(db, None))


@app.post("/playlists/new", dependencies=[Depends(permission_required("stream_users.create"))])
def playlist_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    logo_url: str = Form(""),
    content_mode: str = Form("custom"),
    category_order: str = Form("[]"),
    playlist_order: str = Form("[]"),
    channel_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    cleaned = name.strip()[:150]
    all_enabled = str(content_mode or "custom").strip().lower() == "all"
    selected_channels = db.scalars(
        select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.name)
    ).all() if channel_ids else []
    effective_channels = enabled_main_playlist_channels(db) if all_enabled else list(selected_channels)
    if len(cleaned) < 2 or db.scalar(select(PlaylistProfile).where(func.lower(PlaylistProfile.name) == cleaned.lower())):
        draft = PlaylistProfile(
            name=cleaned, description=description.strip() or None, logo_url=logo_url.strip() or None,
            channel_order="[]", category_order="[]", all_enabled_channels=all_enabled,
        )
        draft.channels = [] if all_enabled else list(selected_channels)
        return render(request, "playlist_form.html", db, **playlist_form_context(db, draft, "Playlist name is invalid or already exists"))
    default_channels = apply_playlist_order(effective_channels, "[]", None)
    playlist = PlaylistProfile(
        name=cleaned,
        description=description.strip() or None,
        logo_url=logo_url.strip() or None,
        all_enabled_channels=all_enabled,
        channel_order=json.dumps([item.id for item in default_channels], separators=(",", ":")),
        category_order=json.dumps(parse_playlist_category_order("[]", default_channels), separators=(",", ":")),
        channels=[] if all_enabled else list(selected_channels),
    )
    db.add(playlist)
    db.commit()
    log_event(
        "Playlist profile created", scope="playlist",
        actor=current_admin(request, db).username if current_admin(request, db) else None,
        details={"playlist": playlist.name, "channels": len(effective_channels), "content_mode": "all_enabled" if all_enabled else "custom"},
    )
    return playlist_redirect(message="Playlist profile created")


@app.get("/playlists/{playlist_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.edit"))])
def playlist_edit_page(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    playlist = db.get(PlaylistProfile, playlist_id)
    if not playlist:
        raise HTTPException(404)
    return render(request, "playlist_form.html", db, **playlist_form_context(db, playlist))


@app.post("/playlists/{playlist_id}/edit", dependencies=[Depends(permission_required("stream_users.edit"))])
def playlist_update(
    playlist_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    logo_url: str = Form(""),
    content_mode: str = Form("custom"),
    category_order: str = Form("[]"),
    playlist_order: str = Form("[]"),
    channel_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    playlist = db.get(PlaylistProfile, playlist_id)
    if not playlist:
        raise HTTPException(404)
    cleaned = name.strip()[:150]
    duplicate = db.scalar(select(PlaylistProfile).where(func.lower(PlaylistProfile.name) == cleaned.lower(), PlaylistProfile.id != playlist.id))
    if len(cleaned) < 2 or duplicate:
        return render(request, "playlist_form.html", db, **playlist_form_context(db, playlist, "Playlist name is invalid or already exists"))
    all_enabled = str(content_mode or "custom").strip().lower() == "all"
    selected_channels = db.scalars(
        select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.name)
    ).all() if channel_ids else []
    effective_channels = enabled_main_playlist_channels(db) if all_enabled else list(selected_channels)
    allowed_ids = {item.id for item in effective_channels}
    default_channels = apply_playlist_order(effective_channels, "[]", playlist.category_order)
    normalized_channel_order = parse_channel_order(
        playlist.channel_order,
        allowed_ids,
        [item.id for item in default_channels],
    )
    normalized_category_order = parse_playlist_category_order(playlist.category_order, effective_channels)
    playlist.name = cleaned
    playlist.description = description.strip() or None
    playlist.logo_url = logo_url.strip() or None
    playlist.all_enabled_channels = all_enabled
    playlist.channels = [] if all_enabled else list(selected_channels)
    playlist.channel_order = json.dumps(normalized_channel_order, separators=(",", ":"))
    playlist.category_order = json.dumps(normalized_category_order, separators=(",", ":"))
    for user in list(playlist.users):
        user.channels = list(effective_channels)
        user.playlist_order = playlist.channel_order
    db.commit()
    log_event(
        "Playlist profile updated", scope="playlist",
        actor=current_admin(request, db).username if current_admin(request, db) else None,
        details={"playlist": playlist.name, "channels": len(effective_channels), "content_mode": "all_enabled" if all_enabled else "custom"},
    )
    return playlist_redirect(message="Playlist profile updated")


@app.get("/playlists/{playlist_id}/order", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.edit"))])
def playlist_order_page(
    playlist_id: int,
    request: Request,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    playlist = db.get(PlaylistProfile, playlist_id)
    if not playlist:
        raise HTTPException(404)
    return render(
        request,
        "playlist_order.html",
        db,
        **playlist_hierarchy_context(playlist, message=message, error=error),
    )


@app.post("/playlists/{playlist_id}/order", dependencies=[Depends(permission_required("stream_users.edit"))])
def playlist_order_save(
    playlist_id: int,
    request: Request,
    category_order: str = Form("[]"),
    playlist_order: str = Form("[]"),
    db: Session = Depends(get_db),
):
    playlist = db.get(PlaylistProfile, playlist_id)
    if not playlist:
        raise HTTPException(404)
    channels = playlist_profile_channels(playlist)
    try:
        parsed_categories = json.loads(category_order or "[]")
        parsed_channels = json.loads(playlist_order or "[]")
        if not isinstance(parsed_categories, list) or not isinstance(parsed_channels, list):
            raise ValueError("Invalid playlist hierarchy")
        current_default = [item.id for item in ordered_playlist_channels(playlist)]
        saved_categories = parse_playlist_category_order(category_order, channels)
        saved_channels = parse_channel_order(playlist_order, {item.id for item in channels}, current_default)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        response = render(
            request,
            "playlist_order.html",
            db,
            **playlist_hierarchy_context(playlist, error=str(exc)),
        )
        response.status_code = 400
        return response
    playlist.category_order = json.dumps(saved_categories, separators=(",", ":"))
    playlist.channel_order = json.dumps(saved_channels, separators=(",", ":"))
    # STREAMFORGE_MAIN_PLAYLIST_CHANNEL_SERIAL_SYNC_V1211:
    # Channels page IDs are generated from the Main catalogue order. Keep that
    # display order in sync with the hierarchy the operator just saved.
    sync_main_channel_catalogue_order(db, channels, saved_categories, saved_channels)
    for user in list(playlist.users):
        user.playlist_order = playlist.channel_order
    db.commit()
    log_event(
        "Main playlist category/channel order updated",
        scope="playlist",
        actor=current_admin(request, db).username if current_admin(request, db) else None,
        details={"playlist": playlist.name, "categories": len(saved_categories), "channels": len(saved_channels)},
    )
    return RedirectResponse(
        f"/playlists/{playlist.id}/order?message=" + quote_plus("Playlist category and channel order saved"),
        status_code=303,
    )


@app.post("/playlists/{playlist_id}/delete", dependencies=[Depends(permission_required("stream_users.delete"))])
def playlist_delete(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    playlist = db.get(PlaylistProfile, playlist_id)
    if not playlist:
        return playlist_redirect()
    if playlist.users:
        return playlist_redirect(error="Move users to another playlist before deleting this playlist profile")
    db.delete(playlist)
    db.commit()
    return playlist_redirect(message="Playlist profile deleted")


@app.get("/users", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.view"))])
def users_page(
    request: Request,
    node_id: str = "",
    page: int = 1,
    limit: str = "20",
    playlist_page: int = 1,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    # STREAMFORGE_MAIN_USERS_PLAYLISTS_SERVER_PAGINATION_V117:
    # STREAMFORGE_MAIN_USERS_PLAYLISTS_RUNTIME_FIX_V1110:
    # Keep the Users & Playlists page truly bounded, but avoid the v11.9
    # aggregate/subquery path that could raise a 500 on existing catalogues.
    # Only the visible user rows, visible playlist rows and their directly
    # needed relationships are loaded. No full user/playlist catalogue load.
    user_limit_options = {"20": 20, "50": 50, "100": 100, "200": 200}
    selected_user_limit = str(limit or "20").strip()
    if selected_user_limit not in user_limit_options:
        selected_user_limit = "20"
    user_page_size = user_limit_options[selected_user_limit]

    selected_node_id = str(node_id or "").strip()
    selected_node_int: int | None = None
    if selected_node_id:
        try:
            selected_node_int = int(selected_node_id)
        except (TypeError, ValueError):
            selected_node_id = ""

    nodes = list(db.scalars(
        select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name, Node.id)
    ).all())

    user_filters: list[object] = []
    if selected_node_int is not None:
        # Preserve the existing allowed-node semantics while keeping the filter
        # in SQL so pagination remains server-side.
        channel_on_node = or_(
            Channel.node_id == selected_node_int,
            Channel.nodes.any(Node.id == selected_node_int),
        )
        dynamic_channel_exists = bool(db.scalar(
            select(func.count(Channel.id)).where(
                Channel.enabled.is_(True),
                Channel.output_type == "hls",
                channel_on_node,
            )
        ) or 0)
        custom_playlist_on_node = PlaylistProfile.channels.any(channel_on_node)
        playlist_derived = or_(
            and_(PlaylistProfile.all_enabled_channels.is_(False), custom_playlist_on_node),
            and_(PlaylistProfile.all_enabled_channels.is_(True), dynamic_channel_exists),
        )
        derived_node_access = or_(
            and_(StreamUser.playlist_id.is_(None), StreamUser.channels.any(channel_on_node)),
            StreamUser.playlist.has(playlist_derived),
        )
        user_filters.append(or_(
            and_(
                StreamUser.delivery_mode == "direct_node",
                StreamUser.direct_node_id == selected_node_int,
            ),
            and_(
                StreamUser.delivery_mode != "direct_node",
                or_(
                    StreamUser.nodes.any(Node.id == selected_node_int),
                    and_(~StreamUser.nodes.any(), derived_node_access),
                ),
            ),
        ))

    # Count the mapped table directly. The v11.9 nested SELECT-from-SELECT count
    # was unnecessary and is removed here for SQLite/SQLAlchemy compatibility.
    total_users = int(db.scalar(
        select(func.count(StreamUser.id)).where(*user_filters)
    ) or 0)
    user_page_count = max(1, math.ceil(total_users / user_page_size))
    current_user_page = min(max(1, int(page or 1)), user_page_count)

    user_query = (
        select(StreamUser)
        .options(
            selectinload(StreamUser.nodes),
            selectinload(StreamUser.direct_node),
            selectinload(StreamUser.playlist),
        )
        .where(*user_filters)
        .order_by(StreamUser.name, StreamUser.id)
        .offset((current_user_page - 1) * user_page_size)
        .limit(user_page_size)
    )
    all_users = list(db.scalars(user_query).all())

    playlist_page_size = 20
    total_playlists = int(db.scalar(select(func.count(PlaylistProfile.id))) or 0)
    playlist_page_count = max(1, math.ceil(total_playlists / playlist_page_size))
    current_playlist_page = min(max(1, int(playlist_page or 1)), playlist_page_count)
    playlists = list(db.scalars(
        select(PlaylistProfile)
        .order_by(PlaylistProfile.name, PlaylistProfile.id)
        .offset((current_playlist_page - 1) * playlist_page_size)
        .limit(playlist_page_size)
    ).all())

    enabled_hls_count = int(db.scalar(
        select(func.count(Channel.id)).where(
            Channel.enabled.is_(True), Channel.output_type == "hls"
        )
    ) or 0)

    # STREAMFORGE_MAIN_USERS_ASSOCIATION_COUNT_ONLY_V1114:
    # The list page displays counts, not Channel objects.  Loading every Channel
    # relationship for each visible user/profile multiplies ORM work and memory.
    # Count only the bounded association rows needed by the visible pages.
    visible_playlist_map: dict[int, PlaylistProfile] = {int(item.id): item for item in playlists}
    for item in all_users:
        if item.playlist is not None:
            visible_playlist_map.setdefault(int(item.playlist.id), item.playlist)
    visible_playlist_ids = sorted(visible_playlist_map)
    custom_playlist_counts: dict[int, int] = {}
    if visible_playlist_ids:
        custom_playlist_counts = {
            int(pid): int(count or 0)
            for pid, count in db.execute(
                select(
                    playlist_profile_channel_links.c.playlist_id,
                    func.count(playlist_profile_channel_links.c.channel_id),
                )
                .where(playlist_profile_channel_links.c.playlist_id.in_(visible_playlist_ids))
                .group_by(playlist_profile_channel_links.c.playlist_id)
            ).all()
        }
    playlist_channel_counts: dict[int, int] = {
        pid: (enabled_hls_count if bool(item.all_enabled_channels) else int(custom_playlist_counts.get(pid, 0)))
        for pid, item in visible_playlist_map.items()
    }

    playlist_ids = [int(item.id) for item in playlists]
    playlist_user_counts: dict[int, int] = {}
    if playlist_ids:
        playlist_user_counts = {
            int(pid): int(count)
            for pid, count in db.execute(
                select(StreamUser.playlist_id, func.count(StreamUser.id))
                .where(StreamUser.playlist_id.in_(playlist_ids))
                .group_by(StreamUser.playlist_id)
            ).all()
            if pid is not None
        }

    direct_user_ids = [int(item.id) for item in all_users if item.playlist is None]
    direct_user_channel_counts: dict[int, int] = {}
    if direct_user_ids:
        direct_user_channel_counts = {
            int(uid): int(count or 0)
            for uid, count in db.execute(
                select(user_channel_links.c.user_id, func.count(user_channel_links.c.channel_id))
                .where(user_channel_links.c.user_id.in_(direct_user_ids))
                .group_by(user_channel_links.c.user_id)
            ).all()
        }
    user_channel_counts: dict[int, int] = {}
    for item in all_users:
        if item.playlist is not None:
            user_channel_counts[int(item.id)] = int(playlist_channel_counts.get(int(item.playlist.id), 0))
        else:
            user_channel_counts[int(item.id)] = int(direct_user_channel_counts.get(int(item.id), 0))

    base_url = main_playlist_public_base(db, request)
    user_xtream_urls: dict[int, str] = {}
    user_server_urls: dict[int, str] = {}
    for item in all_users:
        # A single malformed legacy credential or direct-node URL must not take
        # down the complete Users page. Keep the row visible and suppress only
        # the affected copy-ready URL.
        try:
            user_server_urls[int(item.id)] = stream_user_server_url(item, base_url)
        except Exception:
            user_server_urls[int(item.id)] = base_url.rstrip("/")
        try:
            user_xtream_urls[int(item.id)] = stream_user_xtream_url(item, base_url)
        except Exception:
            user_xtream_urls[int(item.id)] = ""

    def users_url(user_page: int | None = None, playlist_page_number: int | None = None) -> str:
        params: dict[str, str] = {}
        if selected_node_id:
            params["node_id"] = selected_node_id
        if selected_user_limit != "20":
            params["limit"] = selected_user_limit
        target_user_page = current_user_page if user_page is None else int(user_page)
        target_playlist_page = current_playlist_page if playlist_page_number is None else int(playlist_page_number)
        if target_user_page > 1:
            params["page"] = str(target_user_page)
        if target_playlist_page > 1:
            params["playlist_page"] = str(target_playlist_page)
        query_string = urlencode(params)
        return "/users" + (f"?{query_string}" if query_string else "")

    user_start = max(1, min(current_user_page - 2, max(1, user_page_count - 4)))
    user_end = min(user_page_count, user_start + 4)
    user_page_links = [
        {"number": n, "url": users_url(user_page=n), "current": n == current_user_page}
        for n in range(user_start, user_end + 1)
    ]
    playlist_start = max(1, min(current_playlist_page - 2, max(1, playlist_page_count - 4)))
    playlist_end = min(playlist_page_count, playlist_start + 4)
    playlist_page_links = [
        {"number": n, "url": users_url(playlist_page_number=n), "current": n == current_playlist_page}
        for n in range(playlist_start, playlist_end + 1)
    ]

    return render(
        request,
        "users.html",
        db,
        users=all_users,
        playlists=playlists,
        nodes=nodes,
        selected_node_id=selected_node_id,
        selected_user_limit=selected_user_limit,
        user_limit_options=list(user_limit_options.keys()),
        playlist_channel_counts=playlist_channel_counts,
        playlist_user_counts=playlist_user_counts,
        user_channel_counts=user_channel_counts,
        user_xtream_urls=user_xtream_urls,
        user_server_urls=user_server_urls,
        total_users=total_users,
        current_user_page=current_user_page,
        user_page_count=user_page_count,
        user_page_links=user_page_links,
        user_prev_url=users_url(user_page=max(1, current_user_page - 1)),
        user_next_url=users_url(user_page=min(user_page_count, current_user_page + 1)),
        total_playlists=total_playlists,
        current_playlist_page=current_playlist_page,
        playlist_page_count=playlist_page_count,
        playlist_page_links=playlist_page_links,
        playlist_prev_url=users_url(playlist_page_number=max(1, current_playlist_page - 1)),
        playlist_next_url=users_url(playlist_page_number=min(playlist_page_count, current_playlist_page + 1)),
        base_url=base_url,
        direct_playlist_url=direct_playlist_url,
        stream_user_playlist_url=stream_user_playlist_url,
        stream_user_server_url=stream_user_server_url,
        stream_user_xtream_url=stream_user_xtream_url,
        message=message,
        error=error,
    )


@app.post("/users/server-limit", dependencies=[Depends(permission_required("stream_users.edit"))])
def users_server_limit(
    request: Request,
    total_max_connections: int = Form(0),
    db: Session = Depends(get_db),
):
    limit = max(0, min(1000000, int(total_max_connections or 0)))
    save_app_setting(db, MAIN_TOTAL_CONNECTIONS_KEY, limit)
    db.commit()
    connection_tracker.set_total_limit(limit)
    label = "unlimited" if limit == 0 else str(limit)
    log_event("Main server connection limit updated", scope="settings", actor=current_admin(request, db).username if current_admin(request, db) else None, details={"total_max_connections": limit})
    return RedirectResponse(f"/users?message={quote_plus('Main server total connection limit: ' + label)}", status_code=303)


@app.get("/users/new", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.create"))])
def user_new_page(request: Request, db: Session = Depends(get_db)):
    channels = db.scalars(select(Channel).order_by(Channel.name)).all()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    remote_nodes = [item for item in nodes if item.node_type == "remote"]
    playlists = db.scalars(select(PlaylistProfile).order_by(PlaylistProfile.name)).all()
    return render(
        request,
        "user_form.html",
        db,
        user=None,
        playlists=playlists,
        channels=channels,
        nodes=nodes,
        remote_nodes=remote_nodes,
        error=None,
    )


def validate_user_delivery(
    db: Session,
    delivery_mode: str,
    direct_node_id: int | None,
    channel_ids: list[int],
) -> tuple[str, Node | None, list[Channel]]:
    # Node playlist accounts are owned by the Node Panel. Main Panel users are
    # always central and may use the allowed-node/load-balancing controls.
    channels = db.scalars(select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.name)).all() if channel_ids else []
    return "central", None, channels


def selected_playlist(db: Session, playlist_id: int | None) -> PlaylistProfile | None:
    return db.get(PlaylistProfile, playlist_id) if playlist_id else None


def normalize_xtream_username(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > 120:
        raise ValueError("Xtream username must be 120 characters or fewer")
    if not re.fullmatch(r"[A-Za-z0-9._@+-]+", cleaned):
        raise ValueError("Xtream username may contain letters, numbers, dot, underscore, @, + and hyphen")
    return cleaned


def validate_xtream_credentials(
    db: Session,
    username: str | None,
    password: str | None,
    *,
    current_user_id: int | None = None,
    existing_password_hash: str | None = None,
) -> tuple[str | None, str | None]:
    cleaned_username = normalize_xtream_username(username)
    cleaned_password = password or ""
    if not cleaned_username:
        if cleaned_password:
            raise ValueError("Set an Xtream username before setting an Xtream password")
        return None, None
    duplicate_query = select(StreamUser).where(func.lower(StreamUser.xtream_username) == cleaned_username.lower())
    if current_user_id is not None:
        duplicate_query = duplicate_query.where(StreamUser.id != current_user_id)
    if db.scalar(duplicate_query):
        raise ValueError("Xtream username is already in use")
    if not cleaned_password and not existing_password_hash:
        raise ValueError("Xtream password is required when Xtream username is enabled")
    return cleaned_username, (hash_password(cleaned_password) if cleaned_password else existing_password_hash)


@app.post("/users/new", dependencies=[Depends(permission_required("stream_users.create"))])
def user_create(
    request: Request,
    name: str = Form(""),
    xtream_username: str = Form(""),
    xtream_password: str = Form(""),
    enabled: Optional[str] = Form(None),
    expires_at: str = Form(""),
    max_connections: int = Form(1),
    user_type: str = Form("viewer"),
    restream_allowed_ips: str = Form(""),
    load_balance_enabled: Optional[str] = Form(None),
    delivery_mode: str = Form("central"),
    direct_node_id: int | None = Form(None),
    notes: str = Form(""),
    playlist_id: int | None = Form(None),
    playlist_order: str = Form("[]"),
    channel_ids: list[int] = Form(default=[]),
    node_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    load_balancing = as_bool(load_balance_enabled)
    effective_node_ids = list(dict.fromkeys(node_ids))
    if not load_balancing:
        effective_node_ids = effective_node_ids[:1]
    all_channels = db.scalars(select(Channel).order_by(Channel.name)).all()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    remote_nodes = [item for item in nodes if item.node_type == "remote"]
    playlists = db.scalars(select(PlaylistProfile).order_by(PlaylistProfile.name)).all()
    profile = selected_playlist(db, playlist_id)
    effective_ids = [item.id for item in ordered_playlist_channels(profile)] if profile else channel_ids
    try:
        clean_xtream_username, xtream_password_hash = validate_xtream_credentials(
            db, xtream_username, xtream_password
        )
        if not clean_xtream_username:
            raise ValueError("Username is required")
        account_type = "restream" if user_type == "restream" else "viewer"
        normalized_restream_ips = normalize_ip_rules(restream_allowed_ips) if account_type == "restream" else ""
        if account_type == "restream" and not normalized_restream_ips:
            raise ValueError("Restream users require at least one allowed IP or CIDR")
        mode, direct_node, channels = validate_user_delivery(db, delivery_mode, direct_node_id, effective_ids)
    except ValueError as exc:
        draft = type("UserDraft", (), {
            "name": name.strip() or xtream_username.strip(), "xtream_username": xtream_username.strip() or None,
            "xtream_password_hash": None, "enabled": as_bool(enabled), "expires_at": normalize_expiry(expires_at),
            "max_connections": max_connections, "user_type": ("restream" if user_type == "restream" else "viewer"),
            "restream_allowed_ips": restream_allowed_ips, "load_balance_enabled": load_balancing,
            "delivery_mode": delivery_mode, "direct_node_id": direct_node_id, "notes": notes,
            "playlist_id": playlist_id, "playlist": profile,
            "channels": [item for item in all_channels if item.id in set(effective_ids)],
            "nodes": [item for item in nodes if item.id in set(effective_node_ids)], "id": None,
        })()
        return render(request, "user_form.html", db, user=draft, playlists=playlists, channels=all_channels, nodes=nodes, remote_nodes=remote_nodes, error=str(exc))
    allowed_nodes = db.scalars(select(Node).where(Node.id.in_(effective_node_ids), Node.enabled.is_(True))).all() if mode == "central" and effective_node_ids else []
    user = StreamUser(
        name=name.strip() or clean_xtream_username or "Streaming user", token=secrets.token_urlsafe(32),
        xtream_username=clean_xtream_username, xtream_password_hash=xtream_password_hash,
        xtream_password_enc=encrypt_secret(xtream_password) if xtream_password else None,
        enabled=as_bool(enabled), user_type=account_type,
        restream_allowed_ips=normalized_restream_ips or None,
        load_balance_enabled=load_balancing, delivery_mode=mode,
        direct_node=direct_node, expires_at=normalize_expiry(expires_at),
        max_connections=max(0, min(100, int(max_connections or 0))), notes=notes.strip() or None,
        playlist=profile,
        playlist_order=(profile.channel_order if profile else json.dumps(parse_playlist_order(playlist_order, {item.id for item in channels}), separators=(",", ":"))),
        channels=channels, nodes=allowed_nodes,
    )
    db.add(user)
    db.commit()
    return stream_user_redirect(message="Main/load-balanced user created")


@app.get("/users/{user_id}/edit", response_class=HTMLResponse, dependencies=[Depends(permission_required("stream_users.edit"))])
def user_edit_page(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.get(StreamUser, user_id)
    if not user:
        raise HTTPException(404)
    channels = db.scalars(select(Channel).order_by(Channel.name)).all()
    nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
    return render(
        request,
        "user_form.html",
        db,
        user=user,
        playlists=db.scalars(select(PlaylistProfile).order_by(PlaylistProfile.name)).all(),
        channels=channels,
        nodes=nodes,
        remote_nodes=[item for item in nodes if item.node_type == "remote"],
        error=None,
    )


@app.post("/users/{user_id}/edit", dependencies=[Depends(permission_required("stream_users.edit"))])
def user_update(
    user_id: int,
    request: Request,
    name: str = Form(""),
    xtream_username: str = Form(""),
    xtream_password: str = Form(""),
    enabled: Optional[str] = Form(None),
    expires_at: str = Form(""),
    max_connections: int = Form(1),
    user_type: str = Form("viewer"),
    restream_allowed_ips: str = Form(""),
    load_balance_enabled: Optional[str] = Form(None),
    delivery_mode: str = Form("central"),
    direct_node_id: int | None = Form(None),
    notes: str = Form(""),
    playlist_id: int | None = Form(None),
    playlist_order: str = Form("[]"),
    channel_ids: list[int] = Form(default=[]),
    node_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user = db.get(StreamUser, user_id)
    if not user:
        raise HTTPException(404)
    load_balancing = as_bool(load_balance_enabled)
    effective_node_ids = list(dict.fromkeys(node_ids))
    if not load_balancing:
        effective_node_ids = effective_node_ids[:1]
    old_direct_node_id = user.direct_node_id
    profile = selected_playlist(db, playlist_id)
    effective_ids = [item.id for item in ordered_playlist_channels(profile)] if profile else channel_ids
    try:
        clean_xtream_username, xtream_password_hash = validate_xtream_credentials(
            db, xtream_username, xtream_password, current_user_id=user.id,
            existing_password_hash=user.xtream_password_hash,
        )
        account_type = "restream" if user_type == "restream" else "viewer"
        normalized_restream_ips = normalize_ip_rules(restream_allowed_ips) if account_type == "restream" else ""
        if account_type == "restream" and not normalized_restream_ips:
            raise ValueError("Restream users require at least one allowed IP or CIDR")
        mode, direct_node, channels = validate_user_delivery(db, delivery_mode, direct_node_id, effective_ids)
    except ValueError as exc:
        all_channels = db.scalars(select(Channel).order_by(Channel.name)).all()
        nodes = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.node_type, Node.name)).all()
        return render(request, "user_form.html", db, user=user, playlists=db.scalars(select(PlaylistProfile).order_by(PlaylistProfile.name)).all(), channels=all_channels, nodes=nodes, remote_nodes=[item for item in nodes if item.node_type == "remote"], error=str(exc))
    user.name = name.strip() or clean_xtream_username or user.name
    user.xtream_username = clean_xtream_username
    user.xtream_password_hash = xtream_password_hash
    if xtream_password:
        user.xtream_password_enc = encrypt_secret(xtream_password)
    elif not clean_xtream_username:
        user.xtream_password_enc = None
    user.enabled = as_bool(enabled)
    user.expires_at = normalize_expiry(expires_at)
    user.max_connections = max(0, min(100, int(max_connections or 0)))
    user.user_type = account_type
    user.restream_allowed_ips = normalized_restream_ips or None
    user.load_balance_enabled = load_balancing
    user.delivery_mode = mode
    user.direct_node = direct_node
    user.notes = notes.strip() or None
    user.playlist = profile
    user.playlist_order = profile.channel_order if profile else json.dumps(parse_playlist_order(playlist_order, {item.id for item in channels}), separators=(",", ":"))
    user.channels = channels
    user.nodes = db.scalars(select(Node).where(Node.id.in_(effective_node_ids), Node.enabled.is_(True))).all() if mode == "central" and effective_node_ids else []
    db.commit()
    return stream_user_redirect(message="Main/load-balanced user updated")


@app.post("/users/{user_id}/regenerate", dependencies=[Depends(permission_required("stream_users.token"))])
def user_regenerate(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.get(StreamUser, user_id)
    if not user:
        return stream_user_redirect()
    user.token = secrets.token_urlsafe(32)
    db.commit()
    return stream_user_redirect(message="Token regenerated")


@app.post("/users/{user_id}/delete", dependencies=[Depends(permission_required("stream_users.delete"))])
def user_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.get(StreamUser, user_id)
    if not user:
        return stream_user_redirect()
    db.delete(user)
    db.commit()
    return stream_user_redirect(message="User deleted")




def _node_control_identity(request: Request, db: Session) -> Node:
    slug = (request.headers.get("x-node-slug") or "").strip()
    token = request.headers.get("x-node-token") or ""
    if not slug or not token:
        raise HTTPException(401, "Node control credentials are missing")
    node = db.scalar(select(Node).where(Node.slug == slug, Node.node_type == "remote"))
    if not node or not node.enabled or not node.api_token or not hmac.compare_digest(node.api_token, token):
        raise HTTPException(403, "Invalid node control credentials")
    return node


def _node_compatible_channels(node: Node) -> list[Channel]:
    return [
        channel for channel in node.channels
        if channel.enabled and channel.output_type == "hls"
    ]


def _node_user_catalog(node: Node, request: Request, db: Session) -> dict[str, object]:
    assigned = _node_compatible_channels(node)
    assigned_ids = {channel.id for channel in assigned}
    playlists = db.scalars(select(PlaylistProfile).order_by(PlaylistProfile.name)).all()
    users: list[StreamUser] = []
    base = node_public_base(node)

    # A Main playlist can be wider than one Remote Node.  The Node receives the
    # compatible intersection instead of rejecting the whole playlist because
    # one channel is assigned elsewhere.  This also makes dynamic "all enabled"
    # Main profiles usable on Nodes with a smaller assigned catalogue.
    playlist_items: list[dict[str, object]] = []
    for playlist in playlists:
        ordered = ordered_playlist_channels(playlist)
        compatible_channels = [
            channel for channel in ordered
            if channel.id in assigned_ids and channel.enabled and channel.output_type == "hls"
        ]
        playlist_items.append({
            "id": playlist.id,
            "name": playlist.name,
            "channels": len(compatible_channels),
            "main_channels": len(ordered),
            "compatible": bool(compatible_channels),
            "all_enabled_channels": bool(getattr(playlist, "all_enabled_channels", False)),
            "channel_items": [
                {
                    "id": channel.id,
                    "key": node_controller._channel_key(channel),
                    "name": channel.name,
                    "slug": channel.slug,
                    "category": channel.category.name if channel.category else "Uncategorized",
                    "categories": [item.name for item in channel_category_list(channel)],
                    "category_order": playlist_category_rank(playlist, channel),
                    "channel_order": playlist_channel_rank(playlist, channel),
                    "logo_url": channel_logo_public_url(channel, request, db),
                }
                for channel in compatible_channels
            ],
        })

    return {
        "ok": True,
        "node": {"id": node.id, "name": node.name, "slug": node.slug, "base_url": base},
        "users": [
            {
                "id": user.id,
                "name": user.name,
                "username": user.xtream_username or "",
                "user_type": user.user_type,
                "enabled": bool(user.enabled),
                "expires_at": user.expires_at.isoformat() if user.expires_at else None,
                "max_connections": max(0, int(user.max_connections or 0)),
                "playlist": user.playlist.name if user.playlist else ("Independent Node catalogue" if bool(getattr(node, "sync_main_users", False)) else "Custom channels"),
                "channels": len(ordered_user_channels(user)),
                "all_node_channels": bool(getattr(node, "sync_main_users", False)),
                "playlist_url": f"{base}/playlist/{urllib.parse.quote(user.token)}.m3u" if base else "",
            }
            for user in users
        ],
        "playlists": playlist_items,
        "channels": [
            {
                "id": channel.id,
                "key": node_controller._channel_key(channel),
                "name": channel.name,
                "slug": channel.slug,
                "category": channel.category.name if channel.category else "Uncategorized",
                "categories": [item.name for item in channel_category_list(channel)],
                "category_order": int(channel.category.sort_order) if channel.category else 100000,
                "channel_order": int(channel.sort_order or 100000),
                "logo_url": channel_logo_public_url(channel, request, db),
            }
            for channel in sorted(assigned, key=lambda item: (item.category.name.lower() if item.category else "", item.name.lower()))
        ],
    }


@app.get("/api/v1/node-control/users")
def node_control_users(request: Request, db: Session = Depends(get_db)):
    node = _node_control_identity(request, db)
    return JSONResponse(_node_user_catalog(node, request, db))


@app.post("/api/v1/node-control/viewers/kill")
async def node_control_viewer_kill(request: Request, db: Session = Depends(get_db)):
    node = _node_control_identity(request, db)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON payload") from exc
    session_id = re.sub(r"[^A-Za-z0-9._~-]+", "", str(payload.get("session_id") or "").strip())[:96]
    if not session_id:
        raise HTTPException(400, "Session ID is required")
    try:
        user_id = int(payload.get("user_id")) if str(payload.get("user_id") or "").isdigit() else None
    except (TypeError, ValueError):
        user_id = None
    killed = viewer_tracker.kill(session_id, user_id)
    if user_id:
        killed += connection_tracker.kill(user_id, session_id)
    log_event(
        "Viewer session killed from Node Panel",
        scope="user", level="warning", node_id=node.id,
        actor=str(payload.get("actor") or "node-panel")[:120],
        details={"session_id": session_id, "user_id": user_id, "killed": killed},
    )
    return {"ok": True, "killed": killed}


@app.post("/api/v1/node-control/users")
async def node_control_user_create(request: Request, db: Session = Depends(get_db)):
    node = _node_control_identity(request, db)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON payload") from exc
    actor = str(payload.get("actor") or "node-panel")[:120]
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    display_name = str(payload.get("name") or "").strip()
    # Security boundary: a Remote Node Panel can create Normal Viewer accounts
    # only. Restreamer accounts and their source-IP allowlists are Main-Panel-only.
    account_type = "viewer"
    restream_ips_raw = ""
    try:
        raw_max_connections = payload.get("max_connections")
        max_connections = max(0, min(100, int(1 if raw_max_connections in (None, "") else raw_max_connections)))
    except (TypeError, ValueError):
        max_connections = 1
    playlist_id_raw = payload.get("playlist_id")
    try:
        playlist_id = int(playlist_id_raw) if playlist_id_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        playlist_id = None
    channel_ids: list[int] = []
    for item in payload.get("channel_ids") or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in channel_ids:
            channel_ids.append(value)
    try:
        clean_username, password_hash = validate_xtream_credentials(db, username, password)
        if not clean_username:
            raise ValueError("Username is required")
        profile = selected_playlist(db, playlist_id)
        independent_all_channels = bool(getattr(node, "sync_main_users", False)) and bool(payload.get("independent_all_channels"))
        effective_ids = [item.id for item in ordered_playlist_channels(profile)] if profile else channel_ids
        if independent_all_channels:
            mode, direct_node, channels = "direct_node", node, []
            profile = None
        else:
            if not effective_ids:
                raise ValueError("Select a playlist profile or at least one channel")
            mode, direct_node, channels = validate_user_delivery(db, "direct_node", node.id, effective_ids)
        normalized_restream_ips = normalize_ip_rules(restream_ips_raw) if account_type == "restream" else ""
        if account_type == "restream" and not normalized_restream_ips:
            raise ValueError("Restreamer accounts require at least one allowed IP or CIDR")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user = StreamUser(
        name=display_name or clean_username or "Streaming user",
        token=secrets.token_urlsafe(32),
        xtream_username=clean_username,
        xtream_password_hash=password_hash,
        xtream_password_enc=encrypt_secret(password) if password else None,
        enabled=bool(payload.get("enabled", True)),
        user_type=account_type,
        restream_allowed_ips=normalized_restream_ips or None,
        load_balance_enabled=False,
        delivery_mode=mode,
        direct_node=direct_node,
        expires_at=normalize_expiry(str(payload.get("expires_at") or "")),
        max_connections=max_connections,
        notes=f"Created from {node.name} Node Panel by {actor}",
        playlist=profile,
        playlist_order=(profile.channel_order if profile else json.dumps([item.id for item in channels], separators=(",", ":"))),
        channels=channels,
        nodes=[],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    sync_error = ""
    try:
        node_controller.sync_stream_users(node, main_panel_public_base(db, request))
    except NodeError as exc:
        sync_error = str(exc)
    log_event(
        "Direct playlist user created from Node Panel",
        scope="user",
        node_id=node.id,
        actor=actor,
        details={"user": user.name, "username": user.xtream_username, "type": user.user_type},
    )
    result = _node_user_catalog(node, request, db)
    result["created_user_id"] = user.id
    result["message"] = "Playlist user created"
    result["sync_error"] = sync_error
    return JSONResponse(result, status_code=201)


# Old direct-node user URLs are retained only as redirects after the unified migration.
@app.get("/node-users")
def legacy_node_users_redirect():
    return RedirectResponse("/users?message=Node+users+were+moved+into+Users+%26+Playlists", status_code=303)



def _web_player_prefix(request: Request) -> str:
    """Return the Playlist/App public path prefix for this request authority.

    Do not trust ``root_path`` here: when Panel/API and Playlist/App aliases share
    a host, middleware may carry the panel prefix into an internal player render.
    Playback URLs must always stay on the Playlist/App alias.
    """
    enabled, aliases = main_access_policy()
    scheme, host, port = request_main_authority(request)
    if enabled and host:
        playlist_slugs = sorted({
            slug for alias_scheme, alias_host, alias_port, slug, role in aliases
            if role == "playlist"
            and alias_scheme == scheme
            and alias_host == host
            and int(alias_port) == int(port)
        })
        # Root Playlist/App alias is authoritative when configured.
        if "" in playlist_slugs:
            return ""
        if len(playlist_slugs) == 1:
            return f"/{playlist_slugs[0]}"
        current = str(request.scope.get("root_path") or request.scope.get("state", {}).get("main_access_prefix") or "").strip("/")
        if current and current in playlist_slugs:
            return f"/{current}"
    return str(request.scope.get("root_path") or request.scope.get("state", {}).get("main_access_prefix") or "").rstrip("/")


# STREAMFORGE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R4:
def _web_player_home_url(request: Request) -> str:
    """Return the browser-facing canonical Web Player root.

    The configured Playlist/App alias itself is the Web Player URL. Internal
    /web-player routes remain stable for compatibility and Public-plane routing,
    but redirects/history must not expose that implementation suffix.
    """
    prefix = _web_player_prefix(request).rstrip("/")
    return prefix or "/"


# STREAMFORGE_MAIN_WEBPLAYER_FLASH_ERROR_V1082:
# Keep login/authentication failures out of the public URL.  The Web Player
# uses the existing signed SessionMiddleware cookie as a one-shot flash store,
# then renders the message on the clean canonical Playlist/App root.
_WEB_PLAYER_FLASH_ERROR_KEY = "streamforge_web_player_flash_error"


def _web_player_flash_redirect(request: Request, message: str) -> RedirectResponse:
    clean_message = str(message or "").strip()[:240]
    if clean_message:
        request.session[_WEB_PLAYER_FLASH_ERROR_KEY] = clean_message
    return RedirectResponse(
        _web_player_home_url(request),
        status_code=303,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _web_player_current_user(request: Request, db: Session) -> StreamUser | None:
    local_node = public_local_node(db)
    settings = _webplayer_effective_settings(db, local_node.id, request)
    if settings.get("login_mode") == "auto":
        auto_user = _webplayer_auto_user(db, local_node, settings)
        if auto_user:
            return auto_user
    try:
        user_id = int(request.session.get("streamforge_web_player_user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        return None
    user = db.get(StreamUser, user_id)
    if not user or not user_valid(user):
        request.session.pop("streamforge_web_player_user_id", None)
        request.session.pop("streamforge_web_player_login_kind", None)
        return None
    return user


# STREAMFORGE_MAIN_CLIENT_LOG_AUTO_LOGIN_V71:
def _web_player_log_auto_login_once(
    request: Request, db: Session, web_settings: dict, user: StreamUser | None
) -> None:
    """Write one Auto Login client event per browser session/user selection."""
    if _webplayer_login_mode(web_settings.get("login_mode")) != "auto":
        return
    marker_key = "streamforge_web_player_auto_log_marker"
    marker = f"user:{int(user.id)}" if user is not None else "unavailable"
    if str(request.session.get(marker_key) or "") == marker:
        return
    details = {
        "ip": client_ip(request),
        "client": request.headers.get("user-agent", "")[:300],
        "mode": "auto",
    }
    if user is not None:
        details["user_id"] = int(user.id)
        details["session_id"] = catalog_session_id(user, request)
        log_event(
            "Web player auto login",
            scope="client",
            actor=user.name,
            details=details,
            db=db,
        )
    else:
        log_event(
            "Web player auto login unavailable",
            scope="client",
            level="warning",
            actor="Guest",
            details=details,
            db=db,
        )
    request.session[marker_key] = marker


def _web_player_hide_quick_user_info(request: Request, settings: dict) -> bool:
    """Hide account limits only for a one-click Quick Login session."""
    # STREAMFORGE_WEBPLAYER_QUICK_USER_PRIVACY_V3056:
    # The setting remains unchanged; this is a signed-session presentation rule.
    return (
        str(settings.get("login_mode") or "").strip().lower() == "quick"
        and str(request.session.get("streamforge_web_player_login_kind") or "").strip().lower() == "quick"
    )


# STREAMFORGE_MAIN_WEBPLAYER_LOGO_ROUTE_V1010:
def _public_web_player_logo_path(db: Session) -> Path | None:
    logo_url = str(branding_settings(db).get("logo_url") or "").strip()
    if not logo_url.startswith(BRANDING_LOGO_PREFIX):
        return None
    raw_name = logo_url[len(BRANDING_LOGO_PREFIX):]
    filename = Path(raw_name).name
    if not filename or filename != raw_name:
        return None
    path = settings.logo_root / "branding" / filename
    return path if path.is_file() else None


@app.get("/web-player/logo")
def public_web_player_logo(request: Request, db: Session = Depends(get_db)):
    local_node = public_local_node(db)
    brand = _webplayer_brand_for_request(db, local_node.id, request)
    brand_logo = _webplayer_brand_url((brand or {}).get("logo_url"))
    if brand_logo.startswith(BRANDING_LOGO_PREFIX):
        path = local_branding_asset_path(brand_logo)
        if path is not None and path.is_file():
            media = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".gif":"image/gif"}
            return FileResponse(path, media_type=media.get(path.suffix.lower(), "application/octet-stream"), headers={"Cache-Control": "public, max-age=3600"})
    elif brand_logo:
        return RedirectResponse(brand_logo, status_code=302, headers={"Cache-Control": "no-store"})
    if brand is not None:
        raise HTTPException(404)
    path = _public_web_player_logo_path(db)
    if path is None:
        raise HTTPException(404)
    media = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".gif":"image/gif"}
    return FileResponse(
        path,
        media_type=media.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# STREAMFORGE_MAIN_WEBPLAYER_FAVICON_ROUTE_V2288:
@app.get("/web-player/favicon")
def public_web_player_favicon(request: Request, db: Session = Depends(get_db)):
    local_node = public_local_node(db)
    brand = _webplayer_brand_for_request(db, local_node.id, request)
    brand_favicon = _webplayer_brand_url((brand or {}).get("favicon_url"))
    if brand_favicon.startswith(BRANDING_LOGO_PREFIX):
        path = local_branding_asset_path(brand_favicon)
        if path is not None and path.is_file():
            media = {".ico":"image/x-icon",".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".gif":"image/gif"}
            return FileResponse(path, media_type=media.get(path.suffix.lower(), "application/octet-stream"), headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})
    elif brand_favicon:
        return RedirectResponse(brand_favicon, status_code=302, headers={"Cache-Control": "no-store"})
    if brand is not None:
        raise HTTPException(404)
    favicon_url = str(branding_settings(db).get("favicon_url") or "").strip()
    if not favicon_url.startswith(BRANDING_LOGO_PREFIX):
        raise HTTPException(404)
    filename = Path(favicon_url[len(BRANDING_LOGO_PREFIX):]).name
    if not filename or filename != favicon_url[len(BRANDING_LOGO_PREFIX):]:
        raise HTTPException(404)
    path = settings.logo_root / "branding" / filename
    if not path.is_file():
        raise HTTPException(404)
    media = {".ico":"image/x-icon",".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".gif":"image/gif"}
    response = FileResponse(
        path,
        media_type=media.get(path.suffix.lower(), "application/octet-stream"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )
    response.headers["X-StreamForge-WebPlayer-Favicon"] = "1"
    return response


@app.get("/web-player/download")
def public_web_player_download(request: Request, db: Session = Depends(get_db)):
    # STREAMFORGE_MAIN_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045:
    local_node = public_local_node(db)
    enforce_node_access_policy(request, local_node)
    if not _web_player_current_user(request, db):
        return _web_player_flash_redirect(request, "Please sign in first")
    web_settings = _webplayer_effective_settings(db, local_node.id, request)
    stored_name = Path(str(web_settings.get("download_stored_name") or "")).name
    display_name = Path(str(web_settings.get("download_name") or "")).name
    path = settings.webplayer_download_root / stored_name if stored_name else None
    if not display_name or path is None or not path.is_file():
        raise HTTPException(404, "Download is not available")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=display_name,
        headers={"Cache-Control": "private, no-store, max-age=0"},
    )


# STREAMFORGE_ANDROID_UPDATE_MANIFEST_ROUTE_V3061:
# STREAMFORGE_UPDATE_JSON_LOCAL_WEBPLAYER_THEME_V109:
@app.get("/update.json")
def public_android_update_manifest(request: Request, db: Session = Depends(get_db)):
    local_node = public_local_node(db)
    enforce_node_access_policy(request, local_node)
    web_settings = _webplayer_effective_settings(db, local_node.id, request)
    web_branding = _webplayer_branding_for_request(db, local_node.id, request)
    version_name = str(web_settings.get("android_version_name") or "").strip()
    description = str(web_settings.get("android_description") or "").strip()
    display_name = Path(str(web_settings.get("download_name") or "")).name
    stored_name = Path(str(web_settings.get("download_stored_name") or "")).name
    path = settings.webplayer_download_root / stored_name if stored_name else None
    android_ready = bool(
        version_name
        and display_name.lower().endswith(".apk")
        and path is not None
        and path.is_file()
    )
    base = main_playlist_public_base(db, request).rstrip("/")
    payload = {
        "version_name": version_name if android_ready else "",
        "update_url": f"{base}/{urllib.parse.quote(display_name, safe='')}" if android_ready else "",
        "description": description if android_ready else "",
        "webplayer": {
            # STREAMFORGE_UPDATE_JSON_SERVER_LOCAL_LOGO_V1010:
            # Keep this host-relative so Main update.json always points back to
            # the same Main Server that answered the manifest request.
            "logo_url": str(web_branding.get("logo_url") or ""),
            "page_color": str(web_settings.get("page_color") or WEBPLAYER_DEFAULTS["page_color"]),
            "page_alpha": int(web_settings.get("page_alpha") or 0),
            "page_rgba": str(web_settings.get("page_rgba") or ""),
            "panel_color": str(web_settings.get("panel_color") or WEBPLAYER_DEFAULTS["panel_color"]),
            "panel_alpha": int(web_settings.get("panel_alpha") or 0),
            "panel_rgba": str(web_settings.get("panel_rgba") or ""),
            "accent_color": str(web_settings.get("accent_color") or WEBPLAYER_DEFAULTS["accent_color"]),
            "accent_alpha": int(web_settings.get("accent_alpha") or 0),
            "accent_rgba": str(web_settings.get("accent_rgba") or ""),
            "text_color": str(web_settings.get("text_color") or WEBPLAYER_DEFAULTS["text_color"]),
            "text_alpha": int(web_settings.get("text_alpha") or 0),
            "text_rgba": str(web_settings.get("text_rgba") or ""),
        },
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Access-Control-Allow-Origin": "*"},
    )


@app.get("/{apk_name}.apk")
def public_android_update_apk(apk_name: str, request: Request, db: Session = Depends(get_db)):
    local_node = public_local_node(db)
    enforce_node_access_policy(request, local_node)
    web_settings = _webplayer_effective_settings(db, local_node.id, request)
    display_name = Path(str(web_settings.get("download_name") or "")).name
    stored_name = Path(str(web_settings.get("download_stored_name") or "")).name
    requested_name = Path(f"{apk_name}.apk").name
    path = settings.webplayer_download_root / stored_name if stored_name else None
    if (
        not display_name.lower().endswith(".apk")
        or requested_name != display_name
        or path is None
        or not path.is_file()
    ):
        raise HTTPException(404, "Android APK is not available")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=display_name,
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


@app.get("/web-player", response_class=HTMLResponse)
def public_web_player(request: Request, error: str = "", db: Session = Depends(get_db)):
    # STREAMFORGE_MAIN_WEBPLAYER_FLASH_ERROR_V1082:
    # Old/bookmarked ?error= URLs are converted to the same one-shot flash and
    # immediately redirected to the clean canonical root.  New login failures
    # never put the message in the query string at all.
    legacy_error = str(error or "").strip()[:240]
    if legacy_error:
        request.session[_WEB_PLAYER_FLASH_ERROR_KEY] = legacy_error
        return RedirectResponse(
            _web_player_home_url(request),
            status_code=303,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )
    error = str(request.session.pop(_WEB_PLAYER_FLASH_ERROR_KEY, "") or "").strip()[:240]
    # STREAMFORGE_MAIN_CATALOG_PER_NODE_POLICY_V99R18: do not gate the whole Main catalogue by Local-node playback policy.
    local_node = public_local_node(db)
    settings = _webplayer_effective_settings(db, local_node.id, request)
    web_branding = _webplayer_branding_for_request(db, local_node.id, request)
    user = _web_player_current_user(request, db)
    prefix = _web_player_prefix(request)
    quick_login_user = (
        _webplayer_selected_user(db, local_node, settings)
        if settings.get("login_mode") == "quick" else None
    )
    if settings.get("login_mode") == "auto" and not user and not error:
        error = "Auto Login user is unavailable. Select a valid user in Web Player Manage."
    if not user:
        _web_player_log_auto_login_once(request, db, settings, None)
        return TEMPLATES.TemplateResponse(
            request=request, name="web_player.html",
            context={"user": None, "channels": [], "categories": [], "prefix": prefix, "error": error, "branding": web_branding, "app_version": APP_VERSION, "webplayer_settings": settings, "quick_login_user": quick_login_user},
        )
    enforce_stream_user_source(user, request)
    _web_player_log_auto_login_once(request, db, settings, user)
    channels = filter_catalog_channels_for_request(user, webplayer_fast_catalog_channels(user), request, db)
    # STREAMFORGE_WEB_PLAYER_MULTI_CATEGORY_V2205:
    # Web Player filtering must follow the same multi-category channel model as
    # the Main Panel. Include every linked category and retain Uncategorized
    # only for channels that have no category assignment at all.
    categories: list[str] = []
    for channel in channels:
        names = []
        for category in list(getattr(channel, "categories", []) or []):
            cleaned = str(getattr(category, "name", "") or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        primary = str(getattr(getattr(channel, "category", None), "name", "") or "").strip()
        if primary and primary not in names:
            names.insert(0, primary)
        if not names:
            names = ["Uncategorized"]
        for category_name in names:
            if category_name not in categories:
                categories.append(category_name)
    # STREAMFORGE_MAIN_CHANNEL_HEADER_VIEWER_INFO_V2239:
    return TEMPLATES.TemplateResponse(
        request=request, name="web_player.html",
        context={
            "user": user,
            "channels": channels,
            "categories": categories,
            "prefix": prefix,
            "error": error,
            "branding": web_branding,
            "app_version": APP_VERSION,
            "webplayer_settings": settings,
            "hide_quick_user_info": _web_player_hide_quick_user_info(request, settings),
            "header_max_connections": max(0, int(user.max_connections or 0)),
            "header_online_use": max(0, int(xtream_active_connections(user))),
        },
    )


@app.post("/web-player/login")
def public_web_player_login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    prefix = _web_player_prefix(request)
    local_node = public_local_node(db)
    settings = _webplayer_effective_settings(db, local_node.id, request)
    if settings.get("login_mode") == "auto":
        return RedirectResponse(_web_player_home_url(request), status_code=303)
    user = xtream_authenticate(db, username.strip(), password)
    if not user:
        _log_client_denied(request, "Web player login failed", username=username, reason="Invalid username or password", db=db)
        return _web_player_flash_redirect(request, "Invalid username or password")
    enforce_stream_user_source(user, request)
    request.session["streamforge_web_player_user_id"] = int(user.id)
    request.session["streamforge_web_player_logged_at"] = int(time.time())
    request.session["streamforge_web_player_login_kind"] = "manual"
    playback_session_id = catalog_session_id(user, request)
    if _main_client_log_session_once(user, playback_session_id, db):
        log_event(
            "Web player login", scope="client", actor=user.name,
            details={"ip": client_ip(request), "client": request.headers.get("user-agent", "")[:300], "user_id": int(user.id), "session_id": playback_session_id},
            db=db,
        )
    return RedirectResponse(_web_player_home_url(request), status_code=303)


@app.post("/web-player/quick-login")
def public_web_player_quick_login(request: Request, db: Session = Depends(get_db)):
    # STREAMFORGE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041:
    local_node = public_local_node(db)
    prefix = _web_player_prefix(request)
    settings = _webplayer_effective_settings(db, local_node.id, request)
    if settings.get("login_mode") != "quick":
        return RedirectResponse(_web_player_home_url(request), status_code=303)
    user = _webplayer_selected_user(db, local_node, settings)
    if not user:
        _log_client_denied(request, "Web player quick login unavailable", username="Guest", reason="Quick Login user is unavailable", db=db)
        return _web_player_flash_redirect(request, "Quick Login user is unavailable")
    enforce_stream_user_source(user, request)
    request.session["streamforge_web_player_user_id"] = int(user.id)
    request.session["streamforge_web_player_logged_at"] = int(time.time())
    request.session["streamforge_web_player_login_kind"] = "quick"
    playback_session_id = catalog_session_id(user, request)
    if _main_client_log_session_once(user, playback_session_id, db):
        log_event(
            "Web player quick login", scope="client", actor=user.name,
            details={"ip": client_ip(request), "client": request.headers.get("user-agent", "")[:300], "user_id": int(user.id), "session_id": playback_session_id},
            db=db,
        )
    return RedirectResponse(_web_player_home_url(request), status_code=303)


@app.get("/web-player/online-use")
def public_web_player_online_use(request: Request, db: Session = Depends(get_db)):
    # STREAMFORGE_MAIN_WEBPLAYER_LIVE_ONLINE_V2246:
    user = _web_player_current_user(request, db)
    if not user:
        raise HTTPException(401, "Web Player session expired")
    enforce_stream_user_source(user, request)
    return JSONResponse(
        {
            "online_use": max(0, int(xtream_active_connections(user))),
            "max_connections": max(0, int(user.max_connections or 0)),
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/web-player/logout")
def public_web_player_logout(request: Request):
    prefix = _web_player_prefix(request)
    request.session.pop("streamforge_web_player_user_id", None)
    request.session.pop("streamforge_web_player_logged_at", None)
    request.session.pop("streamforge_web_player_login_kind", None)
    return RedirectResponse(_web_player_home_url(request), status_code=303)


@app.get("/web-player/watch/{slug}", response_class=HTMLResponse)
def public_web_player_watch(slug: str, request: Request, db: Session = Depends(get_db)):
    prefix = _web_player_prefix(request)
    user = _web_player_current_user(request, db)
    if not user:
        return _web_player_flash_redirect(request, "Please sign in first")
    enforce_stream_user_source(user, request)
    channel = webplayer_authorized_channel(user, slug)
    if not channel or not filter_catalog_channels_for_request(user, [channel], request, db):
        raise HTTPException(404)
    stream_url = f"{prefix}/play/{urllib.parse.quote(user.token)}/{urllib.parse.quote(channel.slug)}/master.m3u8?wp=1"
    # Keep the sidebar online-only, but resolve it once after the selected
    # channel has already been authorized so click-to-player latency is not
    # blocked by a full-catalogue readiness scan.
    channels = filter_catalog_channels_for_request(user, webplayer_fast_catalog_channels(user), request, db)
    # STREAMFORGE_MAIN_WEBPLAYER_WATCH_SETTINGS_ONCE_V1112:
    # The old watch route rebuilt the complete Web Player settings dictionary
    # twice (and looked up the Local Node twice) for one HTML response.
    local_node = public_local_node(db)
    player_settings = _webplayer_effective_settings(db, local_node.id, request)
    web_branding = _webplayer_branding_for_request(db, local_node.id, request)
    response = TEMPLATES.TemplateResponse(
        request=request, name="player.html",
        context={
            "channel": channel,
            "channels": channels,
            "watch_prefix": f"{prefix}/web-player/watch",
            "stream_url": stream_url,
            "token": user.token,
            "back_url": _web_player_home_url(request),
            "canonical_player_url": _web_player_home_url(request),
            # STREAMFORGE_MAIN_PLAYER_FAVICON_V2289:
            "branding": web_branding,
            "player_favicon_url": f"{prefix}/web-player/favicon?v={APP_VERSION}",
            # STREAMFORGE_WEB_PLAYER_USER_EXPIRY_V2212:
            "player_username": (user.xtream_username or user.name or "").strip(),
            "player_expires": user.expires_at.strftime("%Y-%m-%d %H:%M") if user.expires_at else "Never",
            # STREAMFORGE_MAIN_PLAYER_CONNECTION_INFO_V2238:
            "player_max_connections": max(0, int(user.max_connections or 0)),
            "player_online_use": max(0, int(xtream_active_connections(user))),
            "webplayer_settings": player_settings,
            "hide_quick_user_info": _web_player_hide_quick_user_info(request, player_settings),
            "prefix": prefix,
            "app_version": APP_VERSION,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-StreamForge-Playlist-Prefix"] = prefix or "/"
    return response


@app.get("/player/{token}", response_class=HTMLResponse)
def browser_player_portal(token: str, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(StreamUser).where(StreamUser.token == token))
    if not user or not user_valid(user):
        raise HTTPException(403, "Player disabled or expired")
    enforce_stream_user_source(user, request)
    if user.delivery_mode == "direct_node":
        raise HTTPException(400, "Direct node users use their node-specific M3U playlist")
    channels = filter_catalog_channels_for_request(user, online_user_channels(user), request, db)
    prefix = _web_player_prefix(request)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="player_portal.html",
        context={"user": user, "channels": channels, "token": token, "prefix": prefix, "branding": _webplayer_branding_for_request(db, public_local_node(db).id, request)},
    )


@app.get("/watch/{token}/{slug}", response_class=HTMLResponse)
def browser_player(token: str, slug: str, request: Request, db: Session = Depends(get_db)):
    user, channel = get_stream_access(token, slug, request, db)
    prefix = _web_player_prefix(request)
    stream_url = f"{prefix}/play/{token}/{slug}/master.m3u8"
    return TEMPLATES.TemplateResponse(
        request=request,
        name="player.html",
        context={
            "channel": channel,
            "channels": filter_catalog_channels_for_request(user, online_user_channels(user), request, db),
            "watch_prefix": f"{prefix}/watch/{token}",
            "stream_url": stream_url,
            "token": token,
            "back_url": f"{prefix}/player/{token}",
            "canonical_player_url": f"{prefix}/web-player",
            "branding": _webplayer_branding_for_request(db, public_local_node(db).id, request),
            "player_favicon_url": f"{prefix}/web-player/favicon?v={APP_VERSION}",
            "prefix": prefix,
            "player_username": (user.xtream_username or user.name or "").strip(),
            "player_expires": user.expires_at.strftime("%Y-%m-%d %H:%M") if user.expires_at else "Never",
            "webplayer_settings": _webplayer_effective_settings(db, public_local_node(db).id, request),
            "app_version": APP_VERSION,
        },
    )


@app.get("/playlist/{token}.m3u", response_class=PlainTextResponse)
def playlist(token: str, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(StreamUser).where(StreamUser.token == token))
    if not user or not user_valid(user):
        _log_client_denied(request, "Client playlist access denied", username=(user.name if user else "Guest"), reason="Playlist disabled or expired")
        raise HTTPException(403, "Playlist disabled or expired")
    enforce_stream_user_source(user, request)
    playback_session_id = catalog_session_id(user, request)
    if _main_client_log_session_once(user, playback_session_id, db):
        log_event(
            "Client playlist requested", scope="client", actor=user.name,
            details={"ip": client_ip(request), "client": request.headers.get("user-agent", "")[:300], "format": "token-m3u", "user_id": int(user.id), "session_id": playback_session_id},
            db=db,
        )
    base = main_playlist_public_base(db, request)
    header = "#EXTM3U"
    if user.playlist:
        header += f' x-playlist-name="{user.playlist.name.replace(chr(34), chr(39))}"'
        if user.playlist.logo_url:
            header += f' x-playlist-logo="{user.playlist.logo_url.replace(chr(34), "%22")}"'
    lines = [header]
    catalogue_channels = filter_catalog_channels_for_request(user, online_user_channels(user), request, db)
    playback_keys = issue_playback_keys(user, catalogue_channels, request, db, session_id=playback_session_id)
    public_numbers = channel_public_number_map(db)
    for channel in catalogue_channels:
        group_title = (channel.category.name if channel.category else "Uncategorized").replace('"', "'").replace("\n", " ")
        channel_name = channel.name.replace("\n", " ")
        logo = channel_logo_public_url(channel, request, db, public_base=base).replace('"', "%22").replace("\n", " ")
        logo_attribute = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1 tvg-id="{channel.slug}"{logo_attribute} group-title="{group_title}",{channel_name}')
        if channel.output_type == "hls":
            playback_key = playback_keys[channel.id]
            stream_number = int(channel.id) if user.user_type == "restream" else public_numbers[channel.id]
            lines.append(f"{base}/ek/{urllib.parse.quote(playback_key)}/{stream_number}/master.m3u8")
        elif channel.output_url:
            # Multiple remote mirrors are stored one-per-line; token playlists
            # use the first configured direct destination as the canonical URL.
            first_output = next((item.strip() for item in channel.output_url.splitlines() if item.strip()), "")
            if first_output:
                lines.append(first_output)
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="audio/x-mpegurl",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# STREAMFORGE_MAIN_SINGLE_CHANNEL_PLAYBACK_AUTH_V1054:
# HLS media requests already carry one concrete channel slug. Do not rebuild the
# complete online catalogue (including Remote Node readiness probes) for every
# master, media-playlist and segment request. Membership/policy is resolved for
# this channel only; the master route performs the authoritative readiness
# selection and child routes validate the already-selected Node.
def get_stream_access(token: str, slug: str, request: Request, db: Session) -> tuple[StreamUser, Channel]:
    user = db.scalar(select(StreamUser).where(StreamUser.token == token))
    if not user or not user_valid(user):
        raise HTTPException(403, "User disabled or expired")
    enforce_stream_user_source(user, request)
    if user.delivery_mode == "direct_node":
        raise HTTPException(403, "This user uses encrypted credential-based playback")
    channel = webplayer_authorized_channel(user, slug)
    if not channel or not filter_catalog_channels_for_request(user, [channel], request, db):
        raise HTTPException(404)
    return user, channel


# STREAMFORGE_MEDIA_XACCEL_FASTPATH_V63: authenticated Local/Main HLS segment
# requests keep authorization in the isolated Public plane, but Nginx owns the
# actual file transfer.  This avoids reading multi-megabyte media bodies into
# Python/Gunicorn memory while preserving the existing access checks.
_STREAMFORGE_MEDIA_SUFFIXES = (".ts", ".m4s", ".aac", ".mp3", ".key")
_STREAMFORGE_REMOTE_MEDIA_TTL_SECONDS = 45


def _version_tuple(value: object) -> tuple[int, ...]:
    numbers = re.findall(r"\\d+", str(value or ""))
    return tuple(int(item) for item in numbers[:3]) if numbers else tuple()


def _remote_media_fastpath_supported(node: Node) -> bool:
    return bool(
        node.node_type == "remote"
        and (node.api_token or "").strip()
        and node_public_base(node)
        and _version_tuple(getattr(node, "agent_version", None)) >= (6, 3)
    )


def _remote_media_signature(node: Node, channel_key: str, filename: str, expires: int) -> str:
    secret = str(node.api_token or "").encode("utf-8")
    payload = f"sfmedia-v1|{int(expires)}|{channel_key}|{filename}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _remote_signed_media_url(node: Node, channel: Channel, filename: str) -> str | None:
    # STREAMFORGE_REMOTE_SIGNED_MEDIA_FASTPATH_V63: only advertise this path to
    # Node Agent v6.3+; older Nodes automatically keep the legacy Main proxy.
    if not _remote_media_fastpath_supported(node):
        return None
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(_STREAMFORGE_MEDIA_SUFFIXES):
        return None
    key = node_controller._channel_key(channel)
    expires = int(time.time()) + _STREAMFORGE_REMOTE_MEDIA_TTL_SECONDS
    signature = _remote_media_signature(node, key, safe_name, expires)
    base = node_public_base(node).rstrip("/")
    return (
        f"{base}/_sf-media/{expires}/{signature}/"
        f"{urllib.parse.quote(key, safe='')}/{urllib.parse.quote(safe_name, safe='')}"
    )


def _local_hls_xaccel_response(channel: Channel, filename: str) -> Response:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(_STREAMFORGE_MEDIA_SUFFIXES):
        raise HTTPException(404)
    path = settings.hls_root / channel.slug / safe_name
    if not path.is_file():
        raise HTTPException(404)
    internal_uri = (
        f"/_streamforge_hls/{urllib.parse.quote(channel.slug, safe='')}/"
        f"{urllib.parse.quote(safe_name, safe='')}"
    )
    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": internal_uri,
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Accept-Ranges": "bytes",
            "X-StreamForge-Media-Path": "nginx-x-accel",
        },
    )


def _media_child_url(
    request: Request,
    node: Node,
    channel: Channel,
    filename: str,
    fallback_path: str,
) -> str:
    direct = _remote_signed_media_url(node, channel, filename)
    if direct:
        return direct
    return main_playlist_child_path(request, fallback_path)


def _master_playlist_content(channel: Channel, media_path: str) -> str:
    bandwidth_text = channel.video_bitrate.strip().lower()
    multiplier = 1000 if bandwidth_text.endswith("k") else 1000000 if bandwidth_text.endswith("m") else 1
    try:
        bandwidth = int(float(bandwidth_text.rstrip("km")) * multiplier) + 192000
    except ValueError:
        bandwidth = 3000000
    attributes = [f"BANDWIDTH={max(256000, bandwidth)}"]
    if channel.width and channel.height:
        attributes.append(f"RESOLUTION={channel.width}x{channel.height}")
    return "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:" + ",".join(attributes) + "\n" + media_path + "\n"


def main_playlist_child_path(request: Request, path: str) -> str:
    """Keep every generated playback child below the requested public alias."""
    # STREAMFORGE_PLAYBACK_CHILD_ALIAS_PREFIX_V3036: strict Main alias routing
    # intentionally rejects root /ek, /play, /live and /relay requests when the
    # Playlist/App alias is /test. Every master/media playlist and compatibility
    # redirect must therefore emit /test/... instead of an unprefixed child URL.
    prefix = str(
        request.scope.get("root_path")
        or request.scope.get("state", {}).get("main_access_prefix")
        or ""
    ).rstrip("/")
    normalized = path if str(path or "").startswith("/") else f"/{path}"
    if prefix and (normalized == prefix or normalized.startswith(prefix + "/")):
        return normalized
    return f"{prefix}{normalized}" if prefix else normalized


def get_local_relay_access(relay_key: str, slug: str, db: Session) -> tuple[Channel, Node]:
    channel = db.scalar(select(Channel).where(Channel.slug == slug))
    if not channel or not channel.enabled or channel.output_type != "hls":
        raise HTTPException(404)
    if not hmac.compare_digest(relay_key, channel_stream_key(channel)):
        raise HTTPException(403, "Invalid relay key")
    local = next(
        (item for item in node_controller.assigned_nodes(channel) if item.node_type == "local"),
        None,
    )
    if not local:
        raise HTTPException(503, "Local Node is not assigned to this channel")
    return channel, local


# STREAMFORGE_MAIN_RELAY_RUNTIME_STATE_HTTP_V1141:
# STREAMFORGE_MAIN_RELAY_LIVE_PID_GUARD_V1143:
# STREAMFORGE_MAIN_RELAY_RUNNING_STATE_RETRY_V1144:
# v11.43 treated every playlist miss with a non-live persisted PID as an
# intentional Main restart.  The PID field can legitimately lag the dedicated
# Main supervisor, so healthy/starting channels could return HTTP 409 and Remote
# Local-relay FFmpeg would exit before the first manifest arrived.  v11.44 uses
# three independent pieces of evidence: desired state, DB runtime state, and
# recent Local HLS media.  A visible/running Main remains retryable even if its
# persisted PID is temporarily absent.  HTTP 409 is reserved for a confirmed
# automatic restart window (`restarting`) with neither a live PID nor recent
# Local HLS media.  Manual Stop remains HTTP 410.
def _local_relay_recent_media(channel: Channel) -> bool:
    try:
        out_dir = settings.hls_root / str(channel.slug or "")
        if not out_dir.is_dir():
            return False
        segment_time = max(1, int(getattr(channel, "hls_segment_time", 1) or 1))
        fresh_seconds = max(12.0, float(segment_time * 8 + 6))
        now = time.time()
        for pattern in ("segment_*.ts", "segment_*.m4s", "*.aac", "*.mp3"):
            for path in out_dir.glob(pattern):
                try:
                    if path.is_file() and now - path.stat().st_mtime <= fresh_seconds:
                        return True
                except OSError:
                    continue
        return False
    except Exception:
        return False


def _local_relay_missing_exception(channel: Channel, detail: str) -> HTTPException:
    state = str(channel.status or "").strip().lower()
    headers = {"Cache-Control": "no-store", "X-StreamForge-Relay-State": state or "unknown"}
    if not bool(channel.desired_running) or state == "stopped":
        return HTTPException(410, detail or "Main channel is stopped", headers=headers)

    process_alive = local_channel_process_alive(channel)
    recent_media = _local_relay_recent_media(channel)
    headers["X-StreamForge-Main-FFmpeg"] = "alive" if process_alive else "unknown"
    headers["X-StreamForge-Recent-HLS"] = "1" if recent_media else "0"

    # A real automatic Main recovery clears Local HLS and persists `restarting`
    # before the replacement process is launched.  Only that converged state is
    # non-retryable so the Remote replica performs the expected restart.
    if state == "restarting" and not process_alive and not recent_media:
        headers["X-StreamForge-Main-FFmpeg"] = "down"
        return HTTPException(409, detail or "Main channel process is restarting", headers=headers)

    # `running`, `starting`, `error`, and transient/unknown states are retryable.
    # This is especially important during initial Local-relay start: Remote
    # FFmpeg must be allowed to wait for the first Main index.m3u8 rather than
    # exiting on a false 409 while Main is already Up or becoming ready.
    headers["Retry-After"] = "1"
    return HTTPException(503, detail or "Main relay media is temporarily unavailable", headers=headers)


@app.get("/relay/{relay_key}/{slug}/index.m3u8", response_class=PlainTextResponse)
def local_relay_playlist(relay_key: str, slug: str, request: Request, db: Session = Depends(get_db)):
    channel, local = get_local_relay_access(relay_key, slug, db)
    try:
        raw, _media_type = node_controller.hls_file(channel, "index.m3u8", node=local)
    except (NodeError, FileNotFoundError) as exc:
        raise _local_relay_missing_exception(channel, str(exc) or "Local relay is not ready")
    content = raw.decode("utf-8", errors="replace")
    rewritten = [
        main_playlist_child_path(request, f"/relay/{relay_key}/{slug}/{Path(line).name}") if line and not line.startswith("#") else line
        for line in content.splitlines()
    ]
    return PlainTextResponse(
        "\n".join(rewritten) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Access-Control-Allow-Origin": "*",
            "X-StreamForge-Relay": "local-node",
        },
    )


@app.get("/relay/{relay_key}/{slug}/{filename}")
def local_relay_segment(relay_key: str, slug: str, filename: str, db: Session = Depends(get_db)):
    channel, local = get_local_relay_access(relay_key, slug, db)
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith((".ts", ".m4s", ".aac", ".mp3", ".key")):
        raise HTTPException(404)
    try:
        data, media_type = node_controller.hls_file(channel, safe_name, node=local)
    except (NodeError, FileNotFoundError):
        # STREAMFORGE_MAIN_RELAY_SEGMENT_ROTATION_404_V1143:
        # Segment deletion is a normal live-HLS rotation race.  Do not promote a
        # missing old segment to channel-level 503/409; that made healthy Local-
        # relay FFmpeg reconnect/exit even while the Main channel stayed Up.
        raise HTTPException(404)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Accept-Ranges": "bytes",
            "X-StreamForge-Relay": "local-node",
        },
    )


@app.get("/play/{token}/{slug}/master.m3u8", response_class=PlainTextResponse)
def protected_master_playlist(token: str, slug: str, request: Request, sid: str = "", wp: int = 0, db: Session = Depends(get_db)):
    user, channel = get_stream_access(token, slug, request, db)
    try:
        # STREAMFORGE_MAIN_WEBPLAYER_FAST_NODE_ROUTE_V1113:
        # Interactive channel switches must not synchronously probe every Remote
        # Node. Use recent readiness caches first and fall back to legacy probes
        # only when no cached/local-ready candidate exists.
        choice = choose_playback_node(
            db, channel, request, user=user, require_ready=True,
            prefer_cached_ready=bool(wp),
        )
    except NodeSelectionError as exc:
        raise HTTPException(503, str(exc))
    enforce_node_access_policy(request, choice.node)
    sid = viewer_tracker.request_session_id(request, sid or catalog_session_id(user, request))
    # STREAMFORGE_RESTREAM_MAX_CONNECTIONS: viewer and restream accounts
    # use the same strict per-user session limit.
    if not connection_tracker.allow(user, request, session_id=sid, playback_start=True):
        raise HTTPException(429, "Connection limit reached")
    viewer_tracker.touch(user.id, choice.node.id, channel.id, request, session_id=sid)
    content = _master_playlist_content(channel, main_playlist_child_path(request, f"/play/{token}/{slug}/n/{choice.node.id}/index.m3u8?sid={urllib.parse.quote(sid)}"))
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Access-Control-Allow-Origin": "*",
        "X-StreamForge-Node": choice.node.name,
    }
    if choice.pinned_prefix:
        headers["X-StreamForge-Prefix"] = choice.pinned_prefix
    return PlainTextResponse(content, media_type="application/vnd.apple.mpegurl", headers=headers)


@app.get("/play/{token}/{slug}/n/{node_id}/index.m3u8", response_class=PlainTextResponse)
def protected_playlist(token: str, slug: str, node_id: int, request: Request, sid: str = "", db: Session = Depends(get_db)):
    user, channel = get_stream_access(token, slug, request, db)
    try:
        node = validate_routed_node(db, channel, request, node_id, user=user)
        enforce_node_access_policy(request, node)
        sid = viewer_tracker.request_session_id(request, sid)
        if not connection_tracker.allow(user, request, session_id=sid):
            raise HTTPException(429, "Connection limit reached")
        viewer_tracker.touch(user.id, node.id, channel.id, request, session_id=sid)
        # STREAMFORGE_HLS_PROXY_RELEASE_DB_V32:
        # Authorization/routing is complete. Remote HLS fetches can wait up to
        # their network timeout and must not pin a DB connection meanwhile.
        db.commit()
        raw, _media_type = node_controller.hls_file(channel, "index.m3u8", node=node)
    except (NodeSelectionError, NodeError, FileNotFoundError) as exc:
        # STREAMFORGE_MEDIA_PLAYLIST_ROUTE_FALLBACK_V100: if a previously
        # selected Static/Strict target goes down after the master playlist was
        # issued, move the media-playlist request to a newly ready fallback.
        try:
            fallback = choose_playback_node(db, channel, request, user=user, require_ready=True)
            if int(fallback.node.id) != int(node_id):
                fallback_sid = viewer_tracker.request_session_id(request, sid or catalog_session_id(user, request))
                return RedirectResponse(
                    main_playlist_child_path(request, f"/play/{token}/{slug}/n/{fallback.node.id}/index.m3u8?sid={urllib.parse.quote(fallback_sid)}"),
                    status_code=307,
                )
        except NodeSelectionError:
            pass
        raise HTTPException(503, str(exc) or "Stream is not ready")
    content = raw.decode("utf-8", errors="replace")
    rewritten = [
        _media_child_url(
            request, node, channel, Path(line).name,
            f"/play/{token}/{slug}/n/{node.id}/{Path(line).name}?sid={urllib.parse.quote(sid)}",
        ) if line and not line.startswith("#") else line
        for line in content.splitlines()
    ]
    return PlainTextResponse(
        "\n".join(rewritten) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Access-Control-Allow-Origin": "*", "X-StreamForge-Node": node.name},
    )


@app.get("/play/{token}/{slug}/n/{node_id}/{filename}")
def protected_segment(token: str, slug: str, node_id: int, filename: str, request: Request, sid: str = "", db: Session = Depends(get_db)):
    user, channel = get_stream_access(token, slug, request, db)
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith((".ts", ".m4s", ".aac", ".mp3", ".key")):
        raise HTTPException(404)
    try:
        node = validate_routed_node(db, channel, request, node_id, user=user)
        enforce_node_access_policy(request, node)
        if not connection_tracker.allow(user, request, session_id=sid):
            raise HTTPException(429, "Connection limit reached")
        viewer_tracker.touch(user.id, node.id, channel.id, request, session_id=sid)
        db.commit()
        if node.node_type == "local":
            return _local_hls_xaccel_response(channel, safe_name)
        data, media_type = node_controller.hls_file(channel, safe_name, node=node)
    except NodeSelectionError as exc:
        raise HTTPException(403, str(exc))
    except (NodeError, FileNotFoundError):
        raise HTTPException(404)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes", "X-StreamForge-Node": node.name},
    )


# Compatibility redirects for playlists generated by releases before v0.7.0.
@app.get("/play/{token}/{slug}/index.m3u8", response_class=PlainTextResponse)
def protected_playlist_legacy(token: str, slug: str, request: Request, db: Session = Depends(get_db)):
    user, channel = get_stream_access(token, slug, request, db)
    try:
        choice = choose_playback_node(db, channel, request, user=user, require_ready=True)
    except NodeSelectionError as exc:
        raise HTTPException(503, str(exc))
    enforce_node_access_policy(request, choice.node)
    sid = viewer_tracker.request_session_id(request, catalog_session_id(user, request))
    if not connection_tracker.allow(user, request, session_id=sid, playback_start=True):
        raise HTTPException(429, "Connection limit reached")
    viewer_tracker.touch(user.id, choice.node.id, channel.id, request, session_id=sid)
    return RedirectResponse(main_playlist_child_path(request, f"/play/{token}/{slug}/n/{choice.node.id}/index.m3u8?sid={urllib.parse.quote(sid)}"), status_code=307)



# STREAMFORGE_PRETTY_STREAM_ERROR_PAGE_V301:
def stream_unavailable_response(
    request: Request,
    db: Session,
    detail: str,
    *,
    status_code: int = 503,
    channel: Channel | None = None,
) -> Response:
    """Show a branded HTML error for direct browser navigation.

    HLS players/clients still receive a compact plain-text error with the same
    HTTP status, so playlist retry/failover behavior is not changed.
    """
    accept = str(request.headers.get("accept") or "").lower()
    wants_html = "text/html" in accept or "application/xhtml+xml" in accept
    clean_detail = str(detail or "Stream is temporarily unavailable").strip()
    if not wants_html:
        return PlainTextResponse(
            clean_detail,
            status_code=status_code,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Access-Control-Allow-Origin": "*",
            },
        )

    branding = branding_settings(db)
    lowered = clean_detail.lower()
    if "no assigned load-balancing node is playback-ready" in lowered:
        title = "Stream temporarily unavailable"
        message = "No assigned streaming server is ready to play this channel right now."
        hint = "The channel may still be starting, reconnecting, or waiting for an assigned Node to become ready."
    elif "not playback-ready" in lowered or "not ready" in lowered:
        title = "Stream is getting ready"
        message = "The selected streaming server is not ready for playback yet."
        hint = "Please wait a few seconds and try again."
    else:
        title = "Stream temporarily unavailable"
        message = "This channel cannot be played right now."
        hint = "Please try again shortly."

    return TEMPLATES.TemplateResponse(
        request=request,
        name="stream_error.html",
        context={
            "branding": branding,
            "channel": channel,
            "status_code": int(status_code),
            "error_title": title,
            "error_message": message,
            "error_hint": hint,
            "technical_detail": clean_detail,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def get_direct_stream_access(stream_key: str, slug: str, db: Session) -> Channel:
    channel = db.scalar(select(Channel).where(Channel.slug == slug))
    if not channel or not channel.enabled or channel.output_type != "hls":
        raise HTTPException(404)
    if not hmac.compare_digest(stream_key, channel_stream_key(channel)):
        raise HTTPException(403, "Invalid stream key")
    return channel


@app.get("/live/{stream_key}/{slug}", response_class=HTMLResponse)
def direct_browser_player(stream_key: str, slug: str, request: Request, db: Session = Depends(get_db)):
    channel = get_direct_stream_access(stream_key, slug, db)
    stream_url = main_playlist_child_path(request, f"/live/{stream_key}/{slug}/master.m3u8")
    local_node = public_local_node(db)
    prefix = _web_player_prefix(request)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="player.html",
        context={
            "channel": channel,
            "channels": [channel],
            "watch_prefix": f"{prefix}/live/{stream_key}",
            "stream_url": stream_url,
            "token": stream_key,
            "back_url": None,
            "canonical_player_url": f"{prefix}/web-player",
            "branding": _webplayer_branding_for_request(db, local_node.id, request),
            "player_favicon_url": f"{prefix}/web-player/favicon?v={APP_VERSION}",
            "prefix": prefix,
            "webplayer_settings": _webplayer_effective_settings(db, local_node.id, request),
            "hide_quick_user_info": True,
            "player_username": "",
            "player_expires": "",
            "player_max_connections": 0,
            "player_online_use": 0,
            "app_version": APP_VERSION,
        },
    )


@app.get("/live/{stream_key}/{slug}/master.m3u8", response_class=PlainTextResponse)
def direct_master_playlist(stream_key: str, slug: str, request: Request, db: Session = Depends(get_db)):
    channel = get_direct_stream_access(stream_key, slug, db)
    try:
        choice = choose_playback_node(db, channel, request, user=None, require_ready=True)
    except NodeSelectionError as exc:
        # STREAMFORGE_DIRECT_MASTER_PRETTY_ERROR_V301:
        return stream_unavailable_response(request, db, str(exc), status_code=503, channel=channel)
    enforce_node_access_policy(request, choice.node)
    content = _master_playlist_content(channel, main_playlist_child_path(request, f"/live/{stream_key}/{slug}/n/{choice.node.id}/index.m3u8"))
    return PlainTextResponse(
        content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Access-Control-Allow-Origin": "*", "X-StreamForge-Node": choice.node.name},
    )


@app.get("/live/{stream_key}/{slug}/n/{node_id}/index.m3u8", response_class=PlainTextResponse)
def direct_media_playlist(stream_key: str, slug: str, node_id: int, request: Request, db: Session = Depends(get_db)):
    channel = get_direct_stream_access(stream_key, slug, db)
    try:
        node = validate_routed_node(db, channel, request, node_id, user=None)
        enforce_node_access_policy(request, node)
        db.commit()
        raw, _media_type = node_controller.hls_file(channel, "index.m3u8", node=node)
    except (NodeSelectionError, NodeError, FileNotFoundError) as exc:
        # STREAMFORGE_DIRECT_MEDIA_PLAYLIST_FALLBACK_V100: direct stream media
        # playlists also reselect when their preferred/previous Node is down.
        try:
            fallback = choose_playback_node(db, channel, request, user=None, require_ready=True)
            if int(fallback.node.id) != int(node_id):
                return RedirectResponse(
                    main_playlist_child_path(request, f"/live/{stream_key}/{slug}/n/{fallback.node.id}/index.m3u8"),
                    status_code=307,
                )
        except NodeSelectionError:
            pass
        raise HTTPException(503, str(exc) or "Stream is not ready")
    content = raw.decode("utf-8", errors="replace")
    rewritten = [
        _media_child_url(
            request, node, channel, Path(line).name,
            f"/live/{stream_key}/{slug}/n/{node.id}/{Path(line).name}",
        ) if line and not line.startswith("#") else line
        for line in content.splitlines()
    ]
    return PlainTextResponse(
        "\n".join(rewritten) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Access-Control-Allow-Origin": "*", "X-StreamForge-Node": node.name},
    )


@app.get("/live/{stream_key}/{slug}/n/{node_id}/{filename}")
def direct_segment(stream_key: str, slug: str, node_id: int, filename: str, request: Request, db: Session = Depends(get_db)):
    channel = get_direct_stream_access(stream_key, slug, db)
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith((".ts", ".m4s", ".aac", ".mp3", ".key")):
        raise HTTPException(404)
    try:
        node = validate_routed_node(db, channel, request, node_id, user=None)
        enforce_node_access_policy(request, node)
        db.commit()
        if node.node_type == "local":
            return _local_hls_xaccel_response(channel, safe_name)
        data, media_type = node_controller.hls_file(channel, safe_name, node=node)
    except NodeSelectionError as exc:
        raise HTTPException(403, str(exc))
    except (NodeError, FileNotFoundError):
        raise HTTPException(404)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes", "X-StreamForge-Node": node.name},
    )


@app.get("/live/{stream_key}/{slug}/index.m3u8", response_class=PlainTextResponse)
def direct_media_playlist_legacy(stream_key: str, slug: str, request: Request, db: Session = Depends(get_db)):
    channel = get_direct_stream_access(stream_key, slug, db)
    try:
        choice = choose_playback_node(db, channel, request, user=None, require_ready=True)
    except NodeSelectionError as exc:
        raise HTTPException(503, str(exc))
    enforce_node_access_policy(request, choice.node)
    return RedirectResponse(main_playlist_child_path(request, f"/live/{stream_key}/{slug}/n/{choice.node.id}/index.m3u8"), status_code=307)


# Xtream-compatible API for Android/TV clients.
def xtream_authenticate(db: Session, username: str, password: str) -> StreamUser | None:
    cleaned = (username or "").strip()
    if not cleaned or not password:
        return None
    user = db.scalar(select(StreamUser).where(func.lower(StreamUser.xtream_username) == cleaned.lower()))
    if not user or not user_valid(user) or not user.xtream_password_hash:
        return None
    return user if verify_password(password, user.xtream_password_hash) else None


def xtream_server_info(request: Request, db: Session) -> dict[str, object]:
    parsed = urlsplit(main_playlist_public_base(db, request))
    protocol = parsed.scheme or request.url.scheme or "http"
    host = parsed.hostname or request.url.hostname or "localhost"
    port = parsed.port or (443 if protocol == "https" else 80)
    now = datetime.now(timezone.utc)
    return {
        "url": host,
        "port": str(port),
        "https_port": str(port if protocol == "https" else 443),
        "server_protocol": protocol,
        "rtmp_port": "0",
        "timezone": "UTC",
        "timestamp_now": int(now.timestamp()),
        "time_now": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def xtream_active_connections(user: StreamUser) -> int:
    snapshot = viewer_tracker.snapshot()
    tracked = len({item.session_id for item in snapshot.sessions if item.user_id == user.id})
    return max(tracked, connection_tracker.active_count(user.id))


def xtream_login_payload(user: StreamUser | None, username: str, password: str, request: Request, db: Session) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    if not user:
        user_info: dict[str, object] = {
            "username": username, "password": password, "message": "Invalid credentials",
            "auth": 0, "status": "Disabled", "exp_date": None, "is_trial": "0",
            "active_cons": "0", "created_at": "0", "max_connections": "0",
            "allowed_output_formats": ["m3u8"],
        }
    else:
        expiry = user.expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        created = user.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        user_info = {
            "username": user.xtream_username or username, "password": password, "message": "",
            "auth": 1, "status": "Active",
            "exp_date": str(int(expiry.timestamp())) if expiry else None,
            "is_trial": "0", "active_cons": str(xtream_active_connections(user)),
            "created_at": str(int(created.timestamp())),
            "max_connections": str(max(0, int(user.max_connections or 0))),
            "allowed_output_formats": (["m3u8", "ts"] if user.user_type == "restream" else ["m3u8"]),
        }
    return {"user_info": user_info, "server_info": xtream_server_info(request, db)}


def xtream_channels(user: StreamUser, request: Request | None = None, db: Session | None = None) -> list[Channel]:
    channels = online_user_channels(user)
    if request is not None and db is not None:
        channels = filter_catalog_channels_for_request(user, channels, request, db)
    return channels


def xtream_category_payload(user: StreamUser, request: Request, db: Session) -> list[dict[str, object]]:
    # v2.1.15: category responses never require request-scoped playback keys.
    found: dict[str, tuple[str, int]] = {}
    for channel in xtream_channels(user, request, db):
        categories = channel_category_list(channel)
        if not categories:
            found.setdefault("0", ("Uncategorized", 10**9))
        for category in categories:
            found.setdefault(str(category.id), (category.name, int(category.sort_order or 100000)))
    return [
        {"category_id": category_id, "category_name": item[0], "parent_id": 0}
        for category_id, item in sorted(found.items(), key=lambda pair: (pair[1][1], pair[1][0].lower()))
    ]


def xtream_stream_payload(user: StreamUser, request: Request, db: Session, category_id: str | None = None) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    playback_session_id = catalog_session_id(user, request)
    public_base = main_playlist_public_base(db, request)
    # v2.1.15: define the batch in this endpoint before the get.php loop.
    catalogue_channels = xtream_channels(user, request, db)
    playback_keys = issue_playback_keys(user, catalogue_channels, request, db, session_id=playback_session_id)
    public_numbers = channel_public_number_map(db)
    for channel in catalogue_channels:
        linked_categories = channel_category_list(channel)
        channel_category_ids = [str(item.id) for item in linked_categories] or ["0"]
        channel_category_id = str(channel.category_id or (linked_categories[0].id if linked_categories else 0))
        if category_id not in {None, "", "0"} and str(category_id) not in channel_category_ids:
            continue
        created = channel.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        items.append({
            "num": len(items) + 1,
            "name": channel.name,
            "stream_type": "live",
            "stream_id": channel.id,
            "stream_icon": channel_logo_public_url(channel, request, db, public_base=public_base),
            "epg_channel_id": channel.slug,
            "added": str(int(created.timestamp())),
            "is_adult": "0",
            "category_id": channel_category_id,
            "category_ids": channel_category_ids,
            "custom_sid": playback_session_id,
            "tv_archive": 0,
            "direct_source": f"{public_base}/ek/{playback_keys[channel.id]}/{int(channel.id) if user.user_type == 'restream' else public_numbers[channel.id]}/master.m3u8",
            "tv_archive_duration": 0,
            "container_extension": "m3u8",
        })
    return items


# STREAMFORGE_MAIN_XTREAM_CLIENT_LOGIN_SESSION_CONTEXT_V1060:
def _main_client_log_session_once(user: StreamUser, sid: str, db: Session) -> bool:
    # STREAMFORGE_MAIN_CLIENT_LOGICAL_SESSION_DEDUPE_V1081: the key is refreshed
    # by the control-plane viewer observer while playback is active. Therefore
    # it expires only after the configured offline gap, not one hour after login.
    reset_seconds = max(60, app_setting_int(db, CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY, 60) * 60)
    key = redis_state.key("client-log-session", int(user.id), sid)
    ok, acquired = redis_state.best_effort_call(lambda client: client.set(key, "1", nx=True, ex=reset_seconds))
    return bool(acquired) if ok else True

def _log_xtream_client_login_once(request: Request, user: StreamUser, db: Session) -> None:
    """Log successful Xtream credential use once per logical playback session."""
    sid = catalog_session_id(user, request)
    if not _main_client_log_session_once(user, sid, db):
        return
    log_event(
        "Xtream client login", scope="client", actor=user.name,
        details={
            "ip": client_ip(request),
            "client": request.headers.get("user-agent", "")[:300],
            "username": user.xtream_username or user.name,
            "mode": "manual",
            "user_id": int(user.id),
            "session_id": sid,
        },
        db=db,
    )


@app.get("/player_api.php")
def xtream_player_api(
    request: Request,
    username: str = "",
    password: str = "",
    action: str = "",
    category_id: str | None = None,
    db: Session = Depends(get_db),
):
    local_node = public_local_node(db)
    user = xtream_authenticate(db, username, password)
    if user:
        enforce_stream_user_source(user, request)
    else:
        _log_client_denied(request, "Xtream client authentication failed", username=username, reason="Invalid Xtream credentials", db=db)
    if not action:
        if user:
            _log_xtream_client_login_once(request, user, db)
        return JSONResponse(xtream_login_payload(user, username, password, request, db))
    if not user:
        return JSONResponse([], status_code=200)
    if action == "get_live_categories":
        return JSONResponse(xtream_category_payload(user, request, db))
    if action == "get_live_streams":
        return JSONResponse(xtream_stream_payload(user, request, db, category_id))
    # Return empty arrays for unsupported catalogue actions so clients can continue safely.
    if action in {
        "get_vod_categories", "get_vod_streams", "get_series_categories",
        "get_series", "get_short_epg", "get_simple_data_table",
    }:
        return JSONResponse([])
    return JSONResponse([])


def xtream_stream_target(user: StreamUser, channel: Channel) -> str:
    if user.delivery_mode == "direct_node":
        if not user.direct_node:
            raise HTTPException(503, "Direct node is not configured")
        base = node_public_base(user.direct_node)
        if not base:
            raise HTTPException(503, "Direct node URL is not configured")
        key = node_controller._channel_key(channel)
        return f"{base}/node-play/{urllib.parse.quote(user.token)}/{urllib.parse.quote(key)}/master.m3u8"
    return f"/play/{urllib.parse.quote(user.token)}/{urllib.parse.quote(channel.slug)}/master.m3u8"


def xtream_stream_access(
    username: str, password: str, stream_ref: str, request: Request, db: Session
) -> tuple[StreamUser, Channel]:
    user = xtream_authenticate(db, username, password)
    if not user:
        _log_client_denied(request, "Xtream stream authentication failed", username=username, reason="Invalid Xtream credentials")
        raise HTTPException(403, "Invalid Xtream credentials")
    raw_id = stream_ref.rsplit(".", 1)[0]
    try:
        channel_id = int(raw_id)
    except ValueError:
        raise HTTPException(404, "Invalid stream ID")
    channel = next((item for item in xtream_channels(user, request, db) if item.id == channel_id), None)
    if not channel:
        raise HTTPException(404, "Stream not found")
    return user, channel


@app.get("/live/{username}/{password}/{stream_ref}")
def xtream_live_stream(
    username: str, password: str, stream_ref: str, request: Request,
    sid: str = "", device_id: str = "", db: Session = Depends(get_db)
):
    user, channel = xtream_stream_access(username, password, stream_ref, request, db)
    enforce_stream_user_source(user, request)
    extension = stream_ref.rsplit(".", 1)[-1].lower() if "." in stream_ref else "m3u8"
    if extension == "ts" and user.user_type != "restream":
        raise HTTPException(403, "Viewer accounts cannot use restream/TS output")
    playback_session_id = viewer_tracker.request_session_id(request, sid or device_id)
    key = issue_playback_key(user, channel, request, db, session_id=playback_session_id)
    public_number = int(channel.id) if user.user_type == "restream" else channel_public_number_map(db).get(int(channel.id), int(channel.id))
    return RedirectResponse(main_playlist_child_path(request, f"/ek/{urllib.parse.quote(key)}/{public_number}/master.m3u8"), status_code=307)


@app.get("/get.php", response_class=PlainTextResponse)
def xtream_get_playlist(
    request: Request, username: str = "", password: str = "",
    type: str = "m3u_plus", output: str = "m3u8", db: Session = Depends(get_db),
):
    local_node = public_local_node(db)
    user = xtream_authenticate(db, username, password)
    if not user:
        _log_client_denied(request, "Xtream playlist authentication failed", username=username, reason="Invalid Xtream credentials")
        raise HTTPException(403, "Invalid Xtream credentials")
    enforce_stream_user_source(user, request)
    playback_session_id = catalog_session_id(user, request)
    # STREAMFORGE_MAIN_XTREAM_PLAYLIST_REQUEST_DB_REUSE_V1061:
    # Keep the enriched Xtream playlist Client-log write on this request DB
    # session; user/SID context is only metadata for inline Session age.
    if _main_client_log_session_once(user, playback_session_id, db):
        log_event(
            "Client playlist requested", scope="client", actor=user.name,
            details={"ip": client_ip(request), "client": request.headers.get("user-agent", "")[:300], "format": "xtream-m3u", "username": username, "user_id": int(user.id), "session_id": playback_session_id},
            db=db,
        )
    base = main_playlist_public_base(db, request)
    safe_user = urllib.parse.quote(username, safe="")
    safe_password = urllib.parse.quote(password, safe="")
    extension = "ts" if output.lower() == "ts" and user.user_type == "restream" else "m3u8"
    lines = ["#EXTM3U"]
    catalogue_channels = xtream_channels(user, request, db)
    playback_keys = issue_playback_keys(user, catalogue_channels, request, db, session_id=playback_session_id)
    public_numbers = channel_public_number_map(db)
    for channel in catalogue_channels:
        category = (channel.category.name if channel.category else "Uncategorized").replace('"', "'")
        logo = channel_logo_public_url(channel, request, db, public_base=base).replace('"', "%22")
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(
            f'#EXTINF:-1 tvg-id="{channel.slug}"{logo_attr} group-title="{category}",{channel.name}'
        )
        encrypted_key = playback_keys[channel.id]
        stream_number = int(channel.id) if user.user_type == "restream" else public_numbers[channel.id]
        lines.append(f"{base}/ek/{urllib.parse.quote(encrypted_key)}/{stream_number}/master.m3u8")
    download_name = re.sub(r"[^A-Za-z0-9._-]+", "-", username).strip("-._")[:80] or "playlist"
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="audio/x-mpegurl",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Content-Disposition": f'attachment; filename="{download_name}.m3u"',
        },
    )


def _encrypted_playback_node(db: Session, user: StreamUser, channel: Channel, request: Request, *, require_ready: bool = True) -> Node:
    if user.delivery_mode == "direct_node":
        node = user.direct_node
        if not node or not node.enabled or node.id not in {item.id for item in node_controller.assigned_nodes(channel)}:
            raise HTTPException(503, "Direct playback node is unavailable")
        if require_ready and not node_controller.hls_ready(channel, node):
            raise HTTPException(503, "Direct playback node is not ready")
        enforce_node_access_policy(request, node)
        return node
    try:
        choice = choose_playback_node(db, channel, request, user=user, require_ready=require_ready)
    except NodeSelectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    enforce_node_access_policy(request, choice.node)
    return choice.node


@app.get("/ek/{playback_key}/{channel_id}/master.m3u8", response_class=PlainTextResponse)
def encrypted_master_playlist(playback_key: str, channel_id: int, request: Request, db: Session = Depends(get_db)):
    user, channel, session_id = verify_playback_key(playback_key, request, db)
    # The signed key, not the catalogue-facing path number, authorizes the
    # internal channel. This accepts both new 101+ and legacy database-ID URLs.
    if not connection_tracker.allow(user, request, session_id=session_id, playback_start=True):
        raise HTTPException(429, "Connection limit reached")
    node = _encrypted_playback_node(db, user, channel, request)
    viewer_tracker.touch(user.id, node.id, channel.id, request, session_id=session_id)
    content = _master_playlist_content(channel, main_playlist_child_path(request, f"/ek/{playback_key}/{channel_id}/n/{node.id}/index.m3u8"))
    return PlainTextResponse(content, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "X-StreamForge-Encrypted": "1", "X-StreamForge-Node": node.name})


@app.get("/ek/{playback_key}/{channel_id}/n/{node_id}/index.m3u8", response_class=PlainTextResponse)
def encrypted_media_playlist(playback_key: str, channel_id: int, node_id: int, request: Request, db: Session = Depends(get_db)):
    user, channel, session_id = verify_playback_key(playback_key, request, db)
    if not connection_tracker.allow(user, request, session_id=session_id):
        raise HTTPException(429, "Connection limit reached")
    # STREAMFORGE_ENCRYPTED_CHILD_ROUTE_FASTPATH_V1058:
    # The master playlist already selected a playback node.  Playlist/Xtream
    # clients can issue several child index/segment requests in parallel, so
    # re-running full node selection + HLS readiness on every child can make
    # stricter Android/VLC clients cancel a request (Nginx 499) even while the
    # same HLS output is healthy.  Mirror the proven /play child path: validate
    # the encoded routed node, then fetch media without another readiness probe.
    try:
        node = validate_routed_node(db, channel, request, node_id, user=user)
        enforce_node_access_policy(request, node)
        viewer_tracker.touch(user.id, node.id, channel.id, request, session_id=session_id)
        db.commit()
        raw, _media_type = node_controller.hls_file(channel, "index.m3u8", node=node)
    except (NodeSelectionError, NodeError, FileNotFoundError) as exc:
        # If the master-selected node disappeared between master and child
        # fetch, move the media playlist to a newly-ready fallback exactly as
        # the normal /play route already does.
        try:
            fallback = choose_playback_node(db, channel, request, user=user, require_ready=True)
            if int(fallback.node.id) != int(node_id):
                return RedirectResponse(
                    main_playlist_child_path(request, f"/ek/{playback_key}/{channel_id}/n/{fallback.node.id}/index.m3u8"),
                    status_code=307,
                )
        except NodeSelectionError:
            pass
        raise HTTPException(503, str(exc) or "Stream is not ready") from exc
    rewritten = [
        _media_child_url(
            request, node, channel, Path(line).name,
            f"/ek/{playback_key}/{channel_id}/n/{node.id}/{Path(line).name}",
        ) if line and not line.startswith("#") else line
        for line in raw.decode("utf-8", errors="replace").splitlines()
    ]
    return PlainTextResponse(
        "\n".join(rewritten) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Access-Control-Allow-Origin": "*",
            "X-StreamForge-Encrypted": "1",
            "X-StreamForge-Node": node.name,
        },
    )


@app.get("/ek/{playback_key}/{channel_id}/n/{node_id}/{filename}")
def encrypted_segment(playback_key: str, channel_id: int, node_id: int, filename: str, request: Request, db: Session = Depends(get_db)):
    user, channel, session_id = verify_playback_key(playback_key, request, db)
    if not connection_tracker.allow(user, request, session_id=session_id):
        raise HTTPException(429, "Connection limit reached")
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith((".ts", ".m4s", ".aac", ".mp3", ".key")):
        raise HTTPException(404)
    try:
        # STREAMFORGE_ENCRYPTED_CHILD_ROUTE_FASTPATH_V1058: keep segment
        # authorization on the master-selected node without another expensive
        # readiness/selection pass. Local media still exits through X-Accel.
        node = validate_routed_node(db, channel, request, node_id, user=user)
        enforce_node_access_policy(request, node)
        viewer_tracker.touch(user.id, node.id, channel.id, request, session_id=session_id)
        db.commit()
        if node.node_type == "local":
            return _local_hls_xaccel_response(channel, safe_name)
        data, media_type = node_controller.hls_file(channel, safe_name, node=node)
    except NodeSelectionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (NodeError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes", "X-StreamForge-Encrypted": "1", "X-StreamForge-Node": node.name})


@app.get("/channels/{channel_id}/errors.json", dependencies=[Depends(permission_required("channels.view"))])
def channel_errors_json(
    channel_id: int,
    request: Request,
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),
):
    admin = current_admin(request, db)
    if not admin:
        raise HTTPException(401)
    channel = db.scalar(
        select(Channel)
        .options(selectinload(Channel.nodes), selectinload(Channel.node))
        .where(Channel.id == channel_id)
    )
    if not channel:
        raise HTTPException(404, "Channel not found")

    source = source_endpoint(channel.input_url)
    assigned_nodes = node_controller.assigned_nodes(channel)
    local_node = next((item for item in assigned_nodes if item.node_type == "local"), None)
    remote_node_ids = {int(item.id) for item in assigned_nodes if item.node_type == "remote"}
    known_nodes = {int(local_node.id): (local_node.name or "Main Server")} if local_node is not None else {}
    rows: list[dict[str, object]] = []

    # STREAMFORGE_MAIN_CHANNEL_LOGS_LOCAL_ONLY_V65R9:
    # Main Channels/Dashboard stream logs are deliberately local-only. Remote
    # Node runtime events stay on Node/Logs surfaces and are not fetched here.
    local_query = select(LogEntry).where(LogEntry.channel_id == channel_id)
    if remote_node_ids:
        local_query = local_query.where(
            or_(LogEntry.node_id.is_(None), ~LogEntry.node_id.in_(remote_node_ids))
        )
    local_entries = db.scalars(
        local_query
        .order_by(LogEntry.created_at.desc(), LogEntry.id.desc())
        .limit(2000)
    ).all()

    for item in local_entries:
        message = str(item.message or "").strip()
        message_lower = message.lower()
        # Operator intent alone is not a runtime outcome. Keep Main aggregate
        # completed/stopped rows as local control-plane history.
        if message_lower == "channel start requested":
            continue
        details_text, details_payload = channel_log_details(item.details)
        aggregate_success = False
        if message_lower == "channel start completed":
            errors = details_payload.get("errors") if isinstance(details_payload, dict) else None
            if errors or str(item.level or "").lower() in {"warning", "error"}:
                action, action_class = "START FAILED", "start-failed"
            else:
                action, action_class = "STARTED", "started"
                aggregate_success = True
            server_name = "Main Panel"
        else:
            action, action_class = channel_log_action(message, item.level)
            if item.node_id is not None:
                server_name = known_nodes.get(int(item.node_id), "Main Server")
            elif message_lower.startswith(("local ffmpeg", "input failover", "primary input")):
                server_name = local_node.name if local_node and local_node.name else "Main Server"
            elif message_lower in {"channel stopped on assigned nodes", "restored channel after service startup"}:
                server_name = "Main Panel"
                aggregate_success = message_lower == "channel stopped on assigned nodes" and str(item.level or "").lower() not in {"warning", "error"}
            else:
                server_name = "Main Server"
        stamp = channel_log_datetime(item.created_at)
        rows.append({
            "id": f"main-{item.id}",
            "server_name": server_name,
            "source": source,
            "action": action,
            "action_class": action_class,
            "level": item.level,
            "message": message,
            "details": details_text,
            "created_at": format_local_time(stamp),
            "_sort_time": stamp,
            "_aggregate_success": aggregate_success,
        })

    rows = dedupe_channel_log_rows(collapse_aggregate_channel_log_rows(rows))

    # last_error is a current-state banner, not historical data. Use the same
    # effective runtime status as /status.json so a recovered/running channel
    # cannot show a warning outside the modal while the modal has no error.
    runtime = main_channel_runtime(channel)
    effective_status = str(runtime.get("status") or channel.status or "unknown").strip().lower()
    effective_error = str(runtime.get("last_error") if "last_error" in runtime else (channel.last_error or "")).strip()
    current_error = effective_error if effective_status in {"error", "degraded", "starting", "restarting"} else ""
    if current_error and not rows:
        now = datetime.now(timezone.utc)
        rows.append({
            "id": f"current-{channel.id}",
            "server_name": "Main Panel",
            "source": source,
            "action": "ERROR",
            "action_class": "error",
            "level": "error",
            "message": "Current runtime error",
            "details": current_error,
            "created_at": format_local_time(now),
        })

    safe_page_size = min(100, max(5, int(page_size or 10)))
    total = len(rows)
    pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(1, int(page or 1)), pages)
    selected = rows[(safe_page - 1) * safe_page_size:safe_page * safe_page_size]
    restart_count = sum(1 for item in rows if item.get("action") == "RESTARTED" or "ffmpeg exited" in str(item.get("message") or "").lower())
    error_count = sum(1 for item in rows if str(item.get("level") or "").lower() in {"warning", "error"})
    return {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "current_error": current_error,
        "count": max(error_count, 1 if current_error else 0),
        "restart_count": restart_count,
        "page": safe_page,
        "page_size": safe_page_size,
        "pages": pages,
        "total": total,
        "can_clear": has_permission(admin, "logs.clear"),
        "clear_endpoint": f"/channels/{channel.id}/errors/clear",
        "entries": selected,
    }


@app.post("/channels/{channel_id}/errors/clear", dependencies=[Depends(permission_required("logs.clear"))])
def channel_errors_clear(channel_id: int, request: Request, db: Session = Depends(get_db)):
    admin = current_admin(request, db)
    if not admin:
        raise HTTPException(401)
    channel = db.scalar(
        select(Channel)
        .options(selectinload(Channel.nodes), selectinload(Channel.node))
        .where(Channel.id == channel_id)
    )
    if not channel:
        raise HTTPException(404, "Channel not found")
    assigned_nodes = node_controller.assigned_nodes(channel)
    remote_node_ids = {int(item.id) for item in assigned_nodes if item.node_type == "remote"}
    delete_query = delete(LogEntry).where(LogEntry.channel_id == channel_id)
    if remote_node_ids:
        delete_query = delete_query.where(
            or_(LogEntry.node_id.is_(None), ~LogEntry.node_id.in_(remote_node_ids))
        )
    result = db.execute(delete_query)
    local_cleared = int(result.rowcount or 0)
    channel.last_error = None
    db.commit()
    log_event(
        "Channel stream logs cleared",
        scope="system",
        actor=admin.username,
        details={
            "channel_id": channel_id,
            "channel": channel.name,
            "local_cleared": local_cleared,
            "remote_logs_preserved": True,
        },
    )
    return {"ok": True, "cleared": local_cleared, "warnings": []}


@app.get("/status.json", dependencies=[Depends(permission_required("channels.view"))])
def status_json(request: Request, channel_ids: str = "", db: Session = Depends(get_db)):
    if not current_admin(request, db):
        raise HTTPException(401)
    requested: list[int] = []
    for value in (channel_ids or "").split(","):
        try:
            channel_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if channel_id > 0 and channel_id not in requested:
            requested.append(channel_id)
    # STREAMFORGE_MAIN_STATUS_EAGER_RUNTIME_V34:
    # main_channel_runtime() needs channel.nodes. Eager-load them once so a
    # status poll is O(1) SQL statements instead of two lazy queries per row.
    statement = (
        select(Channel)
        .options(selectinload(Channel.nodes), selectinload(Channel.node))
        .order_by(Channel.sort_order, Channel.name, Channel.id)
    )
    if requested:
        statement = statement.where(Channel.id.in_(requested))
    channels = db.scalars(statement).all()
    channel_id_values = [int(channel.id) for channel in channels]
    # STREAMFORGE_MAIN_STATUS_LOCAL_ONLY_V33:
    # Main Panel status/uptime is the Main Server Local FFmpeg runtime only.
    # Do not poll or aggregate Remote Node process state into Main controls.
    runtimes = {int(channel.id): main_channel_runtime(channel) for channel in channels}
    viewer_stats = collect_viewer_counts_fast(db)
    by_channel = viewer_stats["by_channel"]
    # STREAMFORGE_MAIN_STATUS_NO_HISTORICAL_LOG_SCAN_V1114:
    # /status.json is polled every few seconds by Dashboard/Channels. Historical
    # seven-day LogEntry GROUP BY/LIKE work belongs to the on-demand error modal,
    # not this hot path. Runtime counters/current errors are sufficient here.
    error_counts: dict[int, int] = {}
    restart_counts: dict[int, int] = {}
    payload = []
    for channel in channels:
        runtime = runtimes.get(int(channel.id), {})
        effective_status = str(runtime.get("status") or channel.status or "unknown").strip().lower()
        runtime_error = str(runtime.get("last_error") if "last_error" in runtime else (channel.last_error or "")).strip()
        current_error = runtime_error if effective_status in {"error", "degraded", "starting", "restarting"} else ""
        historical_error_count = int(error_counts.get(int(channel.id), 0)) + int(runtime.get("error_count") or 0)
        error_count = max(historical_error_count, 1 if current_error else 0)
        restart_count = int(restart_counts.get(int(channel.id), 0)) + int(runtime.get("restart_count") or 0)
        runtime_bitrate = int(runtime.get("bitrate_kbps", channel.live_bitrate_kbps) or 0)
        runtime_uptime = int(runtime.get("uptime_seconds", 0) or 0)
        # STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055:
        # Process liveness, bitrate, or elapsed uptime cannot prove that the
        # HLS playlist and its latest segment are playable. Only the Node
        # runtime's explicit HLS-ready result may produce the public Up state.
        hls_ready = bool(runtime.get("hls_ready")) and channel.output_type == "hls"
        delivery_status = channel_delivery_status(channel, effective_status, hls_ready)
        payload.append(
            {
                "id": channel.id,
                "status": delivery_status,
                "runtime_status": effective_status,
                "desired_running": bool(channel.desired_running),
                "output_type": channel.output_type,
                "bitrate": runtime_bitrate,
                "resolution": (
                    f"{int(runtime.get('width'))} × {int(runtime.get('height'))}"
                    if runtime.get("width") and runtime.get("height")
                    else (f"{channel.width} × {channel.height}" if channel.width and channel.height else "Source")
                ),
                "speed": runtime.get("speed_x", 0.0),
                "fps": runtime.get("fps") or channel.fps or 0,
                "pid": runtime.get("pid") or channel.pid,
                "uptime_seconds": runtime_uptime,
                "last_error": current_error,
                "error_count": error_count,
                "restart_count": restart_count,
                "auto_restart": channel.auto_restart,
                "auto_restarted_recently": bool(runtime.get("auto_restarted_recently")),
                "auto_restart_age_seconds": int(runtime.get("auto_restart_age_seconds") or 0),
                "http_ready": hls_ready,
                # STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87
                "active_source": channel_active_source_endpoint(channel, runtime),
                "active_input_index": int(runtime.get("active_input_index", channel.active_input_index or 0) or 0),
                "online_users": int(by_channel.get(channel.id, 0)),
            }
        )
    return {"channels": payload, "online_users": int(viewer_stats["total_users"])}
