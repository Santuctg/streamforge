#!/usr/bin/env python3
"""v12.69 secure saved-password Show/Hide and compact Node notice regression."""
from __future__ import annotations
import re
import subprocess
import shutil
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
node=(ROOT/"node_agent/app.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1269_VAULT_REVEAL_TEXT_DARK" in html
assert "input[data-vault-secret-value]" in html
assert "secretValue.type = isVisible ? 'password' : 'text';" in html
assert html.count('.sf-vault-dialog input[data-vault-secret-value]') >= 3
assert "background:#0b1118 !important" in html
assert "color:var(--text,#edf4fb) !important" in html
assert "color-scheme:dark" in html
assert "Show saved password" in html and "Reveal for 10 seconds" in html
assert "STREAMFORGE_V1269_COMPACT_NODE_PASSWORD_NOTICE" in node
assert "Managed password retention:" not in node
assert "New passwords are encrypted on Main and can be viewed by authorized administrators." in node
assert 'payload["managed_password_notice"] = True' in node
assert 'require_node_panel_user(request, "")' in node
assert 'if permission and not _has_panel_permission(user, permission):' in node
assert '<label style="grid-column:1 / -1">Confirm new password' in node
compile(node, "node_agent/app.py","exec")
nodebin=shutil.which("node")
if nodebin:
    js=re.findall(r"<script>(.*?)</script>",html,flags=re.DOTALL)
    assert js
    with tempfile.TemporaryDirectory(prefix="sf-v1269-js-") as temp:
        for idx,s in enumerate(js):
            p=Path(temp)/f"script-{idx}.js"
            p.write_text(s,encoding="utf-8")
            result=subprocess.run([nodebin,"--check",str(p)],capture_output=True,text=True)
            assert result.returncode==0,result.stderr
print("PASS: Show/Hide maintains dark CSS, compact Node disclosure, Node session+escrow unchanged")
