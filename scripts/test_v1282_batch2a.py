#!/usr/bin/env python3
"""v12.82 batch 2A: source-scheme guard, sessions.kill permission, Node API
token rotation, backup env opt-out and the secret-key startup warning."""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Bind every path the Main app touches to a scratch directory before anything
# imports app.db (the engine captures STREAMFORGE_DATABASE_URL at import).
_workdir = Path(tempfile.mkdtemp(prefix="sf-v1282-2a-"))
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{_workdir}/t.db",
    STREAMFORGE_HLS_ROOT=f"{_workdir}/h",
    STREAMFORGE_LOGO_ROOT=f"{_workdir}/logo",
    STREAMFORGE_NODE_LOGO_ROOT=f"{_workdir}/nodelogo",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{_workdir}/guard.sqlite3",
    STREAMFORGE_XTREAM_GUARD_DB=f"{_workdir}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64,
    STREAMFORGE_ADMIN_USER="admin",
    STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)
os.environ.pop("STREAMFORGE_ALLOW_LOCAL_FILE_SOURCES", None)
os.environ.pop("STREAMFORGE_SOURCE_SCHEME_ALLOWLIST", None)
os.environ.pop("STREAMFORGE_BACKUP_INCLUDE_ENV", None)

from app import backup_manager as bm  # noqa: E402
from app import input_options as io  # noqa: E402
from app.permissions import (  # noqa: E402
    ALL_PERMISSION_KEYS, PERMISSION_DEPENDENCIES, normalize_permissions,
)
from app.ssh_installer import SSHInstallError, run_node_admin_command_over_ssh  # noqa: E402


def check_source_scheme_guard() -> None:
    """Only transport URLs reach FFmpeg/ffprobe."""
    for safe in (
        "http://panel.example/streams/1.m3u8",
        "https://cdn.example:8443/live/key/index.m3u8",
        "rtsp://10.0.0.9:554/ch1",
        "udp://@239.0.0.1:1234",
        "srt://ingest.example:9000",
        "rtmp://live.example/app/key",
        "panel.example/streams/1.m3u8",          # bare host keeps working
        "http://h/a.m3u8|User-Agent=VLC&Referer=http://h/",
    ):
        assert io.ensure_safe_input_target(safe), safe

    for unsafe in (
        "file:///etc/shadow",
        "FILE:///etc/streamforge.env",           # case-insensitive scheme
        "concat:a|b",
        "lavfi://movie=/etc/passwd",
        "data:text/plain,secrets",
        "ftp://files.example/dump",
        "smb://fileserver/share",
        "/var/lib/streamforge/streamforge.db",   # scheme-less local path
        "./clip.mp4",
        "~/clip.mp4",
        "//fileserver/share/x",
    ):
        try:
            io.ensure_safe_input_target(unsafe)
        except ValueError as exc:
            assert "scheme" in str(exc) or "Local file" in str(exc), exc
        else:
            raise AssertionError(f"unsafe source accepted: {unsafe!r}")

    # The run-time choke point refuses too, so a row written by an older
    # release or an import cannot be opened by ffprobe or the encoder.
    io.build_input_args("http://h/a.m3u8")
    try:
        io.build_input_args("file:///etc/shadow|User-Agent=x")
    except ValueError as exc:
        assert "file:" in str(exc), exc
    else:
        raise AssertionError("build_input_args opened a file: source")

    # Save-time check lives where channels are created and edited.
    import app.main as m
    for kwargs in (
        {"source_urls": ["file:///etc/passwd"]},
        {"source_urls": [], "input_url": "concat:x|y"},
        {"source_urls": [], "input_url": "", "backup_inputs": "http://ok/live.m3u8\nsubfile://x"},
    ):
        try:
            m.normalize_source_configs(**kwargs)
        except ValueError as exc:
            assert "scheme" in str(exc) or "Local file" in str(exc), exc
        else:
            raise AssertionError(f"normalize_source_configs accepted {kwargs}")
    # Ordinary sources still normalize exactly as before.
    primary, backups, programs = m.normalize_source_configs(
        ["http://a/1.m3u8", "http://a/2.m3u8"], None, "", "", 7
    )
    assert primary == "http://a/1.m3u8" and backups == "http://a/2.m3u8" and programs == [None, None]


