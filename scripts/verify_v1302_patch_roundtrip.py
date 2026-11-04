#!/usr/bin/env python3
"""v13.02 Notification Center release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.02":
    print("SKIP: v13.02 release round-trip")
    raise SystemExit(0)

# --- Notification Center template -----------------------------------------
page = (ROOT / "app" / "templates" / "notifications.html").read_text(encoding="utf-8")
Environment().parse(page)
for marker in (
    'action="/system/notifications/settings"',
    'action="/system/notifications/test"',
    'action="/system/notifications/campaign"',
    'name="recipients"',
    'data-recipient-type="user"',
    'data-recipient-type="node"',
    "notification-summary-grid",
    "notification-recipient-grid",
    "notification-history",
    "notification-campaigns",
    "Stream User template",
    "Node Server template",
    "@media(max-width:1050px)",
    "@media(max-width:700px)",
):
    assert marker in page, marker
assert "{{ 'Node Server' if item.recipient_type == 'node' else 'Stream User' }}" in page

# --- Forms, sidebar and permissions ----------------------------------------
for name, markers in {
    "user_form.html": ('name="contact_email"', 'name="email_notifications"'),
    "node_form.html": ('name="contact_name"', 'name="contact_email"', 'Node owner notifications'),
    "base.html": ('href="/system/notifications"', "notifications.view"),
}.items():
    text = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
    for marker in markers:
        assert marker in text, (name, marker)

permissions = (ROOT / "app" / "permissions.py").read_text(encoding="utf-8")
for marker in ("notifications.view", "notifications.send", "notifications.manage", '"Notifications"'):
    assert marker in permissions, marker

# --- Model, additive schema and worker ------------------------------------
models = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
for marker in (
    "class NotificationCampaign",
    "class NotificationDelivery",
    '__tablename__ = "notification_campaigns"',
    '__tablename__ = "notification_deliveries"',
    "dedupe_key",
):
    assert marker in models, marker

db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
for marker in (
    "ALTER TABLE nodes ADD COLUMN contact_name",
    "ALTER TABLE nodes ADD COLUMN contact_email",
    "ALTER TABLE stream_users ADD COLUMN contact_email",
    "ix_stream_users_contact_email",
    "ix_nodes_contact_email",
):
    assert marker in db, marker

manager = (ROOT / "app" / "notification_manager.py").read_text(encoding="utf-8")
for marker in (
    "def queue_expiry_reminders",
    "def queue_manual_campaign",
    "def process_delivery_queue",
    "def cancel_stale_expiry_deliveries",
    "def send_test_email",
    "class NotificationWorker",
):
    assert marker in manager, marker

main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_EMAIL_NOTIFICATIONS_V1302",
    '@app.get("/system/notifications"',
    '@app.post("/system/notifications/settings"',
    '@app.post("/system/notifications/campaign"',
    "notification_worker.start()",
    "cancel_stale_expiry_deliveries(db, \"stream_user\"",
    "cancel_stale_expiry_deliveries(db, \"node\"",
):
    assert marker in main, marker

# --- Main Updates confirmation no longer interpolates remote data ----------
updates = (ROOT / "app" / "templates" / "main_updates.html").read_text(encoding="utf-8")
assert "confirm('Update Main Server to v{{" not in updates
assert "confirm('Update the Main Server now?" in updates

# --- Updater ---------------------------------------------------------------
updater = (ROOT / "scripts" / "force_update_v1302.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1302_EMAIL_NOTIFICATION_CENTER_UPDATER",
    "app/notification_manager.py",
    "app/templates/notifications.html",
    "13.01|13.02",
):
    assert marker in updater, marker
assert "force_update_v1302.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

print("PASS: v13.02 Notification Center contacts, permissions, schema, worker, updater and release contract")
