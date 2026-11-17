#!/usr/bin/env python3
"""v13.55 watch-route wiring staged installer tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.55":
    print("SKIP: v13.55 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get("STREAMFORGE_V1355_PACKAGE_ROOT", str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1355.sh"
FILES = (
    "app/db.py",
    "app/main.py",
    "app/models.py",
    "app/notification_manager.py",
    "app/permissions.py",
    "app/static/app.js",
    "app/static/style.css",
    "app/static/dashboard.js",
    "app/static/theme-light.css",
    "app/templates/base.html",
    "app/templates/admin_users.html",
    "app/templates/backups.html",
    "app/templates/dashboard.html",
    "app/templates/login.html",
    "app/templates/logs.html",
    "app/templates/web_player.html",
    "app/templates/main_updates.html",
    "app/templates/node_asn.html",
    "app/templates/node_form.html",
    "app/templates/node_webplayer_manage.html",
    "app/templates/nodes.html",
    "app/templates/notifications.html",
    "app/templates/player.html",
    "app/templates/system_branding.html",
    "app/templates/user_form.html",
    "app/templates/viewer_sessions.html",
    "node_agent/app.py",
    "VERSION",
    "node_agent/VERSION",
    "scripts/main_system_control.py",
)
NEW_FILES = ("app/notification_manager.py", "app/templates/notifications.html", "app/static/theme-light.css")
PREVIOUS = "d9fc41b"
text = SCRIPT.read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1355_WATCH_ROUTE_WIRING_UPDATER",
    "STREAMFORGE_V1355_LOCAL_NODE_AGENT_OPTIONAL",
    "STREAMFORGE_V1355_NEW_NOTIFICATION_FILES",
    "STREAMFORGE_V1355_TEST_FAIL_AFTER_WRITE",
    "STREAMFORGE_V1355_STATIC_CACHE",
    "sync_main_static_cache.py",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31|13.32|13.33|13.34|13.35|13.36|13.37|13.38|13.39|13.40|13.41|13.42|13.43|13.44|13.45|13.46|13.47|13.48|13.49|13.50|13.51|13.52|13.53|13.54|13.55",
    'OLD_NODE_BLOB="e7df974d141a30b1f1703d191ac3db5d63d9e040"',
    'LEGACY_NODE_BLOB="748cd4319e0e6b101f83efddf1b0b670964b0f42"',
    'NEW_NODE_BLOB="77f59232a437de74c84996910bf74845b7da7543"',
    "app/static/app.js",
    "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318",
):
    assert marker in text, marker
assert "force_update_v1355.sh" in (PACKAGE / "scripts/update.sh").read_text(encoding="utf-8")
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
# v13.53: the Node Agent must keep the audited Dark/Light theme on every surface.
assert "STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348" in node_agent
assert ':root[data-theme="light"] input[type=checkbox]:checked::after{background:#071c18!important}' in node_agent
assert "STREAMFORGE_NODE_LIGHT_SURFACES_V1353" in node_agent
assert "STREAMFORGE_NODE_DEVICE_THEME_SHARED_SCRIPT_V1353" in node_agent
assert "STREAMFORGE_NODE_DEVICE_THEME_TOGGLE_CSS_V1353" in node_agent
assert "STREAMFORGE_NODE_LOGIN_DEVICE_THEME_LIGHT_V1353" in node_agent
assert "STREAMFORGE_NODE_WEBPLAYER_DEVICE_THEME_LIGHT_V1353" in node_agent
assert "STREAMFORGE_NODE_WATCH_DEVICE_THEME_LIGHT_V1353" in node_agent
assert "STREAMFORGE_NODE_EXPIRED_DEVICE_THEME_LIGHT_V1353" in node_agent
assert "STREAMFORGE_NODE_DISCONNECTED_DEVICE_THEME_LIGHT_V1353" in node_agent
assert node_agent.count("_node_device_theme_script()") >= 6
assert node_agent.count("_node_theme_toggle_button(") >= 7
assert node_agent.index("def _node_panel_css()") < node_agent.index("STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348") < node_agent.index("def _node_panel_document(")
# v13.54: light dropdown popups follow the element color-scheme, and the Main
# watch screen loads self-hosted HLS.js from the root /static/ cache.
assert (
    ':root[data-theme="light"] html body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] html body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in node_agent
), "Node light select color-scheme override is missing"
light = (PACKAGE / "app/static/theme-light.css").read_text(encoding="utf-8")
assert ":root[data-theme=\"light\"] select:not([multiple]),:root[data-theme=\"light\"] select[multiple]{color-scheme:light!important}" in light
assert ":root[data-theme=\"light\"] select:not([multiple]) option,:root[data-theme=\"light\"] select[multiple] option{background:#f2f6fa!important;color:#16232f!important}" in light
player = (PACKAGE / "app/templates/player.html").read_text(encoding="utf-8")
hls_lines = [line for line in player.splitlines() if "static/vendor/hls.min.js" in line]
assert len(hls_lines) >= 2 and all("prefix" not in line for line in hls_lines), hls_lines
assert "STREAMFORGE_LIGHT_SELECT_POPUP_V1354" in light
# v13.55: every route decorator must bind to a handler, and the watch route
# must bind node_web_player_watch (v13.53-v13.54 shipped the CSS helper
# wedged between them, so every channel click returned the CSS text).
node_lines = node_agent.splitlines()
for _i, _line in enumerate(node_lines):
    _stripped = _line.strip()
    if _stripped.startswith("@app.") or _stripped.startswith("@router."):
        _j = _i + 1
        while _j < len(node_lines) and (
            not node_lines[_j].strip() or node_lines[_j].strip().startswith("#")
        ):
            _j += 1
        _next = node_lines[_j].strip() if _j < len(node_lines) else ""
        assert not _next or _next.startswith("def ") or _next.startswith("async def ") or _next.startswith("@"), (
            f"route decorator at source line {_i + 1} is not bound to a handler: {_stripped[:70]}"
        )
assert node_agent.index("def _node_watch_device_theme_css") < node_agent.index(
    '@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)'
) < node_agent.index("def node_web_player_watch(channel_key: str, request: Request):")
assert "STREAMFORGE_V1355_ROUTE_WIRING_GUARD" in text
manager = (PACKAGE / "app/notification_manager.py").read_text(encoding="utf-8")
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE" in main
assert "STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317" in main
helper = (PACKAGE / "scripts/main_system_control.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317", "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317"):
    assert marker in helper, marker
assert 'NEW_NODE_BLOB="77f59232a437de74c84996910bf74845b7da7543"' in text
assert "STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER" in style
assert "STREAMFORGE_THEME_COLOR_TOKENS_V1349" in style
assert "STREAMFORGE_MAIN_USER_CARD_THEME_TOGGLE_V1350" in style
light = (PACKAGE / "app/static/theme-light.css").read_text(encoding="utf-8")
assert "STREAMFORGE_LIGHT_CHECKBOX_TICK_V1350" in light
assert "input[type=checkbox]:checked::after{background:#071c18!important}" in light
base_html = (PACKAGE / "app/templates/base.html").read_text(encoding="utf-8")
assert "admin-chip-id" in base_html and "data-theme-toggle" in base_html
web_player = (PACKAGE / "app/templates/web_player.html").read_text(encoding="utf-8")
assert "STREAMFORGE_WEBPLAYER_TEMPLATE_SYNTAX_V1351" in web_player
assert "</style>\n{% endif %}\n</head>" not in web_player
assert "STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344" in web_player
player = (PACKAGE / "app/templates/player.html").read_text(encoding="utf-8")
assert "STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344" in player
assert "STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352" in player
assert "STREAMFORGE_DEVICE_LOCAL_THEME_TOGGLE_V1344" in player
assert "data-theme-toggle" in player
assert 'html[data-theme="light"] body.player-body' in player
verify_script = PACKAGE / "scripts/verify_template_syntax.py"
assert verify_script.is_file()
assert "STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351" in verify_script.read_text(encoding="utf-8")
assert "STREAMFORGE_V1355_TEMPLATE_COMPILE_GUARD" in text
assert ":root[data-theme=\"light\"]{" in style
assert "--sf-bg-0b1118:#0b1118" in style
assert "--sf-fg-edf4fb:#edf4fb" in style
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


with tempfile.TemporaryDirectory(prefix="sf-v1355-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (installed / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (installed / "app/notification_manager.py").is_file()
    assert (installed / "app/templates/notifications.html").is_file()
    assert (installed / "VERSION").read_text().strip() == "13.55"
    assert (local / "VERSION").read_text(encoding="utf-8").strip() == "13.55"
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
    result = run(installed, local, base, STREAMFORGE_V1355_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0 and snapshot(installed) == current

with tempfile.TemporaryDirectory(prefix="sf-v1355-rollback-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    before = snapshot(installed)
    result = run(installed, local, base, STREAMFORGE_V1355_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0
    assert snapshot(installed) == before
    assert not (installed / "app/notification_manager.py").exists()
    assert "STREAMFORGE_V1317_MAESTRO_SMS" not in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")
    # The rolled-back tree keeps the carried v13.52/v13.53 theme work but must
    # not carry the v13.54 fixes.
    assert "STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352" in (
        installed / "app/templates/player.html"
    ).read_text(encoding="utf-8")
    # v13.53/v13.54 markers are carried in the previous tree, so they stay
    # present; only the v13.55 wiring fix must be absent.
    assert "STREAMFORGE_NODE_DEVICE_THEME_SHARED_SCRIPT_V1353" in (
        installed / "node_agent/app.py"
    ).read_text(encoding="utf-8")
    assert "STREAMFORGE_NODE_LIGHT_SELECT_POPUP_V1354" in (
        installed / "node_agent/app.py"
    ).read_text(encoding="utf-8")
    assert not (installed / "app/static/theme-light.css").exists() or (
        "STREAMFORGE_LIGHT_SELECT_POPUP_V1354" not in
        (installed / "app/static/theme-light.css").read_text(encoding="utf-8")
    )
    # The rolled-back (v13.54) tree already loads HLS.js from the root path.
    rolled_player = (installed / "app/templates/player.html").read_text(encoding="utf-8")
    assert all("prefix" not in line for line in rolled_player.splitlines() if "static/vendor/hls.min.js" in line)
    # The rolled-back (v13.54) tree still carries the broken watch wiring.
    rolled_node = (installed / "node_agent/app.py").read_text(encoding="utf-8")
    assert rolled_node.index(
        '@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)'
    ) < rolled_node.index("def _node_watch_device_theme_css")

for case in ("main-version", "node-version", "node-drift"):
    with tempfile.TemporaryDirectory(prefix=f"sf-v1355-{case}-") as temporary:
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

print("PASS: v13.55 watch-route wiring staged install, static cache republish, idempotent reinstall, rollback and refusal gates")
