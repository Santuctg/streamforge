#!/usr/bin/env python3
"""Functional crypto, reauthentication, CSRF and stale-hash tests for Main vault."""
from __future__ import annotations
import os
import json
import tempfile
from contextlib import contextmanager
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

# Five incorrect attempts persist across fresh DB connections (and workers).
client.get(f"/test/login/{owner_id}")
# One failed attempt was already recorded in the earlier reauthentication test.
for _ in range(4):
    assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"wrong"}).status_code==403
assert client.post(endpoint,headers=ORIGIN,data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"}).status_code==429
from app.panel_password_vault import _locked
with Session() as db:
    assert _locked(db.get(AdminUser,owner_id))
assert client.post(endpoint,headers={"Origin":"https://evil.example"},data={"csrf_token":csrf,"reauth_password":"SuperRoot!A"}).status_code==403

with Session() as db:
    vault_discard(db,person_id)
    db.commit()
    assert db.get(PanelUserCredentialVault,person_id) is None
GUARD_TEMP.cleanup()
print("PASS: vault encryption, disabled key, origin/CSRF, shared rate-limit, hash drift and discard")
