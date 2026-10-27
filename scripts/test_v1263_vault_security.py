#!/usr/bin/env python3
"""Functional crypto, reauthentication, CSRF and stale-hash tests for Main vault."""
from __future__ import annotations
import os
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware
from app.auth import hash_password
from app.db import Base, get_db
from app.models import AdminUser, PanelUserCredentialVault, Role
from app.panel_password_vault import (
    VAULT_KEY_ENV, router, vault_enabled, vault_store,
    vault_discard, vault_password_available, vault_csrf_token,
)

assert not vault_enabled(), "test must start without vault key"
GUARD_TEMP = tempfile.TemporaryDirectory(prefix="sf-vault-reveal-guard-")
os.environ["STREAMFORGE_PANEL_VAULT_GUARD_DB"] = os.path.join(GUARD_TEMP.name, "guard.sqlite3")
os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"] = os.path.join(GUARD_TEMP.name, "login-guard.sqlite3")
ORIGIN = {"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"}
engine=create_engine("sqlite://", connect_args={"check_same_thread":False},poolclass=StaticPool)
Base.metadata.create_all(engine)
Session=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
role=Role(name="Super Admin",permissions=json.dumps(["*"]))
regular_role=Role(name="Staff",permissions=json.dumps(["panel_users.edit"]))
with Session() as db:
    db.add_all((role, regular_role))
    db.flush()
    owner=AdminUser(username="operatorroot",password_hash=hash_password("SuperRoot!A"),role=role,is_active=True,main_panel_access=True)
    person=AdminUser(username="panelviewer",password_hash=hash_password("ViewerPass!A"),role=regular_role,is_active=True,main_panel_access=True)
    db.add_all((owner,person))
    db.commit()
    owner_id,person_id=owner.id,person.id

# Without an independent key, the helper fails closed.
with Session() as db:
    person=db.get(AdminUser,person_id)
    assert vault_store(db,person,"NewSecret!A") is False
    assert not vault_password_available(db,person)

os.environ[VAULT_KEY_ENV]=Fernet.generate_key().decode()
assert vault_enabled()
with Session() as db:
    person=db.get(AdminUser,person_id)
    assert vault_store(db,person,"NewSecret!A")
    db.commit()
    row=db.get(PanelUserCredentialVault,person_id)
    assert row is not None
    assert "NewSecret!A" not in row.ciphertext
    assert vault_password_available(db,person)
    # Even if another code path changes the login hash, reveal must refuse stale value.
    person.password_hash=hash_password("DifferentSecret!B")
    db.commit()
    assert not vault_password_available(db,person)
    # Reset explicitly repopulates vault.
    assert vault_store(db,person,"DifferentSecret!B")
    db.commit()
    assert vault_password_available(db,person)

app=FastAPI()
app.add_middleware(SessionMiddleware,secret_key="test-cookie-key")
app.include_router(router)
def get_test_db():
    with Session() as db:
        yield db
app.dependency_overrides[get_db]=get_test_db
@app.get("/test/login/{user_id}")
def login(user_id:int, request: __import__("fastapi").Request):
    request.session["admin_id"]=user_id
    return {"csrf": vault_csrf_token(request)}
client=TestClient(app,base_url="https://testserver")
endpoint=f"/admin-users/{person_id}/password/reveal"
http_client=TestClient(app,base_url="http://testserver")
assert http_client.post(endpoint, headers=ORIGIN, data={"csrf_token":"bad","reauth_password":"test"}).status_code==403
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":"bad","reauth_password":"test"}).status_code==403

r=client.get(f"/test/login/{owner_id}")
assert r.status_code==200
csrf=r.json()["csrf"]
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"}).json()["password"]=="DifferentSecret!B"
response=client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"})
assert response.headers.get("cache-control","").startswith("no-store")
assert "NewSecret!A" not in response.text
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":"bad","reauth_password":"SuperRoot!A"}).status_code==403
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"WRONG"}).status_code==403
assert client.get(endpoint).status_code==405
client.get(f"/test/login/{person_id}")
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"ViewerPass!A"}).status_code==403

# Delegated reveal without edit: a Super Admin can grant reveal to a custom role,
# but permission never overrides target hierarchy or account status.
with Session() as db:
    delegated_role = Role(
        name="Credential Auditor",
        permissions=json.dumps(["panel_users.view", "panel_users.password_reveal"]),
    )
    low_role = Role(name="Read Only", permissions=json.dumps(["panel_users.view"]))
    db.add_all((delegated_role, low_role))
    db.flush()
    delegated = AdminUser(username="vaultauditor", password_hash=hash_password("AuditorSecret!1"),
                          role=delegated_role, is_active=True, main_panel_access=True)
    low = AdminUser(username="readonlytarget", password_hash=hash_password("LowSecret!2"),
                    role=low_role, is_active=True, main_panel_access=True)
    db.add_all((delegated, low))
    db.flush()
    assert vault_store(db, low, "LowSecret!2")
    db.commit()
    delegate_id, low_id, delegated_role_id = delegated.id, low.id, delegated_role.id

delegate_csrf = client.get(f"/test/login/{delegate_id}").json()["csrf"]
low_endpoint = f"/admin-users/{low_id}/password/reveal"
delegated_payload = {"csrf_token": delegate_csrf, "reauth_password": "AuditorSecret!1"}
response = client.post(low_endpoint, headers=ORIGIN, data=delegated_payload)
assert response.status_code == 200, response.text
assert response.json()["password"] == "LowSecret!2"
assert client.post(endpoint, headers=ORIGIN, data=delegated_payload).status_code == 403
assert client.post(f"/admin-users/{owner_id}/password/reveal", headers=ORIGIN,
                   data=delegated_payload).status_code == 403
