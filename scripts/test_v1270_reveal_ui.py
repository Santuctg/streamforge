#!/usr/bin/env python3
"""v12.70 Node disclosure placement and exact 10s Show saved password behavior."""
from __future__ import annotations
from pathlib import Path
import re
import subprocess
import shutil
ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
node=(ROOT/"node_agent/app.py").read_text(encoding="utf-8")
assert 'STREAMFORGE_V1270_NODE_PASSWORD_FORM_CLEANUP' in node
assert 'New passwords are encrypted on Main and can be viewed by authorized administrators.' not in node
assert 'As a managed account, authorized administrators can recover changed passwords.' in node
assert 'payload["managed_password_notice"] = True' in node
assert 'STREAMFORGE_V1270_REVEAL_AUTH_STEP' in html
assert 'data-vault-reauth-step' in html
assert 'authStep.hidden = true;' in html
assert 'authStep.hidden = false;' in html
assert 'confirm.hidden = true;' in html and 'confirm.hidden = false;' in html
assert 'const expiresAt = performance.now() + 10000;' in html
assert 'Math.ceil((expiresAt - performance.now()) / 1000)' in html
assert 'hideTimer = setTimeout(wipe, 10000);' in html
assert 'let remaining = 15;' not in html
assert 'STREAMFORGE_V1269_VAULT_REVEAL_TEXT_DARK' in html
assert 'input[data-vault-secret-value]' in html
assert 'secretValue.value = \'\';' in html
assert 'window.addEventListener(\'blur\', wipe);' in html
assert 'role="dialog"' in html and 'aria-modal="true"' in html
assert 'if (seq !== requestSeq || overlay.hidden) return;' in html
compile(node,"node_agent/app.py","exec")
nodebin=shutil.which("node")
if nodebin:
    result=subprocess.run([nodebin,str(ROOT/"scripts/test_v1270_reveal_browser.js")],
                          cwd=ROOT,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
print("PASS: v12.70 Node form disclosure, 10s saved-secret countdown, hidden reauth and wipe")
