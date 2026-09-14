from __future__ import annotations

import base64
import hashlib
import io
import hmac
import json
import os
import re
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, select, text, update
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .db import SessionLocal
from .ffmpeg import stream_manager
from .models import AdminUser, AppSetting, Channel, ChannelCategory, Node, StreamUser
from .audit_log import log_event
from .system_metrics import system_metrics
from .permissions import role_permission_set
from .secrets_store import decrypt_secret
from .playlist_ordering import channel_category_key, category_position_map, ordered_channels as apply_playlist_order
from .sync_queue import (
    clear_node_channel_logo_pending,
    clear_node_channel_logo_pending_many,
    clear_node_channel_sync_pending,
    clear_node_channel_sync_pending_many,
    mark_node_channel_logo_pending,
    mark_node_channel_sync_pending,
    mark_node_sync_pending,
)


class NodeError(RuntimeError):
    pass


DEFAULT_NODE_CONTROL_PORT = 80
LEGACY_NODE_CONTROL_PORTS = (8810,)
DEFAULT_WEB_PORTS = {80, 443}


# STREAMFORGE_NODE_UPDATE_MAINTENANCE_ISOLATION_V57:
# Main-driven Node updates are control-plane maintenance. Playback on the Main
# Server must not wait on the Node that is intentionally restarting/upgrading.
# Keep a short in-memory maintenance lease so Web Player/load-balancer probes
# can fail fast and prefer another assigned Node (normally Local) while the
# update thread is active. Leases expire automatically if a worker crashes.
_NODE_MAINTENANCE_LOCK = threading.RLock()
_NODE_MAINTENANCE_UNTIL: dict[int, float] = {}


def set_node_maintenance(node_id: int, active: bool, *, ttl_seconds: float = 1800.0) -> None:
    node_key = max(0, int(node_id or 0))
    if not node_key:
        return
    with _NODE_MAINTENANCE_LOCK:
        if active:
            _NODE_MAINTENANCE_UNTIL[node_key] = time.monotonic() + max(60.0, float(ttl_seconds or 1800.0))
        else:
            _NODE_MAINTENANCE_UNTIL.pop(node_key, None)


def node_maintenance_active(node_id: int) -> bool:
    node_key = max(0, int(node_id or 0))
    if not node_key:
        return False
    now = time.monotonic()
    with _NODE_MAINTENANCE_LOCK:
        until = float(_NODE_MAINTENANCE_UNTIL.get(node_key) or 0.0)
        if until and until <= now:
            _NODE_MAINTENANCE_UNTIL.pop(node_key, None)
            return False
        return until > now


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _channel_category_names(channel: Channel) -> list[str]:
    names: list[str] = []
    primary = getattr(channel, "category", None)
    if primary is not None:
        names.append(primary.name)
    for item in list(getattr(channel, "categories", []) or []):
        if item.name not in names:
            names.append(item.name)
    return names


def _channel_public_catalogue_number(channel_id: int) -> int:
    """Return the Main catalogue's contiguous category-ordered 101+ number."""
    with SessionLocal() as db:
        channels = db.scalars(
            select(Channel).options(
                selectinload(Channel.category), selectinload(Channel.categories),
            )
        ).all()
    def order_key(channel: Channel) -> tuple[int, str, int, str, int]:
        linked = list(getattr(channel, "categories", []) or [])
        primary = min(
            linked or ([channel.category] if getattr(channel, "category", None) is not None else []),
            key=lambda item: (int(item.sort_order), item.name.lower(), int(item.id)),
            default=None,
        )
        return (
            int(primary.sort_order) if primary else 1_000_000_000,
            primary.name.lower() if primary else "\uffff",
            int(channel.sort_order), channel.name.lower(), int(channel.id),
        )
    channels.sort(key=order_key)
    return next((101 + index for index, item in enumerate(channels) if int(item.id) == int(channel_id)), int(channel_id))


def ensure_local_node(db: Session) -> Node:
    local = db.scalar(select(Node).where(Node.node_type == "local"))
    configured_base = str(settings.public_base_url or "").strip().rstrip("/")
    try:
        parsed_base = urllib.parse.urlsplit(configured_base)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
            configured_base = ""
    except ValueError:
        configured_base = ""
    if not configured_base:
        configured_base = "http://127.0.0.1"

    if not local:
        local = Node(
            name="Local Node",
            slug="local-node",
            node_type="local",
            api_url=configured_base,
            api_urls=configured_base,
            playlist_url=configured_base,
            playlist_urls=configured_base,
            dns_name=urllib.parse.urlsplit(configured_base).hostname,
            dns_scheme=urllib.parse.urlsplit(configured_base).scheme or "http",
            dns_only=True,
            playlist_dns_only=True,
            agent_port=int(urllib.parse.urlsplit(configured_base).port or (443 if configured_base.startswith("https://") else 80)),
            playlist_port=int(urllib.parse.urlsplit(configured_base).port or (443 if configured_base.startswith("https://") else 80)),
            enabled=True,
            status="online",
            last_seen_at=utcnow(),
        )
        db.add(local)
        db.flush()
    else:
        local.enabled = True
        local.status = "online"
        local.last_seen_at = utcnow()
        if not str(local.api_url or "").strip():
            local.api_url = configured_base
        if not str(local.api_urls or "").strip():
            local.api_urls = str(local.api_url or configured_base).strip()
        if not str(local.playlist_url or "").strip():
            local.playlist_url = configured_base
        if not str(local.playlist_urls or "").strip():
            local.playlist_urls = str(local.playlist_url or configured_base).strip()
        try:
            panel = urllib.parse.urlsplit(str(local.api_url or configured_base))
            local.agent_port = int(panel.port or (443 if panel.scheme == "https" else 80))
        except (TypeError, ValueError):
            local.agent_port = int(local.agent_port or 80)
        try:
            stream = urllib.parse.urlsplit(str(local.playlist_url or configured_base))
            local.playlist_port = int(stream.port or (443 if stream.scheme == "https" else 80))
        except (TypeError, ValueError):
            local.playlist_port = int(local.playlist_port or local.agent_port or 80)

    # Keep the legacy primary node populated and ensure every channel has at
    # least one row in the multi-node assignment table.
    db.execute(Channel.__table__.update().where(Channel.node_id.is_(None)).values(node_id=local.id))
    dialect = db.get_bind().dialect.name
    insert_prefix = "INSERT OR IGNORE" if dialect == "sqlite" else "INSERT"
    conflict_suffix = "" if dialect == "sqlite" else " ON CONFLICT(channel_id,node_id) DO NOTHING"
    db.execute(
        text(
            f"""
            {insert_prefix} INTO channel_nodes(channel_id,node_id,priority)
            SELECT c.id, COALESCE(c.node_id, :local_id), 100
            FROM channels c
            WHERE NOT EXISTS (
                SELECT 1 FROM channel_nodes cn WHERE cn.channel_id=c.id
            )
            {conflict_suffix}
            """
        ),
        {"local_id": local.id},
    )
    return local



def effective_node_url(node: Node) -> str:
    """Return the primary configured Node Panel/API base, including path slug."""
    values = [item.strip().rstrip("/") for item in str(getattr(node, "api_urls", None) or "").splitlines() if item.strip()]
    return values[0] if values else (node.api_url or "").strip().rstrip("/")


def _node_agent_version_at_least(node: Node, major: int, minor: int) -> bool:
    """Return True when the persisted Node Agent version is at least major.minor."""
    raw = str(getattr(node, "agent_version", "") or "").strip()
    if not raw:
        return False
    values: list[int] = []
    for part in raw.split(".")[:2]:
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            return False
        values.append(int(digits))
    while len(values) < 2:
        values.append(0)
    return tuple(values[:2]) >= (int(major), int(minor))


def native_node_control_ports(node: Node) -> list[int]:
    """Return safe externally reachable Node control ports in probe order.

    STREAMFORGE_NODE_NO_EXTERNAL_8810_V65R8: v6.5 unified ingress keeps the
    control backend on loopback 127.0.0.1:8810 behind Nginx.  Main must not
    synthesize remote :8810 probes for a Node already known to be v6.5+, while
    older/unknown agents retain the historical recovery candidate for upgrades.
    """
    ports: list[int] = []

    def add(value: object) -> None:
        try:
            port = max(0, min(65535, int(value or 0)))
        except (TypeError, ValueError):
            return
        if port and port not in ports:
            ports.append(port)

    modern_unified = _node_agent_version_at_least(node, 6, 5)
    saved = int(getattr(node, "agent_port", 0) or 0)
    if modern_unified and saved in LEGACY_NODE_CONTROL_PORTS:
        saved = DEFAULT_NODE_CONTROL_PORT
    add(saved or DEFAULT_NODE_CONTROL_PORT)
    if not modern_unified and (saved or DEFAULT_NODE_CONTROL_PORT) == DEFAULT_NODE_CONTROL_PORT:
        for legacy_port in LEGACY_NODE_CONTROL_PORTS:
            add(legacy_port)
    return ports


def node_url_candidates(node: Node) -> list[str]:
    """Return configured URLs plus safe native-listener recovery aliases."""
    configured = [
        item.strip().rstrip("/")
        for item in str(getattr(node, "api_urls", None) or node.api_url or "").splitlines()
        if item.strip()
    ]
    candidates: list[str] = []

    def add(value: str) -> None:
        cleaned = str(value or "").strip().rstrip("/")
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    for value in configured:
        add(value)

    parsed_values: list[urllib.parse.SplitResult] = []
    for value in configured:
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            continue
        if parsed.hostname:
            parsed_values.append(parsed)

    primary_path = parsed_values[0].path.rstrip("/") if parsed_values else ""
    extra_hosts = [
        str(getattr(node, "dns_name", None) or "").strip(),
        str(getattr(node, "ssh_host", None) or "").strip(),
    ]
    for host in extra_hosts:
        if host and all((item.hostname or "").lower() != host.lower() for item in parsed_values):
            parsed_values.append(urllib.parse.urlsplit(f"http://{host}{primary_path}"))

    for direct_port in native_node_control_ports(node):
        for parsed in parsed_values:
            host = parsed.hostname or ""
            if not host:
                continue
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            path = parsed.path.rstrip("/")
            # The native Node Agent listener is HTTP; HTTPS termination belongs
            # to an external reverse proxy.
            direct = f"http://{host}:{direct_port}"
            if path:
                add(direct + path)
            add(direct)
    return candidates


def normalize_node_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Node API URL must start with http:// or https://")
    return cleaned


def node_playlist_base(node: Node) -> str:
    """Build the primary public Xtream/playlist base, preserving its path slug."""
    values = [item.strip().rstrip("/") for item in str(getattr(node, "playlist_urls", None) or "").splitlines() if item.strip()]
    return values[0] if values else (getattr(node, "playlist_url", None) or effective_node_url(node) or "").strip().rstrip("/")



def channel_input_urls(channel: Channel) -> list[str]:
    values = [(channel.input_url or "").strip()]
    for line in (channel.backup_inputs or "").replace("\r", "").split("\n"):
        item = line.strip()
        if item and item not in values:
            values.append(item)
    return [item for item in values if item]


def channel_source_program_ids(channel: Channel) -> list[int | None]:
    """Return one optional MPTS program ID for every configured source."""
    urls = channel_input_urls(channel)
    values: list[int | None] = []
    try:
        raw = json.loads(channel.source_program_ids or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = []
    if not isinstance(raw, list):
        raw = []
    for item in raw[:len(urls)]:
        try:
            parsed = int(item) if item not in (None, "") else None
        except (TypeError, ValueError):
            parsed = None
        values.append(parsed if parsed and 1 <= parsed <= 65535 else None)
    while len(values) < len(urls):
        values.append(None)
    # Upgrade compatibility: the old channel-level program_id belonged to source #1.
    if values and values[0] is None and channel.program_id:
        values[0] = int(channel.program_id)
    return values


def channel_node_input_mode(channel_id: int, node_id: int, fallback: str = "source") -> str:
    with SessionLocal() as db:
        value = db.execute(
            text("SELECT input_mode FROM channel_nodes WHERE channel_id=:channel_id AND node_id=:node_id"),
            {"channel_id": channel_id, "node_id": node_id},
        ).scalar()
    mode = str(value or fallback or "source").strip().lower()
    return mode if mode in {"source", "local_relay"} else "source"


def channel_node_encoding_profile(channel_id: int, node_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT video_codec,video_bitrate,resolution,audio_codec,audio_bitrate,hls_segment_time "
                "FROM channel_nodes WHERE channel_id=:channel_id AND node_id=:node_id"
            ),
            {"channel_id": channel_id, "node_id": node_id},
        ).first()
    if not row:
        return {}
    return {
        "video_codec": row[0] or None,
        "video_bitrate": row[1] or None,
        "resolution": row[2] or None,
        "audio_codec": row[3] or None,
        "audio_bitrate": row[4] or None,
        "hls_segment_time": int(row[5]) if row[5] is not None else None,
    }


def _profile_resolution(value: str | None, default_width: int | None, default_height: int | None) -> tuple[int | None, int | None]:
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return default_width, default_height
    if cleaned == "source":
        return None, None
    try:
        width_text, height_text = cleaned.split("x", 1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError):
        return default_width, default_height
    if width < 2 or height < 2 or width % 2 or height % 2:
        return default_width, default_height
    return width, height


