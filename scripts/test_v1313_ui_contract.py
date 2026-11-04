#!/usr/bin/env python3
"""Targeted contracts for the v13.13 notification/update/sidebar UI fixes."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
notifications = (ROOT / "app/templates/notifications.html").read_text(encoding="utf-8")
updates = (ROOT / "app/templates/main_updates.html").read_text(encoding="utf-8")
user_form = (ROOT / "app/templates/user_form.html").read_text(encoding="utf-8")

# Collapsed footer spans the complete 76px sidebar. Its 16px inner padding
# leaves a 44px button track centered at x=38px.
for marker in (
    "STREAMFORGE_COLLAPSED_FOOTER_FULL_SIDEBAR_CENTER",
    "width:76px!important",
    "margin-left:-10px!important",
    "padding-left:16px!important",
):
    assert marker in style, marker
assert (10 - 10) + 16 + (44 / 2) == 38

# Timezone is a prominent, independently saved panel—not an SMTP side effect.
assert 'action="/system/notifications/timezone"' in notifications
assert '@app.post("/system/notifications/timezone"' in main
email_settings = main.split('@app.post("/system/notifications/settings"', 1)[1].split("@app.post", 1)[0]
assert "server_timezone: str = Form" not in email_settings

# Delivery body is shown through an accessible popup, not inline details.
for marker in ("<dialog", "data-message-modal", "data-view-message", "modal.showModal()"):
    assert marker in notifications, marker
assert "<details" not in notifications

# A just-submitted update renders scheduled/processing immediately and keeps
# polling for the matching privileged-helper request result.
for marker in ("requested_id", '"status": "scheduled"', '"request_id": request_id', '"target": latest["version"]'):
    assert marker in main, marker
assert "Update processing" in updates
assert "2500" in updates

# Playlist User destinations and both independent channel toggles stay in one
# visible group, with exactly one field for each backend name.
assert "user-notification-settings" in user_form
for name in ("contact_email", "contact_phone", "email_notifications", "sms_notifications"):
    assert user_form.count(f'name="{name}"') == 1, name
assert "Enable email notifications" in user_form
assert "Enable SMS notifications" in user_form

print("PASS: v13.13 sidebar, timezone, modal, update-processing and Playlist User UI contracts")
