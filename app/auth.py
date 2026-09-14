from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import AdminUser


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n,
        r,
        p,
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def current_admin(request: Request, db: Session) -> Optional[AdminUser]:
    # STREAMFORGE_MAIN_REQUEST_ADMIN_CACHE_V48:
    # Permission dependencies and HTML rendering both ask for the same admin.
    # Reuse the already joined role for the remainder of this request instead
    # of issuing the same SQLite SELECT two or more times per page/API call.
    if getattr(request.state, "_streamforge_admin_cache_loaded", False):
        return getattr(request.state, "_streamforge_admin_cache", None)
    admin_id = request.session.get("admin_id")
    if not admin_id:
        request.state._streamforge_admin_cache_loaded = True
        request.state._streamforge_admin_cache = None
        return None
    admin = db.scalar(select(AdminUser).options(joinedload(AdminUser.role)).where(AdminUser.id == int(admin_id)))
    if not admin or not admin.is_active or not admin.main_panel_access:
        request.session.clear()
        admin = None
    request.state._streamforge_admin_cache_loaded = True
    request.state._streamforge_admin_cache = admin
    return admin
