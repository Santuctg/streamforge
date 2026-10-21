"""Persistent Main+Remote Node Panel login failed-attempt guard (v12.63).

Exactly five incorrect password attempts in a 15-minute rolling window lock
new logins for three hours, per known active panel user across all workers.
Existing authenticated sessions, streaming subscribers and password-vault
reveal throttles are unaffected.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

GUARD_DB_ENV = "STREAMFORGE_PANEL_LOGIN_GUARD_DB"
GUARD_DB_DEFAULT = "/var/lib/streamforge/panel-login-guard.sqlite3"
FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 3 * 60 * 60


class LoginGuardUnavailable(RuntimeError):
    pass


def _connection():
    path = Path(os.environ.get(GUARD_DB_ENV, GUARD_DB_DEFAULT))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise LoginGuardUnavailable("Unsafe login guard file ownership or mode")
        finally:
            os.close(fd)
        conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("CREATE TABLE IF NOT EXISTS login_failures ("
                         "actor_id INTEGER PRIMARY KEY, window_start INTEGER NOT NULL,"
                         "failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)")
            return conn
        except Exception:
            conn.close()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise LoginGuardUnavailable("Panel login guard unavailable") from exc


def _conn_error(exc: sqlite3.Error) -> LoginGuardUnavailable:
    return LoginGuardUnavailable("Panel login guard unavailable")


def login_locked(actor_id: int) -> bool:
    now = int(time.time())
    try:
        with closing(_connection()) as conn:
            row = conn.execute(
                "SELECT blocked_until FROM login_failures WHERE actor_id = ?",
                (int(actor_id),),
            ).fetchone()
            return bool(row and int(row[0]) > now)
    except sqlite3.Error as exc:
        raise _conn_error(exc) from exc


def record_login_failure(actor_id: int) -> bool:
    """Atomically count a bad password. Return whether the login is locked."""
    now = int(time.time())
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT window_start, failures, blocked_until FROM login_failures WHERE actor_id = ?",
            (int(actor_id),),
        ).fetchone()
        if row is not None and int(row[2]) > now:
            conn.commit()
            return True  # never shorten or extend a lock due to another request
        if row is None or now - int(row[0]) >= ATTEMPT_WINDOW_SECONDS:
            start, failures = now, 1
        else:
            start, failures = int(row[0]), int(row[1]) + 1
        blocked_until = now + LOGIN_LOCK_SECONDS if failures >= FAILED_ATTEMPTS else 0
        conn.execute(
            "INSERT INTO login_failures(actor_id, window_start, failures, blocked_until) "
            "VALUES(?,?,?,?) ON CONFLICT(actor_id) DO UPDATE SET "
            "window_start=excluded.window_start, failures=excluded.failures,"
            "blocked_until=excluded.blocked_until",
            (int(actor_id), start, failures, blocked_until),
        )
        conn.commit()
        return blocked_until > now
    except sqlite3.Error as exc:
        conn.rollback()
        raise _conn_error(exc) from exc
    finally:
        conn.close()


def clear_login_failures(actor_id: int) -> None:
    """Clear failed attempts after successful login (not an active lock)."""
    try:
        with closing(_connection()) as conn:
            conn.execute(
                "DELETE FROM login_failures WHERE actor_id=? AND blocked_until <= ?",
                (int(actor_id), int(time.time())),
            )
    except sqlite3.Error as exc:
        raise _conn_error(exc) from exc


def list_locked_logins(db) -> list[dict]:
    now = int(time.time())
    try:
        with closing(_connection()) as conn:
            locked = conn.execute(
                "SELECT actor_id,failures,blocked_until FROM login_failures "
                "WHERE blocked_until > ? ORDER BY blocked_until DESC,actor_id",
                (now,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise _conn_error(exc) from exc

    from .models import AdminUser
    rows = []
    for actor_id, failures, deadline in locked:
        user = db.get(AdminUser, int(actor_id))
        rows.append({
            "actor_id": int(actor_id),
            "username": user.username if user else "Deleted account",
            "failed_attempts": int(failures),
            "blocked_until": int(deadline),
            "remaining_seconds": max(0, int(deadline) - now),
            "account_exists": user is not None,
        })
    return rows


def unlock_login_user(actor_id: int) -> bool:
    now = int(time.time())
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "DELETE FROM login_failures WHERE actor_id = ? AND blocked_until > ?",
            (int(actor_id), now),
        )
        conn.commit()
        return result.rowcount == 1
    except sqlite3.Error as exc:
        conn.rollback()
        raise _conn_error(exc) from exc
    finally:
        conn.close()
