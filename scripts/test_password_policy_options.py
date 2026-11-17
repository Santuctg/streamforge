#!/usr/bin/env python3
"""Password policy options contract (v13.62).

- The minimum password length is operator-configurable (Settings dropdown,
  default 12, floor 6) and enforced on Panel users, viewer (Xtream) passwords
  and every Remote Node panel (client-side forms mirror the configured value).
- Operators can force a per-user password change; a forced user reaches only
  the password page on Main and every Node panel.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
auth = (ROOT / "app" / "auth.py").read_text(encoding="utf-8")
models = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
node_manager = (ROOT / "app" / "node_manager.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent" / "app.py").read_text(encoding="utf-8")
branding = (ROOT / "app" / "templates" / "system_branding.html").read_text(encoding="utf-8")
admin_users = (ROOT / "app" / "templates" / "admin_users.html").read_text(encoding="utf-8")
account_password = (ROOT / "app" / "templates" / "account_password.html").read_text(encoding="utf-8")

# --- settings option --------------------------------------------------------
assert 'PASSWORD_MIN_LENGTH_KEY = "password_min_length"' in main
assert "def password_min_length(db: Session) -> int:" in main
assert "max(6, min(72, app_setting_int(db, PASSWORD_MIN_LENGTH_KEY, PANEL_PASSWORD_MIN_LENGTH)))" in main
assert 'name="password_min_length"' in branding
assert "{{ password_min_length }}" in branding or "{% for length in [12, 10, 8, 6] %}" in branding

# --- policy enforcement ------------------------------------------------------
assert "min_length: int = PANEL_PASSWORD_MIN_LENGTH" in auth
assert main.count("password_min_length(db))") >= 8, "every policy call site must pass the configured minimum"
assert "Xtream passwords follow the same operator-configured" in main or (
    'minimum = password_min_length(db)' in main and 'Password must contain at least {minimum} characters.' in main
), "viewer (Xtream) passwords must follow the configured minimum"

# --- forced password change --------------------------------------------------
assert "STREAMFORGE_FORCE_PASSWORD_CHANGE_V1362" in auth and main
assert "must_change_password: Mapped[bool]" in models
assert "ALTER TABLE admin_users ADD COLUMN must_change_password" in db
assert "/admin-users/{admin_user_id}/force-password" in main
assert 'admin.must_change_password = False' in main, "a successful self change clears the flag"
assert "panel_user.must_change_password = True" in main, "an operator-set password is temporary"
assert '"password_change_required": bool(admin.must_change_password),' in main
assert '"must_change_password": bool(getattr(user, "must_change_password", False)),' in node_manager
assert "form method=\"post\" action=\"/admin-users/{{ item.id }}/force-password\"" in admin_users
assert "Password change forced" in admin_users

# --- node mirror -------------------------------------------------------------
assert "must_change_password: bool = False" in node
assert "password_min_length: int = 12" in node
assert "enforce_node_forced_password_change" in node
assert "user.password_min_length" in node, "the Node password form renders the configured minimum"
assert node.count('minlength="12"') == 0, "the Node password form must not hardcode 12"

print("PASS: password policy options contract")
