"""Persistent email notifications for Stream Users and Node Server owners."""
from __future__ import annotations

import math
import re
import smtplib
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import (
    AppSetting,
    Node,
    NotificationCampaign,
    NotificationDelivery,
    StreamUser,
)
from .secrets_store import decrypt_secret


SETTING_PREFIX = "notification_email_"
DEFAULT_USER_SUBJECT = "Your {service_name} subscription expires in {days_remaining} day(s)"
DEFAULT_USER_BODY = """Hello {customer_name},

Your streaming subscription expires on {expiry_date}. Please renew before the expiry time to keep your service active.

Support: {support_contact}
"""
DEFAULT_NODE_SUBJECT = "Node Server {node_name} subscription expiry notice"
DEFAULT_NODE_BODY = """Hello {customer_name},

Your Node Server {node_name} subscription expires on {expiry_date}. Please renew before the expiry time to avoid service interruption.

Support: {support_contact}
"""
DEFAULT_REMINDER_DAYS = (30, 14, 7, 3, 1, 0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def valid_email(value: str | None) -> bool:
    cleaned = str(value or "").strip()
    parsed = parseaddr(cleaned)[1]
    return bool(parsed == cleaned and len(cleaned) <= 320 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", cleaned))


def _setting(db: Session, name: str, default: str = "") -> str:
    row = db.get(AppSetting, SETTING_PREFIX + name)
    return str(row.value if row else default).strip()


def _boolean(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    auto_enabled: bool
    host: str
    port: int
    security: str
    username: str
    password: str
    from_email: str
    from_name: str
    reply_to: str
    service_name: str
    support_contact: str
    reminder_days: tuple[int, ...]
    user_subject: str
    user_body: str
    node_subject: str
    node_body: str

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.host and self.port and valid_email(self.from_email))


def email_config(db: Session) -> EmailConfig:
    raw_days = _setting(db, "reminder_days", ",".join(map(str, DEFAULT_REMINDER_DAYS)))
    days = sorted(
        {max(0, min(365, int(item.strip()))) for item in raw_days.split(",") if item.strip().isdigit()},
        reverse=True,
    )
    if not days:
        days = list(DEFAULT_REMINDER_DAYS)
    encrypted = _setting(db, "smtp_password_enc")
    try:
        password = str(decrypt_secret(encrypted) or "") if encrypted else ""
    except Exception:
        password = ""
    return EmailConfig(
        enabled=_boolean(_setting(db, "enabled", "0")),
        auto_enabled=_boolean(_setting(db, "auto_enabled", "0")),
        host=_setting(db, "smtp_host"),
        port=max(1, min(65535, int(_setting(db, "smtp_port", "587") or 587))),
        security=_setting(db, "smtp_security", "starttls") if _setting(db, "smtp_security", "starttls") in {"starttls", "ssl"} else "starttls",
        username=_setting(db, "smtp_username"),
        password=password,
        from_email=_setting(db, "from_email"),
        from_name=_setting(db, "from_name", "StreamForge"),
        reply_to=_setting(db, "reply_to"),
        service_name=_setting(db, "service_name", "StreamForge"),
        support_contact=_setting(db, "support_contact", "your service provider"),
        reminder_days=tuple(days),
        user_subject=_setting(db, "user_subject", DEFAULT_USER_SUBJECT) or DEFAULT_USER_SUBJECT,
        user_body=_setting(db, "user_body", DEFAULT_USER_BODY) or DEFAULT_USER_BODY,
        node_subject=_setting(db, "node_subject", DEFAULT_NODE_SUBJECT) or DEFAULT_NODE_SUBJECT,
        node_body=_setting(db, "node_body", DEFAULT_NODE_BODY) or DEFAULT_NODE_BODY,
    )


def public_email_config(db: Session) -> dict[str, object]:
    config = email_config(db)
    return {
        "enabled": config.enabled,
        "auto_enabled": config.auto_enabled,
        "host": config.host,
        "port": config.port,
        "security": config.security,
        "username": config.username,
        "password_saved": bool(_setting(db, "smtp_password_enc")),
        "from_email": config.from_email,
        "from_name": config.from_name,
        "reply_to": config.reply_to,
        "service_name": config.service_name,
        "support_contact": config.support_contact,
        "reminder_days": ",".join(map(str, config.reminder_days)),
        "user_subject": config.user_subject,
        "user_body": config.user_body,
        "node_subject": config.node_subject,
        "node_body": config.node_body,
        "ready": config.ready,
    }


def _clean_header(value: str, limit: int = 255) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def send_email(config: EmailConfig, recipient: str, subject: str, body: str) -> None:
    if not config.ready:
        raise RuntimeError("Email delivery is not configured")
    if not valid_email(recipient):
        raise RuntimeError("Recipient email address is invalid")
    message = EmailMessage()
    message["From"] = formataddr((_clean_header(config.from_name, 120), config.from_email))
    message["To"] = recipient
    message["Subject"] = _clean_header(subject)
    if valid_email(config.reply_to):
        message["Reply-To"] = config.reply_to
    message.set_content(str(body or "").strip() + "\n")
    context = ssl.create_default_context()
    if config.security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=20, context=context)
    else:
        client = smtplib.SMTP(config.host, config.port, timeout=20)
    try:
        client.ehlo()
        if config.security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if config.username:
            client.login(config.username, config.password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def send_test_email(db: Session, recipient: str) -> None:
    config = email_config(db)
    send_email(
        config,
        recipient.strip(),
        f"{config.service_name} email notification test",
        "Email notifications are configured correctly.\n\nThis test did not change any subscription or service.",
    )


def _template_values(
    *, recipient_name: str, expiry: datetime | None = None, node_name: str = "", days_remaining: int = 0,
    config: EmailConfig,
) -> dict[str, str]:
    expiry_value = _aware(expiry)
    return {
        "customer_name": recipient_name or "Customer",
        "node_name": node_name,
        "expiry_date": expiry_value.strftime("%Y-%m-%d %H:%M UTC") if expiry_value else "—",
        "days_remaining": str(max(0, days_remaining)),
        "service_name": config.service_name,
        "support_contact": config.support_contact,
    }


def render_template(template: str, values: dict[str, str]) -> str:
    result = str(template or "")
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def queue_manual_campaign(
    db: Session, *, name: str, subject: str, body: str, recipients: list[str], actor: str,
) -> tuple[NotificationCampaign, int]:
    cleaned_subject = _clean_header(subject)
    cleaned_body = str(body or "").strip()[:20000]
    if not cleaned_subject or not cleaned_body:
        raise ValueError("Subject and message are required")
    selected = set(recipients)
    users = db.scalars(select(StreamUser).where(StreamUser.contact_email.is_not(None))).all()
    nodes = db.scalars(select(Node).where(Node.contact_email.is_not(None))).all()
    targets: list[tuple[str, int, str, str, str]] = []
    for user in users:
        if f"user:{user.id}" in selected and user.email_notifications and valid_email(user.contact_email):
            targets.append(("stream_user", user.id, user.name, str(user.contact_email), ""))
    for node in nodes:
        if f"node:{node.id}" in selected and node.email_notifications and valid_email(node.contact_email):
            targets.append(("node", node.id, node.contact_name or node.name, str(node.contact_email), node.name))
    if not targets:
        raise ValueError("Select at least one recipient with email notifications enabled")
    campaign = NotificationCampaign(
        name=(name.strip() or cleaned_subject)[:180], target_type="selected", subject=cleaned_subject,
        body=cleaned_body, status="queued", created_by=actor[:120], scheduled_at=_utcnow(),
    )
    db.add(campaign)
    db.flush()
    config = email_config(db)
    for recipient_type, recipient_id, recipient_name, email, node_name in targets:
        values = _template_values(recipient_name=recipient_name, node_name=node_name, config=config)
        db.add(NotificationDelivery(
            campaign_id=campaign.id, recipient_type=recipient_type, recipient_id=recipient_id,
            recipient_name=recipient_name[:180], email=email, event_type="manual",
            dedupe_key=f"manual:{campaign.id}:{recipient_type}:{recipient_id}",
            subject=render_template(cleaned_subject, values), body=render_template(cleaned_body, values),
            status="queued", scheduled_at=_utcnow(),
        ))
    db.commit()
    return campaign, len(targets)


def _reminder_threshold(expiry: datetime, now: datetime, thresholds: tuple[int, ...]) -> int | None:
    seconds = (expiry - now).total_seconds()
    if seconds < -86400:
        return None
    if seconds <= 0:
        return 0 if 0 in thresholds else None
    remaining = max(1, math.ceil(seconds / 86400))
    candidates = [item for item in thresholds if item >= remaining]
    return min(candidates) if candidates else None


def queue_expiry_reminders(db: Session) -> int:
    config = email_config(db)
    if not config.ready or not config.auto_enabled:
        return 0
    now = _utcnow()
    queued = 0
    recipients: list[tuple[str, int, str, str, str, datetime]] = []
    for user in db.scalars(select(StreamUser).where(StreamUser.expires_at.is_not(None))).all():
        if user.email_notifications and valid_email(user.contact_email):
            recipients.append(("stream_user", user.id, user.name, str(user.contact_email), "", _aware(user.expires_at)))
    for node in db.scalars(select(Node).where(Node.expires_at.is_not(None))).all():
        if node.email_notifications and valid_email(node.contact_email):
            recipients.append(("node", node.id, node.contact_name or node.name, str(node.contact_email), node.name, _aware(node.expires_at)))
    for recipient_type, recipient_id, recipient_name, email, node_name, expiry in recipients:
        threshold = _reminder_threshold(expiry, now, config.reminder_days)
        if threshold is None:
            continue
        event = "expired" if threshold == 0 else f"expiry_{threshold}_days"
        dedupe = f"auto:{recipient_type}:{recipient_id}:{expiry.isoformat()}:{event}"
        if db.scalar(select(NotificationDelivery.id).where(NotificationDelivery.dedupe_key == dedupe)):
            continue
        template_subject = config.node_subject if recipient_type == "node" else config.user_subject
        template_body = config.node_body if recipient_type == "node" else config.user_body
        values = _template_values(
            recipient_name=recipient_name, expiry=expiry, node_name=node_name,
            days_remaining=threshold, config=config,
        )
        db.add(NotificationDelivery(
            recipient_type=recipient_type, recipient_id=recipient_id, recipient_name=recipient_name[:180],
            email=email, event_type=event, dedupe_key=dedupe,
            subject=render_template(template_subject, values), body=render_template(template_body, values),
            status="queued", scheduled_at=now,
        ))
        queued += 1
    if queued:
        db.commit()
    return queued


def process_delivery_queue(db: Session, limit: int = 10) -> int:
    config = email_config(db)
    if not config.ready:
        return 0
    now = _utcnow()
    deliveries = db.scalars(
        select(NotificationDelivery)
        .where(NotificationDelivery.status == "queued", NotificationDelivery.scheduled_at <= now)
        .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        .limit(max(1, min(50, limit)))
    ).all()
    for delivery in deliveries:
        delivery.attempts += 1
        try:
            send_email(config, delivery.email, delivery.subject, delivery.body)
            delivery.status = "sent"
            delivery.sent_at = _utcnow()
            delivery.error = None
        except Exception as exc:
            delivery.error = str(exc)[-1000:]
            if delivery.attempts >= 3:
                delivery.status = "failed"
            else:
                delivery.scheduled_at = _utcnow() + timedelta(minutes=5 * delivery.attempts)
        db.commit()
    campaign_ids = {item.campaign_id for item in deliveries if item.campaign_id}
    for campaign_id in campaign_ids:
        campaign = db.get(NotificationCampaign, campaign_id)
        if not campaign:
            continue
        pending = db.scalar(select(func.count()).select_from(NotificationDelivery).where(
            NotificationDelivery.campaign_id == campaign_id, NotificationDelivery.status == "queued"
        )) or 0
        failed = db.scalar(select(func.count()).select_from(NotificationDelivery).where(
            NotificationDelivery.campaign_id == campaign_id, NotificationDelivery.status == "failed"
        )) or 0
        campaign.status = "queued" if pending else ("completed_with_errors" if failed else "completed")
    if campaign_ids:
        db.commit()
    return len(deliveries)


def retry_delivery(db: Session, delivery_id: int) -> bool:
    delivery = db.get(NotificationDelivery, delivery_id)
    if not delivery or delivery.status not in {"failed", "sent"}:
        return False
    delivery.status = "queued"
    delivery.attempts = 0
    delivery.error = None
    delivery.sent_at = None
    delivery.scheduled_at = _utcnow()
    delivery.dedupe_key = f"retry:{delivery.id}:{uuid.uuid4().hex}"
    db.commit()
    return True


def cancel_stale_expiry_deliveries(
    db: Session, recipient_type: str, recipient_id: int, current_expiry: datetime | None,
) -> int:
    """Cancel unsent reminders after an expiry is renewed, changed or cleared."""
    deliveries = db.scalars(
        select(NotificationDelivery).where(
            NotificationDelivery.recipient_type == recipient_type,
            NotificationDelivery.recipient_id == recipient_id,
            NotificationDelivery.status == "queued",
            NotificationDelivery.event_type != "manual",
        )
    ).all()
    expiry_marker = _aware(current_expiry).isoformat() if current_expiry else ""
    canceled = 0
    for delivery in deliveries:
        if not expiry_marker or expiry_marker not in delivery.dedupe_key:
            delivery.status = "canceled"
            delivery.error = "Canceled because the subscription expiry changed"
            canceled += 1
    return canceled


class NotificationWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="streamforge-email-notifications", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        last_reminder_scan = 0.0
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    if time.monotonic() - last_reminder_scan >= 3600:
                        queue_expiry_reminders(db)
                        last_reminder_scan = time.monotonic()
                    process_delivery_queue(db)
            except Exception:
                pass
            self._stop.wait(20)


notification_worker = NotificationWorker()