class NodeController:
    def __init__(self) -> None:
        self._cache: dict[tuple[int, int, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        # STREAMFORGE_MAIN_NODE_TRANSPORT_BACKOFF_V1064:
        # When every configured control URL for a Node times out, repeating the
        # same DNS/IP timeout on every panel poll and heartbeat reconcile can
        # tie up Main worker threads and flood SQLite activity logs.  Keep a
        # short per-Node circuit breaker for ordinary background/status calls.
        # Explicit health/Test refreshes bypass it so an operator can verify
        # recovery immediately.
        self._transport_backoff_until: dict[int, float] = {}
        self._transport_failure_count: dict[int, int] = {}
        self._transport_last_error: dict[int, str] = {}
        self._transport_last_log_at: dict[int, float] = {}
        # STREAMFORGE_MAIN_ASYNC_HLS_READY_V114: Channels/status polling must
        # never parse/stat live HLS files on the request worker. A small bounded
        # executor refreshes the same readiness cache in the background while
        # hot panel requests consume only cached booleans.
        self._hls_ready_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sf-hls-ready")
        self._hls_ready_refreshing: set[tuple[int, int, str]] = set()

    @staticmethod
    def _is_local(node: Node | None) -> bool:
        return node is None or node.node_type == "local"

    # STREAMFORGE_HEARTBEAT_AUTHORITATIVE_LIVENESS_V1132:
    # A reverse Main->Node control timeout is not the same thing as a dead Node.
    # Node heartbeat (or any successful authenticated reverse request) is the
    # authoritative liveness signal.  With the default ~60s heartbeat cadence,
    # a 90s grace avoids status flapping while still marking genuinely stale
    # Nodes offline promptly.  Control failures continue to use the independent
    # in-memory transport backoff and targeted pending queues.
    # STREAMFORGE_TARGETED_RETRY_BYPASS_TRANSPORT_BACKOFF_V1133:
    # Scheduled DB-backed pending retries use their own bounded scheduler backoff.
    # When that scheduler grants a retry slot, the call must reach the wire once
    # instead of being rejected locally by the ad-hoc transport backoff.
    @staticmethod
    def node_liveness_recent(node: Node | None, *, max_age_seconds: float = 90.0) -> bool:
        if node is None or node.node_type != "remote":
            return False
        last_seen = getattr(node, "last_seen_at", None)
        if last_seen is None:
            return False
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        try:
            age = (utcnow() - last_seen).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return False
        return age <= max(1.0, float(max_age_seconds or 90.0))

    @classmethod
    def is_effectively_offline(cls, node: Node | None) -> bool:
        # A stale persisted `offline` bit must not suppress work while a recent
        # heartbeat proves the Node is alive.
        return bool(
            node is not None
            and node.node_type == "remote"
            and str(node.status or "").strip().lower() == "offline"
            and not cls.node_liveness_recent(node)
        )

    @classmethod
    def _is_known_offline(cls, node: Node | None) -> bool:
        return cls.is_effectively_offline(node)

    def note_control_failure(
        self,
        node: Node | None,
        error: object,
        *,
        transport_hint: bool | None = None,
    ) -> bool:
        """Persist control-path diagnostics without clobbering fresh heartbeat liveness.

        Returns True when the failure is transport/backoff related.
        """
        if node is None or node.node_type != "remote":
            return False
        detail = str(error or "Node control request failed").strip()[-2000:]
        transport_failure = bool(transport_hint) if transport_hint is not None else False
        if transport_hint is None and isinstance(error, BaseException):
            transport_failure = self.is_transport_error(error)
        if "retry backoff active" in detail.lower():
            transport_failure = True
        node.last_error = detail or "Node control request failed"
        if transport_failure and not self.node_liveness_recent(node):
            node.status = "offline"
        return transport_failure

    def note_control_success(self, node: Node | None) -> None:
        if node is None or node.node_type != "remote":
            return
        self.clear_transport_backoff(int(node.id))
        node.status = "online"
        node.last_seen_at = utcnow()
        node.last_error = None

    @staticmethod
    def _is_transport_exception(exc: BaseException | None) -> bool:
        if exc is None or isinstance(exc, urllib.error.HTTPError):
            return False
        return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))

    @classmethod
    def is_transport_error(cls, exc: BaseException) -> bool:
        """Return True only for an error chain ending in network/timeout failure."""
        current: BaseException | None = exc
        for _ in range(6):
            cause = getattr(current, "__cause__", None) if current is not None else None
            if cause is None:
                return False
            if isinstance(cause, urllib.error.HTTPError):
                return False
            if cls._is_transport_exception(cause):
                return True
            current = cause
        return False

    def _transport_backoff_remaining(self, node_id: int) -> float:
        now = time.monotonic()
        with self._lock:
            until = float(self._transport_backoff_until.get(int(node_id), 0.0) or 0.0)
            if until <= now:
                self._transport_backoff_until.pop(int(node_id), None)
                return 0.0
            return max(0.0, until - now)

    def clear_transport_backoff(self, node_id: int) -> None:
        with self._lock:
            self._transport_backoff_until.pop(int(node_id), None)
            self._transport_failure_count.pop(int(node_id), None)
            self._transport_last_error.pop(int(node_id), None)

    def _record_transport_failure(self, node_id: int, detail: str) -> float:
        # 15, 30, 60, 120, then 180 seconds.  Long enough to keep broken
        # reverse connectivity from dominating Main, short enough to recover
        # automatically without an operator action.
        now = time.monotonic()
        with self._lock:
            count = min(5, int(self._transport_failure_count.get(int(node_id), 0)) + 1)
            delay = min(180.0, 15.0 * (2 ** (count - 1)))
            self._transport_failure_count[int(node_id)] = count
            self._transport_backoff_until[int(node_id)] = now + delay
            self._transport_last_error[int(node_id)] = str(detail or "Node control connection failed")[-2000:]
            return delay

    @staticmethod
    def _channel_key(channel: Channel) -> str:
        return f"sf-{channel.id}-{channel.slug}"[:96]

    @staticmethod
    def _relay_key(channel: Channel) -> str:
        message = f"streamforge-channel:{channel.id}:{channel.slug}".encode("utf-8")
        return hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]

    def relay_base_url(self, channel: Channel) -> str:
        local = next((item for item in self.assigned_nodes(channel) if item.node_type == "local"), None)
        if local and local.dns_name:
            scheme = local.dns_scheme if local.dns_scheme in {"http", "https"} else "http"
            return f"{scheme}://{local.dns_name.strip().rstrip('/')}"
        return settings.relay_base_url.strip().rstrip("/")

    def _ensure_local_relay_link(self, channel: Channel) -> None:
        # STREAMFORGE_NGINX_RELAY_FASTPATH_V60: the public Main Nginx serves
        # signed relay media directly from this symlink namespace. The key is
        # still the same HMAC used by the legacy FastAPI relay endpoint.
        slug = str(channel.slug or "").strip()
        if not slug or not all(ch.isalnum() or ch in "_-" for ch in slug):
            return
        hls_root = Path(settings.hls_root)
        relay_root = hls_root.parent / "relay"
        key_dir = relay_root / self._relay_key(channel)
        target = hls_root / slug
        link = key_dir / slug
        try:
            hls_root.mkdir(parents=True, exist_ok=True)
            relay_root.mkdir(parents=True, exist_ok=True)
            key_dir.mkdir(parents=True, exist_ok=True)
            for path, bits in ((hls_root.parent, 0o001), (hls_root, 0o005), (relay_root, 0o005), (key_dir, 0o005)):
                try:
                    current = stat.S_IMODE(path.stat().st_mode)
                    os.chmod(path, current | bits)
                except OSError:
                    pass
            if target.exists():
                try:
                    current = stat.S_IMODE(target.stat().st_mode)
                    os.chmod(target, current | 0o005)
                except OSError:
                    pass
            if link.is_symlink():
                try:
                    if link.resolve(strict=False) == target.resolve(strict=False):
                        return
                except OSError:
                    pass
                link.unlink(missing_ok=True)
            elif link.exists():
                return
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            # The legacy FastAPI /relay endpoint remains a safe fallback if a
            # custom filesystem blocks symlink creation.
            return

    def relay_input_url(self, channel: Channel) -> str:
        self._ensure_local_relay_link(channel)
        return f"{self.relay_base_url(channel)}/relay/{self._relay_key(channel)}/{channel.slug}/index.m3u8"

    def _payload(self, channel: Channel, node: Node) -> dict[str, Any]:
        input_mode = channel_node_input_mode(channel.id, node.id, channel.remote_input_mode)
        input_url = channel.input_url
        relay_mode = node.node_type == "remote" and input_mode == "local_relay"
        if relay_mode:
            input_url = self.relay_input_url(channel)
        source_programs = channel_source_program_ids(channel)
        payload_programs = [None] if relay_mode else source_programs
        profile = channel_node_encoding_profile(channel.id, node.id)
        width, height = _profile_resolution(profile.get("resolution"), channel.width, channel.height)
        return {
            "key": NodeController._channel_key(channel),
            "name": channel.name,
            "slug": channel.slug,
            "category": channel.category.name if channel.category else "Uncategorized",
            "categories": _channel_category_names(channel),
            "category_order": int(channel.category.sort_order) if channel.category else 100000,
            "channel_order": int(channel.sort_order or 100000),
            "display_id": _channel_public_catalogue_number(channel.id),
            "logo_url": channel.logo_url or "",
            "input_url": input_url,
            "input_urls": ([input_url] if node.node_type == "remote" and input_mode == "local_relay" else channel_input_urls(channel)),
            "input_mode": input_mode,
            "active_input_index": int(channel.active_input_index or 0),
            "failback_enabled": bool(channel.failback_enabled),
            "failback_interval": max(10, int(channel.failback_interval or 30)),
            "program_id": payload_programs[0] if payload_programs else None,
            "input_program_ids": payload_programs,
            "enabled": channel.enabled,
            "auto_restart": channel.auto_restart,
            "video_codec": profile.get("video_codec") or channel.video_codec,
            "video_bitrate": profile.get("video_bitrate") or channel.video_bitrate,
            "width": width,
            "height": height,
            "fps": channel.fps,
            "preset": channel.preset,
            "audio_codec": profile.get("audio_codec") or channel.audio_codec,
            "audio_bitrate": profile.get("audio_bitrate") or channel.audio_bitrate,
            "output_type": channel.output_type,
            "output_url": channel.output_url,
            "hls_segment_time": profile.get("hls_segment_time") or channel.hls_segment_time,
        }

    @staticmethod
    def assigned_nodes(channel: Channel) -> list[Node]:
        # Both Shared and Independent remote nodes may be explicitly assigned
        # a Main Panel channel.  On an Independent node that synchronized row
        # remains Main-owned/read-only in the Node UI, while Start/Stop/Restart
        # are still allowed locally.
        nodes = [node for node in channel.nodes if node.enabled]
        if not nodes and channel.node and channel.node.enabled:
            nodes = [channel.node]
        unique: dict[int, Node] = {node.id: node for node in nodes}
        return sorted(unique.values(), key=lambda item: (0 if item.node_type == "local" else 1, item.name.lower(), item.id))

    @staticmethod
    def configured_channels(node: Node) -> list[Channel]:
        """Return every Main Panel channel assigned to this node, including legacy primary assignments."""
        unique: dict[int, Channel] = {}
        for channel in list(node.channels) + list(node.primary_channels):
            if channel.id is not None:
                unique[int(channel.id)] = channel
        return sorted(unique.values(), key=lambda item: (item.name.lower(), item.id))

    def sync_node_mode(self, node: Node) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "local": True, "independent_mode": False}
        with SessionLocal() as db:
            current = db.get(Node, node.id)
            if not current:
                raise NodeError("Node not found")
            independent = bool(getattr(current, "sync_main_users", False))
            desired_keys = [self._channel_key(channel) for channel in self.configured_channels(current)]
            categories = db.scalars(select(ChannelCategory).order_by(ChannelCategory.sort_order, ChannelCategory.name)).all()
            payload = {
                "independent_mode": independent,
                "desired_channel_keys": desired_keys,
                "local_channel_limit": max(0, int(getattr(current, "local_channel_limit", 0) or 0)),
                "total_max_connections": max(0, int(getattr(current, "total_max_connections", 0) or 0)),
                "categories": [
                    {"category_id": item.id, "name": item.name, "slug": item.slug, "sort_order": int(item.sort_order or 100000), "owner": "main"}
                    for item in categories
                ],
            }
        return self._request(node, "POST", "/api/v1/mode/sync", payload, timeout=20.0)

    @staticmethod
    def _context(node: Node, base_url: str = "") -> ssl.SSLContext | None:
        target = (base_url or effective_node_url(node) or node.api_url or "").strip()
        if not target.lower().startswith("https://"):
            return None
        if node.verify_tls:
            return ssl.create_default_context()
        return ssl._create_unverified_context()  # noqa: SLF001

    def _request(
        self,
        node: Node,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 8.0,
        expect_json: bool = True,
        base_url: str = "",
        include_connected_base: bool = False,
        bypass_transport_backoff: bool = False,
    ) -> Any:
        if node.node_type == "local":
            raise NodeError("Local node does not use the remote API")
        if not node.enabled:
            raise NodeError(f"Node is disabled: {node.name}")
        request_bases = [str(base_url).strip().rstrip("/")] if str(base_url or "").strip() else node_url_candidates(node)
        request_bases = [item for item in request_bases if item]
        if not request_bases:
            raise NodeError(f"Node API URL is missing: {node.name}")
        if not node.api_token:
            raise NodeError(f"Node API token is missing: {node.name}")
        if not bypass_transport_backoff:
            remaining = self._transport_backoff_remaining(int(node.id))
            if remaining > 0:
                with self._lock:
                    last_error = str(self._transport_last_error.get(int(node.id), "") or "").strip()
                detail = f"Node control retry backoff active for {max(1, int(remaining + 0.999))}s"
                if last_error:
                    detail += f": {last_error}"
                raise NodeError(detail)
        body = None
        headers = {
            "X-Node-Token": node.api_token,
            "Accept": "application/json" if expect_json else "*/*",
            "User-Agent": f"StreamForge-Control/{settings.http_user_agent}",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        failures: list[str] = []
        last_exc: Exception | None = None
        for request_base in request_bases:
            url = f"{request_base}/{path.lstrip('/')}"
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=self._context(node, request_base)) as response:
                    data = response.read()
                    if expect_json:
                        decoded = json.loads(data.decode("utf-8") or "{}")
                        if include_connected_base and isinstance(decoded, dict):
                            decoded = dict(decoded)
                            decoded["_connected_base_url"] = request_base
                        self.note_control_success(node)
                        return decoded
                    self.note_control_success(node)
                    return data, response.headers.get("Content-Type", "application/octet-stream")
            except urllib.error.HTTPError as exc:
                last_exc = exc
                detail = exc.read().decode("utf-8", errors="replace")[-700:]
                failures.append(f"{request_base}: HTTP {exc.code} {detail or exc.reason}")
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exc = exc
                failures.append(f"{request_base}: {exc}")
        detail = " | ".join(failures)[-3000:]
        transport_failure = self._is_transport_exception(last_exc)
        backoff_seconds = self._record_transport_failure(int(node.id), detail) if transport_failure else 0.0
        should_log = True
        if transport_failure:
            now = time.monotonic()
            with self._lock:
                last_log = float(self._transport_last_log_at.get(int(node.id), 0.0) or 0.0)
                if now - last_log < 30.0:
                    should_log = False
                else:
                    self._transport_last_log_at[int(node.id)] = now
        if should_log:
            details = {"path": path, "error": detail}
            if backoff_seconds:
                details["retry_backoff_seconds"] = int(backoff_seconds)
                details["repeat_transport_errors_suppressed_seconds"] = 30
            log_event("Node connection failed through every URL", scope="node", level="error", node_id=node.id, details=details)
        raise NodeError(f"Node connection failed through every URL: {detail}") from last_exc

    def _request_raw(
        self,
        node: Node,
        method: str,
        path: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if node.node_type == "local":
            raise NodeError("Local node does not use the remote API")
        if not node.enabled or not effective_node_url(node) or not node.api_token:
            raise NodeError(f"Node connection is incomplete: {node.name}")
        headers = {
            "X-Node-Token": node.api_token,
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": f"StreamForge-Control/{settings.http_user_agent}",
        }
        if extra_headers:
            headers.update(extra_headers)
        failures: list[str] = []
        last_exc: Exception | None = None
        for request_base in node_url_candidates(node):
            request = urllib.request.Request(
                f"{request_base.rstrip('/')}/{path.lstrip('/')}",
                data=body,
                headers=headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=self._context(node, request_base)) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as exc:
                last_exc = exc
                detail = exc.read().decode("utf-8", errors="replace")[-700:]
                failures.append(f"{request_base}: HTTP {exc.code} {detail or exc.reason}")
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exc = exc
                failures.append(f"{request_base}: {exc}")
        raise NodeError("Node update failed through every URL: " + " | ".join(failures)[-3000:]) from last_exc

    @staticmethod
    def _agent_update_archive(app_root: Path) -> bytes:
        agent_root = app_root / "node_agent"
        required = [agent_root / "app.py", agent_root / "redis_state.py", agent_root / "requirements.txt"]
        for item in required:
            if not item.is_file():
                raise NodeError(f"Node Agent update file is missing: {item.name}")
        buffer = io.BytesIO()
        # STREAMFORGE_NODE_UPDATE_LOW_CPU_PACKAGE_V57: Node package creation
        # happens inside the Main process. Fast compression avoids starving the
        # single ASGI worker/Web Player while an update is being prepared.
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for item in required:
                archive.write(item, arcname=item.name)
            version_file = app_root / "VERSION"
            if version_file.is_file():
                archive.write(version_file, arcname="VERSION")
            cache_clear = app_root / "scripts" / "cache_clear.sh"
            if cache_clear.is_file():
                archive.write(cache_clear, arcname="cache_clear.sh")
            # STREAMFORGE_NODE_HLSJS_UPDATE_PAYLOAD_V1163:
            hls_fetch = app_root / "scripts" / "fetch_hlsjs.sh"
            if hls_fetch.is_file():
                archive.write(hls_fetch, arcname="fetch_hlsjs.sh")
        return buffer.getvalue()

    def update_agent(self, node: Node, app_root: Path, version: str) -> dict[str, Any]:
        payload = self._agent_update_archive(app_root)
        return self._request_raw(
            node,
            "POST",
            "/api/v1/agent/update",
            payload,
            content_type="application/zip",
            timeout=90.0,
            extra_headers={"X-StreamForge-Version": version},
        )

    def webplayer_users_on_node(self, node: Node) -> list[dict[str, Any]]:
        if node.node_type != "remote":
            return []
        result = self._request(node, "GET", "/api/v1/webplayer/users", timeout=8.0)
        users = result.get("users") if isinstance(result, dict) else []
        return [item for item in users if isinstance(item, dict)]

    # STREAMFORGE_WEBPLAYER_BRAND_ASSET_TRANSPORT_V1158:
    def _webplayer_brand_transport_profiles(self, webplayer_settings: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in list(webplayer_settings.get("brands") or [])[:16]:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            brand_id = re.sub(r"[^a-z0-9_-]+", "", str(item.get("id") or "").strip().lower())[:40]
            if not brand_id:
                continue
            item["id"] = brand_id
            # STREAMFORGE_WEBPLAYER_BRAND_TRANSPORT_DERIVED_FIELDS_V1159:
            # RGBA strings are UI-derived from color+alpha and are recomputed on
            # the Node. Do not include them in the strict sync echo payload.
            for derived in ("page_rgba", "panel_rgba", "accent_rgba", "text_rgba"):
                item.pop(derived, None)
            for key, kind in (("logo_url", "logo"), ("favicon_url", "favicon")):
                value = str(item.get(key) or "").strip()
                asset_key = f"{kind}_asset"
                item[asset_key] = ""
                if value.startswith("/branding-assets/"):
                    filename = Path(value[len("/branding-assets/"):]).name
                    suffix = Path(filename).suffix.lower()
                    if filename and suffix in {".ico", ".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                        item[key] = ""
                        item[asset_key] = f"{brand_id}-{kind}{suffix}"
            stored = Path(str(item.get("download_stored_name") or "")).name
            display = Path(str(item.get("download_name") or "")).name
            item["download_asset"] = f"{brand_id}-download.bin" if stored and display else ""
            item["download_name"] = display
            item.pop("download_stored_name", None)
            result.append(item)
        return result

    def sync_access_settings(
        self,
        node: Node,
        panel_url: str = "",
        connect_urls: list[str] | tuple[str, ...] | None = None,
        webplayer_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply access settings through an already-authorized Node URL.

        When an operator changes the Panel/API host, port, or path, the Node still
        accepts the previous URL until this request succeeds. Try the previous
        aliases first, then the newly configured aliases. This prevents a
        successful Main Panel save from stranding the Node on its old path.
        """
        if node.node_type == "local":
            return {"ok": True, "local": True, "dns_only": False}
        payload = {
            # STREAMFORGE_MAIN_NODE_NAME_SYNC_V304: access sync is the primary
            # branding path; panel-user sync remains a compatible fallback.
            "node_name": str(node.name or "").strip()[:120],
            "dns_only": True,
            "allowed_host": (node.dns_name or "").strip(),
            "panel_dns_only": True,
            "panel_host": (node.dns_name or "").strip(),
            "panel_urls": [item.strip() for item in str(getattr(node, "api_urls", None) or node.api_url or "").splitlines() if item.strip()],
            "stream_dns_only": True,
            "stream_host": (urllib.parse.urlsplit((getattr(node, "playlist_url", None) or "").strip()).hostname or ""),
            "stream_urls": [item.strip() for item in str(getattr(node, "playlist_urls", None) or getattr(node, "playlist_url", None) or "").splitlines() if item.strip()],
            "access_slug": str(getattr(node, "access_slug", None) or "").strip(),
            "stream_slug": str(getattr(node, "playlist_access_slug", None) or "").strip(),
            "main_panel_url": (panel_url or settings.public_base_url).strip().rstrip("/"),
            "stream_port": int(urllib.parse.urlsplit((getattr(node, "playlist_url", None) or "").strip()).port or getattr(node, "playlist_port", 0) or 80),
            "total_max_connections": int(getattr(node, "total_max_connections", 0) or 0),
            "panel_ip_whitelist": (getattr(node, "panel_ip_whitelist", None) or "").strip(),
            "panel_ip_blacklist": (getattr(node, "panel_ip_blacklist", None) or "").strip(),
            "panel_asn_whitelist": (getattr(node, "panel_asn_whitelist", None) or "").strip(),
            "panel_asn_blacklist": (getattr(node, "panel_asn_blacklist", None) or "").strip(),
            "ip_whitelist": (node.ip_whitelist or "").strip(),
            "ip_blacklist": (node.ip_blacklist or "").strip(),
            "asn_whitelist": (node.asn_whitelist or "").strip(),
            "asn_blacklist": (node.asn_blacklist or "").strip(),
        }
        if webplayer_settings is not None:
            # STREAMFORGE_WEBPLAYER_QUICK_MODE_SYNC_V3041:
            requested_login_mode = str(webplayer_settings.get("login_mode") or "manual").strip().lower()
            transport_brands = self._webplayer_brand_transport_profiles(webplayer_settings)
            normalized_login_mode = requested_login_mode if requested_login_mode in {"manual", "auto", "quick"} else "manual"
            payload.update({
                "webplayer_show_user_info": bool(webplayer_settings.get("show_user_info", True)),
                "webplayer_show_connection_info": bool(webplayer_settings.get("show_connection_info", True)),
                "webplayer_login_mode": normalized_login_mode,
                "webplayer_auto_user_id": int(webplayer_settings.get("auto_user_id") or 0),
                "webplayer_auto_user_token": str(webplayer_settings.get("auto_user_token") or ""),
                "webplayer_page_color": str(webplayer_settings.get("page_color") or "#04080d"),
                "webplayer_page_alpha": int(webplayer_settings.get("page_alpha", 100)),
                "webplayer_panel_color": str(webplayer_settings.get("panel_color") or "#071019"),
                "webplayer_panel_alpha": int(webplayer_settings.get("panel_alpha", 100)),
                "webplayer_accent_color": str(webplayer_settings.get("accent_color") or "#ff2020"),
                "webplayer_accent_alpha": int(webplayer_settings.get("accent_alpha", 100)),
                "webplayer_text_color": str(webplayer_settings.get("text_color") or "#f6fbff"),
                "webplayer_text_alpha": int(webplayer_settings.get("text_alpha", 100)),
                "viewer_ttl_seconds": max(5, min(3600, int(webplayer_settings.get("viewer_ttl_seconds", 5)))),
                "client_session_reset_offline_minutes": max(1, min(10080, int(webplayer_settings.get("client_session_reset_offline_minutes", 60)))),
                "hide_panel_hover_urls": bool(webplayer_settings.get("hide_hover_urls", True)),
                "android_version_name": str(webplayer_settings.get("android_version_name") or "")[:64],
                "android_description": str(webplayer_settings.get("android_description") or "")[:2000],
                "webplayer_brands": transport_brands,
            })
        candidates: list[str] = []
        for value in list(connect_urls or []) + node_url_candidates(node) + payload["panel_urls"]:
            cleaned = str(value or "").strip().rstrip("/")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
        if not candidates:
            raise NodeError(f"Node API URL is missing: {node.name}")
        failures: list[str] = []
        last_exc: NodeError | None = None
        for candidate in candidates:
            try:
                result = self._request(
                    node,
                    "POST",
                    "/api/v1/settings/sync",
                    payload,
                    timeout=12.0,
                    base_url=candidate,
                    # This function is itself the controlled alias failover loop.
                    # Let it try the next configured URL even if the previous
                    # exact alias opened the ordinary background circuit breaker.
                    bypass_transport_backoff=True,
                )
                # STREAMFORGE_WEBPLAYER_THEME_SYNC_VERIFY_V2229:
                # When Main sends Web Player settings, require the Node to echo
                # the applied values. This prevents a "saved" success message
                # when an older/partial Node silently ignored the theme.
                if webplayer_settings is not None:
                    applied = result.get("webplayer") if isinstance(result, dict) else None
                    if not isinstance(applied, dict):
                        raise NodeError("Node did not confirm Web Player theme application")
                    expected = {
                        "show_user_info": bool(webplayer_settings.get("show_user_info", True)),
                        "show_connection_info": bool(webplayer_settings.get("show_connection_info", True)),
                        "login_mode": normalized_login_mode,
                        "auto_user_id": int(webplayer_settings.get("auto_user_id") or 0),
                        "auto_user_token": str(webplayer_settings.get("auto_user_token") or ""),
                        "page_color": str(webplayer_settings.get("page_color") or "#04080d").lower(),
                        "page_alpha": int(webplayer_settings.get("page_alpha", 100)),
                        "panel_color": str(webplayer_settings.get("panel_color") or "#071019").lower(),
                        "panel_alpha": int(webplayer_settings.get("panel_alpha", 100)),
                        "accent_color": str(webplayer_settings.get("accent_color") or "#ff2020").lower(),
                        "accent_alpha": int(webplayer_settings.get("accent_alpha", 100)),
                        "text_color": str(webplayer_settings.get("text_color") or "#f6fbff").lower(),
                        "text_alpha": int(webplayer_settings.get("text_alpha", 100)),
                        "viewer_ttl_seconds": max(5, min(3600, int(webplayer_settings.get("viewer_ttl_seconds", 5)))),
                        "client_session_reset_offline_minutes": max(1, min(10080, int(webplayer_settings.get("client_session_reset_offline_minutes", 60)))),
                        "hide_hover_urls": bool(webplayer_settings.get("hide_hover_urls", True)),
                        "android_version_name": str(webplayer_settings.get("android_version_name") or "")[:64],
                        "android_description": str(webplayer_settings.get("android_description") or "")[:2000],
                        "brands": transport_brands,
                    }
                    for key, value in expected.items():
                        if applied.get(key) != value:
                            raise NodeError(
                                f"Node Web Player theme mismatch for {key}: "
                                f"expected {value!r}, got {applied.get(key)!r}"
                            )
                return result
            except NodeError as exc:
                last_exc = exc
                failures.append(f"{candidate}: {exc}")
        raise NodeError("Access settings sync failed through every configured URL: " + " | ".join(failures)) from last_exc

    def remove_webplayer_brand_assets(self, node: Node, brand_id: str) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "local": True}
        safe_id = re.sub(r"[^a-z0-9_-]+", "", str(brand_id or "").strip().lower())[:40]
        if not safe_id:
            return {"ok": True, "removed": False}
        for kind in ("logo", "favicon", "download"):
            self._request(node, "DELETE", f"/api/v1/webplayer-brand/{safe_id}/{kind}/sync", timeout=15.0)
        return {"ok": True, "removed": True}

    def asn_status(self, node: Node) -> dict[str, Any]:
        if node.node_type == "local":
            from .access_control import asn_database_status
            return asn_database_status()
        return self._request(node, "GET", "/api/v1/asn/status", None, timeout=10.0)

    # STREAMFORGE_WEBPLAYER_DOWNLOAD_NODE_SYNC_V3045:
    def sync_webplayer_download(self, node: Node, webplayer_settings: dict[str, Any]) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "local": True}
        stored_name = Path(str(webplayer_settings.get("download_stored_name") or "")).name
        display_name = Path(str(webplayer_settings.get("download_name") or "")).name
        source = settings.webplayer_download_root / stored_name if stored_name else None
        if not stored_name or not display_name or source is None or not source.is_file():
            return self._request(node, "DELETE", "/api/v1/webplayer-download/sync", timeout=15.0)
        data = source.read_bytes()
        if not data or len(data) > 100 * 1024 * 1024:
            raise NodeError("Managed Web Player download must be between 1 byte and 100 MB")
        return self._request_raw(
            node,
            "POST",
            "/api/v1/webplayer-download/sync",
            data,
            content_type="application/octet-stream",
            timeout=120.0,
            extra_headers={"X-StreamForge-Filename": urllib.parse.quote(display_name, safe="")},
        )

    def sync_webplayer_brand_assets(self, node: Node, webplayer_settings: dict[str, Any]) -> dict[str, Any]:
        """Push uploaded per-brand logo/favicon/download files to a Remote Node."""
        if node.node_type == "local":
            return {"ok": True, "local": True, "assets": 0}
        pushed = 0
        for raw in list(webplayer_settings.get("brands") or [])[:16]:
            if not isinstance(raw, dict):
                continue
            brand_id = re.sub(r"[^a-z0-9_-]+", "", str(raw.get("id") or "").strip().lower())[:40]
            if not brand_id:
                continue
            for key, kind in (("logo_url", "logo"), ("favicon_url", "favicon")):
                value = str(raw.get(key) or "").strip()
                if value.startswith("/branding-assets/"):
                    filename = Path(value[len("/branding-assets/"):]).name
                    source = settings.logo_root / "branding" / filename
                    if source.is_file():
                        data = source.read_bytes()
                        self._request_raw(
                            node, "POST", f"/api/v1/webplayer-brand/{brand_id}/{kind}/sync", data,
                            content_type="application/octet-stream", timeout=30.0,
                            extra_headers={"X-StreamForge-Filename": urllib.parse.quote(filename, safe="")},
                        )
                        pushed += 1
                else:
                    self._request(node, "DELETE", f"/api/v1/webplayer-brand/{brand_id}/{kind}/sync", timeout=15.0)
            stored = Path(str(raw.get("download_stored_name") or "")).name
            display = Path(str(raw.get("download_name") or "")).name
            source = settings.webplayer_download_root / stored if stored else None
            if source is not None and display and source.is_file():
                data = source.read_bytes()
                if not data or len(data) > 100 * 1024 * 1024:
                    raise NodeError("Brand Player download must be between 1 byte and 100 MB")
                self._request_raw(
                    node, "POST", f"/api/v1/webplayer-brand/{brand_id}/download/sync", data,
                    content_type="application/octet-stream", timeout=120.0,
                    extra_headers={"X-StreamForge-Filename": urllib.parse.quote(display, safe="")},
                )
                pushed += 1
            else:
                self._request(node, "DELETE", f"/api/v1/webplayer-brand/{brand_id}/download/sync", timeout=15.0)
        return {"ok": True, "assets": pushed}

    def asn_lookup(self, node: Node, ip_text: str) -> dict[str, Any]:
        if node.node_type == "local":
            from .access_control import asn_database_status, geo_details
            import ipaddress
            normalized = str(ipaddress.ip_address(ip_text.strip()))
            status = asn_database_status()
            result = geo_details(normalized)
            result.update({"ok": bool(status.get("loaded") or status.get("ipinfo_configured")), "status": status})
            return result
        quoted = urllib.parse.quote(ip_text.strip(), safe="")
        return self._request(node, "GET", f"/api/v1/asn/lookup?ip={quoted}", None, timeout=10.0)

    def geo_settings_status(self, node: Node) -> dict[str, Any]:
        if node.node_type == "local":
            from .geoip_config import load_geoip_settings
            return load_geoip_settings()
        return self._request(node, "GET", "/api/v1/geo/settings", None, timeout=10.0)

    def save_geo_settings(self, node: Node, payload: dict[str, Any]) -> dict[str, Any]:
        if node.node_type == "local":
            from .geoip_config import save_geoip_settings
            return save_geoip_settings(**payload)
        return self._request(node, "POST", "/api/v1/geo/settings", payload, timeout=12.0)

    def run_geo_update(self, node: Node) -> dict[str, Any]:
        if node.node_type == "local":
            from .geoip_config import run_maxmind_update
            ok, output = run_maxmind_update()
            if not ok:
                raise NodeError(output)
            return {"ok": True, "output": output}
        return self._request(node, "POST", "/api/v1/geo/update", {}, timeout=180.0)

    def sync_channel_config(
        self, channel: Channel, node: Node, *, restart_running: bool = False,
        bypass_transport_backoff: bool = False,
    ) -> dict[str, Any]:
        # STREAMFORGE_BULK_PROFILE_RUNNING_RESTART_V1025:
        # Ordinary Main->Node configuration synchronization remains config-only.
        # Bulk encoding-profile updates explicitly opt in to restart_running so
        # an already-running Local/Remote FFmpeg process immediately consumes
        # the saved profile while stopped channels remain stopped.
        if node.node_type == "local":
            snapshot = stream_manager.runtime_snapshot(channel.id)
            running = bool(snapshot.get("alive"))
            if restart_running and running:
                stream_manager.restart(channel.id)
                return {"ok": True, "restarted": True, "local": True, "config_synced": True}
            return {
                "ok": True, "restarted": False, "local": True,
                "config_synced": True, "restart_required": bool(running),
            }
        suffix = f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}/sync"
        if restart_running:
            suffix += "?restart_running=1"
        return self._request(
            node,
            "POST",
            suffix,
            self._payload(channel, node),
            timeout=20.0 if restart_running else 15.0,
            bypass_transport_backoff=bool(bypass_transport_backoff),
        )

    def sync_channel_logo(
        self, channel: Channel, node: Node, *, bypass_transport_backoff: bool = False
    ) -> dict[str, Any]:
        """Copy a Main-uploaded channel logo into one Remote Node's local logo path."""
        if node.node_type == "local" or not (channel.logo_url or "").startswith("/channel-logos/"):
            return {"ok": True, "skipped": True}
        filename = Path(str(channel.logo_url)[len("/channel-logos/"):]).name
        if not filename or filename != str(channel.logo_url)[len("/channel-logos/"):]:
            raise NodeError("Invalid local channel logo path")
        source = settings.logo_root / filename
        if not source.is_file():
            raise NodeError(f"Main channel logo file is missing: {filename}")
        data = source.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            raise NodeError(f"Main channel logo is larger than 2 MB: {filename}")
        return self._request(
            node,
            "POST",
            f"/api/v1/channel-logos/{urllib.parse.quote(filename, safe='')}/sync",
            {"content_base64": base64.b64encode(data).decode("ascii")},
            timeout=15.0,
            bypass_transport_backoff=bool(bypass_transport_backoff),
        )

    def sync_channel_to_nodes(
        self, channel_id: int, *, restart_running: bool = False, node_ids: set[int] | None = None,
        sync_logo: bool = True,
    ) -> list[str]:
        # STREAMFORGE_OFFLINE_CHANNEL_SETTINGS_QUEUE_V3045:
        # STREAMFORGE_NODE_TARGETED_CHANNEL_PENDING_V1127:
        # Main is the source of truth. A failed/offline per-channel config sync
        # is queued only for that (Node, channel). Heartbeat retries that exact
        # channel later; it must never promote one timeout into a full catalogue
        # reconcile.
        # STREAMFORGE_TARGETED_CHANNEL_SYNC_V1027:
        # node_ids=None preserves the historical all-assigned-nodes behavior.
        # A concrete set scopes config sync/restart to only those assignments;
        # this is required for per-node bulk encoding changes so Main is not
        # restarted when only a Remote Node was selected.
        errors: list[str] = []
        restart_local_after_commit = False
        channel_name = f"Channel {channel_id}"
        target_ids = None if node_ids is None else {int(item) for item in node_ids}
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return ["Channel not found"]
            channel_name = str(channel.name)
            nodes = self.assigned_nodes(channel)
            if target_ids is not None:
                nodes = [node for node in nodes if int(node.id) in target_ids]
            else:
                # STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118: normal channel
                # edits can skip the expensive all-Node logo copy when the
                # logo did not change. Other callers keep the historical
                # default so explicit/full reconciles still include assets.
                if sync_logo:
                    errors.extend(self._sync_channel_logo_to_all_nodes(channel, db))
            for node in nodes:
                if node.node_type == "local":
                    # Do not restart the Local FFmpeg while this SQLAlchemy
                    # session still owns a SQLite transaction. stream_manager
                    # uses its own DB session during stop/start, so defer it
                    # until after this config-sync transaction commits.
                    restart_local_after_commit = bool(restart_running)
                    continue
                if self._is_known_offline(node):
                    mark_node_channel_sync_pending(db, node, int(channel.id), f"Channel settings queued: {channel.name}")
                    continue
                try:
                    self.sync_channel_config(channel, node, restart_running=restart_running)
                    clear_node_channel_sync_pending(db, int(node.id), int(channel.id))
                except NodeError as exc:
                    self.note_control_failure(node, exc)
                    mark_node_channel_sync_pending(db, node, int(channel.id), f"Channel settings queued after connection failure: {channel.name}")
                    errors.append(f"{channel.name} / {node.name}: {exc}")
            db.commit()
        if restart_local_after_commit:
            try:
                snapshot = stream_manager.runtime_snapshot(channel_id)
                if bool(snapshot.get("alive")):
                    stream_manager.restart(channel_id)
            except (RuntimeError, NodeError) as exc:
                errors.append(f"{channel_name} / Main Server: {exc}")
        return errors

    # STREAMFORGE_NODE_TRUE_BATCH_CONFIG_SYNC_V1127:
    # One Main->Node HTTP request carries up to 500 channel configs. Newer Nodes
    # apply the batch inside the dedicated supervisor and persist state once.
    # Older Nodes transparently fall back to the historical per-channel API.
    def sync_channel_batch_to_node(
        self, channel_ids: set[int] | list[int], node_id: int, *,
        bypass_transport_backoff: bool = False,
    ) -> list[str]:
        requested = sorted({int(item) for item in channel_ids if int(item) > 0})
        if not requested:
            return []
        errors: list[str] = []
        with SessionLocal() as db:
            node = db.get(Node, int(node_id))
            if not node:
                return [f"Node {node_id} not found"]
            if node.node_type == "local":
                return []
            configured = {int(channel.id): channel for channel in self.configured_channels(node)}
            channels = [configured[channel_id] for channel_id in requested if channel_id in configured]
            if not channels:
                clear_node_channel_sync_pending_many(db, int(node.id), requested)
                db.commit()
                return []
            if self._is_known_offline(node):
                for channel in channels:
                    mark_node_channel_sync_pending(
                        db, node, int(channel.id), f"Channel settings queued: {channel.name}"
                    )
                db.commit()
                return []
            payload = {"channels": [self._payload(channel, node) for channel in channels]}
            try:
                self._request(
                    node,
                    "POST",
                    "/api/v1/channels/bulk-sync",
                    payload,
                    timeout=max(20.0, min(60.0, 12.0 + (len(channels) * 0.08))),
                    bypass_transport_backoff=bool(bypass_transport_backoff),
                )
                clear_node_channel_sync_pending_many(
                    db, int(node.id), [int(channel.id) for channel in channels]
                )
                db.commit()
                return []
            except NodeError as exc:
                detail = str(exc)
                # Nodes older than v11.27 do not expose bulk-sync yet. Keep the
                # update rolling by falling back to config-only individual calls.
                if "404" not in detail and "not found" not in detail.lower():
                    self.note_control_failure(node, exc)
                    for channel in channels:
                        mark_node_channel_sync_pending(
                            db, node, int(channel.id),
                            f"Channel batch settings queued after connection failure: {channel.name}",
                        )
                    db.commit()
                    return [f"{node.name}: {detail}"]

            # Compatibility fallback for a Node that has not been upgraded yet.
            for index, channel in enumerate(channels):
                try:
                    self.sync_channel_config(
                        channel, node, restart_running=False,
                        bypass_transport_backoff=bool(bypass_transport_backoff),
                    )
                    clear_node_channel_sync_pending(db, int(node.id), int(channel.id))
                except NodeError as exc:
                    self.note_control_failure(node, exc)
                    mark_node_channel_sync_pending(
                        db, node, int(channel.id),
                        f"Channel settings queued after connection failure: {channel.name}",
                    )
                    errors.append(f"{channel.name} / {node.name}: {exc}")
                    if self.is_transport_error(exc) or "retry backoff active" in str(exc).lower():
                        # STREAMFORGE_NODE_BATCH_FALLBACK_REMAINDER_QUEUE_V1128:
                        # The control path is unavailable, so channels after the
                        # first failed fallback call were never attempted. Queue
                        # those exact configs instead of losing them or asking a
                        # later heartbeat to replay the whole Node catalogue.
                        for remaining in channels[index + 1:]:
                            mark_node_channel_sync_pending(
                                db, node, int(remaining.id),
                                f"Channel settings deferred after connection failure: {remaining.name}",
                            )
                        break
            db.commit()
        return errors

    def _sync_channel_logo_to_all_nodes(self, channel: Channel, db: Session) -> list[str]:
        # STREAMFORGE_NODE_LOGO_TARGETED_PENDING_V1129:
        # Logo failures are scoped to (Node, channel), just like config failures.
        # They must never create the legacy generic full-reconcile key.
        errors: list[str] = []
        if not (channel.logo_url or "").startswith("/channel-logos/"):
            return errors
        all_remote_nodes = db.scalars(
            select(Node).where(Node.enabled.is_(True), Node.node_type == "remote").order_by(Node.name)
        ).all()
        for node in all_remote_nodes:
            if self._is_known_offline(node):
                mark_node_channel_logo_pending(
                    db, node, int(channel.id), f"Channel logo queued: {channel.name}"
                )
                continue
            try:
                self.sync_channel_logo(channel, node)
                clear_node_channel_logo_pending(db, int(node.id), int(channel.id))
            except NodeError as exc:
                self.note_control_failure(node, exc)
                mark_node_channel_logo_pending(
                    db, node, int(channel.id),
                    f"Channel logo queued after connection failure: {channel.name}",
                )
                errors.append(f"{channel.name} logo / {node.name}: {exc}")
        return errors

    def sync_channel_logo_batch_to_node(
        self, channel_ids: set[int] | list[int], node_id: int, *,
        bypass_transport_backoff: bool = False,
    ) -> list[str]:
        # STREAMFORGE_NODE_LOGO_TARGETED_RETRY_V1129:
        # Retry only queued logos for one Node. Transport failure queues the
        # unattempted remainder and stops immediately instead of causing a full
        # catalogue sweep.
        requested = sorted({int(item) for item in channel_ids if int(item) > 0})
        if not requested:
            return []
        errors: list[str] = []
        with SessionLocal() as db:
            node = db.get(Node, int(node_id))
            if not node or node.node_type == "local":
                return []
            configured = {int(channel.id): channel for channel in self.configured_channels(node)}
            channels = [configured[channel_id] for channel_id in requested if channel_id in configured]
            missing = [channel_id for channel_id in requested if channel_id not in configured]
            if missing:
                clear_node_channel_logo_pending_many(db, int(node.id), missing)
            for index, channel in enumerate(channels):
                if not (channel.logo_url or "").startswith("/channel-logos/"):
                    clear_node_channel_logo_pending(db, int(node.id), int(channel.id))
                    continue
                if self._is_known_offline(node):
                    for remaining in channels[index:]:
                        mark_node_channel_logo_pending(
                            db, node, int(remaining.id), f"Channel logo queued: {remaining.name}"
                        )
                    break
                try:
                    self.sync_channel_logo(
                        channel, node,
                        bypass_transport_backoff=bool(bypass_transport_backoff),
                    )
                    clear_node_channel_logo_pending(db, int(node.id), int(channel.id))
                except NodeError as exc:
                    detail = str(exc)
                    self.note_control_failure(node, exc)
                    mark_node_channel_logo_pending(
                        db, node, int(channel.id),
                        f"Channel logo queued after connection failure: {channel.name}",
                    )
                    errors.append(f"{channel.name} logo / {node.name}: {detail}")
                    if self.is_transport_error(exc) or "retry backoff active" in detail.lower():
                        for remaining in channels[index + 1:]:
                            mark_node_channel_logo_pending(
                                db, node, int(remaining.id),
                                f"Channel logo deferred after connection failure: {remaining.name}",
                            )
                        break
            db.commit()
        return errors

    def sync_channel_logo_to_all_nodes(self, channel_id: int) -> list[str]:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return ["Channel not found"]
            errors = self._sync_channel_logo_to_all_nodes(channel, db)
            db.commit()
            return errors

    def sync_node_channels(self, node: Node, *, sync_logos: bool = True) -> list[str]:
        # STREAMFORGE_NODE_CATALOGUE_TARGETED_PENDING_V1128:
        # Historical guarantee retained: catalogue failures use exact per-channel
        # pending keys and never the generic node_<id>_sync_pending flag.
        # STREAMFORGE_NODE_RECONCILE_TRANSPORT_FAILFAST_V1064:
        # Historical fail-fast guarantee retained at batch granularity: after a
        # failed batch, remaining chunks are queued without further wire waits.
        # STREAMFORGE_NODE_UPDATE_SKIP_REDUNDANT_LOGO_SYNC_V57:
        # Post-update callers may pass sync_logos=False; preserve that low-I/O
        # behavior while catalogue config now uses true batch delivery.
        # STREAMFORGE_MAIN_NODE_RECONCILE_CONFIG_ONLY_V33:
        # Full catalogue reconciliation remains config-only; it never copies
        # Main desired_running or sends start/stop/restart commands to Remote Nodes.
        # STREAMFORGE_NODE_CATALOGUE_TRUE_BATCH_RECONCILE_V1135:
        # Full/manual/update catalogue reconciliation must use the same bounded
        # true-batch config API as targeted retries.  The historical per-channel
        # loop turned one transport timeout into hundreds of "catalogue deferred"
        # rows and could spend one network timeout per channel on older paths.
        # Config is now delivered in <=500-channel batches, logos use their own
        # targeted queue, and process desired/running state remains Node-local.
        errors: list[str] = []
        with SessionLocal() as db:
            current = db.get(Node, int(node.id))
            if not current:
                return ["Node not found"]
            channels = self.configured_channels(current)
            channel_ids = [int(channel.id) for channel in channels if channel.id is not None]
            if self._is_known_offline(current):
                for channel in channels:
                    mark_node_channel_sync_pending(
                        db, current, int(channel.id),
                        f"Channel catalogue config queued: {channel.name}",
                    )
                    if sync_logos and str(channel.logo_url or "").startswith("/channel-logos/"):
                        mark_node_channel_logo_pending(
                            db, current, int(channel.id),
                            f"Channel catalogue logo queued: {channel.name}",
                        )
                db.commit()
                return []
            current_id = int(current.id)

        # Config first: one HTTP request for the normal catalogue size, with a
        # hard 500-item chunk limit matching the Node API contract.  A failed
        # chunk already owns exact pending rows through sync_channel_batch_to_node().
        config_failed = False
        for offset in range(0, len(channel_ids), 500):
            chunk = channel_ids[offset:offset + 500]
            chunk_errors = self.sync_channel_batch_to_node(
                chunk,
                current_id,
                bypass_transport_backoff=True,
            )
            if chunk_errors:
                config_failed = True
                errors.extend(f"catalogue config batch: {item}" for item in chunk_errors)
                # Remaining chunks were not attempted. Queue their exact configs
                # once without replaying per-channel control requests.
                remaining_ids = channel_ids[offset + len(chunk):]
                if remaining_ids:
                    with SessionLocal() as pending_db:
                        pending_node = pending_db.get(Node, current_id)
                        if pending_node:
                            configured = {
                                int(channel.id): channel
                                for channel in self.configured_channels(pending_node)
                                if channel.id is not None
                            }
                            for channel_id in remaining_ids:
                                remaining = configured.get(int(channel_id))
                                if remaining is not None:
                                    mark_node_channel_sync_pending(
                                        pending_db,
                                        pending_node,
                                        int(channel_id),
                                        f"Channel catalogue batch deferred: {remaining.name}",
                                    )
                            pending_db.commit()
                break

        # Logo delivery is independent from config delivery.  A logo transport
        # failure queues only logo work; it can never convert untouched config
        # rows into catalogue-deferred config work.
        if sync_logos and channel_ids:
            logo_errors = self.sync_channel_logo_batch_to_node(
                channel_ids,
                current_id,
                bypass_transport_backoff=True,
            )
            errors.extend(f"catalogue logo: {item}" for item in logo_errors)

        # Mode is a small control request. Skip it only when config transport is
        # already known failed; targeted config retry will recover independently.
        if not config_failed:
            with SessionLocal() as mode_db:
                current = mode_db.get(Node, current_id)
                if current:
                    try:
                        self.sync_node_mode(current)
                    except NodeError as exc:
                        errors.append(f"mode reconcile: {exc}")
                        self.note_control_failure(current, exc)
                        mode_db.commit()
        return errors

    def managed_tls_status(self, node: Node) -> dict[str, Any]:
        """Fetch managed TLS state through any working Node control alias."""
        if self._is_local(node):
            return {}
        result = self._request(node, "GET", "/api/v1/status/quick", timeout=4.0)
        tls = result.get("managed_tls") if isinstance(result, dict) else {}
        return tls if isinstance(tls, dict) else {}

    # STREAMFORGE_MAIN_NODE_DNS01_REMOTE_TEST_V82: execute the DNS test on the
    # Remote Node itself so Main and the Node Settings page use the same resolver,
    # registration state and hostname authority. This avoids Main-side stale TLS
    # status turning an otherwise valid Test CNAME action into a hidden 404/444.
    def dns01_cname_test(self, node: Node, host: str) -> dict[str, Any]:
        if self._is_local(node):
            return {"ok": False, "state": "local", "message": "Local node uses the Main DNS tester"}
        result = self._request(
            node,
            "POST",
            "/api/v1/tls/dns01/test",
            {"host": str(host or "").strip().lower().rstrip(".")},
            timeout=7.0,
        )
        return result if isinstance(result, dict) else {"ok": False, "state": "invalid_response", "message": "Node DNS test returned an invalid response"}

    # STREAMFORGE_NODE_TLS_RECONCILE_CLIENT_V42: ask the unprivileged Agent to
    # touch its root-owned systemd path trigger; the HTTP control channel stays
    # responsive while Certbot/Nginx reconciliation runs asynchronously.
    # STREAMFORGE_MAIN_NODE_DNS01_RETRY_HOST_V82: send the selected hostname to
    # new Nodes for authoritative validation; older Nodes safely ignore the body.
    def request_tls_reconcile(self, node: Node, host: str = "") -> dict[str, Any]:
        if self._is_local(node):
            return {"ok": True, "queued": False}
        payload = {"host": str(host or "").strip().lower().rstrip(".")} if str(host or "").strip() else {}
        result = self._request(node, "POST", "/api/v1/tls/reconcile", payload, timeout=5.0)
        return result if isinstance(result, dict) else {"ok": True, "queued": True}

    def quick_status(self, node: Node, *, refresh: bool = False) -> dict[str, Any]:
        """Fetch the lightweight Node card payload from one exact public URL.

        The full health endpoint inspects every channel, encoder capability,
        gateway and viewer session. It is appropriate for Test/Update, but it
        can take long enough to leave the Nodes page on ``checking...``. The
        quick endpoint has no callback to Main and is bounded to one URL.
        """
        if self._is_local(node):
            return {
                "ok": True,
                "status": "online",
                "version": "local",
                "metrics": system_metrics.snapshot(),
            }
        cache_key = (0, node.id, "quick-status")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and not refresh and now - cached[0] < 3.0:
                return dict(cached[1])
        primary = effective_node_url(node)
        if not primary:
            raise NodeError(f"Node API URL is missing: {node.name}")
        result = self._request(
            node,
            "GET",
            "/api/v1/status/quick",
            timeout=2.5,
            base_url=primary,
            include_connected_base=True,
            bypass_transport_backoff=bool(refresh),
        )
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), dict(result))
        return result

    def health(self, node: Node, *, refresh: bool = False) -> dict[str, Any]:
        if self._is_local(node):
            metrics = system_metrics.snapshot()
            capabilities: dict[str, object] = {}
            for family in ("h264", "h265"):
                try:
                    capabilities[family] = stream_manager.auto_video_encoder_status(family)
                except RuntimeError as exc:
                    capabilities[family] = {"selected": None, "hardware": False, "error": str(exc)}
            return {
                "ok": True,
                "status": "online",
                "version": "local",
                "hostname": "local",
                "metrics": metrics,
                "capabilities": capabilities,
            }
        cache_key = (0, node.id, "health")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and not refresh and now - cached[0] < 3.0:
                return cached[1]
        result = self._request(
            node, "GET", "/api/v1/health", timeout=6.0, include_connected_base=True,
            bypass_transport_backoff=bool(refresh),
        )
        with self._lock:
            self._cache[cache_key] = (now, result)
        return result

    # STREAMFORGE_CHANNELS_CACHED_NODE_HEALTH_V3044:
    def cached_health(self, node: Node, *, max_age: float = 30.0) -> dict[str, Any] | None:
        """Return recent Node health without performing any network request."""
        if self._is_local(node):
            return self.health(node)
        cache_key = (0, int(node.id), "health")
        with self._lock:
            cached = self._cache.get(cache_key)
            if not cached or time.monotonic() - cached[0] > max(0.0, float(max_age)):
                return None
            return dict(cached[1])

    def test(self, node: Node) -> dict[str, Any]:
        result = self.health(node, refresh=True)
        with SessionLocal() as db:
            current = db.get(Node, node.id)
            if current:
                current.status = "online" if result.get("ok") else "error"
                current.last_seen_at = utcnow() if result.get("ok") else current.last_seen_at
                current.last_error = None if result.get("ok") else str(result.get("error") or "Node test failed")
                db.commit()
        return result

    def health_at(self, node: Node, base_url: str) -> dict[str, Any]:
        """Verify one exact configured URL without direct-listener fallback."""
        return self._request(
            node, "GET", "/api/v1/health", timeout=6.0, base_url=base_url,
            bypass_transport_backoff=True,
        )

    def _relay_local_node(self, channel: Channel, nodes: list[Node] | None = None) -> Node | None:
        selected = nodes if nodes is not None else self.assigned_nodes(channel)
        return next((item for item in selected if item.node_type == "local" and item.enabled), None)

    def _validate_relay_mode(self, channel: Channel, nodes: list[Node]) -> Node | None:
        relay_nodes = [item for item in nodes if item.node_type == "remote" and channel_node_input_mode(channel.id, item.id, channel.remote_input_mode) == "local_relay"]
        if not relay_nodes:
            return None
        if channel.output_type != "hls":
            raise NodeError("Main-node relay requires HTTP HLS output")
        local = self._relay_local_node(channel, nodes)
        if local is None:
            raise NodeError("Main-node relay requires Local Node to be selected for this channel")
        base = self.relay_base_url(channel)
        if not base.startswith(("http://", "https://")):
            raise NodeError("Main-node relay base URL must start with http:// or https://")
        if base.startswith(("http://127.0.0.1", "https://127.0.0.1", "http://localhost", "https://localhost")):
            raise NodeError("Set Local Node DNS or STREAMFORGE_RELAY_BASE_URL to an address reachable by remote nodes")
        return local

    def _wait_for_local_relay(self, channel: Channel, local: Node) -> None:
        deadline = time.monotonic() + settings.relay_start_wait_seconds
        while time.monotonic() < deadline:
            if self.hls_ready(channel, local):
                return
            time.sleep(0.5)
        raise NodeError(
            f"Local relay did not become playback-ready within {settings.relay_start_wait_seconds} seconds"
        )

    def _start_on_node(self, channel: Channel, node: Node, *, recovery: bool = False) -> dict[str, Any]:
        if self._is_local(node):
            stream_manager.start(channel.id, recovery=recovery)
            return stream_manager.runtime_snapshot(channel.id)
        return self._request(
            node,
            "POST",
            f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}/start",
            self._payload(channel, node),
            timeout=12.0,
        )

    def _stop_on_node(self, channel: Channel, node: Node) -> None:
        if self._is_local(node):
            stream_manager.stop(channel.id)
            return
        self._request(
            node,
            "POST",
            f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}/stop",
            timeout=10.0,
        )

    def _restart_on_node(self, channel: Channel, node: Node) -> dict[str, Any]:
        if self._is_local(node):
            stream_manager.restart(channel.id)
            return stream_manager.runtime_snapshot(channel.id)
        return self._request(
            node,
            "POST",
            f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}/restart",
            self._payload(channel, node),
            timeout=15.0,
        )

    def start_on_node(self, channel_id: int, node_id: int, *, recovery: bool = False) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            node = db.get(Node, node_id)
            if not channel or not node:
                raise NodeError("Channel or node not found")
            # STREAMFORGE_OFFLINE_CHANNEL_CONTROL_FAST_QUEUE_V3050:
            # Known-offline Nodes never receive per-channel network calls.
            # The first newly observed connection failure also marks the Node
            # offline so every remaining item in the same bulk action is fast.
            if self._is_known_offline(node):
                mark_node_sync_pending(db, node, f"Channel start queued: {channel.name}")
                db.commit()
                self._invalidate(channel_id, node_id)
                return
            nodes = self.assigned_nodes(channel)
            local = self._validate_relay_mode(channel, nodes)
            if node.node_type == "remote" and local is not None and channel_node_input_mode(channel.id, node.id, channel.remote_input_mode) == "local_relay":
                if not self.hls_ready(channel, local):
                    self._start_on_node(channel, local, recovery=recovery)
                    self._wait_for_local_relay(channel, local)
            try:
                self._start_on_node(channel, node, recovery=recovery)
            except NodeError as exc:
                if node.node_type != "remote":
                    raise
                self.note_control_failure(node, exc)
                mark_node_sync_pending(db, node, f"Channel start queued after connection failure: {channel.name}")
                db.commit()
            self._invalidate(channel_id, node_id)

    def stop_on_node(self, channel_id: int, node_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            node = db.get(Node, node_id)
            if not channel or not node:
                return
            if self._is_known_offline(node):
                mark_node_sync_pending(db, node, f"Channel stop queued: {channel.name}")
                db.commit()
                self._invalidate(channel_id, node_id)
                return
            try:
                self._stop_on_node(channel, node)
            except NodeError as exc:
                if node.node_type == "remote":
                    self.note_control_failure(node, exc)
                    mark_node_sync_pending(db, node, f"Channel stop queued after connection failure: {channel.name}")
                    db.commit()
            self._invalidate(channel_id, node_id)

    def restart_on_node(self, channel_id: int, node_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            node = db.get(Node, node_id)
            if not channel or not node:
                raise NodeError("Channel or node not found")
            if self._is_known_offline(node):
                mark_node_sync_pending(db, node, f"Channel restart queued: {channel.name}")
                db.commit()
                self._invalidate(channel_id, node_id)
                return
            nodes = self.assigned_nodes(channel)
            local = self._validate_relay_mode(channel, nodes)
            if node.node_type == "remote" and local is not None and channel_node_input_mode(channel.id, node.id, channel.remote_input_mode) == "local_relay":
                if not self.hls_ready(channel, local):
                    self._restart_on_node(channel, local)
                    self._wait_for_local_relay(channel, local)
            try:
                self._restart_on_node(channel, node)
            except NodeError as exc:
                if node.node_type != "remote":
                    raise
                self.note_control_failure(node, exc)
                mark_node_sync_pending(db, node, f"Channel restart queued after connection failure: {channel.name}")
                db.commit()
            self._invalidate(channel_id, node_id)

    def start(self, channel_id: int, *, recovery: bool = False) -> None:
        # STREAMFORGE_MAIN_NODE_LOCAL_CONTROL_SCOPE_V33:
        # Main Start/Stop/Restart controls only the Main Server (Local Node).
        # Remote replicas keep their own Node-local desired state.
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                raise NodeError("Channel not found")
            if not channel.enabled:
                raise NodeError("Channel is disabled")
            local = next((node for node in self.assigned_nodes(channel) if self._is_local(node)), None)
            if local is None:
                raise NodeError("Main Server (Local Node) is not assigned to this channel")
            local_id = int(local.id)
        stream_manager.start(channel_id, recovery=recovery)
        self._invalidate(channel_id, local_id)
        log_event(
            "Main Server channel start completed", scope="channel", channel_id=channel_id,
            details={"control_scope": "main_local_only"},
        )

    def stop(self, channel_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return
            local = next((node for node in self.assigned_nodes(channel) if self._is_local(node)), None)
            if local is None:
                # A remote-only shared channel has no Main process to stop. Do
                # not mutate its Remote Node desired state from the Main Panel.
                return
            local_id = int(local.id)
        stream_manager.stop(channel_id)
        self._invalidate(channel_id, local_id)
        log_event(
            "Main Server channel stopped", scope="channel", channel_id=channel_id,
            details={"control_scope": "main_local_only"},
        )

    def restart(self, channel_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                raise NodeError("Channel not found")
            if not channel.enabled:
                raise NodeError("Channel is disabled")
            local = next((node for node in self.assigned_nodes(channel) if self._is_local(node)), None)
            if local is None:
                raise NodeError("Main Server (Local Node) is not assigned to this channel")
            local_id = int(local.id)
        stream_manager.restart(channel_id)
        self._invalidate(channel_id, local_id)
        log_event(
            "Main Server channel restarted", scope="channel", channel_id=channel_id,
            details={"control_scope": "main_local_only"},
        )

    def forget_on_node(self, channel_id: int, node_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            node = db.get(Node, node_id)
            if not channel or not node:
                return
            if node.node_type == "remote":
                # STREAMFORGE_OFFLINE_BULK_ACTION_FAST_QUEUE_V3050:
                # Bulk removal/delete must not spend one remote timeout per
                # channel. Mode reconciliation removes stale Main-owned rows
                # as soon as the Node's heartbeat returns.
                if self._is_known_offline(node):
                    mark_node_sync_pending(db, node, f"Channel removal queued: {channel.name}")
                    db.commit()
                    self._invalidate(channel_id, node_id)
                    return
                try:
                    self._request(
                        node,
                        "DELETE",
                        f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}",
                        timeout=8.0,
                    )
                except NodeError as exc:
                    self.note_control_failure(node, exc)
                    mark_node_sync_pending(db, node, f"Channel removal queued after connection failure: {channel.name}")
                    db.commit()
            else:
                stream_manager.forget(channel_id)
            self._invalidate(channel_id, node_id)

    def forget(self, channel_id: int) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return
            node_ids = [node.id for node in self.assigned_nodes(channel)]
        for node_id in node_ids:
            self.forget_on_node(channel_id, node_id)
        self._invalidate(channel_id)

    def runtime_snapshot_on_node(self, channel_id: int, node_id: int, *, refresh: bool = False) -> dict[str, Any]:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            node = db.get(Node, node_id)
            if not channel or not node:
                return {"managed": False, "alive": False, "bitrate_kbps": 0, "speed_x": 0.0}
            if self._is_local(node):
                result = stream_manager.runtime_snapshot(channel_id)
                result["node_id"] = node.id
                result["node_name"] = node.name
                result["node_online"] = True
                # STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7:
                # Stream Info replica cards are on-demand only. Include the
                # Local/Main host uptime beside this channel's process uptime.
                result["node_uptime_seconds"] = int(system_metrics.snapshot().get("uptime_seconds") or 0)
                return result
            cache_key = (channel_id, node_id, "status")
            now = time.monotonic()
            # STREAMFORGE_OFFLINE_RUNTIME_SNAPSHOT_SHORT_CIRCUIT_V3050:
            # A known-offline Node has no live runtime to probe. Explicit
            # refresh/Test calls may still bypass this cached-only fast path.
            if self._is_known_offline(node) and not refresh:
                result = {
                    "managed": True,
                    "alive": False,
                    "status": "error",
                    "pid": None,
                    "bitrate_kbps": 0,
                    "speed_x": 0.0,
                    "uptime_seconds": 0,
                    "last_error": str(node.last_error or "Node is offline"),
                    "metrics_fresh": False,
                    "offline_backoff": True,
                    "node_id": node.id,
                    "node_name": node.name,
                    "node_online": False,
                }
                with self._lock:
                    self._cache[cache_key] = (now, result)
                return result
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and not refresh and now - cached[0] < 2.0:
                    return cached[1]
            # STREAMFORGE_RUNTIME_STATUS_RELEASE_DB_V32:
            # The remote status call can block for its timeout. End the local
            # read transaction first; loaded channel/node values remain usable.
            db.commit()
            try:
                result = self._request(
                    node,
                    "GET",
                    f"/api/v1/channels/{urllib.parse.quote(self._channel_key(channel))}/status",
                    timeout=4.0,
                )
                node.status = "online"
                node.last_seen_at = utcnow()
                node.last_error = None
                result["node_online"] = True
            except NodeError as exc:
                result = {
                    "managed": True,
                    "alive": False,
                    "status": "error",
                    "pid": None,
                    "bitrate_kbps": 0,
                    "speed_x": 0.0,
                    "uptime_seconds": 0,
                    "last_error": str(exc),
                    "metrics_fresh": False,
                    "node_online": False,
                }
                self.note_control_failure(node, exc)
            result["node_id"] = node.id
            result["node_name"] = node.name
            db.commit()
            with self._lock:
                self._cache[cache_key] = (now, result)
            return result

    def runtime_snapshot(self, channel_id: int, *, refresh: bool = False) -> dict[str, Any]:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return {"managed": False, "alive": False, "bitrate_kbps": 0, "speed_x": 0.0, "nodes": []}
            nodes = self.assigned_nodes(channel)
        snapshots = [self.runtime_snapshot_on_node(channel_id, node.id, refresh=refresh) for node in nodes]
        alive = [item for item in snapshots if item.get("alive")]
        errors = [str(item.get("last_error")) for item in snapshots if item.get("last_error")]
        aggregate = {
            "managed": bool(snapshots),
            "alive": bool(alive),
            "status": "running" if len(alive) == len(snapshots) and alive else "degraded" if alive else "error",
            "pid": alive[0].get("pid") if alive else None,
            "bitrate_kbps": sum(int(item.get("bitrate_kbps") or 0) for item in alive),
            "width": next((int(item.get("width")) for item in alive if item.get("width")), None),
            "height": next((int(item.get("height")) for item in alive if item.get("height")), None),
            "fps": next((float(item.get("fps")) for item in alive if item.get("fps")), 0.0),
            "speed_x": min((float(item.get("speed_x") or 0.0) for item in alive), default=0.0),
            "uptime_seconds": min((int(item.get("uptime_seconds") or 0) for item in alive), default=0),
            "last_error": "; ".join(errors)[-4000:] if errors else None,
            "nodes": snapshots,
            "ready_nodes": len(alive),
            "total_nodes": len(snapshots),
        }
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if channel:
                if channel.status != "stopped" or aggregate["alive"]:
                    channel.status = aggregate["status"]
                channel.pid = aggregate["pid"]
                channel.live_bitrate_kbps = aggregate["bitrate_kbps"]
                channel.last_error = aggregate["last_error"] or None
                db.commit()
        return aggregate

    def runtime_snapshots(
        self, channel_ids: list[int] | None = None, *, refresh: bool = False,
        persist: bool = True, allow_remote_fetch: bool = True,
    ) -> dict[int, dict[str, Any]]:
        """Read many channel runtimes with one request per Remote Node.

        Older releases performed one HTTP request and one SQLite commit for every
        channel on every panel refresh.  Large catalogues therefore generated a
        request storm.  This method batches Remote Node state and commits all
        aggregate status updates once.
        """
        requested = sorted({int(item) for item in (channel_ids or []) if int(item) > 0})
        with SessionLocal() as db:
            statement = select(Channel).options(selectinload(Channel.nodes), selectinload(Channel.node))
            if requested:
                statement = statement.where(Channel.id.in_(requested))
            channels = db.scalars(statement.order_by(Channel.id)).unique().all()

            remote_nodes: dict[int, Node] = {}
            channel_nodes_map: dict[int, list[Node]] = {}
            for channel in channels:
                assigned = self.assigned_nodes(channel)
                channel_nodes_map[int(channel.id)] = assigned
                for node in assigned:
                    if not self._is_local(node):
                        remote_nodes[int(node.id)] = node

            remote_statuses: dict[int, dict[str, dict[str, Any]]] = {}
            now = time.monotonic()
            pending_nodes: dict[int, Node] = {}
            cached_payloads: dict[int, dict[str, Any]] = {}
            for node_id, node in remote_nodes.items():
                cache_key = (0, node_id, "bulk-status")
                # STREAMFORGE_NODE_UPDATE_STATUS_NO_REMOTE_WAIT_V57: an actively
                # updating Node is expected to disappear briefly. Do not spend
                # the 5-second bulk-status timeout on it and do not persist it
                # as offline; Local/other assigned Nodes remain usable.
                if node_maintenance_active(node_id):
                    cached_payloads[node_id] = {"channels": {}, "maintenance": True}
                    continue
                cached_payload: dict[str, Any] | None = None
                cached_entry: tuple[float, Any] | None = None
                with self._lock:
                    cached = self._cache.get(cache_key)
                    cached_entry = cached
                    cache_ttl = 60.0 if cached and isinstance(cached[1], dict) and cached[1].get("error") else 15.0
                    if cached and not refresh and now - cached[0] < cache_ttl:
                        cached_payload = cached[1]
                if cached_payload is None and allow_remote_fetch:
                    # STREAMFORGE_OFFLINE_NODE_STATUS_BACKOFF_V3044:
                    # A Node already known as offline must not delay the first
                    # Channels page refresh after a Main restart. Seed one
                    # cached offline result, then retry at most once per minute.
                    if (
                        not refresh
                        and cached_entry is None
                        and self._is_known_offline(node)
                    ):
                        cached_payload = {
                            "channels": {},
                            "error": str(node.last_error or "Node is offline"),
                            "offline_backoff": True,
                        }
                        with self._lock:
                            self._cache[cache_key] = (now, cached_payload)
                        cached_payloads[node_id] = cached_payload
                    else:
                        pending_nodes[node_id] = node
                else:
                    cached_payloads[node_id] = cached_payload or {"channels": {}, "cache_miss": True}

            def fetch_bulk_status(node: Node) -> dict[str, Any]:
                try:
                    raw = self._request(node, "GET", "/api/v1/channel-statuses", timeout=5.0)
                    rows = raw.get("channels", {}) if isinstance(raw, dict) else {}
                    return {"channels": rows if isinstance(rows, dict) else {}}
                except NodeError as exc:
                    return {"channels": {}, "error": str(exc)}

            fetched_payloads: dict[int, dict[str, Any]] = {}
            if pending_nodes:
                # STREAMFORGE_BULK_STATUS_RELEASE_DB_V32:
                # Everything needed for the remote requests is eagerly loaded.
                # Release this Session's transaction before the concurrent Node
                # network waits, then reacquire only for the final status write.
                db.commit()
                # A dead Node may consume the full request timeout. Fetch every
                # Node concurrently so catalogue latency is bounded by one
                # timeout instead of timeout multiplied by the number of Nodes.
                workers = min(16, len(pending_nodes))
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sf-node-status") as executor:
                    futures = {executor.submit(fetch_bulk_status, node): node_id for node_id, node in pending_nodes.items()}
                    for future in as_completed(futures):
                        node_id = futures[future]
                        try:
                            fetched_payloads[node_id] = future.result()
                        except Exception as exc:
                            # One failed worker must not turn a catalogue
                            # request into an HTTP 500 response.
                            fetched_payloads[node_id] = {"channels": {}, "error": str(exc)}

            for node_id, node in remote_nodes.items():
                cache_key = (0, node_id, "bulk-status")
                cached_payload = cached_payloads.get(node_id) or fetched_payloads.get(node_id) or {"channels": {}}
                if node_id in fetched_payloads:
                    if persist and not cached_payload.get("error"):
                        node.status = "online"
                        node.last_seen_at = utcnow()
                        node.last_error = None
                    elif persist:
                        self.note_control_failure(
                            node,
                            str(cached_payload.get("error") or "Node status unavailable"),
                            transport_hint=True,
                        )
                    with self._lock:
                        self._cache[cache_key] = (time.monotonic(), cached_payload)
                rows = cached_payload.get("channels", {}) if isinstance(cached_payload, dict) else {}
                remote_statuses[node_id] = rows if isinstance(rows, dict) else {}

            results: dict[int, dict[str, Any]] = {}
            for channel in channels:
                snapshots: list[dict[str, Any]] = []
                for node in channel_nodes_map.get(int(channel.id), []):
                    if self._is_local(node):
                        # Local FFmpeg snapshots historically omitted hls_ready,
                        # while Remote Node /api/v1/channel-statuses included it.
                        # The online-only Main playlist catalogue requires both
                        # alive and hls_ready, so a Local-only playlist appeared
                        # empty even when its local HLS output was healthy.
                        item = dict(stream_manager.runtime_snapshot(channel.id))
                        local_alive = bool(item.get("alive"))
                        try:
                            local_hls_ready = bool(self.hls_ready(channel, node))
                        except (OSError, RuntimeError):
                            local_hls_ready = False
                        item["hls_ready"] = bool(local_alive and local_hls_ready)
                        item.setdefault(
                            "status",
                            "running" if local_alive else ("starting" if channel.desired_running else "stopped"),
                        )
                        item.setdefault("last_error", channel.last_error or None)
                    else:
                        key = self._channel_key(channel)
                        item = dict(remote_statuses.get(int(node.id), {}).get(key) or {
                            "managed": True,
                            "alive": False,
                            "status": "error" if node.status == "offline" else "unknown",
                            "pid": None,
                            "bitrate_kbps": 0,
                            "speed_x": 0.0,
                            "uptime_seconds": 0,
                            "last_error": node.last_error if node.status == "offline" else None,
                            "hls_ready": False,
                            "metrics_fresh": False,
                        })
                    item["node_id"] = node.id
                    item["node_name"] = node.name
                    snapshots.append(item)

                alive = [item for item in snapshots if item.get("alive")]
                errors = [str(item.get("last_error")) for item in snapshots if item.get("last_error")]
                if alive:
                    status = "running" if len(alive) == len(snapshots) else "degraded"
                elif channel.desired_running:
                    transient = next((str(item.get("status")) for item in snapshots if item.get("status") in {"starting", "restarting"}), None)
                    status = transient or "error"
                else:
                    status = "stopped"
                aggregate = {
                    "managed": bool(snapshots),
                    "alive": bool(alive),
                    "status": status,
                    "pid": alive[0].get("pid") if alive else None,
                    "bitrate_kbps": sum(int(item.get("bitrate_kbps") or 0) for item in alive),
                    "width": next((int(item.get("width")) for item in alive if item.get("width")), None),
                    "height": next((int(item.get("height")) for item in alive if item.get("height")), None),
                    "fps": next((float(item.get("fps")) for item in alive if item.get("fps")), 0.0),
                    "speed_x": min((float(item.get("speed_x") or 0.0) for item in alive), default=0.0),
                    "uptime_seconds": min((int(item.get("uptime_seconds") or 0) for item in alive), default=0),
                    "last_error": "; ".join(errors)[-4000:] if errors else None,
                    "error_count": sum(int(item.get("error_count") or 0) for item in snapshots),
                    "restart_count": sum(int(item.get("restart_count") or 0) for item in snapshots),
                    "hls_ready": any(bool(item.get("hls_ready")) and bool(item.get("alive")) for item in snapshots),
                    "nodes": snapshots,
                    "ready_nodes": len(alive),
                    "total_nodes": len(snapshots),
                }
                if persist:
                    channel.status = status
                    channel.pid = aggregate["pid"]
                    channel.live_bitrate_kbps = aggregate["bitrate_kbps"]
                    channel.last_error = aggregate["last_error"] or None
                results[int(channel.id)] = aggregate
            if persist:
                db.commit()
            return results

    @staticmethod
    def _compute_local_hls_ready(slug: str, segment_time: int) -> bool:
        """Inspect one Local HLS output without holding NodeController locks."""
        playlist = settings.hls_root / str(slug) / "index.m3u8"
        ready = False
        try:
            if playlist.is_file() and playlist.stat().st_size > 0:
                playlist_stat = playlist.stat()
                segment_time = max(1, int(segment_time or 1))
                freshness_window = max(20, segment_time * 8)
                lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
                segments = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
                if segments and any(line.startswith("#EXTINF:") for line in lines):
                    segment = playlist.parent / Path(segments[-1]).name
                    if segment.is_file():
                        latest_mtime = max(playlist_stat.st_mtime, segment.stat().st_mtime)
                        ready = (time.time() - latest_mtime) <= freshness_window
                # STREAMFORGE_MAIN_LOCAL_HLS_READY_RACE_FALLBACK_V60R2:
                # FFmpeg atomically replaces playlists. If a check lands in the
                # tiny replacement window, a fresh media file still proves the
                # local output is advancing.
                if not ready and (time.time() - playlist_stat.st_mtime) <= freshness_window:
                    newest_segment = 0.0
                    for candidate in playlist.parent.iterdir():
                        if candidate.is_file() and candidate.suffix.lower() in {".ts", ".m4s", ".aac", ".mp3"}:
                            try:
                                newest_segment = max(newest_segment, candidate.stat().st_mtime)
                            except OSError:
                                continue
                    ready = bool(newest_segment and (time.time() - newest_segment) <= freshness_window)
        except OSError:
            ready = False
        return bool(ready)

    def _refresh_local_hls_ready_async(self, cache_key: tuple[int, int, str], slug: str, segment_time: int) -> None:
        try:
            ready = self._compute_local_hls_ready(slug, segment_time)
            with self._lock:
                self._cache[cache_key] = (time.monotonic(), {"ready": bool(ready)})
        finally:
            with self._lock:
                self._hls_ready_refreshing.discard(cache_key)

    def cached_local_hls_ready(self, channel: Channel, node: Node, *, alive: bool = True) -> bool:
        """Return Local HLS readiness without filesystem I/O on this caller.

        STREAMFORGE_MAIN_ASYNC_HLS_READY_V114: the Channels page polls every few
        seconds. Its request worker consumes the last cached result and schedules
        a bounded background refresh when stale, so panel navigation can never
        compete with Nginx Local-relay reads for synchronous directory scans.
        """
        if channel.output_type != "hls" or not alive or not self._is_local(node):
            return False
        cache_key = (int(channel.id), int(node.id), "hls-ready")
        now = time.monotonic()
        cached_ready = False
        should_refresh = False
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached:
                cached_ready = bool(cached[1].get("ready"))
                if now - cached[0] < 3.0:
                    return cached_ready
            if cache_key not in self._hls_ready_refreshing:
                self._hls_ready_refreshing.add(cache_key)
                should_refresh = True
        if should_refresh:
            try:
                self._hls_ready_executor.submit(
                    self._refresh_local_hls_ready_async,
                    cache_key,
                    str(channel.slug or ""),
                    max(1, int(getattr(channel, "hls_segment_time", 1) or 1)),
                )
            except RuntimeError:
                with self._lock:
                    self._hls_ready_refreshing.discard(cache_key)
        return cached_ready

    # STREAMFORGE_MAIN_CACHED_HLS_READY_STATE_V1113:
    def cached_hls_ready_state(self, channel: Channel, node: Node, *, max_age: float = 20.0) -> bool | None:
        """Return ready/not-ready from recent state without Remote network I/O.

        Local readiness may perform one filesystem check when its tiny cache is
        cold; Remote readiness is strictly cache-only. None means unknown.
        """
        if channel.output_type != "hls" or not node.enabled:
            return False
        if self._is_local(node):
            alive = bool(stream_manager.runtime_snapshot(int(channel.id)).get("alive"))
            if not alive:
                return False
            cache_key = (int(channel.id), int(node.id), "hls-ready")
            now = time.monotonic()
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and now - cached[0] <= max(0.0, float(max_age)):
                    return bool(cached[1].get("ready"))
            # Local disk is on-box and bounded; populate the shared cache once.
            return bool(self.hls_ready(channel, node))
        if node_maintenance_active(int(node.id)) or self._is_known_offline(node):
            return False
        now = time.monotonic()
        with self._lock:
            single = self._cache.get((int(channel.id), int(node.id), "status"))
            if single and now - single[0] <= max(0.0, float(max_age)):
                payload = single[1] if isinstance(single[1], dict) else {}
                return bool(payload.get("alive") and payload.get("hls_ready"))
            bulk = self._cache.get((0, int(node.id), "bulk-status"))
            if bulk and now - bulk[0] <= max(0.0, float(max_age)):
                payload = bulk[1] if isinstance(bulk[1], dict) else {}
                rows = payload.get("channels") if isinstance(payload, dict) else {}
                if isinstance(rows, dict):
                    row = rows.get(self._channel_key(channel))
                    if isinstance(row, dict):
                        return bool(row.get("alive") and row.get("hls_ready"))
                    if payload.get("error") or payload.get("offline_backoff"):
                        return False
        return None

    def hls_ready(self, channel: Channel, node: Node | None = None) -> bool:
        if channel.output_type != "hls":
            return False
        selected = node or (self.assigned_nodes(channel)[0] if self.assigned_nodes(channel) else None)
        if not selected:
            return False
        if self._is_local(selected):
            # STREAMFORGE_MAIN_LOCAL_HLS_READY_CACHE_V55:
            # Playback/readiness decisions outside the hot panel path may still
            # perform one synchronous check, then feed the shared cache used by
            # the asynchronous Channels/status poller.
            cache_key = (int(channel.id), int(selected.id), "hls-ready")
            now_mono = time.monotonic()
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and now_mono - cached[0] < 3.0:
                    return bool(cached[1].get("ready"))
            ready = self._compute_local_hls_ready(
                str(channel.slug or ""),
                max(1, int(getattr(channel, "hls_segment_time", 1) or 1)),
            )
            with self._lock:
                self._cache[cache_key] = (now_mono, {"ready": bool(ready)})
            return bool(ready)
        # STREAMFORGE_NODE_UPDATE_HLS_FAIL_FAST_V57: never make a Main playback
        # request wait on a Remote Node that the Main update worker is actively
        # restarting. The load balancer can immediately use another ready Node.
        if node_maintenance_active(int(selected.id)):
            return False
        try:
            result = self.runtime_snapshot_on_node(channel.id, selected.id)
            return bool(result.get("hls_ready") and result.get("alive"))
        except NodeError:
            return False

    def hls_file(self, channel: Channel, filename: str, node: Node | None = None) -> tuple[bytes, str]:
        safe_name = Path(filename).name
        if safe_name != filename:
            raise NodeError("Invalid HLS filename")
        selected = node or (self.assigned_nodes(channel)[0] if self.assigned_nodes(channel) else None)
        if not selected:
            raise NodeError("No node is assigned")
        if self._is_local(selected):
            path = settings.hls_root / channel.slug / safe_name
            if not path.is_file():
                raise FileNotFoundError(path)
            media_types = {
                ".m3u8": "application/vnd.apple.mpegurl",
                ".ts": "video/mp2t",
                ".m4s": "video/iso.segment",
                ".aac": "audio/aac",
                ".mp3": "audio/mpeg",
                ".key": "application/octet-stream",
            }
            return path.read_bytes(), media_types.get(path.suffix.lower(), "application/octet-stream")
        if node_maintenance_active(int(selected.id)):
            raise NodeError(f"Node is updating: {selected.name}")
        return self._request(
            selected,
            "GET",
            f"/api/v1/hls/{urllib.parse.quote(self._channel_key(channel))}/{urllib.parse.quote(safe_name)}",
            timeout=8.0,
            expect_json=False,
        )

    def sync_stream_users(self, node: Node, panel_url: str) -> dict[str, Any]:
        """Clear legacy Main-managed Node users while preserving Node-local users.

        Playlist accounts on a Remote Node are authoritative on that Node. The
        Main Panel only exposes its playlist catalogue so a Node operator can
        copy a Main playlist into a Node-local account.
        """
        if node.node_type == "local":
            return {"ok": True, "users": 0, "local": True}
        with SessionLocal() as db:
            current = db.get(Node, node.id)
            if not current:
                raise NodeError("Node not found")
            payload = {
                "panel_url": panel_url.strip().rstrip("/"),
                "node_slug": current.slug,
                "independent_mode": bool(getattr(current, "sync_main_users", False)),
                "users": [],
            }
        result = self._request(node, "POST", "/api/v1/users/sync", payload, timeout=12.0)
        with self._lock:
            self._cache.pop((0, node.id, "health"), None)
        return result

    def sync_panel_users(self, node: Node, panel_url: str) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "users": 0, "local": True}
        with SessionLocal() as db:
            current = db.get(Node, node.id)
            if not current:
                raise NodeError("Node not found")
            users = db.scalars(
                select(AdminUser).where(AdminUser.is_active.is_(True), AdminUser.nodes.any(Node.id == current.id)).order_by(AdminUser.username)
            ).all()
            logo = (current.logo_url or "").strip()
            logo_filename = ""
            logo_content_base64 = ""
            favicon_url = ""
            favicon_filename = ""
            favicon_content_base64 = ""
            favicon_row = db.get(AppSetting, f"node_favicon_{int(current.id)}")
            if favicon_row:
                favicon_url = str(favicon_row.value or "").strip()
            # STREAMFORGE_STALE_NODE_FAVICON_SELF_HEAL_V3048:
            # A stale/malformed favicon reference must not abort the complete
            # Node sync. Clear it and send remove_node_favicon so both Main and
            # Remote converge on an empty favicon state.
            if favicon_url:
                if favicon_url.startswith("/node-logos/"):
                    favicon_filename = Path(favicon_url[len("/node-logos/"):]).name
                    favicon_source = settings.node_logo_root / favicon_filename
                    if favicon_filename and favicon_source.is_file():
                        favicon_url = f"/node-logos/{favicon_filename}"
                        favicon_content_base64 = base64.b64encode(favicon_source.read_bytes()).decode("ascii")
                    else:
                        favicon_url = ""
                        favicon_filename = ""
                else:
                    favicon_url = ""
                if not favicon_url and favicon_row is not None:
                    favicon_row.value = ""
                    db.commit()
            if logo.startswith("/node-logos/"):
                filename = Path(logo[len("/node-logos/"):]).name
                source = settings.node_logo_root / filename
                if not filename or not source.is_file():
                    raise NodeError("Main Server Node logo file is missing")
                logo = f"/node-logos/{filename}"
                logo_filename = filename
                logo_content_base64 = base64.b64encode(source.read_bytes()).decode("ascii")
            payload = {
                "panel_url": panel_url.strip().rstrip("/"),
                "node_slug": current.slug,
                "node_name": current.name,
                "node_logo_url": logo,
                "node_logo_filename": logo_filename,
                "node_logo_content_base64": logo_content_base64,
                "node_favicon_filename": favicon_filename,
                "node_favicon_content_base64": favicon_content_base64,
                "remove_node_favicon": not bool(favicon_url),
                "independent_mode": bool(getattr(current, "sync_main_users", False)),
                "users": [
                    {
                        "username": user.username,
                        # v1.11.117: Node panel credentials are verified live by
                        # the Main Server. Never copy reusable password hashes to
                        # a Remote Node.
                        "enabled": bool(user.is_active),
                        "permissions": sorted(role_permission_set(user.role)),
                    }
                    for user in users
                ],
            }
        result = self._request(node, "POST", "/api/v1/panel-users/sync", payload, timeout=12.0)
        with self._lock:
            self._cache.pop((0, node.id, "health"), None)
        return result

    def logs(self, node: Node, *, limit: int = 300, level: str = "", scope: str = "", q: str = "") -> list[dict[str, Any]]:
        if node.node_type == "local":
            return []
        # STREAMFORGE_NODE_LOG_LIMIT_SELECTOR_V84:
        # limit=0 means fetch the full Node ring-buffer so Main can show the
        # exact total count while still slicing rows in the UI.
        safe_limit = 0 if int(limit or 0) <= 0 else max(1, min(5000, int(limit or 0)))
        # STREAMFORGE_NODE_LOG_SEARCH_CLIENT_V1035: new agents filter at source;
        # Main still rechecks results for rolling compatibility with old agents.
        query = urllib.parse.urlencode({"limit": safe_limit, "level": level, "scope": scope, "q": str(q or "")[:240]})
        result = self._request(node, "GET", f"/api/v1/logs?{query}", timeout=8.0)
        return list(result.get("items") or [])

    def channel_logs(self, node: Node, key: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        if node.node_type == "local":
            return []
        safe_key = urllib.parse.quote(str(key or "").strip(), safe="")
        query = urllib.parse.urlencode({"limit": max(1, min(1000, int(limit or 1000)))})
        try:
            result = self._request(node, "GET", f"/api/v1/channels/{safe_key}/logs?{query}", timeout=8.0)
            return list(result.get("items") or [])
        except NodeError:
            # Rolling-update compatibility with agents that only expose the
            # combined log endpoint.
            return [
                item for item in self.logs(node, limit=limit, scope="channel")
                if str(item.get("channel") or "").strip() == str(key or "").strip()
            ]

    def clear_channel_logs(self, node: Node, key: str) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "cleared": 0}
        safe_key = urllib.parse.quote(str(key or "").strip(), safe="")
        return self._request(node, "POST", f"/api/v1/channels/{safe_key}/logs/clear", timeout=8.0)

    # STREAMFORGE_MAIN_NODE_LIVE_SESSIONS_CLIENT_V89:
    # Fetch detailed Node viewer rows only on demand. /health remains a cheap
    # count-only heartbeat so large viewer populations do not bloat polling.
    def viewer_sessions(self, node: Node, *, timeout: float = 3.0) -> dict[str, Any]:
        # STREAMFORGE_MAIN_NODE_SESSION_FETCH_V1049:
        # Historical compatibility marker retained; v11.23 moves the fetch out
        # of the browser request and bounds its background wait.
        # STREAMFORGE_MAIN_NODE_SESSION_BACKGROUND_TIMEOUT_V1123:
        # Live-session detail is refreshed outside the browser request. Keep the
        # control wait bounded so one busy/offline Node cannot hold the cache
        # worker for the historical 8-second timeout.
        if node.node_type == "local":
            return {"ok": True, "total_users": 0, "direct_sessions": []}
        safe_timeout = max(1.0, min(8.0, float(timeout or 3.0)))
        return self._request(node, "GET", "/api/v1/viewers/sessions", timeout=safe_timeout)

    def kill_viewer_session(self, node: Node, session_id: str) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "killed": 0}
        return self._request(node, "POST", "/api/v1/viewers/kill", {"session_id": session_id}, timeout=8.0)

    def clear_logs(
        self,
        node: Node,
        *,
        log_type: str = "",
        level: str = "",
        scope: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        if node.node_type == "local":
            return {"ok": True, "cleared": 0}
        # STREAMFORGE_NODE_SCOPED_LOG_CLEAR_V66: preserve unrelated Node log
        # tabs when Main asks to clear the currently selected log view.
        query = urllib.parse.urlencode({"log_type": log_type, "level": level, "scope": scope, "q": str(q or "")[:240]})
        return self._request(node, "POST", f"/api/v1/logs/clear?{query}", timeout=8.0)

    # Backward-compatible alias used by older route code during rolling updates.
    def sync_node_users(self, node: Node, panel_url: str) -> dict[str, Any]:
        return self.sync_stream_users(node, panel_url)

    def reset_stale_state(self) -> None:
        stream_manager.reset_stale_state()
        with SessionLocal() as db:
            local = ensure_local_node(db)
            local.status = "online"
            local.last_seen_at = utcnow()
            # STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2
            # Reset channel runtime fields with one set-based statement.
            # This avoids SQLAlchemy StaleDataError if a channel is removed by a
            # concurrent control/update path while Main is starting.
            db.execute(
                update(Channel).values(
                    status=case(
                        (Channel.desired_running.is_(True), "unknown"),
                        else_="stopped",
                    ),
                    pid=None,
                    live_bitrate_kbps=0,
                )
            )
            db.commit()

    def restore_desired_channels(self, *, startup_delay: float = 1.5) -> None:
        """Restart channels that were intentionally running before reboot."""
        if startup_delay > 0:
            time.sleep(startup_delay)
        with SessionLocal() as db:
            channel_ids = [
                item.id for item in db.scalars(
                    select(Channel).where(Channel.enabled.is_(True), Channel.desired_running.is_(True))
                ).all()
            ]
        for channel_id in channel_ids:
            try:
                self.start(channel_id, recovery=True)
                log_event("Restored channel after service startup", scope="channel", channel_id=channel_id)
            except Exception as exc:
                log_event(
                    "Channel restore after startup failed", scope="channel", level="warning",
                    channel_id=channel_id, details=str(exc),
                )

    def stop_all_local(self, *, preserve_desired: bool = True) -> None:
        if preserve_desired:
            stream_manager.shutdown_all()
        else:
            stream_manager.stop_all()

    def _invalidate(self, channel_id: int, node_id: int | None = None) -> None:
        with self._lock:
            keys = [
                key for key in self._cache
                if (key[0] == channel_id and (node_id is None or key[1] == node_id))
                or (key[0] == 0 and (node_id is None or key[1] == node_id))
            ]
            for key in keys:
                self._cache.pop(key, None)


node_controller = NodeController()
