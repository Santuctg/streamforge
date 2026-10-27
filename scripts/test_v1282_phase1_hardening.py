#!/usr/bin/env python3
"""v12.82 phase-1 hardening: vault fallback, SMB sanitising, fail-closed backup
encryption, confirm-dialog XSS and installer vault key provisioning."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Bind every path the Main app touches to a scratch directory before anything
# imports app.db (the engine captures STREAMFORGE_DATABASE_URL at import).
_workdir = Path(tempfile.mkdtemp(prefix="sf-v1282-phase1-"))
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{_workdir}/t.db",
    STREAMFORGE_HLS_ROOT=f"{_workdir}/h",
    STREAMFORGE_LOGO_ROOT=f"{_workdir}/l",
    STREAMFORGE_NODE_LOGO_ROOT=f"{_workdir}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{_workdir}/guard.sqlite3",
    STREAMFORGE_XTREAM_GUARD_DB=f"{_workdir}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64,
    STREAMFORGE_ADMIN_USER="admin",
    STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)
# The point of this check: no vault key at all.
os.environ.pop("STREAMFORGE_PANEL_PASSWORD_VAULT_KEY", None)

from app import backup_manager as bm  # noqa: E402
from app import panel_password_vault as vault  # noqa: E402


def check_smb_sanitising() -> None:
    # Payload form that smbclient would execute as local shell commands: the
    # destination is embedded in one "-c" string where ";" separates commands
    # and "!" runs a local shell command.
    for bad in (
        "//server/share/x; ! touch /tmp/pwn",
        "//server/share/keep!me",
        "//server/share/quote\"break",
        'smb://server/share/x; id',
        "//server/share/\nuid",
    ):
        try:
            bm._parse_smb_destination(bad)
        except ValueError as exc:
            assert "must not contain" in str(exc), exc
        else:
            raise AssertionError(f"SMB destination accepted: {bad!r}")

    # Valid destinations keep working, including unicode folder names.
    assert bm._parse_smb_destination("//server/share/backups") == ("server", "share", "backups")
    assert bm._parse_smb_destination("smb://10.0.0.5/share/daily/run-1") == ("10.0.0.5", "share", "daily/run-1")
    assert bm._parse_smb_destination("\\\\server\\share\\ওয়েব") == ("server", "share", "ওয়েব")

    # The "-A" credentials file must not accept line breaks (format injection).
    with tempfile.TemporaryDirectory(prefix="sf-smb-auth-test-") as tmp:
        bm.BACKUP_SMB_AUTH_ROOT = Path(tmp)
        path = bm._write_smb_auth_file("svc", "pass word;!", "")
        assert path.exists() and "username = svc" in path.read_text(encoding="utf-8")
        for bad_value, label in (("svc\nroot = yes", "username"), ("pw\nextra = 1", "password"), ("dom\nx = 1", "domain")):
            try:
                bm._write_smb_auth_file(bad_value, bad_value, bad_value)
            except ValueError as exc:
                assert label in str(exc) or "must not contain" in str(exc), exc
            else:
                raise AssertionError(f"{label} with a line break was accepted")

        # Saving such a destination must fail loudly in the panel, not at run
        # time; the secret box is redirected so this works on a developer host.
        bm.BACKUP_SECRET_KEY = Path(tmp) / "secret.key"
        try:
            bm.add_target("SMB", "smb", "//server/share/x; ! id", username="u", password="p")
        except ValueError as exc:
            assert "must not contain" in str(exc), exc
        else:
            raise AssertionError("add_target accepted an injecting SMB destination")


def check_backup_encryption_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="sf-backup-enc-test-") as tmp:
        secret = Path(tmp) / "secret.key"
        encryption = Path(tmp) / "backup-encryption.json"
        bm.BACKUP_SECRET_KEY = secret
        bm.BACKUP_ENCRYPTION_FILE = encryption

        # Never configured: plaintext delivery stays an explicit choice.
        assert bm.backup_encryption_passphrase() == ""
        assert bm.required_backup_encryption_passphrase() == ""
        assert bm.backup_encryption_enabled() is False

        # Configured but unreadable (lost secret.key / corrupt payload): the
        # upload path must refuse rather than silently downgrade to plaintext.
        encryption.write_text(json.dumps({"passphrase_enc": "not-a-fernet-token"}), encoding="utf-8")
        assert bm.backup_encryption_passphrase() == ""  # lenient readers stay lenient
        assert bm.backup_encryption_enabled() is False
        try:
            bm.required_backup_encryption_passphrase()
        except RuntimeError as exc:
            assert "refusing to upload an unencrypted archive" in str(exc)
        else:
            raise AssertionError("configured-but-unreadable encryption was allowed to run")

        # A configured passphrase that still decrypts keeps working.
        bm.save_backup_encryption_passphrase("correct horse battery staple")
        assert bm.required_backup_encryption_passphrase() == "correct horse battery staple"
        assert bm.backup_encryption_enabled() is True

        # Clearing the setting returns the deployment to plaintext delivery.
        bm.clear_backup_encryption_passphrase()
        assert bm.required_backup_encryption_passphrase() == ""


def check_vault_fallback() -> None:
    # No key in the test environment: the vault reports itself unavailable and
    # vault_store() refuses, which is exactly why the callers now gate on it.
    os.environ.pop("STREAMFORGE_PANEL_PASSWORD_VAULT_KEY", None)
    assert vault.vault_enabled() is False
    assert vault.vault_store(None, None, "whatever") is False

    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    # Every password-bearing write is hash-only when the key is missing.
    assert source.count("if vault_enabled() and not vault_store(") == 4, source.count(
        "if vault_enabled() and not vault_store("
    )
    assert "Managed password storage is unavailable; password not changed." not in source
    # Once at startup plus the four guarded password writes.
    assert source.count("STREAMFORGE_VAULT_OPTIONAL_V1282") >= 5
    assert "Panel password vault disabled: STREAMFORGE_PANEL_PASSWORD_VAULT_KEY is not set" in source

    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "STREAMFORGE_PANEL_PASSWORD_VAULT_KEY=$VAULT_KEY" in install
    assert "^STREAMFORGE_PANEL_PASSWORD_VAULT_KEY=" in install
    assert "os.urandom(32)" in install


def check_confirm_dialog_xss() -> None:
    # User-controlled names must never sit inside an inline JavaScript string:
    # Jinja's HTML escaping is decoded by the browser before the handler runs,
    # so &#39; would still close the literal.
    offender = re.compile(r"onsubmit=\"[^\"]*confirm\('[^\"]*\{\{")
    offenders = []
    for template in sorted((ROOT / "app" / "templates").glob("*.html")):
        text = template.read_text(encoding="utf-8")
        for match in offender.finditer(text):
            offenders.append(template.name)
    # admin_users.html interpolates usernames, which are locked to
    # [A-Za-z0-9_.-]{3,80} by both the create and the edit handlers.
    assert set(offenders) <= {"admin_users.html"}, offenders

    for name in ("categories.html", "nodes.html", "roles.html", "node_update.html"):
        text = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert 'data-confirm-name="' in text, name
        assert "+ this.getAttribute('data-confirm-name') +" in text, name

    # Username charset stays the reason admin_users.html is safe.
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert source.count('re.fullmatch(r"[A-Za-z0-9_.-]{3,80}"') >= 2


def check_password_change_without_vault() -> None:
    """A install without a vault key must still rotate the default password.

    Before v12.82 this exact flow answered 503 and kept the published
    admin/ChangeMe123! credential alive forever.
    """
    from fastapi.testclient import TestClient

    import app.main as m
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Role

    m.metrics_history.path = _workdir / "metrics-history.jsonl"
    m.main_panel_client_ip = lambda request: "198.51.100.7"
    base = "http://127.0.0.1"
    assert vault.vault_enabled() is False

    def login(client, password):
        return client.post(
            "/login", data={"username": "admin", "password": password}, follow_redirects=False
        )

    with TestClient(m.app, base_url=base) as c:
        r = login(c, "ChangeMe123!")
        assert r.status_code == 303 and r.headers["location"] == "/account/password", (
            r.status_code,
            r.headers.get("location"),
        )
        new_password = "A-much-better-pass1"
        r = c.post(
            "/account/password",
            data={
                "current_password": "ChangeMe123!",
                "new_password": new_password,
                "new_password_confirm": new_password,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, (r.status_code, r.text[:400])
        # Hash-only save is disclosed on the success page.
        assert "Managed+password+storage+is+off" in r.headers["location"], r.headers["location"]
        # The forced-change gate clears, so the Panel is usable again.
        assert c.get("/", follow_redirects=False).status_code == 200
        # Creating a panel user must not depend on the vault either.
        with SessionLocal() as db:
            role_id = db.scalar(select(Role.id).order_by(Role.id))
        r = c.post(
            "/admin-users/new",
            data={
                "username": "phase1helper",
                "password": "Another-fine-pass9",
                "password_confirm": "Another-fine-pass9",
                "role_id": str(role_id),
                "is_active": "1",
                "main_panel_access": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303 and "Panel+user+created" in r.headers["location"], (
            r.status_code,
            r.headers.get("location"),
            r.text[:400],
        )
        assert vault.vault_enabled() is False  # still hash-only, no key appeared

    # A later login with the new password works (the hash was really written)
    # and the published default no longer authenticates at all.
    with TestClient(m.app, base_url=base) as c:
        assert login(c, new_password).status_code == 303
        assert login(c, "ChangeMe123!").status_code != 303


def main() -> None:
    check_smb_sanitising()
    check_backup_encryption_fail_closed()
    check_vault_fallback()
    check_confirm_dialog_xss()
    check_password_change_without_vault()
    print("PASS: v12.82 phase-1 hardening (vault fallback, SMB sanitize, fail-closed backup encryption, confirm XSS, installer vault key)")


if __name__ == "__main__":
    main()
