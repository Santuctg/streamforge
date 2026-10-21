"""Encrypted, opt-in recoverable Panel User password escrow.

This intentionally does NOT decrypt existing one-way scrypt hashes.
The vault key is domain-separated from the configured Main application secret.
Changing the Main application secret invalidates vaulted passwords; restore its
secure backup or reset each password after a secret rotation.
"""
from __future__ import annotations
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from .config import settings

_PURPOSE = b"streamforge:panel-users:password-vault:v1:"

def _vault_fernet() -> Fernet:
    secret = settings.secret_key
    if not isinstance(secret, str) or len(secret) < 32 or secret == "dev-change-me-now":
        raise RuntimeError("Set a stable 32+ character STREAMFORGE_SECRET_KEY before enabling recoverable passwords")
    digest = hashlib.sha256(_PURPOSE + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def encrypt_panel_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Panel password must contain at least eight characters")
    return _vault_fernet().encrypt(password.encode("utf-8")).decode("ascii")

def decrypt_panel_password(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _vault_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return None