def check_secret_key_warning() -> None:
    """A published development signing key must be announced at startup."""
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "STREAMFORGE_SECRET_KEY is missing or a known development value; " in source
    assert "session cookies are signed with a public key" in source
    assert 'if secret_key in {"", "dev-change-me-now"} or len(secret_key) < 32:' in source
    # It is an error-level event, not a warning that could be missed.
    assert 'scope="system", level="error"' in source
    # The warning sits in the startup block, next to the vault warning.
    assert source.index("STREAMFORGE_VAULT_OPTIONAL_V1282") < source.index(
        "STREAMFORGE_V1282_SECRET_KEY_WARN_V1"
    )


def check_session_kill_permission_catalogue() -> None:
    assert "sessions.kill" in ALL_PERMISSION_KEYS
    assert PERMISSION_DEPENDENCIES["sessions.kill"] == {"dashboard.view"}
    # Its dependency is pulled in automatically when granted on its own.
    assert normalize_permissions(["sessions.kill"]) == ["dashboard.view", "sessions.kill"]


def check_node_token_rotate_command() -> None:
    """The rotation command is built only from a safe token character class."""
    try:
        run_node_admin_command_over_ssh(
            host="127.0.0.1", port=1, username="root", password="x",
            action="token_rotate", node_token="bad; rm -rf /tmp/pwn",
        )
    except SSHInstallError as exc:
        assert "Refusing to rotate" in str(exc), exc
    else:
        raise AssertionError("a shell metacharacter token reached the SSH command")

    try:
        run_node_admin_command_over_ssh(
            host="127.0.0.1", port=1, username="root", password="x",
            action="token_rotate", node_token="short",
        )
    except SSHInstallError as exc:
        assert "Refusing to rotate" in str(exc), exc
    else:
        raise AssertionError("a short token reached the SSH command")

    # A well-formed token passes validation and fails only on the connection,
    # which proves the command itself was accepted.
    try:
        run_node_admin_command_over_ssh(
            host="127.0.0.1", port=1, username="root", password="x",
            action="token_rotate", node_token="a" * 43,
        )
    except SSHInstallError as exc:
        assert "Refusing to rotate" not in str(exc), exc
    else:
        raise AssertionError("SSH to port 1 unexpectedly succeeded")

    installer = (ROOT / "app" / "ssh_installer.py").read_text(encoding="utf-8")
    assert "action_key == \"token_rotate\"" in installer
    assert "systemctl restart streamforge-node" in installer
    assert "grep -Fqx" in installer and "/etc/streamforge-node.env" in installer

    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "/nodes/{node_id}/token/regenerate" in main_source
    assert 'permission_required("nodes.edit")' in main_source
    assert 'action="token_rotate", node_token=new_token' in main_source
    # The push must happen before the Panel records the new value.
    assert main_source.index('action="token_rotate", node_token=new_token') < main_source.index(
        "node.api_token = new_token"
    )
    assert "Node API token rotated" in main_source

    nodes_html = (ROOT / "app" / "templates" / "nodes.html").read_text(encoding="utf-8")
    assert "/token/regenerate" in nodes_html and "Regenerate API token" in nodes_html


