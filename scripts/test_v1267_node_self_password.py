#!/usr/bin/env python3
"""Regression for remote Node self-password update on least-privilege Panel User.

The personal route authenticates a live session, but MUST NOT require
channels.view, panel_users.edit or a Super Admin wildcard. All resource
routes continue to enforce their named RBAC permission.
"""
from __future__ import annotations
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
tree = ast.parse(src)
defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
assert src.count('require_node_panel_user(request, "")') == 2, "Both self-password GET and POST must require only a session"
assert 'STREAMFORGE_V1267_SELF_PASSWORD_PERMISSION_FIX' in src
assert 'if permission and not _has_panel_permission(user, permission):' in src

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

class PanelAccessUser:
    def __init__(self, permissions):
        self.permissions = permissions

class Request: pass

ns = {
    "HTTPException": HTTPException,
    "PanelAccessUser": PanelAccessUser,
    "Request": Request,
    "MAIN_PANEL_DISCONNECTED_DETAIL": "Main unavailable",
}
selected = [defs["_has_panel_permission"], defs["require_node_panel_user"]]
module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
exec(compile(module, "node_agent/app.py", "exec"), ns)

user = PanelAccessUser(set())
ns["_session_claims"] = lambda request: ("ownuser", "version1")
ns["_cached_panel_session_authorize"] = lambda *_: ("ok", user, "")
assert ns["require_node_panel_user"](Request(), "") is user
try:
    ns["require_node_panel_user"](Request())
    raise AssertionError("No channels.view should be denied")
except HTTPException as e:
    assert e.status_code == 403 and e.detail == "Permission denied"
try:
    ns["require_node_panel_user"](Request(), "panel_users.edit")
    raise AssertionError("No panel_users.edit should be denied")
except HTTPException as e:
    assert e.status_code == 403
ns["_session_claims"] = lambda request: None
try:
    ns["require_node_panel_user"](Request(), "")
    raise AssertionError("Missing session should be rejected")
except HTTPException as e:
    assert e.status_code == 401
ns["_session_claims"] = lambda request: ("ownuser", "version1")
ns["_cached_panel_session_authorize"] = lambda *_: ("denied", None, "revoked")
try:
    ns["require_node_panel_user"](Request(), "")
    raise AssertionError("Revoked login should be rejected")
except HTTPException as e:
    assert e.status_code == 401
ns["_cached_panel_session_authorize"] = lambda *_: ("unavailable", None, "")
try:
    ns["require_node_panel_user"](Request(), "")
    raise AssertionError("Main unavailable must fail closed")
except HTTPException as e:
    assert e.status_code == 503
ns["_cached_panel_session_authorize"] = lambda *_: ("ok", PanelAccessUser({"channels.view"}), "")
assert ns["require_node_panel_user"](Request(), "channels.view")
try:
    ns["require_node_panel_user"](Request(), "panel_users.edit")
    raise AssertionError("Role scope should still be enforced")
except HTTPException as e:
    assert e.status_code == 403

print("PASS: Node self password requires active session, not edit permission; resource RBAC and fail-closed unchanged")
