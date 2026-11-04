#!/usr/bin/env python3
"""Email notification queue, deduplication, delivery and renewal tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppSetting, Node, NotificationDelivery, StreamUser
import app.notification_manager as manager


engine = create_engine("sqlite+pysqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def setting(db, name: str, value: str) -> None:
    db.add(AppSetting(key=manager.SETTING_PREFIX + name, value=value))


with Session() as db:
    for name, value in {
        "enabled": "1",
        "auto_enabled": "1",
        "smtp_host": "smtp.example.test",
        "smtp_port": "587",
        "smtp_security": "starttls",
        "from_email": "service@example.test",
        "from_name": "StreamForge",
        "service_name": "Example TV",
        "support_contact": "+8801000000000",
        "reminder_days": "30,14,7,3,1,0",
    }.items():
        setting(db, name, value)
    user = StreamUser(
        name="Viewer One", token="viewer-token", enabled=True,
        contact_email="viewer@example.test", email_notifications=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    node = Node(
        name="Node One", slug="node-one", node_type="remote", enabled=True,
        contact_name="Node Owner", contact_email="owner@example.test", email_notifications=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    db.add_all([user, node])
    db.commit()

    assert manager.email_config(db).ready
    assert manager.queue_expiry_reminders(db) == 2
    assert manager.queue_expiry_reminders(db) == 0, "automatic reminders must be idempotent"
    automatic = db.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id)).all()
    assert [item.event_type for item in automatic] == ["expiry_3_days", "expiry_14_days"]
    assert "Viewer One" in automatic[0].body and "Node One" in automatic[1].body

    campaign, count = manager.queue_manual_campaign(
        db,
        name="Discontinuation",
        subject="Notice from {service_name}",
        body="Hello {customer_name}; expires={expiry_date}; days={{ days_remaining }}; node={node_name}; support={support_contact}",
        recipients=[f"user:{user.id}", f"node:{node.id}"],
        actor="admin",
    )
    assert campaign.id and count == 2
    manual = db.scalars(select(NotificationDelivery).where(NotificationDelivery.campaign_id == campaign.id).order_by(NotificationDelivery.id)).all()
    assert all("{expiry_date}" not in item.body and "days={{" not in item.body for item in manual)
    assert all("expires=—" not in item.body for item in manual)
    assert "days=2" in manual[0].body and "days=10" in manual[1].body

    # Test SMS renders the saved User template with sample expiry values; it
    # must not send raw variables to the provider.
    captured_sms: list[str] = []
    original_send_sms = manager.send_sms
    manager.send_sms = lambda _config, _phone, body: captured_sms.append(body) or "test-id"
    try:
        manager.send_test_sms(db, "01700000000")
    finally:
        manager.send_sms = original_send_sms
    assert captured_sms and "{expiry_date}" not in captured_sms[0] and "{days_remaining}" not in captured_sms[0]
    assert "Test Customer" in captured_sms[0]

    sent: list[tuple[str, str]] = []
    original_send = manager.send_email
    manager.send_email = lambda _config, email, subject, _body: sent.append((email, subject))
    try:
        assert manager.process_delivery_queue(db, limit=10) == 4
    finally:
        manager.send_email = original_send
    assert len(sent) == 4
    assert db.scalar(select(NotificationDelivery).where(NotificationDelivery.status != "sent")) is None

    # A changed expiry cancels only stale queued automatic reminders, never a
    # manual campaign or already-sent delivery.
    user.expires_at = datetime.now(timezone.utc) + timedelta(days=40)
    stale = NotificationDelivery(
        recipient_type="stream_user", recipient_id=user.id, recipient_name=user.name,
        email=user.contact_email, event_type="expiry_1_days", dedupe_key="auto:stale",
        subject="stale", body="stale", status="queued", scheduled_at=datetime.now(timezone.utc),
    )
    db.add(stale)
    db.flush()
    assert manager.cancel_stale_expiry_deliveries(db, "stream_user", user.id, user.expires_at) == 1
    assert stale.status == "canceled"

    failed = NotificationDelivery(
        recipient_type="node", recipient_id=node.id, recipient_name=node.name,
        email=node.contact_email, event_type="manual", dedupe_key="failed:test",
        subject="retry", body="retry", status="failed", attempts=3,
        scheduled_at=datetime.now(timezone.utc),
    )
    db.add(failed)
    db.commit()
    assert manager.retry_delivery(db, failed.id)
    assert failed.status == "queued" and failed.attempts == 0

assert manager.valid_email("valid@example.com")
assert not manager.valid_email("bad\n@example.com")
assert not manager.valid_email("missing-at.example.com")
print("PASS: Stream User and Node email queue, deduplication, templates, delivery, cancellation and retry")
