"""Persistent secret behind viewer stream keys and Remote Node relay keys.

STREAMFORGE_STREAM_LINK_SECRET_V1276: playlist stream keys and Node relay keys
used to be HMACs keyed directly by STREAMFORGE_SECRET_KEY, so rotating that
key broke every Node relay and every viewer link. They now use a secret kept
in app_settings. Main seeds it once with the current STREAMFORGE_SECRET_KEY,
so every existing key stays byte-identical, and later secret-key rotation no
longer changes them. The row travels with database backups and restores.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time

from .config import settings
from .db import SessionLocal
from .models import AppSetting

STREAM_LINK_SECRET_KEY = "stream_link_secret"
_CACHE_TTL_SECONDS = 60.0
_cache_lock = threading.Lock()
_cached: tuple[float, str] | None = None


def ensure_stream_link_secret(db) -> None:
    """Seed the stream-link secret once; never overwrite an existing value."""
    row = db.get(AppSetting, STREAM_LINK_SECRET_KEY)
    if row is None or not str(row.value or "").strip():
        if row is None:
            db.add(AppSetting(key=STREAM_LINK_SECRET_KEY, value=settings.secret_key))
        else:
            row.value = settings.secret_key


def stream_link_secret() -> str:
    global _cached
    now = time.monotonic()
    with _cache_lock:
        cached = _cached
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        with SessionLocal() as db:
            row = db.get(AppSetting, STREAM_LINK_SECRET_KEY)
            value = str(row.value or "").strip() if row is not None else ""
    except Exception:
        # Keep serving the last known secret through a transient database
        # error; never fall back to a possibly rotated secret key instead.
        if cached is not None:
            return cached[1]
        value = ""
    # Before Main has seeded the row, the seed value is the secret key itself.
    value = value or settings.secret_key
    with _cache_lock:
        _cached = (now, value)
    return value


def stream_link_hmac(message: bytes) -> str:
    return hmac.new(stream_link_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]
