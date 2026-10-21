#!/usr/bin/env python3
"""Functional security + UI contracts for v12.68 Panel Users password modal."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware
from app.auth import hash_password, verify_password
from app.db import Base, get_db
from app.models import AdminUser, PanelUserCredentialVault, Role
from app.panel_password_vault import (
    router, vault_csrf_token, vault_password_available, VAULT_KEY_ENV,
)

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
source = (ROOT/"app/panel_password_vault.py").read_text(encoding="utf-8")
patcher = (ROOT/"scripts/apply_v1268_password_modal.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1268_PASSWORD_RESET_MODAL_API" in source
assert "STREAMFORGE_V1268_PW_MODAL_CSRF" in patcher
assert 'edit?focus=password' not in html
assert html.count('data-panel-pw-reset-id="{{ item.id }}"') == 2
assert 'data-panel-pw-reset-form' in html
assert 'data-panel-pw-reset-overlay' in html
assert '/password/reset' in html and 'data-panel-pw-reset-csrf' in html
assert "sf-vault-dialog input[type=\"password\"]" in html
assert "background:#0b1118 !important" in html
assert "color-scheme:dark" in html
assert "autocomplete=\"new-password\"" in html

from scripts.apply_v1268_password_modal import transform
base=(ROOT/"app/main.py").read_text(encoding="utf-8")
updated=transform(base)
assert transform(updated)==updated
assert "has_permission(actor, \"panel_users.edit\")" in updated
compile(updated, "app/main.py", "exec")

temp=tempfile.TemporaryDirectory(prefix="sf-reset-modal-v1268-")
os.environ[VAULT_KEY_ENV]=Fernet.generate_key().decode()
engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
Base.metadata.create_all(engine)
Session=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
with Session() as db:
    super_role=Role(name="Root",permissions=json.dumps(["*"]))
    edit_role=Role(name="Editor",permissions=json.dumps(["panel_users.view","panel_users.edit"]))
    low_role=Role(name="Low",permissions=json.dumps(["panel_users.view"]))
    db.add_all([super_role,edit_role,low_role])
    db.flush()
    super_user=AdminUser(username="rootuser",password_hash=hash_password("Root!Pass1234"),role=super_role,is_active=True,main_panel_access=True)
    editor=AdminUser(username="edituser",password_hash=hash_password("Editor!Pass1234"),role=edit_role,is_active=True,main_panel_access=True)
    target=AdminUser(username="target",password_hash=hash_password("Original!Pass9"),role=low_role,is_active=True,main_panel_access=True)
    db.add_all([super_user,editor,target])
    db.commit()
    ids={"root":super_user.id,"editor":editor.id,"target":target.id}
app=FastAPI()
app.add_middleware(SessionMiddleware,secret_key="test-v1268-key")
app.include_router(router)
def get_test_db():
    with Session() as db: yield db
app.dependency_overrides[get_db]=get_test_db
@app.get("/test/login/{user_id}")
def login(user_id:int,request:Request):
    request.session["admin_id"]=user_id
    return {"csrf":vault_csrf_token(request)}
client=TestClient(app,base_url="https://testserver")
headers={"Origin":"https://testserver","Sec-Fetch-Site":"same-origin"}
endpoint=f"/admin-users/{ids['target']}/password/reset"
csrf=client.get(f"/test/login/{ids['editor']}").json()["csrf"]
body={"csrf_token":csrf,"password":"NewManagedSecret!8","password_confirm":"NewManagedSecret!8"}
response=client.post(endpoint,headers=headers,data=body)
assert response.status_code==200,response.text
assert response.json()["ok"] is True
assert response.headers["cache-control"].startswith("no-store")
assert "NewManagedSecret!8" not in response.text
with Session() as db:
    user=db.get(AdminUser,ids["target"])
    row=db.get(PanelUserCredentialVault,user.id)
    assert verify_password("NewManagedSecret!8", user.password_hash)
    assert vault_password_available(db,user)
    assert row is not None and "NewManagedSecret!8" not in row.ciphertext

assert client.post(endpoint,headers=headers,data={**body,"csrf_token":"wrong"}).status_code==403
assert client.post(endpoint,headers=headers,data={**body,"password_confirm":"nope"}).status_code==400
assert client.post(f"/admin-users/{ids['root']}/password/reset",headers=headers,data=body).status_code==403
assert client.post(endpoint,headers={"Origin":"https://evil.test","Sec-Fetch-Site":"cross-site"},data=body).status_code==403
assert client.get(endpoint,headers=headers).status_code==405
csrf=client.get(f"/test/login/{ids['target']}").json()["csrf"]
assert client.post(endpoint,headers=headers,data={**body,"csrf_token":csrf}).status_code==403
csrf=client.get(f"/test/login/{ids['root']}").json()["csrf"]
assert client.post(endpoint,headers=headers,data={**body,"csrf_token":csrf,"password":"short","password_confirm":"short"}).status_code==400
del os.environ[VAULT_KEY_ENV]
assert client.post(endpoint,headers=headers,data={**body,"csrf_token":csrf,"password":"SomeOther!123","password_confirm":"SomeOther!123"}).status_code==503
with Session() as db:
    user=db.get(AdminUser,ids["target"])
    assert verify_password("NewManagedSecret!8",user.password_hash)
temp.cleanup()
print("PASS: v12.68 popup UI, RBAC, hierarchy, CSRF, HTTPS origin, atomic vault and dark password inputs")
