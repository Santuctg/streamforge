#!/usr/bin/env python3
"""v13.07 SMS templates release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.07":
    print("SKIP: v13.07 release round-trip")
    raise SystemExit(0)

# --- SMS integration ------------------------------------------------------
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}"), "style.css braces unbalanced"
manager = (ROOT / "app" / "notification_manager.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1307_NOTIFICATION_FIXES", "contact_phone", "sms.maestro.com.bd", "DEFAULT_SMS_USER_BODY", "DEFAULT_SMS_NODE_BODY"):
    assert marker in manager, marker
for path in ("app/models.py", "app/db.py", "app/main.py", "app/templates/notifications.html"):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "sms_notifications" in text or "SMS" in text, path

# --- Version and updater wiring -------------------------------------------
for path in ("VERSION", "node_agent/VERSION"):
    assert (ROOT / path).read_text().strip() == "13.07", path

updater = (ROOT / "scripts" / "force_update_v1307.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1307_NOTIFICATION_FIXES_UPDATER",
    "app/static/style.css",
    "STREAMFORGE_V1307_STATIC_CACHE",
    "sync_main_static_cache.py",
    "STREAMFORGE_V1307_TEST_FAIL_AFTER_WRITE",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07",
    "contact_phone",
    "sms_notifications",
    "sms.maestro.com.bd",
    'OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
):
    assert marker in updater, marker
assert "systemctl restart nginx" not in updater
assert "force_update_v1307.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

staging = (ROOT / "scripts" / "verify_release_updater_staging.py").read_text(encoding="utf-8")

# --- Release notes, changelog and docs ------------------------------------
notes = (ROOT / "release-notes" / "v13.07.md").read_text(encoding="utf-8")
assert "streamforge-update --force /root/streamforge-13.07.zip" in notes

changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert changelog.startswith("## 13.07 - ")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "releases/tag/v13.07" in readme
assert "streamforge-13.07.zip" in readme

installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
assert "VERSION=13.07\n" in installation

api = (ROOT / "docs" / "API_AND_TESTING.md").read_text(encoding="utf-8")
for name in ("test_v1307_updater.py", "verify_v1307_patch_roundtrip.py"):
    assert name in api, name

# --- v13.06 Notification Center contract still intact ----------------------
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

print("PASS: v13.07 SMS templates, static cache republish, updater, release notes and Notification Center contract")
