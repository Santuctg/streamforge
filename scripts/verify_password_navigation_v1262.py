#!/usr/bin/env python3
"""Regression: hidden canonical-root panel navigation must never persist a fragment."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
listing=(ROOT/"app/templates/admin_users.html").read_text()
form=(ROOT/"app/templates/admin_user_form.html").read_text()
nav=(ROOT/"app/static/panel_nav.js").read_text()
main=(ROOT/"app/main.py").read_text()
assert (ROOT/"VERSION").read_text().strip()=="12.62"
assert (ROOT/"node_agent/VERSION").read_text().strip()=="12.62"
assert "STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262" in listing
assert "STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262" in form
assert "edit?focus=password" in listing
assert "edit#panel-user-password" not in listing
assert "request.query_params.get('focus') == 'password'" in form
assert "dataset.focusOnLoad === '1'" in form
assert "window.location.hash === '#panel-user-password'" not in form
assert "parts.fragment" in main and 'return ""' in main
assert "const internalValue=" in nav
assert "STREAMFORGE_PANEL_USER_PASSWORD_VISIBILITY_V1261" in form
assert "name=\"password\" value=" not in form
print("PASS: v12.62 Change password hidden-route navigation and focus contract")
