#!/usr/bin/env python3
"""Isolated execution of the production reveal handler: RBAC, Origin, reauth, audit.
Never uses real credentials. Production function is extracted from source AST.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading
import time

source=(Path(__file__).resolve().parents[1]/"app/main.py").read_text()
tree=ast.parse(source)
fn=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="panel_user_reveal_password")
fn.decorator_list=[]
fn.returns=None
for arg in fn.args.args:
    arg.annotation=None
fn.args.defaults=[]
fake_module=ast.Module(body=[fn],type_ignores=[])
ast.fix_missing_locations(fake_module)

class MockError(Exception):
    def __init__(self,status_code):
        self.status_code=status_code

class MockResponse:
    def __init__(self,content,status_code,headers):
        self.data=content
        self.status=status_code
        self.headers=headers

class FakeDB:
    def __init__(self,target):
        self.target=target
    def get(self,cls,id):
        return self.target if id==3 else None

class FakeRequest:
    def __init__(self,origin="https://example.test",host="example.test"):
        self.headers={"origin":origin,"host":host}
        self.base_url="https://example.test/"

actor=SimpleNamespace(id=1,username="superadmin",password_hash="hashed_admin")
target=SimpleNamespace(id=3,password_vault_enc="cipher",password_hash="hashed_user")
events=[]
context={
    "JSONResponse":MockResponse, "HTTPException":MockError,
    "enforce_permission": lambda request,db,permission:actor,
    "admin_is_super_admin": lambda a:a is actor,
    "time":time, "threading":threading,
    "_VAULT_ATTEMPT_LOCK":threading.RLock(), "_VAULT_FAILED_ATTEMPTS":{},
    "AdminUser":object,
    "verify_password":lambda text,hash: (text=="synthetic-admin-pass" if hash=="hashed_admin" else text=="synthetic-user-pass"),
    "decrypt_panel_password":lambda enc:"synthetic-user-pass" if enc=="cipher" else None,
    "log_event":lambda message,**kw:events.append((message,kw)),
}
exec(compile(fake_module, "app/main.py", "exec"),context)
reveal=context["panel_user_reveal_password"]
def call(pwd="synthetic-admin-pass",request=None,db=None):
    return reveal(3,request or FakeRequest(),pwd,db or FakeDB(target))

r=call()
assert r.status==200 and r.data["password"]=="synthetic-user-pass"
assert r.headers["Cache-Control"].startswith("no-store")
assert "synthetic-user-pass" not in str(events)
assert call(request=FakeRequest("https://evil.example")).status==403
assert call(request=FakeRequest("http://example.test")).status==403
assert call(request=FakeRequest(origin="")).status==403
assert call(db=FakeDB(None)).status==404
assert call(db=FakeDB(SimpleNamespace(id=3,password_vault_enc=None,password_hash="hashed_user"))).status==404
assert call("wrong-admin").status==403
assert "synthetic-admin-pass" not in str(events)
context["admin_is_super_admin"]=lambda actor:False
try:
    call()
    raise AssertionError("non-Super Admin was not denied")
except MockError as e:
    assert e.status_code==404
context["admin_is_super_admin"]=lambda actor:True
context["decrypt_panel_password"]=lambda enc:"wrong-escrow"
assert call().status==409
context["decrypt_panel_password"]=lambda enc:"synthetic-user-pass"
for n in range(4):
    assert call("wrong-again").status==403
assert call("synthetic-admin-pass").status==429
print("PASS: v12.63 backend reveal handler RBAC, reauthentication, no-cache, origin, rate-limit and audit secrecy")
