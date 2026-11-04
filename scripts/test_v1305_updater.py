#!/usr/bin/env python3
"""v13.05 SMS templates staged installer and rollback tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.05":
    print("SKIP: v13.05 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get("STREAMFORGE_V1305_PACKAGE_ROOT", str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1305.sh"
FILES = (
    "app/db.py",
    "app/main.py",
    "app/models.py",
    "app/notification_manager.py",
    "app/permissions.py",
    "app/static/style.css",
    "app/templates/base.html",
    "app/templates/main_updates.html",
    "app/templates/node_form.html",
    "app/templates/notifications.html",
    "app/templates/user_form.html",
    "node_agent/app.py",
    "VERSION",
    "node_agent/VERSION",
)
NEW_FILES = ("app/notification_manager.py", "app/templates/notifications.html")
PREVIOUS = "v13.02"
text = SCRIPT.read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1305_SMS_TEMPLATES_UPDATER",
    "STREAMFORGE_V1305_LOCAL_NODE_AGENT_OPTIONAL",
    "STREAMFORGE_V1305_NEW_NOTIFICATION_FILES",
    "STREAMFORGE_V1305_TEST_FAIL_AFTER_WRITE",
    "STREAMFORGE_V1305_STATIC_CACHE",
    "sync_main_static_cache.py",
    "13.01|13.02|13.03|13.04|13.05",
    'OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
    'NEW_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
):
    assert marker in text, marker
assert "force_update_v1305.sh" in (PACKAGE / "scripts/update.sh").read_text(encoding="utf-8")
style = (PACKAGE / "app/static/style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}")
manager = (PACKAGE / "app/notification_manager.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1305_SMS_TEMPLATES" in manager
assert "DEFAULT_SMS_USER_BODY" in manager and "DEFAULT_SMS_NODE_BODY" in manager


def tagged(relative: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{PREVIOUS}:{relative}"], cwd=ROOT)


def prepare(base: Path) -> tuple[Path, Path]:
    installed = base / "installed"
    for relative in FILES:
        if relative in NEW_FILES:
            continue
        payload = tagged(relative)
        path = installed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o640)
    local = base / "local-node"
    local.mkdir()
    (local / "app.py").write_bytes(tagged("node_agent/app.py"))
    (local / "VERSION").write_text("13.02\n", encoding="utf-8")
    return installed, local


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in root.rglob("*") if path.is_file()
    }


def run(installed: Path, local: Path, base: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        STREAMFORGE_ROOT=str(installed),
        STREAMFORGE_BACKUP_ROOT=str(base / "backups"),
        STREAMFORGE_LOCAL_NODE_ROOT=str(local),
        STREAMFORGE_MAIN_STATIC_CACHE_ROOT=str(base / "main-static"),
        STREAMFORGE_HOTFIX_TEST_MODE="1",
    )
    env.update(extra)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix="sf-v1304-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (installed / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (installed / "app/notification_manager.py").is_file()
    assert (installed / "app/templates/notifications.html").is_file()
    assert (installed / "VERSION").read_text().strip() == "13.05"
    assert (local / "VERSION").read_text(encoding="utf-8").strip() == "13.05"
    cache_style = base / "main-static" / "style.css"
    assert cache_style.is_file(), "Nginx static cache missing style.css"
    assert cache_style.read_bytes() == (PACKAGE / "app/static/style.css").read_bytes()
    current = snapshot(installed)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(installed) == current
    result = run(installed, local, base, STREAMFORGE_V1305_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0 and snapshot(installed) == current

with tempfile.TemporaryDirectory(prefix="sf-v1304-rollback-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    before = snapshot(installed)
    result = run(installed, local, base, STREAMFORGE_V1305_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0
    assert snapshot(installed) == before
    assert not (installed / "app/notification_manager.py").exists()
    assert "STREAMFORGE_V1305_MAESTRO_SMS" not in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")

for case in ("main-version", "node-version", "node-drift"):
    with tempfile.TemporaryDirectory(prefix=f"sf-v1304-{case}-") as temporary:
        base = Path(temporary)
        installed, local = prepare(base)
        if case == "main-version":
            (installed / "VERSION").write_text("12.99\n", encoding="utf-8")
        elif case == "node-version":
            (installed / "node_agent/VERSION").write_text("12.99\n", encoding="utf-8")
        else:
            (installed / "node_agent/app.py").write_text("# drifted", encoding="utf-8")
        before = snapshot(installed)
        assert run(installed, local, base).returncode != 0
        assert snapshot(installed) == before

print("PASS: v13.05 SMS templates staged install, static cache republish, idempotent reinstall, rollback and refusal gates")
