"""Durable IP-based Main/Remote Node Panel login throttling (v12.65).

Five bad login attempts in 15 minutes block the source IP for 3 hours.
No Panel User account is locked. Existing 12.63 actor-ID rows are ignored;
Main and Node share the same persistent ip_failures SQLite table.

This is a Panel authentication gate, not an OS firewall rule.
"""
from __future__ import annotations

import ipaddress
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


def normalize_client_ip(ip: str) -> str:
    """Validate an untrusted source IP, canonicalizing IPv4-mapped IPv6."""
    try:
        parsed = ipaddress.ip_address(str(ip).strip())
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            return str(parsed.ipv4_mapped)
        return str(parsed)
    except (ValueError, TypeError) as exc:
        raise LoginGuardUnavailable("Invalid Panel login client IP") from exc


def main_panel_client_ip(request) -> str:
    """Trust reverse-proxy XFF only from loopback; take rightmost hop.

    With Nginx proxy_add_x_forwarded_for, the rightmost entry is the actual
    client socket IP and cannot be changed by prepending a spoofed XFF value.
    The Nginx upstream must not be publicly reachable and its actual client
    IP must be passed as the final entry. If a CDN sits before Nginx, configure
    Nginx real_ip trust for those specific CDN ranges first.
    """
    peer = normalize_client_ip(request.client.host if request.client else "")
    header = request.headers.get("x-forwarded-for", "")
    # The local socket bind is authoritative even if Uvicorn's trusted-proxy
    # middleware has rewritten scope["client"] using an untrusted first XFF hop.
    server = request.scope.get("server") if hasattr(request, "scope") else None
    if server:
        try:
            on_loopback_backend = ipaddress.ip_address(server[0]).is_loopback
        except (ValueError, TypeError, IndexError):
            raise LoginGuardUnavailable("Unknown Panel backend bind address")
    else:
        on_loopback_backend = ipaddress.ip_address(peer).is_loopback
    if header:
        if not on_loopback_backend:
            # Do not accept forged proxy headers on publicly bound backends.
            raise LoginGuardUnavailable("Panel forwarded IP requires loopback backend")
        return normalize_client_ip(header.split(",")[-1])
    return peer


def _connection():
    path = Path(os.environ.get(GUARD_DB_ENV, GUARD_DB_DEFAULT))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise LoginGuardUnavailable("Unsafe Panel login guard permissions")
        finally:
            os.close(fd)
        conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ip_failures ("
                "ip TEXT PRIMARY KEY, window_start INTEGER NOT NULL,"
                "failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)"
            )
            # STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER: independently record
            # the server handling the 5th failure; older active blocks lack it.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ip_block_origins ("
                "ip TEXT PRIMARY KEY, source_kind TEXT NOT NULL,"
                "source_name TEXT NOT NULL, source_node_id INTEGER)"
            )
            return conn
        except Exception:
            conn.close()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc


def ip_locked(ip: str) -> bool:
    addr = normalize_client_ip(ip)
    now = int(time.time())
    try:
        with closing(_connection()) as conn:
            row = conn.execute(
                "SELECT blocked_until FROM ip_failures WHERE ip=?", (addr,)
            ).fetchone()
            return bool(row and int(row[0]) > now)
    except sqlite3.Error as exc:
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc


