#!/usr/bin/env python3
"""Role landing pages, audit events and server-side hierarchy regression."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
work = tempfile.TemporaryDirectory(prefix="sf-role-manager-")
d = Path(work.name)
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/test.db",
    STREAMFORGE_HLS_ROOT=str(d / "hls"),
    STREAMFORGE_LOGO_ROOT=str(d / "logos"),
    STREAMFORGE_NODE_LOGO_ROOT=str(d / "node-logos"),
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=str(d / "guard.db"),
    STREAMFORGE_XTREAM_GUARD_DB=str(d / "xtream.db"),
    STREAMFORGE_SECRET_KEY="x" * 64,
    STREAMFORGE_ADMIN_USER="admin",
    STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)
# Test automatic vault saving too, without the legacy management flag.
from cryptography.fernet import Fernet
os.environ["STREAMFORGE_PANEL_PASSWORD_VAULT_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main as m
from app.auth import hash_password
from app.db import SessionLocal, engine
from app.models import AdminUser, LogEntry, Role
from app.panel_password_vault import vault_password_available
from app.permissions import ALL_PERMISSION_KEYS, PERMISSION_GROUPS, decode_permissions, encode_permissions, normalize_permissions

m.metrics_history.path = d / "metrics.jsonl"
m.main_panel_client_ip = lambda request: "198.51.100.71"
PASSWORD = "Role-test-password-123!"
BASE = "http://127.0.0.1"


def login(client, name, destination=None):
    response = client.post("/login", data={"username": name, "password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303, response.text
    if destination:
        assert response.headers["location"] == destination, response.headers


def audit_events():
    with SessionLocal() as db:
        return [(entry.message, entry.actor, json.loads(entry.details)) for entry in
                db.scalars(select(LogEntry).where(LogEntry.message.in_(
                    ["Panel role created", "Panel role updated", "Panel role deleted"]
                )).order_by(LogEntry.id)).all()]


def assert_hidden(response):
    # The Panel alias middleware maps hidden 404s to an internal silent-drop.
    assert response.status_code == 404 or (
        response.status_code == 418 and
        response.headers.get("X-StreamForge-Route") == "main-panel-unknown-silent-drop"
    ), (response.status_code, response.text)


with TestClient(m.app, base_url=BASE) as root:
    with SessionLocal() as db:
        admin = db.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        admin.password_hash = hash_password(PASSWORD)
        super_id = admin.role_id
        ids = {}
        for name, keys in {
            "settingsonly": ["settings.view"],
            "backupsonly": ["backups.view"],
            "limited": ["roles.create", "roles.edit", "roles.delete", "channels.view"],
            "usereditor": ["panel_users.create", "panel_users.edit"],
            "nopermissions": [],
        }.items():
            role = Role(name=name, permissions=encode_permissions(keys), is_system=False)
            db.add(role)
            db.flush()
            ids[name] = role.id
            db.add(AdminUser(username=name, password_hash=hash_password(PASSWORD),
                             role=role, is_active=True, main_panel_access=True))
        db.commit()

    for name, path in (("settingsonly", "/system/branding"), ("backupsonly", "/system/backups"),
                       ("nopermissions", "/no-access")):
        with TestClient(m.app, base_url=BASE) as client:
            login(client, name, path)
            assert client.get(path).status_code == 200
            for denied in ("/roles", "/channels"):
                assert client.get(denied).status_code == 403

    login(root, "admin")
    editor_page = root.get("/roles/new")
    assert "Legacy vault-management flag" in editor_page.text
    assert "does not control password saving" in editor_page.text
    response = root.post("/roles/new", data={"name": "Audit role", "permissions": ["channels.view"]},
                         follow_redirects=False)
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        role_id = db.scalar(select(Role).where(Role.name == "Audit role")).id
    response = root.post(f"/roles/{role_id}/edit", data={"name": "Audit renamed", "permissions": ["settings.edit"]},
                         follow_redirects=False)
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        assert decode_permissions(db.get(Role, role_id).permissions) == {"settings.view", "settings.edit"}
    response = root.post(f"/roles/{role_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    events = audit_events()
    assert [x[0] for x in events] == ["Panel role created", "Panel role updated", "Panel role deleted"]
    assert all(actor == "admin" and detail["role_id"] == role_id for _, actor, detail in events)
    assert events[1][2]["previous_name"] == "Audit role"
    assert events[1][2]["granted"] == ["settings.edit", "settings.view"]
    assert events[1][2]["revoked"] == ["channels.view"]

    # Forged POSTs cannot bypass the role ceiling, own-role lock or wildcard ban.
    with TestClient(m.app, base_url=BASE) as client:
        login(client, "limited")
        for key in ("*", "nodes.edit", "panel_users.password_reveal", "panel_users.password_vault_manage"):
            assert client.post("/roles/new", data={"name": "Forbidden", "permissions": [key]}).status_code == 403
        assert_hidden(client.post(f"/roles/{super_id}/edit", data={"name": "No"}))
        assert client.post(f"/roles/{ids['limited']}/edit", data={"name": "No"}).status_code == 403
        assert client.post(f"/roles/{ids['limited']}/delete").status_code == 403
        assert_hidden(client.get(f"/roles/{super_id}/edit"))
    # Locked/assigned roles cannot be changed/deleted even by an unrestricted actor.
    root.post(f"/roles/{super_id}/edit", data={"name": "Renamed root"}, follow_redirects=False)
    root.post(f"/roles/{super_id}/delete", follow_redirects=False)
    root.post(f"/roles/{ids['limited']}/delete", follow_redirects=False)
    assert audit_events() == events, "refused mutations must not log success"
    with SessionLocal() as db:
        assert db.get(Role, super_id).is_system
        assert db.get(Role, super_id).name == "Super Admin"
        assert db.get(Role, ids["limited"]) is not None

    # Automatic saving remains intentional; the legacy flag is not an opt-in.
    with TestClient(m.app, base_url=BASE) as client:
        login(client, "usereditor")
        response = client.post("/admin-users/new", data={
            "username": "hashandvault", "password": PASSWORD, "password_confirm": PASSWORD,
            "role_id": ids["nopermissions"], "is_active": "on", "main_panel_access": "on",
        }, follow_redirects=False)
        assert response.status_code == 303, response.text
    with SessionLocal() as db:
        target = db.scalar(select(AdminUser).where(AdminUser.username == "hashandvault"))
        assert target and vault_password_available(db, target)

assert normalize_permissions(["*"]) == []
legacy = next(item for group in PERMISSION_GROUPS.values() for item in group
              if item[0] == "panel_users.password_vault_manage")
assert "Legacy" in legacy[1] and "automatically" in legacy[2]
assert len(ALL_PERMISSION_KEYS) == len(set(ALL_PERMISSION_KEYS))
for key in ALL_PERMISSION_KEYS:
    closure = normalize_permissions([key])
    assert normalize_permissions(closure) == closure
engine.dispose()
work.cleanup()
print("PASS: role landing pages, create/update/delete audit, hierarchy, wildcard, locks and automatic vault saving")
