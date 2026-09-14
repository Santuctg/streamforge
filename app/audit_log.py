from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete

from .db import SessionLocal
from .models import AppSetting, LogEntry

_LOG_RETENTION_KEY = "log_retention_days"
_LOG_RETENTION_PRUNE_INTERVAL = 3600.0
_log_retention_lock = threading.RLock()
_last_log_retention_prune = 0.0


def _configured_log_retention_days(db) -> int:
    row = db.get(AppSetting, _LOG_RETENTION_KEY)
    try:
        value = int(row.value) if row else 30
    except (TypeError, ValueError):
        value = 30
    return max(1, min(3650, value))


def prune_logs(retention_days: int | None = None, *, db=None, force: bool = True) -> int:
    """Delete Main LogEntry rows older than the configured retention window."""
    global _last_log_retention_prune
    now_mono = time.monotonic()
    with _log_retention_lock:
        if not force and now_mono - _last_log_retention_prune < _LOG_RETENTION_PRUNE_INTERVAL:
            return 0
        _last_log_retention_prune = now_mono
    owns_session = db is None
    session = db or SessionLocal()
    try:
        days = _configured_log_retention_days(session) if retention_days is None else max(1, min(3650, int(retention_days)))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = session.execute(delete(LogEntry).where(LogEntry.created_at < cutoff))
        if owns_session:
            session.commit()
        return int(result.rowcount or 0)
    except Exception:
        if owns_session:
            session.rollback()
        return 0
    finally:
        if owns_session:
            session.close()


def log_event(
    message: str,
    *,
    scope: str = "system",
    level: str = "info",
    channel_id: int | None = None,
    node_id: int | None = None,
    actor: str | None = None,
    details: Any = None,
    db: Session | None = None,
) -> None:
    try:
        encoded = None
        if details is not None:
            encoded = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, default=str)
        if db is not None:
            prune_logs(db=db, force=False)
            # STREAMFORGE_CLIENT_LOG_REQUEST_COMMIT_V69:
            # Client/WebPlayer hot paths intentionally reuse the request DB
            # session so logging does not open a competing SQLite connection.
            # Mark the session dirty and let get_db() commit once, at the end
            # of a successful request; exceptions still roll the request back.
            db.add(LogEntry(
                scope=(scope or "system")[:40],
                level=(level or "info")[:20],
                message=str(message)[-8000:],
                channel_id=channel_id,
                node_id=node_id,
                actor=(actor or "")[:120] or None,
                details=(encoded or "")[-16000:] or None,
            ))
            db.info["streamforge_audit_log_dirty"] = True
            return
        with SessionLocal() as db:
            prune_logs(db=db, force=False)
            db.add(LogEntry(
                scope=(scope or "system")[:40],
                level=(level or "info")[:20],
                message=str(message)[-8000:],
                channel_id=channel_id,
                node_id=node_id,
                actor=(actor or "")[:120] or None,
                details=(encoded or "")[-16000:] or None,
            ))
            db.commit()
    except Exception:
        # Logging must never break streaming operations.
        pass


def clear_logs(*, scope: str | None = None, channel_id: int | None = None, node_id: int | None = None) -> int:
    with SessionLocal() as db:
        stmt = delete(LogEntry)
        if scope:
            stmt = stmt.where(LogEntry.scope == scope)
        if channel_id:
            stmt = stmt.where(LogEntry.channel_id == channel_id)
        if node_id:
            stmt = stmt.where(LogEntry.node_id == node_id)
        result = db.execute(stmt)
        db.commit()
        return int(result.rowcount or 0)
