#!/usr/bin/env python3
"""v13.13 visible notification/update UI release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.13":
    print("SKIP: v13.13 release round-trip")
    raise SystemExit(0)

# --- SMS integration ------------------------------------------------------
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}"), "style.css braces unbalanced"
for marker in ("STREAMFORGE_COLLAPSED_FOOTER_FULL_SIDEBAR_CENTER", "width:76px!important", "margin-left:-10px!important"):
    assert marker in style, marker
manager = (ROOT / "app" / "notification_manager.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_EXACT_UI_TEMPLATE_FIX", "contact_phone", "sms.maestro.com.bd", "DEFAULT_SMS_USER_BODY", "DEFAULT_SMS_NODE_BODY"):
    assert marker in manager, marker
for path in ("app/models.py", "app/db.py", "app/main.py", "app/templates/notifications.html"):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "sms_notifications" in text or "SMS" in text, path
node_form = (ROOT / "app/templates/node_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_NODE_OWNER_SINGLE_ROW", "node-owner-notification-row"):
    assert marker in node_form, marker
assert 'render_template(sms.user_body, values)' in manager

# --- Version and updater wiring -------------------------------------------
for path in ("VERSION", "node_agent/VERSION"):
    assert (ROOT / path).read_text().strip() == "13.13", path

updater = (ROOT / "scripts" / "force_update_v1313.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1313_VISIBLE_NOTIFICATION_UPDATE_UI_FIX_UPDATER",
    "app/static/style.css",
    "STREAMFORGE_V1313_STATIC_CACHE",
    "sync_main_static_cache.py",
    "STREAMFORGE_V1313_TEST_FAIL_AFTER_WRITE",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13",
    "contact_phone",
    "sms_notifications",
    "sms.maestro.com.bd",
    'OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
):
    assert marker in updater, marker
assert "systemctl restart nginx" not in updater
assert "force_update_v1313.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

staging = (ROOT / "scripts" / "verify_release_updater_staging.py").read_text(encoding="utf-8")

# --- Release notes, changelog and docs ------------------------------------
notes = (ROOT / "release-notes" / "v13.13.md").read_text(encoding="utf-8")
assert "streamforge-update --force /root/streamforge-13.13.zip" in notes

changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert changelog.startswith("## 13.13 - ")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "releases/tag/v13.13" in readme
assert "streamforge-13.13.zip" in readme

installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
assert "VERSION=13.13\n" in installation

api = (ROOT / "docs" / "API_AND_TESTING.md").read_text(encoding="utf-8")
for name in ("test_v1313_updater.py", "verify_v1313_patch_roundtrip.py", "test_v1313_ui_contract.py"):
    assert name in api, name

# --- v13.11 Notification Center contract still intact ----------------------
page = (ROOT / "app" / "templates" / "notifications.html").read_text(encoding="utf-8")
Environment().parse(page)
for marker in (
    'action="/system/notifications/settings"',
    'action="/system/notifications/test"',
    "notification-summary-grid",
    "notification-recipient-grid",
    "@media(max-width:700px)",
):
    assert marker in page, marker

main_updates = (ROOT / "app" / "templates" / "main_updates.html").read_text(encoding="utf-8")
assert "confirm('Update Main Server to v{{" not in main_updates
assert "confirm('Update the Main Server now?" in main_updates

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
user_form = (ROOT / "app/templates/user_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1313_VISIBLE_NOTIFICATION_UPDATE_UI_FIX", '"status": "scheduled"', '"request_id": request_id'):
    assert marker in main, marker
for marker in ('action="/system/notifications/timezone"', "data-message-modal", "modal.showModal()"):
    assert marker in page, marker
for marker in ("user-notification-settings", "Enable email notifications", "Enable SMS notifications"):
    assert marker in user_form, marker

print("PASS: v13.13 visible notification/update UI, updater and release contract")
