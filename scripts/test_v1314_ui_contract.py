#!/usr/bin/env python3
"""Targeted contracts for the v13.14 alignment, copy and global timezone fixes."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
manager = (ROOT / "app/notification_manager.py").read_text(encoding="utf-8")
style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
notifications = (ROOT / "app/templates/notifications.html").read_text(encoding="utf-8")
settings = (ROOT / "app/templates/system_branding.html").read_text(encoding="utf-8")
user_form = (ROOT / "app/templates/user_form.html").read_text(encoding="utf-8")

# Only the bottom footer group is translated; upper navigation geometry stays
# untouched. The screenshot's right shift equals the 10px sidebar padding.
assert "STREAMFORGE_COLLAPSED_FOOTER_EXACT_NAV_CENTER" in style
assert "transform:translateX(-10px)!important" in style
assert "margin-left:-10px!important" not in style

# Requested concise Playlist User copy.
for visible in ("Email notifications", "SMS notifications"):
    assert f"<b>{visible}</b>" in user_form
for removed in (
    "Enable email notifications", "Enable SMS notifications",
    "Expiry reminders and notices.", "Bangladesh SMS number.",
):
    assert removed not in user_form, removed

# The timezone is a Settings-owned global system setting, not a Notification
# Center-only control. Existing notification_timezone remains fallback-only.
assert 'name="system_timezone"' in settings
assert "System time zone" in settings
assert "/system/notifications/timezone" not in notifications
for marker in (
    "STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE",
    'save_app_setting(db, "system_timezone", cleaned_timezone)',
    "DISPLAY_TZ = ZoneInfo(system_timezone_name(db))",
    '"system_timezone": system_timezone_name(db)',
):
    assert marker in main, marker
assert 'db.get(AppSetting, "system_timezone") or db.get(AppSetting, "notification_timezone")' in manager

# v13.13 popup and matching update-processing state remain intact.
for marker in ("data-message-modal", "modal.showModal()"):
    assert marker in notifications
updates = (ROOT / "app/templates/main_updates.html").read_text(encoding="utf-8")
assert "Update processing" in updates and "2500" in updates

print("PASS: v13.14 footer alignment, concise User copy and global system timezone contracts")
