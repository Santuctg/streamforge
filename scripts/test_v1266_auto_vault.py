#!/usr/bin/env python3
"""v12.66 managed-credential auto-retention Main and Remote Node contracts."""
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.66":
    print("SKIP: v12.66-only regression check (later releases changed the Node notice)")
    raise SystemExit(0)
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
original = (ROOT/"app/main.py").read_text(encoding="utf-8")
main64 = runpy.run_path(str(ROOT/"scripts/apply_v1264_ip_lock.py"))["transform"](original)
main65 = runpy.run_path(str(ROOT/"scripts/apply_v1265_ip_origin.py"))["transform"](main64)
patch = runpy.run_path(str(ROOT/"scripts/apply_v1266_auto_vault.py"))["transform"]
main = patch(main65)
assert main != main65
assert patch(main) == main
compile(main, "app/main.py", "exec")
assert "STREAMFORGE_V1266_AUTO_VAULT" in main
assert main.count('vault_remember: Optional[str] = Form(None)') == 0
assert main.count('if as_bool(vault_remember)') == 0
assert main.count('if not vault_store(db, panel_user, password):') == 2
assert main.count('if not vault_store(db, admin, new_password):') == 2
assert 'payload.get("managed_password_notice") is True' in main
assert 'vault_discard(db, admin.id)' in main  # legacy Node not silently captured
assert 'raise HTTPException(503, "Encrypted password vault unavailable' in main
assert "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" in main
assert "ip_locked(source_ip)" in main

form = (ROOT/"app/templates/admin_user_form.html").read_text(encoding="utf-8")
account = (ROOT/"app/templates/account_password.html").read_text(encoding="utf-8")
listing = (ROOT/"app/templates/admin_users.html").read_text(encoding="utf-8")
node = (ROOT/"node_agent/app.py").read_text(encoding="utf-8")
assert "data-vault-password-consent" not in form
assert 'name="vault_remember"' not in form
assert "Managed password retention" in form
assert "Managed password retention" in account
assert "Managed password retention:" in node
assert 'payload["managed_password_notice"] = True' in node
assert "STREAMFORGE_V1266_NODE_VAULT_DISCLOSURE" in node
assert "Show saved password" in listing
assert "item.id in vault_saved_ids" in listing
assert "Triggered by server" in listing
compile(node,"node_agent/app.py","exec")
print("PASS: automatic Main/Node vault escrow, explicit notice, legacy compatibility and syntax")
