#!/usr/bin/env python3
"""Real Fernet crypto tests using a disposable application secret."""
import types
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from importlib import import_module

# Import the production vault in isolation: no real credentials or database.
fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(secret_key="D" * 48)
sys.modules["app.config"] = fake_config
vault = import_module("app.panel_password_vault")

password = "test-only value 2026! completely synthetic"
cipher = vault.encrypt_panel_password(password)
assert cipher != password
assert password not in cipher
assert vault.decrypt_panel_password(cipher) == password
assert vault.encrypt_panel_password(password) != cipher, "random nonce mandatory"
assert vault.decrypt_panel_password("invalid") is None
assert vault.decrypt_panel_password(None) is None
fake_config.settings.secret_key = "E" * 48
assert vault.decrypt_panel_password(cipher) is None, "rotated key must not leak plaintext"
fake_config.settings.secret_key = "dev-change-me-now"
try:
    vault.encrypt_panel_password(password)
    raise AssertionError("default secret accepted")
except RuntimeError:
    pass
fake_config.settings.secret_key = "abc"
try:
    vault.encrypt_panel_password(password)
    raise AssertionError("weak secret accepted")
except RuntimeError:
    pass
print("PASS: v12.63 Fernet roundtrip/randomness/tamper/key-rotation/default-secret safety")
