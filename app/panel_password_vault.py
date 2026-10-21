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
import threading
import time

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
# Best-effort in-process throttling. Deployment operators must also rate-limit
# this authenticated endpoint at the reverse proxy for cross-worker protection.
_failed_attempts: dict[int, tuple[int, float]] = {}
_lock = threading.RLock()


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


def _throttle_key(actor: AdminUser) -> int:
    return int(actor.id)


def _locked(actor: AdminUser) -> bool:
    with _lock:
        count, until = _failed_attempts.get(_throttle_key(actor), (0, 0.0))
        if until <= time.monotonic():
            _failed_attempts.pop(_throttle_key(actor), None)
            return False
        return count >= 5


def _failure(actor: AdminUser) -> None:
    with _lock:
        key = _throttle_key(actor)
        count, until = _failed_attempts.get(key, (0, 0.0))
        if until <= time.monotonic():
            count = 0
        _failed_attempts[key] = (count + 1, time.monotonic() + 900)


@router.post("/admin-users/{admin_user_id}/password/reveal")
def reveal_panel_user_password(
    admin_user_id: int,
    request: Request,
    reauth_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authenticated one-time reveal; never cache or embed plaintext in pages."""
    # Credentials may only cross the wire over HTTPS; proxies must forward scheme correctly.
    if request.url.scheme != "https":
        return _secret_response({"error": "HTTPS is required to reveal a saved password"}, 403)
    actor = current_admin(request, db)
    if not is_vault_admin(actor):
        return _secret_response({"error": "Not permitted"}, 403)

    expected = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(expected, str) or not hmac.compare_digest(expected, csrf_token):
        return _secret_response({"error": "Invalid request. Reload page."}, 403)
    if _locked(actor):
        return _secret_response({"error": "Too many attempts. Try later."}, 429)

    if not verify_password(reauth_password, actor.password_hash):
        _failure(actor)
        log_event("Panel credential vault reauthentication denied", scope="auth",
                  level="warning", actor=actor.username,
                  details={"target_user_id": int(admin_user_id)})
        return _secret_response({"error": "Authentication failed"}, 403)

    with _lock:
        _failed_attempts.pop(_throttle_key(actor), None)

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
    return _secret_response({"password": password, "hide_after_seconds": 15})
