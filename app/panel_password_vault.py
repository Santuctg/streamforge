"""Main-only, opt-in, re-authenticated panel-user credential vault.

STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263:
Unlike the scrypt login hash, this explicit opt-in vault stores independently
encrypted passwords for later inspection. Never expose a vault value through
GET, template HTML, logs, Node sync, or the regular panel-user API.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import time
from pathlib import Path
from contextlib import closing
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .auth import current_admin, verify_password
from .audit_log import log_event
from .db import get_db
from .models import AdminUser, PanelUserCredentialVault
from .permissions import SUPERUSER_PERMISSION, role_permission_set

router = APIRouter()
CSRF_SESSION_KEY = "sf_panel_vault_csrf"
VAULT_KEY_ENV = "STREAMFORGE_PANEL_PASSWORD_VAULT_KEY"
# STREAMFORGE_V1263_DURABLE_REVEAL_GUARD:
# A shared SQLite failure guard applies across every Main HTTP worker.
# It never gates existing Main or Remote Node login flows.
GUARD_DB_ENV = "STREAMFORGE_PANEL_VAULT_GUARD_DB"
GUARD_DB_DEFAULT = "/var/lib/streamforge/panel-vault-reveal-guard.sqlite3"


class VaultGuardUnavailable(RuntimeError):
    pass


def _guard_connection():
    path = Path(os.environ.get(GUARD_DB_ENV, GUARD_DB_DEFAULT))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
                raise VaultGuardUnavailable("Vault reveal guard permissions unsafe")
        finally:
            os.close(fd)
        conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("CREATE TABLE IF NOT EXISTS reveal_failures ("
                         "actor_id INTEGER PRIMARY KEY, window_start INTEGER NOT NULL,"
                         "failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)")
            return conn
        except Exception:
            conn.close()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise VaultGuardUnavailable("Vault reveal guard unavailable") from exc


def _locked(actor: AdminUser) -> bool:
    now = int(time.time())
    try:
        with closing(_guard_connection()) as conn:
            record = conn.execute(
                "SELECT blocked_until FROM reveal_failures WHERE actor_id = ?", (int(actor.id),)
            ).fetchone()
            return bool(record and int(record[0]) > now)
    except sqlite3.Error as exc:
        raise VaultGuardUnavailable("Vault reveal guard unavailable") from exc


def _failure(actor: AdminUser) -> None:
    now = int(time.time())
    conn = _guard_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT window_start, failures, blocked_until FROM reveal_failures WHERE actor_id = ?",
            (int(actor.id),),
        ).fetchone()
        if row is None or now - int(row[0]) >= 900:
            start, failures, blocked = now, 1, 0
        else:
            start, failures, blocked = int(row[0]), int(row[1]) + 1, int(row[2])
        if failures >= 5:
            blocked = max(blocked, now + 900)
        conn.execute(
            "INSERT INTO reveal_failures(actor_id,window_start,failures,blocked_until) "
            "VALUES(?,?,?,?) ON CONFLICT(actor_id) DO UPDATE SET "
            "window_start=excluded.window_start,failures=excluded.failures,"
            "blocked_until=excluded.blocked_until",
            (int(actor.id), start, failures, blocked),
        )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise VaultGuardUnavailable("Vault reveal guard unavailable") from exc
    finally:
        conn.close()


def _clear_failures(actor: AdminUser) -> None:
    try:
        with closing(_guard_connection()) as conn:
            conn.execute("DELETE FROM reveal_failures WHERE actor_id = ?", (int(actor.id),))
    except sqlite3.Error as exc:
        raise VaultGuardUnavailable("Vault reveal guard unavailable") from exc


def _request_is_secure_same_origin(request: Request) -> bool:
    # A forwarding header proves TLS only if our direct peer is loopback Nginx.
    peer = str(request.client.host if request.client else "")
    direct_tls = request.url.scheme == "https"
    proxied_tls = peer in {"127.0.0.1", "::1"} and (
        request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower() == "https"
    )
    parsed = urlsplit(request.headers.get("origin", ""))
    host = (request.headers.get("host") or "").lower()
    fetch_site = request.headers.get("sec-fetch-site", "same-origin")
    return bool((direct_tls or proxied_tls) and parsed.scheme == "https"
                and parsed.netloc.lower() == host and fetch_site in {"same-origin", "none"})


def _cipher() -> Fernet | None:
    value = os.environ.get(VAULT_KEY_ENV, "").strip()
    if not value:
        return None
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, TypeError):
        return None


def vault_enabled() -> bool:
    """No legacy cookie secret fallback; missing/malformed key disables vault."""
    return _cipher() is not None


def vault_hash_fingerprint(encoded_hash: str) -> str:
    return hashlib.sha256(encoded_hash.encode("utf-8")).hexdigest()


def vault_store(db: Session, user: AdminUser, password: str) -> bool:
    """Store a new password only when explicit opt-in and key are present.

    Caller is responsible for transaction commit after changing the login hash.
    """
    cipher = _cipher()
    if cipher is None:
        return False
    if not password or user.id is None:
        raise ValueError("Vault needs a persisted account and nonempty password.")
    payload = json.dumps(
        {"uid": int(user.id), "password": password}, separators=(",", ":")
    ).encode("utf-8")
    ciphertext = cipher.encrypt(payload).decode("ascii")
    row = db.get(PanelUserCredentialVault, int(user.id))
    if row is None:
        db.add(PanelUserCredentialVault(
            user_id=int(user.id), ciphertext=ciphertext,
            password_hash_fingerprint=vault_hash_fingerprint(user.password_hash),
        ))
    else:
        row.ciphertext = ciphertext
        row.password_hash_fingerprint = vault_hash_fingerprint(user.password_hash)
    return True


def vault_discard(db: Session, user_id: int) -> None:
    """Remove stale stored passwords when a reset is not vault-opted-in."""
    row = db.get(PanelUserCredentialVault, int(user_id))
    if row is not None:
        db.delete(row)


def vault_password_available(db: Session, user: AdminUser) -> bool:
    if not vault_enabled():
        return False
    row = db.get(PanelUserCredentialVault, user.id)
    return bool(
        row and hmac.compare_digest(
            row.password_hash_fingerprint, vault_hash_fingerprint(user.password_hash)
        )
    )


def is_vault_admin(actor: AdminUser | None) -> bool:
    return bool(
        actor and actor.is_active and actor.main_panel_access and actor.role
        and SUPERUSER_PERMISSION in role_permission_set(actor.role)
    )


def vault_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def _secret_response(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status,
        headers={
            "Cache-Control": "no-store, max-age=0, private",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/admin-users/{admin_user_id}/password/reveal")
def reveal_panel_user_password(
    admin_user_id: int,
    request: Request,
    reauth_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authenticated one-time reveal; never cache or embed plaintext in pages."""
    if not _request_is_secure_same_origin(request):
        return _secret_response({"error": "HTTPS same-origin request required"}, 403)
    actor = current_admin(request, db)
    if not is_vault_admin(actor):
        return _secret_response({"error": "Not permitted"}, 403)

    expected = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(expected, str) or not hmac.compare_digest(expected, csrf_token):
        return _secret_response({"error": "Invalid request. Reload page."}, 403)
    try:
        if _locked(actor):
            log_event("Panel credential vault reveal throttled", scope="auth", level="warning",
                      actor=actor.username, details={"target_user_id": int(admin_user_id)})
            return _secret_response({"error": "Too many attempts. Try later."}, 429)
        if not verify_password(reauth_password, actor.password_hash):
            _failure(actor)
            log_event("Panel credential vault reauthentication denied", scope="auth",
                      level="warning", actor=actor.username,
                      details={"target_user_id": int(admin_user_id)})
            return _secret_response({"error": "Authentication failed"}, 403)
        _clear_failures(actor)
    except VaultGuardUnavailable:
        log_event("Panel credential vault reveal guard unavailable", scope="auth", level="error",
                  actor=actor.username, details={"target_user_id": int(admin_user_id)})
        return _secret_response({"error": "Password reveal protection unavailable"}, 503)

    cipher = _cipher()
    if cipher is None:
        return _secret_response({"error": "Credential vault is not configured"}, 503)

    target = db.get(AdminUser, admin_user_id)
    row = db.get(PanelUserCredentialVault, admin_user_id) if target else None
    if target is None or row is None:
        return _secret_response({"error": "No saved password. Reset and opt in."}, 404)
    if not hmac.compare_digest(
        row.password_hash_fingerprint, vault_hash_fingerprint(target.password_hash)
    ):
        return _secret_response({"error": "Saved password is outdated. Reset and opt in."}, 409)

    try:
        payload = json.loads(cipher.decrypt(row.ciphertext.encode("ascii")))
        if payload.get("uid") != int(target.id) or not isinstance(payload.get("password"), str):
            raise ValueError("Vault account mismatch")
        password = payload["password"]
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return _secret_response({"error": "Vault entry cannot be decrypted"}, 409)

    log_event("Panel credential vault password revealed", scope="auth",
              actor=actor.username, details={"target_user_id": int(target.id)})
    return _secret_response({"password": password, "hide_after_seconds": 10})
