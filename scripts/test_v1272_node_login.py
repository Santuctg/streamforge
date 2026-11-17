#!/usr/bin/env python3
"""Node login UX v12.72: HTML rendering, aliases, safe escaping and old auth contract."""
from __future__ import annotations
import ast
import html
import json
from pathlib import Path
from types import SimpleNamespace

root=Path(__file__).resolve().parents[1]
source=(root/"node_agent/app.py").read_text(encoding="utf-8")
compile(source,"node_agent/app.py","exec")
tree=ast.parse(source)
func=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=="node_panel_login_page")
assert "STREAMFORGE_V1272_NODE_LOGIN_DARK_RESPONSIVE" in ast.get_source_segment(source,func)
func.decorator_list = []  # Test the route body without registering a server endpoint.
assert 'manager.live_panel_authorize(\n        username, action="login", password=password, client_ip=browser_ip' in source
assert 'Invalid username or password' in source
# v13.53: extract the real device theme helpers the login page now shares.
_helpers={}
for _node in tree.body:
    if isinstance(_node,ast.FunctionDef) and _node.name in ("_node_device_theme_script","_node_theme_toggle_button"):
        exec(compile(ast.fix_missing_locations(ast.Module(body=[_node],type_ignores=[])),"node_agent/app.py","exec"),_helpers)
    elif isinstance(_node,ast.Assign) and any(getattr(t,"id",None)=="_NODE_THEME_TOGGLE_CSS" for t in _node.targets):
        exec(compile(ast.fix_missing_locations(ast.Module(body=[_node],type_ignores=[])),"node_agent/app.py","exec"),_helpers)
_node_device_theme_script=_helpers["_node_device_theme_script"]
_node_theme_toggle_button=_helpers["_node_theme_toggle_button"]
_NODE_THEME_TOGGLE_CSS=_helpers["_NODE_THEME_TOGGLE_CSS"]
def render(prefix="/tslive",name="TS Live",error=""):
    node=SimpleNamespace(
        node_name=name,node_logo_url="/node-logos/logo.webp",
        access_slug="tslive",panel_connected=lambda force=False: True,
    )
    context={
        "Request": object, "HTMLResponse": lambda body: SimpleNamespace(body=body),
        "_CURRENT_PANEL_PREFIX": SimpleNamespace(get=lambda:prefix),
        "_session_user":lambda r:None,
        "manager":node,"html":html,"json":json,
        "_node_favicon_link":lambda prefix:'<link rel="icon" href="/favicon.ico">',
        # v13.53: the login page now shares the real device theme helpers.
        "_node_device_theme_script":lambda:_node_device_theme_script(),
        "_node_theme_toggle_button":lambda:_node_theme_toggle_button(),
        "_NODE_THEME_TOGGLE_CSS":_NODE_THEME_TOGGLE_CSS,
    }
    namespace={}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[])),"node_agent/app.py","exec"),context,namespace)
    # Function globals are context, not namespace.
    return namespace["node_panel_login_page"](SimpleNamespace(),error).body

page=render()
for value in (
    'STREAMFORGE_NODE_FIXED_LOGIN_ADDRESS_BAR_V3059',
    'action="/tslive/login"','src="/tslive/logo"',
    '<label for="node-login-user">Username</label>',
    '<label for="node-login-password">Password</label>',
    'autocomplete="username"','autocomplete="current-password"',
    'background:#0b141e!important','color-scheme:dark',
    '-webkit-autofill','-webkit-text-fill-color:',
    'width:min(440px,100%)','@media(max-width:480px)',
    'type="submit">Sign in</button>',
):
    assert value in page,value
# v13.53: white surfaces are now legal, but only inside the scoped light block;
# the dark base card keeps its dark gradient and the dark input stays dark.
assert 'html[data-theme="light"] .login-card{border-color:#c7d4df!important;background:linear-gradient(180deg,#ffffff,#f4f8fb)!important' in page
assert 'background:radial-gradient(circle at 50% 18%,#142636 0,#0b111a 43%,#0b0f14 78%)' in page
assert 'html[data-theme="light"] body{' in page and 'data-theme-toggle' in page
assert 'STREAMFORGE_NODE_LOGIN_DEVICE_THEME_LIGHT_V1353' in page
assert 'STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344' in page
assert 'width:min(360px,90vw)' not in page
assert render(prefix="").count('action="/login"')==1
assert render(prefix=None).count('action="/tslive/login"')==1
evil=render(name='<img src=x onerror=alert(1)>',error='<svg/onload=alert(1)>')
assert '&lt;img' in evil and '&lt;svg' in evil
assert '<svg/onload=' not in evil
assert 'role="alert"' in evil or 'class="error"' in evil
print("PASS: v12.72 responsive Node login, dark autofill, accessible fields, aliases, escaping and unchanged live authentication")