def record_ip_failure(
    ip: str, *, origin: str = "unknown",
    node_id: int | None = None, node_name: str | None = None,
) -> bool:
    """Atomically count bad logins and record which server caused the block.

    The block origin belongs to the *fifth* failed request, not necessarily
    all five failed requests when a Main/Node source IP is shared.
    """
    addr = normalize_client_ip(ip)
    if origin not in {"main", "node", "unknown"}:
        raise LoginGuardUnavailable("Invalid Panel server origin")
    if origin == "node":
        if not isinstance(node_id, int) or node_id < 1 or not isinstance(node_name, str) or not node_name.strip():
            raise LoginGuardUnavailable("Missing authenticated Node origin")
        server = node_name.strip()[:120]
    elif origin == "main":
        server, node_id = "Main Server", None
    else:
        server, node_id = "Unknown", None
    now = int(time.time())
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT window_start,failures,blocked_until FROM ip_failures WHERE ip=?", (addr,)
        ).fetchone()
        if row is not None and int(row[2]) > now:
            conn.commit()
            return True  # never extend an existing lock
        if row is None or now - int(row[0]) >= ATTEMPT_WINDOW_SECONDS:
            start, failures = now, 1
        else:
            start, failures = int(row[0]), int(row[1]) + 1
        blocked = now + LOGIN_LOCK_SECONDS if failures >= FAILED_ATTEMPTS else 0
        # An expired block must never retain a stale server label.
        if row is not None and int(row[2]) <= now:
            conn.execute("DELETE FROM ip_block_origins WHERE ip=?", (addr,))
        conn.execute(
            "INSERT INTO ip_failures(ip,window_start,failures,blocked_until)"
            " VALUES(?,?,?,?) ON CONFLICT(ip) DO UPDATE SET "
            "window_start=excluded.window_start,failures=excluded.failures,"
            "blocked_until=excluded.blocked_until",
            (addr, start, failures, blocked),
        )
        if blocked > now:
            conn.execute(
                "INSERT INTO ip_block_origins(ip,source_kind,source_name,source_node_id) "
                "VALUES(?,?,?,?) ON CONFLICT(ip) DO UPDATE SET "
                "source_kind=excluded.source_kind,source_name=excluded.source_name,"
                "source_node_id=excluded.source_node_id",
                (addr, origin, server, node_id),
            )
        conn.commit()
        return blocked > now
    except sqlite3.Error as exc:
        conn.rollback()
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc
    finally:
        conn.close()


def clear_ip_failures(ip: str) -> None:
    """After a valid login clear only nonblocked attempts from the same IP."""
    addr = normalize_client_ip(ip)
    try:
        with closing(_connection()) as conn:
            removed = conn.execute(
                "DELETE FROM ip_failures WHERE ip=? AND blocked_until <= ?",
                (addr, int(time.time())),
            )
            if removed.rowcount:
                conn.execute("DELETE FROM ip_block_origins WHERE ip=?", (addr,))
    except sqlite3.Error as exc:
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc


def list_locked_ips() -> list[dict]:
    now = int(time.time())
    try:
        with closing(_connection()) as conn:
            rows = conn.execute(
                "SELECT f.ip,f.failures,f.blocked_until,"
                "s.source_kind,s.source_name,s.source_node_id "
                "FROM ip_failures f LEFT JOIN ip_block_origins s ON s.ip=f.ip "
                "WHERE f.blocked_until > ? ORDER BY f.blocked_until DESC,f.ip", (now,)
            ).fetchall()
    except sqlite3.Error as exc:
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc
    result = []
    for ip, failures, deadline, kind, server, node_id in rows:
        if kind == "node":
            source_label = "Remote Node: " + (server or "Unknown")
        elif kind == "main":
            source_label = "Main Server"
        else:
            source_label = "Unknown (blocked before tracking)"
        result.append({
            "ip": str(ip),
            "failed_attempts": int(failures),
            "blocked_until": int(deadline),
            "remaining_seconds": max(0, int(deadline) - now),
            "blocked_by_server": source_label,
            "blocked_by_node_id": int(node_id) if kind == "node" and node_id is not None else None,
        })
    return result


def unlock_ip(ip: str) -> bool:
    addr = normalize_client_ip(ip)
    now = int(time.time())
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "DELETE FROM ip_failures WHERE ip=? AND blocked_until > ?", (addr, now)
        )
        if result.rowcount:
            conn.execute("DELETE FROM ip_block_origins WHERE ip=?", (addr,))
        conn.commit()
        return result.rowcount == 1
    except sqlite3.Error as exc:
        conn.rollback()
        raise LoginGuardUnavailable("Panel login IP guard unavailable") from exc
    finally:
        conn.close()
