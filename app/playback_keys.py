from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
import threading
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .access_control import client_ip, ip_matches
from .config import settings
from .models import Channel, PlaybackGrant, StreamUser
from .redis_state import redis_state

# v1.11.116: new playback URLs use an eight-character, cryptographically
# random opaque handle. The protected user/channel/IP/expiry grant is stored
# server-side, so a short URL never exposes credentials or a decryptable payload.
# Legacy Fernet URLs remain valid until their original expiry.
_SHORT_KEY_LENGTH = 8
_SHORT_KEY_ALPHABET = string.ascii_letters + string.digits
_short_key_lock = threading.RLock()
_short_key_cache: dict[str, dict[str, Any]] = {}
_short_key_index: dict[tuple[int, int, str, str, str], str] = {}
_short_key_last_cleanup = 0.0
_SHORT_KEY_MAX_ENTRIES = 250_000

_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode("utf-8")).digest())
_cipher = Fernet(_fernet_key)


def _cache_grant_local(key: str, payload: dict[str, Any]) -> None:
    _short_key_cache[key] = payload
    index_key = payload.get("index_key")
    if index_key:
        _short_key_index[tuple(index_key)] = key


def _redis_index_cache_key(index_key: tuple[int, int, str, str, str]) -> str:
    digest = hashlib.sha256(
        json.dumps(list(index_key), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return redis_state.key("playback", "index", digest)


def _redis_grant_cache_key(key: str) -> str:
    return redis_state.key("playback", "grant", key)


def _decode_shared_payload(raw: object) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
        payload["u"] = int(payload.get("u"))
        payload["c"] = int(payload.get("c"))
        payload["exp"] = int(payload.get("exp"))
        payload["ip"] = str(payload.get("ip") or "")
        payload["kind"] = str(payload.get("kind") or "viewer")
        payload["sid"] = str(payload.get("sid") or "")[:96]
        index_key = payload.get("index_key")
        if isinstance(index_key, list) and len(index_key) == 5:
            payload["index_key"] = (
                int(index_key[0]), int(index_key[1]), str(index_key[2]), str(index_key[3]), str(index_key[4])
            )
        else:
            payload["index_key"] = (
                payload["u"], payload["c"], payload["ip"], payload["sid"], payload["kind"]
            )
        return payload
    except (TypeError, ValueError, json.JSONDecodeError, IndexError):
        return None


def _cache_grants_shared(items: list[tuple[str, dict[str, Any]]], now: float | None = None) -> None:
    if not items:
        return
    current = time.time() if now is None else now

    def write(client):
        pipe = client.pipeline(transaction=False)
        for key, payload in items:
            ttl = max(1, int(float(payload.get("exp") or 0) - current))
            index_key = tuple(payload.get("index_key") or ())
            if len(index_key) != 5:
                continue
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
            pipe.setex(_redis_grant_cache_key(key), ttl, encoded)
            pipe.setex(_redis_index_cache_key(index_key), ttl, key)
        return pipe.execute()

    redis_state.call(write)


def _shared_existing_keys(
    index_keys: dict[int, tuple[int, int, str, str, str]],
    now: float,
) -> dict[int, tuple[str, dict[str, Any]]]:
    if not index_keys:
        return {}

    def read(client):
        channel_ids = list(index_keys)
        index_names = [_redis_index_cache_key(index_keys[channel_id]) for channel_id in channel_ids]
        keys = client.mget(index_names)
        found: list[tuple[int, str]] = []
        for channel_id, key in zip(channel_ids, keys):
            if key:
                found.append((channel_id, str(key)))
        if not found:
            return []
        payloads = client.mget([_redis_grant_cache_key(key) for _, key in found])
        return list(zip(found, payloads))

    ok, rows = redis_state.call(read)
    if not ok:
        return {}
    result: dict[int, tuple[str, dict[str, Any]]] = {}
    for (channel_id, key), raw in rows or []:
        payload = _decode_shared_payload(raw)
        if payload and int(payload.get("exp") or 0) > int(now) + 60:
            result[int(channel_id)] = (str(key), payload)
    return result


def _shared_grant(key: str, now: float) -> dict[str, Any] | None:
    ok, raw = redis_state.call(lambda client: client.get(_redis_grant_cache_key(key)))
    if not ok:
        return None
    payload = _decode_shared_payload(raw)
    if payload and int(payload.get("exp") or 0) >= int(now):
        return payload
    return None


def _cleanup_short_keys(db: Session, now: float, *, force: bool = False) -> None:
    global _short_key_last_cleanup
    if not force and now - _short_key_last_cleanup < 30 and len(_short_key_cache) < _SHORT_KEY_MAX_ENTRIES:
        return
    expired = [key for key, grant in _short_key_cache.items() if float(grant.get("exp") or 0) < now]
    for key in expired:
        grant = _short_key_cache.pop(key, None)
        if grant:
            index_key = grant.get("index_key")
            if index_key and _short_key_index.get(tuple(index_key)) == key:
                _short_key_index.pop(tuple(index_key), None)
    db.execute(delete(PlaybackGrant).where(PlaybackGrant.expires_at_epoch < int(now)))
    db.info["streamforge_playback_grants_dirty"] = True
    if len(_short_key_cache) > _SHORT_KEY_MAX_ENTRIES:
        ordered = sorted(_short_key_cache.items(), key=lambda item: float(item[1].get("exp") or 0))
        for key, grant in ordered[: len(_short_key_cache) - _SHORT_KEY_MAX_ENTRIES]:
            _short_key_cache.pop(key, None)
            index_key = grant.get("index_key")
            if index_key and _short_key_index.get(tuple(index_key)) == key:
                _short_key_index.pop(tuple(index_key), None)
    _short_key_last_cleanup = now


def _new_short_key(db: Session) -> str:
    for _attempt in range(128):
        candidate = "".join(secrets.choice(_SHORT_KEY_ALPHABET) for _ in range(_SHORT_KEY_LENGTH))
        if candidate not in _short_key_cache and db.get(PlaybackGrant, candidate) is None:
            return candidate
    raise RuntimeError("Unable to allocate a unique playback key")


def issue_playback_key(
    user: StreamUser,
    channel: Channel,
    request: Request,
    db: Session,
    *,
    session_id: str = "",
) -> str:
    ttl = settings.restream_key_ttl_seconds if user.user_type == "restream" else settings.viewer_key_ttl_seconds
    now = time.time()
    expires = int(now) + max(60, int(ttl))
    ip_address = client_ip(request)
    resolved_session_id = session_id.strip()[:96] or secrets.token_urlsafe(12)
    index_key = (int(user.id), int(channel.id), ip_address, resolved_session_id, str(user.user_type or "viewer"))

    # STREAMFORGE_REDIS_PLAYBACK_CACHE_V61: process-local cache is the hot path;
    # Redis is consulted only on a local miss so segment verification does not
    # turn into one Redis round-trip per request.
    with _short_key_lock:
        _cleanup_short_keys(db, now)
        existing_key = _short_key_index.get(index_key)
        existing = _short_key_cache.get(existing_key or "")
        if existing and int(existing.get("exp") or 0) > int(now) + 60:
            return str(existing_key)

    shared = _shared_existing_keys({int(channel.id): index_key}, now).get(int(channel.id))
    if shared:
        existing_key, payload = shared
        with _short_key_lock:
            _cache_grant_local(existing_key, payload)
        return existing_key

    with _short_key_lock:
        # Re-check after the network miss in case another request populated the
        # local cache while Redis was being queried.
        existing_key = _short_key_index.get(index_key)
        existing = _short_key_cache.get(existing_key or "")
        if existing and int(existing.get("exp") or 0) > int(now) + 60:
            return str(existing_key)
        key = _new_short_key(db)
        payload = {
            "u": int(user.id),
            "c": int(channel.id),
            "ip": ip_address,
            "exp": expires,
            "kind": str(user.user_type or "viewer"),
            "sid": resolved_session_id,
            "index_key": index_key,
        }
        db.add(
            PlaybackGrant(
                key=key,
                user_id=int(user.id),
                channel_id=int(channel.id),
                ip_address=ip_address,
                session_id=resolved_session_id,
                kind=str(user.user_type or "viewer"),
                expires_at_epoch=expires,
            )
        )
        db.info["streamforge_playback_grants_dirty"] = True
        _cache_grant_local(key, payload)
    _cache_grants_shared([(key, payload)], now)
    return key

def issue_playback_keys(
    user: StreamUser,
    channels: list[Channel],
    request: Request,
    db: Session,
    *,
    session_id: str = "",
) -> dict[int, str]:
    """Issue a catalogue of playback keys with local-first batched Redis/SQL misses."""
    unique_channels = {int(channel.id): channel for channel in channels}
    if not unique_channels:
        return {}
    ttl = settings.restream_key_ttl_seconds if user.user_type == "restream" else settings.viewer_key_ttl_seconds
    now = time.time()
    expires = int(now) + max(60, int(ttl))
    ip_address = client_ip(request)
    resolved_session_id = session_id.strip()[:96] or secrets.token_urlsafe(12)
    kind = str(user.user_type or "viewer")
    index_keys = {
        channel_id: (int(user.id), channel_id, ip_address, resolved_session_id, kind)
        for channel_id in unique_channels
    }
    result: dict[int, str] = {}

    with _short_key_lock:
        _cleanup_short_keys(db, now)
        for channel_id, index_key in index_keys.items():
            existing_key = _short_key_index.get(index_key)
            existing = _short_key_cache.get(existing_key or "")
            if existing and int(existing.get("exp") or 0) > int(now) + 60:
                result[channel_id] = str(existing_key)

    redis_missing = {channel_id: index_keys[channel_id] for channel_id in unique_channels if channel_id not in result}
    shared = _shared_existing_keys(redis_missing, now) if redis_missing else {}
    created_shared: list[tuple[str, dict[str, Any]]] = []

    with _short_key_lock:
        for channel_id, (key, payload) in shared.items():
            _cache_grant_local(key, payload)
            result[channel_id] = key

        missing: list[int] = []
        for channel_id in unique_channels:
            if channel_id in result:
                continue
            index_key = index_keys[channel_id]
            # Re-check for a concurrent request that populated local state.
            existing_key = _short_key_index.get(index_key)
            existing = _short_key_cache.get(existing_key or "")
            if existing and int(existing.get("exp") or 0) > int(now) + 60:
                result[channel_id] = str(existing_key)
            else:
                missing.append(channel_id)

        while missing:
            candidates = {
                channel_id: "".join(secrets.choice(_SHORT_KEY_ALPHABET) for _ in range(_SHORT_KEY_LENGTH))
                for channel_id in missing
            }
            values = list(candidates.values())
            duplicates = {key for key in values if values.count(key) > 1}
            stored = set(db.scalars(select(PlaybackGrant.key).where(PlaybackGrant.key.in_(values))).all())
            retry: list[int] = []
            for channel_id, key in candidates.items():
                if key in duplicates or key in stored or key in _short_key_cache:
                    retry.append(channel_id)
                    continue
                index_key = index_keys[channel_id]
                payload = {
                    "u": int(user.id), "c": channel_id, "ip": ip_address,
                    "exp": expires, "kind": kind, "sid": resolved_session_id,
                    "index_key": index_key,
                }
                db.add(PlaybackGrant(
                    key=key, user_id=int(user.id), channel_id=channel_id,
                    ip_address=ip_address, session_id=resolved_session_id,
                    kind=kind, expires_at_epoch=expires,
                ))
                _cache_grant_local(key, payload)
                created_shared.append((key, payload))
                result[channel_id] = key
            missing = retry
        if created_shared:
            db.info["streamforge_playback_grants_dirty"] = True

    _cache_grants_shared(created_shared, now)
    return result

def _short_grant(key: str, db: Session) -> dict[str, Any] | None:
    if len(key) != _SHORT_KEY_LENGTH or any(character not in _SHORT_KEY_ALPHABET for character in key):
        return None
    now = time.time()
    with _short_key_lock:
        _cleanup_short_keys(db, now)
        cached = _short_key_cache.get(key)
        if cached:
            return dict(cached)

    shared = _shared_grant(key, now)
    if shared:
        with _short_key_lock:
            _cache_grant_local(key, shared)
        return dict(shared)

    with _short_key_lock:
        # A concurrent request may have filled the cache while Redis was read.
        cached = _short_key_cache.get(key)
        if cached:
            return dict(cached)
        stored = db.get(PlaybackGrant, key)
        if not stored:
            return None
        payload = {
            "u": int(stored.user_id),
            "c": int(stored.channel_id),
            "ip": str(stored.ip_address or ""),
            "exp": int(stored.expires_at_epoch),
            "kind": str(stored.kind or "viewer"),
            "sid": str(stored.session_id or "")[:96],
            "index_key": (
                int(stored.user_id), int(stored.channel_id), str(stored.ip_address or ""),
                str(stored.session_id or "")[:96], str(stored.kind or "viewer"),
            ),
        }
        _cache_grant_local(key, payload)
    _cache_grants_shared([(key, payload)], now)
    return dict(payload)

def _legacy_grant(key: str) -> dict[str, Any]:
    try:
        raw = _cipher.decrypt(key.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(403, "Invalid encrypted playback key") from exc


def verify_playback_key(key: str, request: Request, db: Session) -> tuple[StreamUser, Channel, str]:
    payload = _short_grant(key, db)
    if payload is None:
        payload = _legacy_grant(key)
    try:
        user_id = int(payload.get("u"))
        channel_id = int(payload.get("c"))
        expires = int(payload.get("exp"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "Malformed encrypted playback key") from exc
    if expires < int(time.time()):
        raise HTTPException(403, "Encrypted playback key expired")
    request_ip = client_ip(request)
    if str(payload.get("ip") or "") != request_ip:
        raise HTTPException(403, "Encrypted playback key is bound to another IP")
    user = db.get(StreamUser, user_id)
    channel = db.get(Channel, channel_id)
    if not user or not channel or not user.enabled or not channel.enabled:
        raise HTTPException(403, "Playback account or channel is unavailable")
    if user.expires_at:
        from datetime import datetime, timezone
        expiry = user.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(403, "Playback account expired")
    # Dynamic "All enabled Main channels" profiles intentionally do not keep
    # a static relationship; catalogue and playback authorization share the
    # same current enabled-HLS source.
    if user.playlist and bool(getattr(user.playlist, "all_enabled_channels", False)):
        allowed_channel_ids = set(
            db.scalars(
                select(Channel.id).where(
                    Channel.enabled.is_(True),
                    Channel.output_type == "hls",
                )
            ).all()
        )
    else:
        allowed_channels = user.playlist.channels if user.playlist else user.channels
        allowed_channel_ids = {item.id for item in allowed_channels}
    if channel.id not in allowed_channel_ids:
        raise HTTPException(404, "Channel is not assigned to this account")
    if user.user_type == "restream":
        if not (user.restream_allowed_ips or "").strip():
            raise HTTPException(403, "Restream account has no allowed source IP")
        if not ip_matches(user.restream_allowed_ips, request_ip):
            raise HTTPException(403, "This IP is not allowed for the restream account")
    session_id = str(payload.get("sid") or "")[:96]
    return user, channel, session_id
