#!/usr/bin/env python3
"""v12.61 password-visibility UI contract: never expose stored credentials."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
listing = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")
form = (ROOT / "app/templates/admin_user_form.html").read_text(encoding="utf-8")
auth = (ROOT / "app/auth.py").read_text(encoding="utf-8")
model = (ROOT / "app/models.py").read_text(encoding="utf-8")
checks = [
    ((ROOT / "VERSION").read_text().strip() == "12.61", "Main version"),
    ((ROOT / "node_agent/VERSION").read_text().strip() == "12.61", "Node version metadata"),
    ("STREAMFORGE_PANEL_USER_PASSWORD_VISIBILITY_V1261" in form, "visibility marker"),
    ("data-panel-password-toggle" in form, "clickable visibility control"),
    ("data-panel-password-field" in form, "input binding"),
    ("aria-pressed=" in form, "accessible visibility state"),
    ("type=\"button\"" in form, "non-submitting visibility button"),
    ("type=\"password\"" in form, "passwords hidden by default"),
    ("input.type = visible ? 'password' : 'text'" in form, "visibility changes only on click"),
    ("button.addEventListener('click'" in form, "explicit user interaction"),
    ("data-panel-user-password-focus" in form, "reset navigation focus"),
    ("#panel-user-password" in listing, "reset navigation in listing"),
    ("can('panel_users.edit')" in listing, "reset option permission gated"),
    ("Change password" in listing, "password change action"),
    ("password_hash" not in listing + form, "never render password hashes"),
    ("name=\"password\" value=" not in form, "never prepopulate existing credentials"),
    ("hashlib.scrypt" in auth, "one-way password hashing retained"),
    ("password_hash:" in model, "stored password remains hash-only"),
]
errors = [name for ok, name in checks if not ok]
if errors:
    raise SystemExit("FAIL: v12.61 contract: " + ", ".join(errors))
print("PASS: v12.61 password Show/Hide and Change password actions are client-only, scoped and safe")
