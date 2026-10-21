"""STREAMFORGE_PANEL_PASSWORD_VAULT_LOGIN_GUARD_V1263.

Recoverable values are encrypted under an independent root-managed key.
This module never prints credentials or stores raw usernames / IPs in guard DB.
The primary login password hash remains authoritative for authentication.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import sqlite3
import stat
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultUnavailable(RuntimeError):
    pass


class AuthGuardUnavailable(RuntimeError):
    pass


KEY_PATH = "/etc/streamforge-panel-vault.key"
GUARD_PATH = "/var/lib/streamforge/panel-auth-guard.sqlite3"


def _vault_key() -> bytes:
    path = Path(os.environ.get("STREAMFORGE_PANEL_VAULT_KEY_FILE", KEY_PATH))
    try:
        info = path.stat()
        # Refuse symlinks, weak permissions or unexpected owners.
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or (info.st_mode & 0o007):
            raise VaultUnavailable("Vault key file permissions are unsafe")
        if info.st_uid not in (0, os.geteuid()):
            raise VaultUnavailable("Vault key owner is unsafe")
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        if len(key) != 32:
            raise ValueError("Invalid key length")
        return key
    except (OSError, ValueError) as exc:
        raise VaultUnavailable("Vault key unavailable") from exc


def _aad(admin_id: int) -> bytes:
    if type(admin_id) is not int or admin_id <= 0:
        raise ValueError("Invalid panel-user id")
    return ("streamforge-panel-password:v1:" + str(admin_id)).encode("ascii")


def encrypt_panel_password(password: str, admin_id: int) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    nonce = os.urandom(12)
    ct = AESGCM(_vault_key()).encrypt(nonce, password.encode("utf-8"), _aad(admin_id))
    return "v1:" + (nonce + ct).hex()


def decrypt_panel_password(encoded: str | None, admin_id: int) -> str | None:
    if not encoded:
        return None
    if not isinstance(encoded, str) or not encoded.startswith("v1:"):
        raise VaultUnavailable("Vault record format unknown")
    try:
        raw = bytes.fromhex(encoded[3:])
        if len(raw) < 29:
            raise ValueError("Invalid encrypted record")
        return AESGCM(_vault_key()).decrypt(raw[:12], raw[12:], _aad(admin_id)).decode("utf-8")
    except Exception as exc:
        # Never fall back to plaintext, never expose the encrypted payload.
        raise VaultUnavailable("Vault record could not be decrypted") from exc


def _fingerprint(scope: str, client: str, username: str) -> tuple[str, str]:
    try:
        ip = str(ipaddress.ip_address(client))
    except ValueError:
        ip = "unknown"
    normalized_name = (username or "").strip().casefold()[:128]
    # Scope partitions Main and delegated Node panels. No raw PII in DB.
    ip_key = hashlib.sha256(("ip:" + scope + ":" + ip).encode("utf-8")).hexdigest()
    pair_key = hashlib.sha256(("pair:" + scope + ":" + ip + ":" + normalized_name).encode("utf-8")).hexdigest()
    return ip_key, pair_key


def _connect():
    db_path = Path(os.environ.get("STREAMFORGE_LOGIN_GUARD_DB", GUARD_PATH))
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.is_symlink():
            raise AuthGuardUnavailable("Login protection store path is unsafe")
        if not db_path.exists():
            try:
                fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        if not stat.S_ISREG(db_path.stat().st_mode) or db_path.stat().st_mode & 0o007:
            raise AuthGuardUnavailable("Login protection store permissions unsafe")
        connection = sqlite3.connect(str(db_path), timeout=3, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("CREATE TABLE IF NOT EXISTS login_guard ("
                           "key TEXT PRIMARY KEY, window_start INTEGER NOT NULL,"
                           "failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise AuthGuardUnavailable("Login protection store unavailable") from exc


def _limits(key_type: str) -> int:
    return 20 if key_type == "ip" else 5


def login_allowed(scope: str, client: str, username: str, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else int(now)
    keys = _fingerprint(scope, client, username)
    try:
        with _connect() as db:
            for key in keys:
                record = db.execute("SELECT blocked_until FROM login_guard WHERE key = ?", (key,)).fetchone()
                if record and int(record[0]) > current:
                    return False
        return True
    except sqlite3.Error as exc:
        raise AuthGuardUnavailable("Login protection store unavailable") from exc


def record_login_failure(scope: str, client: str, username: str, *, now: int | None = None) -> None:
    current = int(time.time()) if now is None else int(now)
    keys = _fingerprint(scope, client, username)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for index, key in enumerate(keys):
            row = conn.execute("SELECT window_start,failures,blocked_until FROM login_guard WHERE key = ?", (key,)).fetchone()
            if not row or current >= int(row[0]) + 900:
                count, started, blocked = 1, current, 0
            else:
                count, started, blocked = int(row[1]) + 1, int(row[0]), int(row[2])
            if count >= _limits("ip" if index == 0 else "pair"):
                blocked = max(blocked, current + 900)
            conn.execute("INSERT INTO login_guard(key,window_start,failures,blocked_until) VALUES (?,?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start,"
                         "failures=excluded.failures,blocked_until=excluded.blocked_until",
                         (key, started, count, blocked))
        conn.execute("DELETE FROM login_guard WHERE window_start < ?", (current - 86400,))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise AuthGuardUnavailable("Login protection store unavailable") from exc
    finally:
        conn.close()


def clear_login_failures(scope: str, client: str, username: str) -> None:
    conn = _connect()
    try:
        conn.executemany("DELETE FROM login_guard WHERE key = ?", ((key,) for key in _fingerprint(scope, client, username)))
    except sqlite3.Error as exc:
        raise AuthGuardUnavailable("Login protection store unavailable") from exc
    finally:
        conn.close()
