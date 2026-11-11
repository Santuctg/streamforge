#!/usr/bin/env python3
"""v13.27 info-button help text staged installer tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.27":
    print("SKIP: v13.27 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get("STREAMFORGE_V1327_PACKAGE_ROOT", str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1327.sh"
FILES = (
    "app/db.py",
    "app/main.py",
    "app/models.py",
    "app/notification_manager.py",
    "app/permissions.py",
    "app/static/app.js",
    "app/static/style.css",
    "app/templates/base.html",
    "app/templates/main_updates.html",
    "app/templates/node_form.html",
    "app/templates/notifications.html",
    "app/templates/system_branding.html",
    "app/templates/user_form.html",
    "node_agent/app.py",
    "VERSION",
    "node_agent/VERSION",
    "scripts/main_system_control.py",
)
NEW_FILES = ("app/notification_manager.py", "app/templates/notifications.html")
PREVIOUS = "3060443"
text = SCRIPT.read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1327_EXPLICIT_CONTAINER_HELP_MOUNT_UPDATER",
    "STREAMFORGE_V1327_LOCAL_NODE_AGENT_OPTIONAL",
    "STREAMFORGE_V1327_NEW_NOTIFICATION_FILES",
    "STREAMFORGE_V1327_TEST_FAIL_AFTER_WRITE",
    "STREAMFORGE_V1327_STATIC_CACHE",
    "sync_main_static_cache.py",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27",
    'OLD_NODE_BLOB="43d18ca8056512029e2628ab8314b498b2c9fff4"',
    'NEW_NODE_BLOB="7cf9f6174af1fcfe802d3935e476864ad791139f"',
    "app/static/app.js",
    "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318",
):
    assert marker in text, marker
assert "force_update_v1327.sh" in (PACKAGE / "scripts/update.sh").read_text(encoding="utf-8")
style = (PACKAGE / "app/static/style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}")
assert "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318" in style
assert "body .sf-help-source{display:none!important}" in style
assert "body .sf-help-bubble.sf-help-bubble{" in style
assert "body .sf-help-btn.sf-help-btn{" in style
app_js = (PACKAGE / "app/static/app.js").read_text(encoding="utf-8")
assert "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318" in app_js
assert "btn.type = 'button'" in app_js
assert "sf-help-bubble" in app_js and "sf-help-source" in app_js
node_agent = (PACKAGE / "node_agent/app.py").read_text(encoding="utf-8")
assert "STREAMFORGE_NODE_EXPIRY_DISK_REFRESH_V1320" in node_agent
assert '"supervisor_recovery": "pending_until_service_ready"' in node_agent
manager = (PACKAGE / "app/notification_manager.py").read_text(encoding="utf-8")
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE" in main
assert "STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317" in main
helper = (PACKAGE / "scripts/main_system_control.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317", "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317"):
    assert marker in helper, marker
assert 'NEW_NODE_BLOB="7cf9f6174af1fcfe802d3935e476864ad791139f"' in text
assert "STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER" in style
assert "position:fixed!important" in style and "left:9.5px!important" in style
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
        STREAMFORGE_MAIN_HELPER_BIN=str(base / "helper/streamforge-main-system-control"),
        STREAMFORGE_HOTFIX_TEST_MODE="1",
    )
    env.update(extra)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix="sf-v1327-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (installed / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (installed / "app/notification_manager.py").is_file()
    assert (installed / "app/templates/notifications.html").is_file()
    assert (installed / "VERSION").read_text().strip() == "13.27"
    assert (local / "VERSION").read_text(encoding="utf-8").strip() == "13.27"
    cache_style = base / "main-static" / "style.css"
    assert cache_style.is_file(), "Nginx static cache missing style.css"
    assert cache_style.read_bytes() == (PACKAGE / "app/static/style.css").read_bytes()
    cache_app_js = base / "main-static" / "app.js"
    assert cache_app_js.is_file(), "Nginx static cache missing app.js"
    assert cache_app_js.read_bytes() == (PACKAGE / "app/static/app.js").read_bytes()
    current = snapshot(installed)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(installed) == current
    result = run(installed, local, base, STREAMFORGE_V1327_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0 and snapshot(installed) == current

with tempfile.TemporaryDirectory(prefix="sf-v1327-rollback-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    before = snapshot(installed)
    result = run(installed, local, base, STREAMFORGE_V1327_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0
    assert snapshot(installed) == before
    assert not (installed / "app/notification_manager.py").exists()
    assert "STREAMFORGE_V1317_MAESTRO_SMS" not in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")
    # The rolled-back tree must not carry the v13.27 tooltip layout payload.
    assert "STREAMFORGE_HELP_BUTTON_WRAPPED_TITLE_LAYOUT_V1327" not in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")

for case in ("main-version", "node-version", "node-drift"):
    with tempfile.TemporaryDirectory(prefix=f"sf-v1327-{case}-") as temporary:
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

print("PASS: v13.27 info-button help staged install, static cache republish, idempotent reinstall, rollback and refusal gates")
