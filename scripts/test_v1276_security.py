#!/usr/bin/env python3
"""v12.76: security headers, session revocation on password change, Xtream auth limits."""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1276-sec-")
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3", STREAMFORGE_XTREAM_GUARD_DB=f"{d}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64, STREAMFORGE_PANEL_PASSWORD_VAULT_KEY=Fernet.generate_key().decode(),
    STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="Env-Seed-Only-Pass1",
)
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402
import app.xtream_guard as guard  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AdminUser, StreamUser  # noqa: E402
m.metrics_history.path = Path(d) / "metrics-history.jsonl"  # default path is under /var/lib/streamforge
# The operator's own password differs from the env seed password; since v12.77
# logging in with the seed (installation default) forces a password change.
m.Base.metadata.create_all(bind=m.engine)
with m.SessionLocal() as _db:
    _db.add(m.AdminUser(username="admin", password_hash=m.hash_password("BootTest!12345")))
    _db.commit()

m.main_panel_client_ip = lambda request: str(request.headers.get("x-test-ip") or "198.51.100.7")
BASE = "http://127.0.0.1"


def login(client: TestClient, password: str) -> None:
    r = client.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.status_code


def signed_in(client: TestClient) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


with TestClient(m.app, base_url=BASE) as a, TestClient(m.app, base_url=BASE) as b:
    # --- 4. security headers -------------------------------------------------
    panel = a.get("/login")
    assert panel.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in panel.headers["content-security-policy"]
    assert panel.headers["x-content-type-options"] == "nosniff"
    assert panel.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" not in panel.headers  # HSTS is opt-in
    player = a.get("/web-player", follow_redirects=False)
    assert "x-frame-options" not in player.headers and "content-security-policy" not in player.headers
    assert player.headers["x-content-type-options"] == "nosniff"

    # --- 5. session revocation ----------------------------------------------
    login(a, "BootTest!12345")
    login(b, "BootTest!12345")
    assert signed_in(a) and signed_in(b)
    r = a.post("/account/password", data={
        "current_password": "BootTest!12345", "new_password": "NewPass!67890",
        "new_password_confirm": "NewPass!67890"}, follow_redirects=False)
    assert r.status_code in (200, 303), r.status_code
    assert signed_in(a), "the browser that changed the password stays signed in"
    assert not signed_in(b), "other sessions end after a password change"
    # A password reset by someone else ends this browser's session too.
    with SessionLocal() as db:
        admin = db.query(AdminUser).filter_by(username="admin").one()
        admin.password_hash = hash_password("Third!Pass123")
        db.commit()
    assert not signed_in(a)
    login(a, "Third!Pass123")
    assert signed_in(a)
    # Sessions issued before v12.76 carry no version stamp: they are adopted
    # (stamped with the current password) instead of being logged out.
    import base64, json
    from itsdangerous import TimestampSigner
    with SessionLocal() as db:
        admin_id = db.query(AdminUser).filter_by(username="admin").one().id
    legacy = TimestampSigner("x" * 64).sign(base64.b64encode(json.dumps({"admin_id": admin_id}).encode())).decode()
    with TestClient(m.app, base_url=BASE, cookies={"session": legacy}) as c:
        assert signed_in(c)

    # --- 6. Xtream auth limits ----------------------------------------------
    with SessionLocal() as db:
        db.add(StreamUser(name="viewer", token="tok-v1276", xtream_username="viewer",
                          xtream_password_hash=hash_password("RightPass!1"), enabled=True))
        db.commit()

    def api(user: str, password: str, ip: str) -> bool:
        r = a.get("/player_api.php", params={"username": user, "password": password}, headers={"x-test-ip": ip})
        return '"auth": 1' in r.text or '"auth":1' in r.text

    assert api("viewer", "RightPass!1", "203.0.113.10")
    for _ in range(guard.PAIR_FAILURES):
        assert not api("viewer", "wrong", "203.0.113.10")
    assert not api("viewer", "RightPass!1", "203.0.113.10"), "pair blocked after repeated failures"
    assert api("viewer", "RightPass!1", "203.0.113.11"), "other IPs are unaffected"
    # Refused attempts while blocked are not counted toward the IP-wide limit.
    for _ in range(50):
        api("viewer", "wrong", "203.0.113.10")
    assert not guard.xtream_auth_blocked("203.0.113.10", "someone-else")
    # Credential stuffing across usernames from one IP trips the IP-wide limit.
    for i in range(guard.IP_FAILURES):
        api(f"user{i}", "wrong", "203.0.113.20")
    assert guard.xtream_auth_blocked("203.0.113.20", "viewer")
    # A successful login clears the pair's unblocked failures.
    for _ in range(3):
        api("viewer", "wrong", "203.0.113.30")
    assert api("viewer", "RightPass!1", "203.0.113.30")
    guard.clear_xtream_failures("203.0.113.30", "viewer")
    # Guard errors fail open.
    os.environ[guard.GUARD_DB_ENV] = "/proc/forbidden/xtream.sqlite3"
    assert not guard.xtream_auth_blocked("203.0.113.10", "viewer")
    assert guard.record_xtream_failure("203.0.113.10", "viewer") is False
    os.environ[guard.GUARD_DB_ENV] = f"{d}/xtream.sqlite3"

# HSTS appears only when enabled and the request is HTTPS.
os.environ["STREAMFORGE_HSTS_MAX_AGE"] = "31536000"
captured = {}


async def inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def run(headers):
    async def send(message):
        if message["type"] == "http.response.start":
            captured["headers"] = dict(message["headers"])
    scope = {"type": "http", "path": "/channels", "scheme": "http", "headers": headers}
    await m.SecurityHeadersMiddleware(inner)(scope, None, send)
    return captured["headers"]

run_https = asyncio.run(run([(b"x-forwarded-proto", b"https")]))
assert run_https[b"strict-transport-security"] == b"max-age=31536000"
assert b"strict-transport-security" not in asyncio.run(run([(b"x-forwarded-proto", b"http")]))
print("PASS: v12.76 Panel-only frame headers, opt-in HSTS, password change ends other sessions, Xtream pair/IP limits fail open")
