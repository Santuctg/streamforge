#!/usr/bin/env python3
"""v12.63 security regression: AES-GCM, password-storage and login attempt guards."""
from __future__ import annotations
import os
from pathlib import Path
import stat
import tempfile

from app.panel_password_security import (
    VaultUnavailable, encrypt_panel_password, decrypt_panel_password,
    login_allowed, record_login_failure, clear_login_failures,
)

ROOT = Path(__file__).resolve().parents[1]


def run():
    assert (ROOT / "VERSION").read_text().strip() == "12.63"
    assert (ROOT / "node_agent/VERSION").read_text().strip() == "12.63"
    with tempfile.TemporaryDirectory(prefix="sf-v1263-sec-") as d:
        key=Path(d)/"vault.key"
        key.write_text(os.urandom(32).hex())
        key.chmod(0o600)
        os.environ["STREAMFORGE_PANEL_VAULT_KEY_FILE"]=str(key)
        os.environ["STREAMFORGE_LOGIN_GUARD_DB"]=str(Path(d)/"attempts.sqlite")
        for secret in ('secret123!','é特殊!Z',"' OR 1=1 --"):
            token=encrypt_panel_password(secret,42)
            assert token.startswith("v1:")
            assert secret not in token
            assert decrypt_panel_password(token,42)==secret
            for wrong in (43,44):
                try:
                    decrypt_panel_password(token,wrong)
                    raise AssertionError("bound ciphertext replay decrypted")
                except VaultUnavailable:
                    pass
            tampered=token[:-3]+("aaa" if token[-3:]!="aaa" else "bbb")
            try:
                decrypt_panel_password(tampered,42)
                raise AssertionError("tampered ciphertext decrypted")
            except VaultUnavailable:
                pass
        key.chmod(0o644)
        try:
            encrypt_panel_password("s",42)
            raise AssertionError("insecure key permissions accepted")
        except VaultUnavailable:
            pass
        key.chmod(0o600)
        assert decrypt_panel_password(None,42) is None

        scope="main"
        ip="198.51.100.44"
        user="' OR 1=1 --"
        assert login_allowed(scope,ip,user,now=1000)
        for _ in range(5):
            record_login_failure(scope,ip,user,now=1000)
        assert not login_allowed(scope,ip,user,now=1000)
        assert not login_allowed(scope,ip,user,now=1899)
        assert login_allowed(scope,ip,user,now=1900)
        assert login_allowed(scope,"198.51.100.45",user,now=1000)
        assert login_allowed("node:x",ip,user,now=1000)
        clear_login_failures(scope,ip,user)
        assert login_allowed(scope,ip,user,now=1000)
        # Global-IP bucket protects against cycling multiple account names.
        for i in range(20):
            record_login_failure("main","203.0.113.7",f"try-{i}",now=3000)
        assert not login_allowed("main","203.0.113.7","new-name",now=3000)
        assert login_allowed("main","203.0.113.8","new-name",now=3000)
        db=Path(os.environ["STREAMFORGE_LOGIN_GUARD_DB"])
        assert user.encode() not in db.read_bytes()
        # Do not allow SQL injection strings to affect database schema.
        assert login_allowed("main","203.0.113.8",user,now=3000)

    main=(ROOT/"app/main.py").read_text()
    template=(ROOT/"app/templates/admin_users.html").read_text()
    form=(ROOT/"app/templates/admin_user_form.html").read_text()
    assert "STREAMFORGE_PANEL_PASSWORD_VAULT_LOGIN_GUARD_V1263" in main
    assert "panel_user.password_vault_enc = encrypt_panel_password(password, panel_user.id)" in main
    assert "_update_panel_password(panel_user, password)" in main
    assert "_guard_allowed(\"main\", ip, submitted)" in main
    assert "_guard_allowed(node_scope, node_client, username)" in main
    assert "_trusted_login_guard_ip(request)" in main
    assert 'request.headers.get("x-real-ip")' in main
    assert 'request.headers.get("x-forwarded-proto"' in main
    assert "Use HTTPS for this operation" in main
    assert "request.headers.get(\"sec-fetch-site\"" in main
    assert "request.headers.get(\"origin\")" in main
    assert "admin_is_super_admin(actor)" in main
    assert "hmac.compare_digest(token, expected_token)" in main
    assert "verify_password(verifier_password, actor.password_hash)" in main
    assert "scope=\"auth\"" in main and "Panel password revealed" in main
    assert "password_vault_enc" in (ROOT/"app/models.py").read_text()
    assert "ALTER TABLE admin_users ADD COLUMN password_vault_enc TEXT" in (ROOT/"app/db.py").read_text()
    assert "data-vault-user-id" in template and "data-panel-vault-csrf" in template
    assert "credentials:'same-origin'" in template and "cache:'no-store'" in template
    assert "setTimeout(" in template and "10000" in template
    assert "secret.textContent = data.password" in template
    assert "STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262" in form
    assert "SELECT * FROM admin_users WHERE username = '" not in main
    print("PASS: v12.63 authenticated vault, rate limits, anti-tamper, SQL parameters and UI safety")


if __name__=="__main__":
    run()
