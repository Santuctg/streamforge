#!/usr/bin/env python3
"""v12.77: 12-character Panel passwords and a forced change after a weak/default login."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1277-pw-")
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3", STREAMFORGE_XTREAM_GUARD_DB=f"{d}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64, STREAMFORGE_PANEL_PASSWORD_VAULT_KEY=Fernet.generate_key().decode(),
    STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402
from app.auth import PANEL_PASSWORD_MIN_LENGTH, hash_password, panel_password_policy_error  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AdminUser  # noqa: E402
m.metrics_history.path = Path(d) / "metrics-history.jsonl"  # default path is under /var/lib/streamforge
m.main_panel_client_ip = lambda request: "198.51.100.7"
BASE = "http://127.0.0.1"

assert PANEL_PASSWORD_MIN_LENGTH == 12
assert panel_password_policy_error("short-pass1")            # 11 characters
assert panel_password_policy_error("ChangeMe123!")           # known default
assert panel_password_policy_error("Env-Default-Pass", "Env-Default-Pass")
assert panel_password_policy_error("A-good-password!") == ""


def login(client: TestClient, password: str) -> str:
    r = client.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    return r.headers["location"]


with TestClient(m.app, base_url=BASE) as c:
    # The installation default logs in, but only to the password page.
    assert login(c, "ChangeMe123!") == "/account/password"
    for path in ("/", "/channels", "/admin-users", "/system/backups"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/account/password", (path, r.status_code)
    page = c.get("/account/password")
    assert page.status_code == 200 and "Choose a new password to continue" in page.text
    assert 'minlength="12"' in page.text
    # Non-Panel routes keep working while the change is pending.
    assert c.get("/health").text == "ok"
    assert c.get("/web-player", follow_redirects=False).headers.get("location") != "/account/password"

    def change(new: str):
        return c.post("/account/password", data={"current_password": current, "new_password": new,
                                                  "new_password_confirm": new}, follow_redirects=False)
    current = "ChangeMe123!"
    assert "at least 12 characters" in change("Short-1!").text
    assert c.get("/", follow_redirects=False).status_code == 303
    r = change("A-much-better-pass1")
    assert r.status_code in (200, 303)
    assert c.get("/", follow_redirects=False).status_code == 200, "flag cleared after a good change"

with TestClient(m.app, base_url=BASE) as c:
    # A strong password logs straight in.
    assert login(c, "A-much-better-pass1") != "/account/password"
    assert c.get("/", follow_redirects=False).status_code == 200
    # A legacy 8-11 character password is forced to change at its next login.
    with SessionLocal() as db:
        admin = db.query(AdminUser).filter_by(username="admin").one()
        admin.password_hash = hash_password("Legacy-8ch")
        db.commit()
    c.post("/logout")
    assert login(c, "Legacy-8ch") == "/account/password"
    # Creating a Panel user with a short password is refused.
    current = "Legacy-8ch"
    r = c.post("/account/password", data={"current_password": "Legacy-8ch", "new_password": "Another-good-pass2",
                                          "new_password_confirm": "Another-good-pass2"}, follow_redirects=False)
    assert c.get("/", follow_redirects=False).status_code == 200
    with SessionLocal() as db:
        roles = m.visible_panel_roles(db, db.query(AdminUser).filter_by(username="admin").one())
        role_id = roles[0].id
    r = c.post("/admin-users/new", data={"username": "staff1", "password": "Eight8ch", "password_confirm": "Eight8ch",
                                          "role_id": str(role_id)}, follow_redirects=False)
    assert "at least 12 characters" in r.text, r.status_code
    with SessionLocal() as db:
        assert db.query(AdminUser).filter_by(username="staff1").first() is None
print("PASS: v12.77 12-character Panel passwords, default/short login forced to change password, other routes unaffected")
