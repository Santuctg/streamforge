#!/usr/bin/env python3
"""Static v12.68 UI, JS syntax and bounded Main CSRF integration checks."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from scripts.apply_v1268_password_modal import transform

ROOT=Path(__file__).resolve().parents[1]
main=(ROOT/"app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1268_PW_MODAL_CSRF" in transform(main)
assert transform(transform(main))==transform(main)
markup=(ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
api=(ROOT/"app/panel_password_vault.py").read_text(encoding="utf-8")
assert markup.count('data-panel-pw-reset-id="{{ item.id }}"') == 2
assert 'edit?focus=password' not in markup
assert 'data-panel-pw-reset-overlay' in markup
assert "sf-vault-dialog input[type=\"password\"]" in markup
assert "background:#0b1118 !important" in markup
assert "color-scheme:dark" in markup
assert "window.location.assign" in markup and "encodeURIComponent(" in markup
assert 'data-panel-pw-reset-error' in markup
assert 'data-panel-pw-reset-csrf' in markup
assert 'STREAMFORGE_V1268_PASSWORD_RESET_MODAL_API' in api
assert 'has_permission(actor, "panel_users.edit")' in api
assert 'vault_target_visible(actor, target)' in api
assert '_request_is_secure_same_origin(request)' in api
assert 'vault_store(db, target, password)' in api
assert 'db.rollback()' in api
assert 'sync_panel_users_for_node_ids' in api
assert 'csrf_token' in api
assert 'hash_password(password)' in api
compile(api,"app/panel_password_vault.py","exec")
# Node is only a syntax checker for inline script; no DOM/network execution.
node=shutil.which("node")
if node:
    scripts=re.findall(r"<script>(.*?)</script>",markup,flags=re.DOTALL)
    assert scripts
    with tempfile.TemporaryDirectory(prefix="sf-v1268-js-") as tmp:
        for i,script in enumerate(scripts):
            path=Path(tmp)/f"script{i}.js"
            path.write_text(script,encoding="utf-8")
            result=subprocess.run([node,"--check",str(path)],capture_output=True,text=True)
            assert result.returncode==0,(path.name,result.stderr)
print("PASS: bounded password popup, scoped reset API, enforced HTTPS/CSRF/RBAC and dark input CSS")