with Session() as db:
    delegated_role = db.get(Role, delegated_role_id)
    delegated_role.permissions = json.dumps(["panel_users.view"])
    db.commit()
assert client.post(low_endpoint, headers=ORIGIN, data=delegated_payload).status_code == 403
with Session() as db:
    delegated_role = db.get(Role, delegated_role_id)
    delegated_role.permissions = json.dumps(["panel_users.view", "panel_users.password_reveal"])
    delegated = db.get(AdminUser, delegate_id)
    delegated.main_panel_access = False
    db.commit()
assert client.post(low_endpoint, headers=ORIGIN, data=delegated_payload).status_code == 403

# Five incorrect attempts persist across fresh DB connections (and workers).
# Reset earlier failures explicitly, so the lockout test is independent of the
# preceding authorization/revocation cases.
from app.panel_password_vault import _locked, _clear_failures
from app.panel_login_guard import (record_ip_failure, ip_locked, clear_ip_failures,
                                   LOGIN_LOCK_SECONDS, FAILED_ATTEMPTS)
assert LOGIN_LOCK_SECONDS == 10800 and FAILED_ATTEMPTS == 5
with Session() as db:
    _clear_failures(db.get(AdminUser, owner_id))
# Disabling delegated Main access may revoke that session/CSRF token.
csrf = client.get(f"/test/login/{owner_id}").json()["csrf"]
for _ in range(5):
    response = client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"wrong"})
    assert response.status_code==403, (response.status_code, response.text)
response = client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"})
assert response.status_code==429, (response.status_code, response.text)
with Session() as db:
    assert _locked(db.get(AdminUser,owner_id))

# Locked Users console concerns real Main/Node Panel login, NOT saved-password reveal.
# The same guard is used by both Main login entrypoints.
blocked_ip = "198.51.100.41"
other_ip = "198.51.100.42"
assert not ip_locked(blocked_ip)
for attempt in range(5):
    assert record_ip_failure(blocked_ip) is (attempt == 4)
assert ip_locked(blocked_ip) and not ip_locked(other_ip)
list_url = "/admin-users/login-security/blocked-ips"
unlock_url = f"/admin-users/login-security/blocked-ips/{blocked_ip}/unlock"
client.get(f"/test/login/{delegate_id}")
assert client.get(list_url).status_code == 403
assert client.post(unlock_url, headers=ORIGIN,
                   data={"csrf_token": csrf, "reauth_password": "AuditorSecret!1"}).status_code == 403
csrf = client.get(f"/test/login/{owner_id}").json()["csrf"]
response = client.get(list_url)
assert response.status_code == 200, response.text
assert response.headers.get("cache-control","").startswith("no-store")
assert response.json()["count"] == 1
assert response.json()["blocked_ips"][0]["ip"] == blocked_ip
assert response.json()["blocked_ips"][0]["failed_attempts"] == 5
remaining = response.json()["blocked_ips"][0]["remaining_seconds"]
assert LOGIN_LOCK_SECONDS - 30 <= remaining <= LOGIN_LOCK_SECONDS
assert client.get(unlock_url).status_code == 405
assert client.post(unlock_url, headers=ORIGIN,
                   data={"csrf_token": "wrong", "reauth_password": "SuperRoot!A"}).status_code == 403
assert client.post(unlock_url, headers={"Origin":"https://evil.example"},
                   data={"csrf_token": csrf, "reauth_password": "SuperRoot!A"}).status_code == 403
assert client.post(unlock_url, headers=ORIGIN,
                   data={"csrf_token": csrf, "reauth_password": "wrong"}).status_code == 403
assert client.get(list_url).json()["count"] == 1
response = client.post(unlock_url, headers=ORIGIN,
                       data={"csrf_token": csrf, "reauth_password": "SuperRoot!A"})
assert response.status_code == 200, response.text
assert response.json()["unblocked_ip"] == blocked_ip
assert client.get(list_url).json()["count"] == 0
assert client.post(unlock_url, headers=ORIGIN,
                   data={"csrf_token": csrf, "reauth_password": "SuperRoot!A"}).status_code == 409
assert not ip_locked(blocked_ip)
# Unlocking Panel login never wipes the unrelated saved-password reveal lock.
with Session() as db:
    assert _locked(db.get(AdminUser, owner_id))
# Independent unlock throttle: a reveal-locked Super Admin can still unlock
# after valid reauth, but brute force attempts against unlock itself are capped.
from app.panel_password_vault import _locked as guard_locked, _clear_failures as guard_clear
for _ in range(5):
    assert client.post(unlock_url, headers=ORIGIN,
                       data={"csrf_token": csrf, "reauth_password": "wrong"}).status_code == 403
assert client.post(unlock_url, headers=ORIGIN,
                   data={"csrf_token": csrf, "reauth_password": "SuperRoot!A"}).status_code == 429
with Session() as db:
    assert guard_locked(db.get(AdminUser, owner_id), purpose="unlock")
    guard_clear(db.get(AdminUser, owner_id), purpose="unlock")
assert client.post(endpoint,headers={"Origin":"https://evil.example"},data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"}).status_code==403

with Session() as db:
    vault_discard(db,person_id)
    db.commit()
    assert db.get(PanelUserCredentialVault,person_id) is None
GUARD_TEMP.cleanup()
print("PASS: vault encryption, origin/CSRF, RBAC delegation/revocation, hierarchy, cross-worker throttle and discard")
