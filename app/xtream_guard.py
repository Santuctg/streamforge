"""Brute-force limits for Xtream / playlist credential checks (v12.76).

STREAMFORGE_XTREAM_AUTH_GUARD_V1276: /get.php, /player_api.php and
/live/<user>/<pass>/... verify a username and password on every request, with
no failure limit. Failures are now counted in a small SQLite file shared by all
Main workers:

* the same source IP and username: 10 failures in 10 minutes block that pair
  for 15 minutes (protects a single account);
* the same source IP across usernames: 100 failures in 10 minutes block the IP
  for 15 minutes (stops credential stuffing).

Requests refused while blocked are not counted, so one app retrying a wrong
saved password cannot escalate into an IP-wide block for everyone behind the
same carrier-grade NAT. Any guard error fails open: playback availability wins
over the limiter.
"""
from __future__ import annotations

import ipaddress
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

GUARD_DB_ENV = "STREAMFORGE_XTREAM_GUARD_DB"
GUARD_DB_DEFAULT = "/var/lib/streamforge/xtream-auth-guard.sqlite3"
WINDOW_SECONDS = 10 * 60
BLOCK_SECONDS = 15 * 60
PAIR_FAILURES = 10
IP_FAILURES = 100


def _connection() -> sqlite3.Connection:
    path = Path(os.environ.get(GUARD_DB_ENV, GUARD_DB_DEFAULT))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2, isolation_level=None)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS xtream_failures ("
        "key TEXT PRIMARY KEY, window_start INTEGER NOT NULL,"
        "failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)"
    )
    return conn


def _keys(ip: str, username: str) -> tuple[str, str]:
    try:
        parsed = ipaddress.ip_address(str(ip).strip())
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            parsed = parsed.ipv4_mapped
        address = str(parsed)
    except ValueError:
        address = str(ip).strip()[:64]
    return f"ip:{address}", f"pair:{address}|{str(username).strip().lower()[:120]}"


def xtream_auth_blocked(ip: str, username: str) -> bool:
    if not ip:
        return False
    ip_key, pair_key = _keys(ip, username)
    now = int(time.time())
    try:
        with closing(_connection()) as conn:
            row = conn.execute(
                "SELECT 1 FROM xtream_failures WHERE key IN (?, ?) AND blocked_until > ? LIMIT 1",
                (ip_key, pair_key, now),
            ).fetchone()
        return row is not None
    except (OSError, sqlite3.Error):
        return False


def record_xtream_failure(ip: str, username: str) -> bool:
    """Count one failed credential check; return True when it starts a block."""
    if not ip:
        return False
    ip_key, pair_key = _keys(ip, username)
    now = int(time.time())
    started_block = False
    try:
        with closing(_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for key, limit in ((pair_key, PAIR_FAILURES), (ip_key, IP_FAILURES)):
                    row = conn.execute(
                        "SELECT window_start, failures, blocked_until FROM xtream_failures WHERE key=?", (key,)
                    ).fetchone()
                    if row is not None and int(row[2]) > now:
                        continue
                    if row is None or now - int(row[0]) >= WINDOW_SECONDS:
                        start, failures = now, 1
                    else:
                        start, failures = int(row[0]), int(row[1]) + 1
                    blocked = now + BLOCK_SECONDS if failures >= limit else 0
                    started_block = started_block or bool(blocked)
                    conn.execute(
                        "INSERT INTO xtream_failures(key, window_start, failures, blocked_until) VALUES(?,?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start,"
                        "failures=excluded.failures, blocked_until=excluded.blocked_until",
                        (key, start, failures, blocked),
                    )
                # Keep the table small: drop idle rows older than a day.
                conn.execute(
                    "DELETE FROM xtream_failures WHERE blocked_until <= ? AND window_start < ?",
                    (now, now - 86400),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except (OSError, sqlite3.Error):
        return False
    return started_block


def clear_xtream_failures(ip: str, username: str) -> None:
    """After a valid login, forget unblocked failures of that IP and username."""
    if not ip:
        return
    _, pair_key = _keys(ip, username)
    try:
        with closing(_connection()) as conn:
            conn.execute(
                "DELETE FROM xtream_failures WHERE key=? AND blocked_until <= ?",
                (pair_key, int(time.time())),
            )
    except (OSError, sqlite3.Error):
        pass
