#!/usr/bin/env python3
"""v12.76: stream/relay keys keep their values on upgrade and survive secret-key rotation."""
from __future__ import annotations
import hashlib
import hmac
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1276-keys-")
OLD_SECRET = "a" * 64
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3", STREAMFORGE_SECRET_KEY=OLD_SECRET,
)
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402
import app.stream_keys as stream_keys  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AppSetting, Channel  # noqa: E402
from app.node_manager import node_controller  # noqa: E402


def legacy_key(secret: str, channel_id: int, slug: str) -> str:
    message = f"streamforge-channel:{channel_id}:{slug}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def reset_cache() -> None:
    stream_keys._cached = None


channel = Channel(id=7, name="Seven", slug="seven", input_url="http://127.0.0.1:9/x.m3u8")
expected = legacy_key(OLD_SECRET, 7, "seven")

# Before Main has started (no row yet) keys equal the pre-v12.76 values.
m.Base.metadata.create_all(bind=m.engine)
assert m.channel_stream_key(channel) == expected
assert node_controller._relay_key(channel) == expected

# Main startup seeds the row with the current secret key.
with TestClient(m.app, base_url="http://127.0.0.1"):
    pass
with SessionLocal() as db:
    row = db.get(AppSetting, "stream_link_secret")
    assert row is not None and row.value == OLD_SECRET
reset_cache()
assert m.channel_stream_key(channel) == expected

# Rotating STREAMFORGE_SECRET_KEY no longer changes stream or relay keys...
object.__setattr__(settings, "secret_key", "b" * 64)  # settings is a frozen dataclass
reset_cache()
assert m.channel_stream_key(channel) == expected
assert node_controller._relay_key(channel) == expected
# ...and a restart never overwrites the seeded value.
with TestClient(m.app, base_url="http://127.0.0.1"):
    pass
with SessionLocal() as db:
    assert db.get(AppSetting, "stream_link_secret").value == OLD_SECRET

# A transient database error keeps the last known secret, not the rotated key.
real_session = stream_keys.SessionLocal
stream_keys._cached = (0.0, OLD_SECRET)  # expired cache entry
stream_keys.SessionLocal = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
try:
    assert stream_keys.stream_link_secret() == OLD_SECRET
finally:
    stream_keys.SessionLocal = real_session

# The root Nginx relay helper derives the same relay key from the database.
spec = importlib.util.spec_from_file_location("apply_main_access", ROOT / "scripts/apply_main_access.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.DB_PATH = Path(d) / "t.db"
secret = helper._stream_link_secret()
assert secret == OLD_SECRET
assert helper._relay_key(7, "seven", secret) == expected
helper.DB_PATH = Path(d) / "missing.db"
assert helper._stream_link_secret() == ""  # falls back to the env secret key
print("PASS: v12.76 stream/relay keys unchanged on upgrade, stable across secret-key rotation, shared with the Nginx relay helper")