def check_backup_env_opt_out() -> None:
    """The env snapshot stays by default and can be dropped deliberately."""
    assert bm._backup_include_env_enabled() is True
    db_file = _workdir / "backup-src.db"
    import sqlite3

    # No channels/nodes/app_settings tables: _local_logo_references() only
    # probes tables that exist, so an empty database is a valid backup source.
    with sqlite3.connect(db_file) as connection:
        connection.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
    original = bm._configured_backup_database_path
    bm._configured_backup_database_path = lambda: db_file
    try:
        for enabled, expect_member, expect_manifest in (
            ("1", True, "environment=etc/streamforge.env (runtime STREAMFORGE_* snapshot)"),
            ("0", False, "environment=excluded by STREAMFORGE_BACKUP_INCLUDE_ENV=0"),
        ):
            os.environ["STREAMFORGE_BACKUP_INCLUDE_ENV"] = enabled
            assert bm._backup_include_env_enabled() is (enabled == "1")
            bm.ARCHIVE_ROOT = _workdir / f"archives-{enabled}"
            archive = bm._archive()
            with tarfile.open(archive, "r:gz") as tar:
                names = {str(member.name).lstrip("./") for member in tar.getmembers()}
                manifest = tar.extractfile("BACKUP-MANIFEST.txt").read().decode("utf-8")
            assert ("etc/streamforge.env" in names) is expect_member, names
            assert expect_manifest in manifest, manifest
            if not expect_member:
                # A restore of an env-less archive stays valid: the key file
                # is optional in the shared validator and the root helper.
                import app.main as m
                assert "var/lib/streamforge/streamforge.db" in m._validate_main_restore_archive(archive)
    finally:
        bm._configured_backup_database_path = original
        os.environ.pop("STREAMFORGE_BACKUP_INCLUDE_ENV", None)
        bm.ARCHIVE_ROOT = Path(tempfile.mkdtemp(prefix="sf-v1282-archives-"))


