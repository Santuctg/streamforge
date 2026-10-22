#!/usr/bin/env python3
"""v12.71 Main login autofill and standalone Blocked IP card regression."""
from __future__ import annotations
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
login=(ROOT/"app/templates/login.html").read_text(encoding="utf-8")
users=(ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
assert "STREAMFORGE_V1271_LOGIN_AUTOFILL_DARK" in login
assert ".login-card input:autofill" in login
assert ".login-card input:is(:-webkit-autofill" in login
assert "-webkit-text-fill-color:#edf4fb !important" in login
assert "box-shadow:0 0 0 1000px #0b1118 inset" in login
assert "color-scheme:dark" in login
assert 'name="username" autocomplete="username"' in login
assert 'name="password" autocomplete="current-password"' in login
assert 'action="/login"' in login
assert "STREAMFORGE_V1271_BLOCKED_IP_STANDALONE_CARD" in users
assert '<section class="panel admin-users-panel">' in users
assert '<section class="panel sf-login-guard-card" data-vault-lock-console' in users
assert 'sf-guard-policy' in users
for label in ("5 incorrect logins","15 minutes","After 3 hours","Main + Remote Nodes"):
    assert label in users,label
assert "Triggered by server" in users
assert "data-vault-lock-status" in users
assert "data-vault-lock-refresh" in users
assert "data-vault-unlock-password" in users
assert "data-vault-lock-csrf" in users
assert "/admin-users/login-security/blocked-ips" in users
assert "data-vault-unlock-confirm" in users
assert "IP addresses are currently blocked" in users
assert "sf-guard-empty" in users
assert "@media(max-width:850px)" in users

class Sections(HTMLParser):
    def __init__(self):
        super().__init__();self.stack=[];self.parents=[];self.opened=0;self.closed=0
    def handle_starttag(self,tag,attrs):
        if tag=="section":
            kind=dict(attrs).get("class","")
            if "admin-users-panel" in kind or "sf-login-guard-card" in kind:
                self.parents.append((kind, tuple(self.stack)))
            self.stack.append(kind); self.opened+=1
    def handle_endtag(self,tag):
        if tag=="section":
            assert self.stack,"Unexpected closing section"
            self.stack.pop();self.closed+=1

parser=Sections()
parser.feed(users)
assert parser.opened==parser.closed and not parser.stack
assert len(parser.parents)==2,parser.parents
assert parser.parents[0][1]==(),parser.parents
assert parser.parents[1][1]==(),parser.parents
assert parser.parents[0][0]!=parser.parents[1][0]

# Ensure login/Panel Users scripts continue parsing; no auth/reveal behavior changes.
node=shutil.which("node")
if node:
    with tempfile.TemporaryDirectory(prefix="sf-v1271-js-") as tmp:
        for name,html in (("login",login),("users",users)):
            for i,script in enumerate(re.findall(r"<script>(.*?)</script>",html,re.DOTALL)):
                file=Path(tmp)/f"{name}-{i}.js"
                file.write_text(script,encoding="utf-8")
                result=subprocess.run([node,"--check",str(file)],capture_output=True,text=True)
                assert result.returncode==0,result.stderr
# Existing reveal timeout, 2FA reauthentication, and IP-unlock hooks remain intact.
for fragment in ("const expiresAt = performance.now() + 10000;",
                 "authStep.hidden = true;", "hideTimer = setTimeout(wipe, 10000);",
                 "data-vault-unlock-confirm", "data-panel-pw-reset-form"):
    assert fragment in users,fragment
print("PASS: v12.71 Chrome autofill dark login, sibling Blocked IP card, mobile layout and unchanged security hooks")
