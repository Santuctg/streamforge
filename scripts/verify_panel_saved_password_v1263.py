#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
main=(ROOT/"app/main.py").read_text()
db=(ROOT/"app/db.py").read_text()
models=(ROOT/"app/models.py").read_text()
tpl=(ROOT/"app/templates/admin_users.html").read_text()
vault=(ROOT/"app/panel_password_vault.py").read_text() if (ROOT/"app/panel_password_vault.py").exists() else ""
assert (ROOT/"VERSION").read_text().strip()=="12.63"
assert (ROOT/"node_agent/VERSION").read_text().strip()=="12.63"
assert "STREAMFORGE_PANEL_USER_SAVED_PASSWORD_V1263" in main
assert "STREAMFORGE_PANEL_USER_SAVED_PASSWORD_V1263" in tpl
assert "password_vault_enc" in models and "password_vault_enc" in db
assert "encrypt_panel_password" in vault and "decrypt_panel_password" in vault
assert 'def panel_user_reveal_password(' in main
assert 'admin_is_super_admin(actor)' in main
assert 'verify_password(admin_password, actor.password_hash)' in main
assert 'password_vault_enc = encrypt_panel_password(password)' in main
assert 'panel_user.password_vault_enc = encrypt_panel_password(password)' in main
assert 'Cache-Control' in main
assert "Show saved password" in tpl
assert "No saved password" in tpl
assert main.count("admin.password_vault_enc = encrypt_panel_password(new_password)") >= 2, "Main+Node self resets must escrow"
assert "password_vault_enc=encrypt_panel_password(password)" in main
assert "panel_user.password_vault_enc = encrypt_panel_password(password)" in main
assert "5:" not in main or "_VAULT_FAILED_ATTEMPTS" in main
assert "password_hash" not in tpl
print("PASS: v12.63 Panel User encrypted saved-password vault contract")