def check_panel_behaviour() -> None:
    """Permission gates, role migration and template flags in a live app."""
    from fastapi.testclient import TestClient

    import app.main as m
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AdminUser, Role
    from app.permissions import encode_permissions, role_permission_set

    m.metrics_history.path = _workdir / "metrics-history.jsonl"
    m.main_panel_client_ip = lambda request: "198.51.100.7"
    m.Base.metadata.create_all(bind=m.engine)

    with SessionLocal() as db:
        # Role created BEFORE startup: ensure_default_roles must grant the
        # capability it used to follow automatically.
        legacy = Role(
            name="Legacy viewer admin",
            description="had dashboard.view before the split",
            permissions=encode_permissions(["dashboard.view"]),
        )
        # Role without dashboard.view: must NOT receive the kill capability.
        readonly = Role(
            name="Logs only",
            description="no dashboard access",
            permissions=encode_permissions(["logs.view"]),
        )
        db.add_all([legacy, readonly])
        db.flush()
        legacy_id, readonly_id = legacy.id, readonly.id
        db.add(AdminUser(username="legacyviewer", password_hash=m.hash_password("Legacy-viewer-pass1"), role_id=legacy_id))
        db.add(AdminUser(username="logsonly", password_hash=m.hash_password("Logs-only-pass12"), role_id=readonly_id))
        # No role: startup adopts it into Super Admin (the legacy-account
        # rule), which is what the role editor check below needs.
        db.add(AdminUser(username="rootadmin", password_hash=m.hash_password("Root-admin-pass99")))
        db.commit()

    base = "http://127.0.0.1"
    with TestClient(m.app, base_url=base) as c:
        # Migration ran during startup.
        with SessionLocal() as db:
            granted = role_permission_set(db.get(Role, legacy_id))
            denied = role_permission_set(db.get(Role, readonly_id))
        assert "sessions.kill" in granted, granted
        assert "sessions.kill" not in denied, denied

        # Anonymous callers are sent to login rather than killing anything.
        r = c.post("/viewer-sessions/kill", data={"session_id": "abc"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", (r.status_code, r.headers.get("location"))

        # The pre-split role keeps the capability it used to have, so existing
        # operators do not lose their kill button on upgrade.
        assert c.post("/login", data={"username": "legacyviewer", "password": "Legacy-viewer-pass1"}, follow_redirects=False).status_code == 303
        r = c.get("/viewer-sessions", follow_redirects=False)
        assert r.status_code == 200 and 'data-can-kill="1"' in r.text, "kill control missing after migration"
        r = c.post("/viewer-sessions/kill", data={"session_id": "no-such-session"}, follow_redirects=False)
        assert r.status_code == 303 and "message=" in r.headers["location"], (r.status_code, r.headers.get("location"))

        # The kill is audited on the Panel route now.
        with SessionLocal() as db:
            rows = [
                str(row.message)
                for row in db.scalars(select(m.LogEntry).order_by(m.LogEntry.id.desc())).all()[:8]
            ]
        assert any(
            "Viewer session killed" in row or "offline session" in row for row in rows
        ), rows

        # Revoking it really revokes it: hidden control, refused route...
        with SessionLocal() as db:
            role = db.get(Role, legacy_id)
            role.permissions = encode_permissions(["dashboard.view"])
            db.commit()
        # ...and the grant does not come back on the next start-up.
        with SessionLocal() as db:
            m.ensure_default_roles(db)
            db.commit()
        with SessionLocal() as db:
            assert "sessions.kill" not in role_permission_set(db.get(Role, legacy_id)), "revocation undone by restart"
        r = c.get("/viewer-sessions", follow_redirects=False)
        # The JS always mentions the selector, so match the rendered attribute.
        assert r.status_code == 200 and 'data-can-kill="1"' not in r.text, "kill control shown without permission"
        r = c.post("/viewer-sessions/kill", data={"session_id": "abc"}, follow_redirects=False)
        assert r.status_code == 403 and "permission" in r.text.lower(), (r.status_code, r.text[:200])
        assert "Access denied" in r.text
        assert "Online session killed" not in r.text

        # Granting it explicitly restores the control and the route.
        with SessionLocal() as db:
            role = db.get(Role, legacy_id)
            role.permissions = encode_permissions(["dashboard.view", "sessions.kill"])
            db.commit()
        r = c.get("/viewer-sessions", follow_redirects=False)
        assert r.status_code == 200 and 'data-can-kill="1"' in r.text, "kill control missing with permission"
        r = c.post("/viewer-sessions/kill", data={"session_id": "no-such-session-2"}, follow_redirects=False)
        assert r.status_code == 303 and "message=" in r.headers["location"], (r.status_code, r.headers.get("location"))

        # The role without dashboard.view never received the capability, and
        # the marker row proves the migration will not run a second time.
        with SessionLocal() as db:
            assert db.get(m.AppSetting, "v1282_sessions_kill_migrated") is not None

        # Rotation route: gated by nodes.edit, never anonymous, and a local
        # node (the one ensure_local_node() seeds) has nothing to rotate.
        c.post("/logout", follow_redirects=False)
        r = c.post("/nodes/1/token/regenerate", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", (r.status_code, r.headers.get("location"))
        assert c.post(
            "/login", data={"username": "legacyviewer", "password": "Legacy-viewer-pass1"}, follow_redirects=False
        ).status_code == 303
        r = c.post("/nodes/1/token/regenerate", follow_redirects=False)
        assert r.status_code == 403 and "nodes.edit" in r.text, (r.status_code, r.text[:200])

        # The role editor publishes the new key automatically.
        with SessionLocal() as db:
            super_admin = db.scalar(select(AdminUser).where(AdminUser.username == "rootadmin"))
            assert super_admin is not None and m.admin_is_super_admin(super_admin)
            groups = m.permission_groups_for_role_editor(super_admin)
        keys = {key for entries in groups.values() for key, _label, _description in entries}
        assert "sessions.kill" in keys, keys


def main() -> None:
    check_source_scheme_guard()
    check_secret_key_warning()
    check_session_kill_permission_catalogue()
    check_node_token_rotate_command()
    check_backup_env_opt_out()
    check_panel_behaviour()
    print(
        "PASS: v12.82 batch 2A (source-scheme guard, sessions.kill permission + audit, "
        "Node API token rotation, backup env opt-out, secret-key warning)"
    )


if __name__ == "__main__":
    main()
