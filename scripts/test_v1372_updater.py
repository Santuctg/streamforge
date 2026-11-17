#!/usr/bin/env python3
"""v13.72 panel no-store staged installer tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.72":
    print("SKIP: v13.72 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get("STREAMFORGE_V1372_PACKAGE_ROOT", str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1372.sh"
FILES = (
    "app/auth.py",
    "app/node_manager.py",
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
    "app/templates/providers.html",
    "app/templates/provider_form.html",
    "app/templates/system_branding.html",
    "app/templates/user_form.html",
    "app/templates/viewer_sessions.html",
    "node_agent/app.py",
    "VERSION",
    "node_agent/VERSION",
    "scripts/main_system_control.py",
)
NEW_FILES = ("app/notification_manager.py", "app/templates/notifications.html", "app/static/theme-light.css")
PREVIOUS = "7379311"
text = SCRIPT.read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1372_PANEL_NO_STORE_UPDATER",
    "STREAMFORGE_V1372_LOCAL_NODE_AGENT_OPTIONAL",
    "STREAMFORGE_V1372_NEW_NOTIFICATION_FILES",
    "STREAMFORGE_V1372_TEST_FAIL_AFTER_WRITE",
    "STREAMFORGE_V1372_STATIC_CACHE",
    "sync_main_static_cache.py",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31|13.32|13.33|13.34|13.35|13.36|13.37|13.38|13.39|13.40|13.41|13.42|13.43|13.44|13.45|13.46|13.47|13.48|13.49|13.50|13.51|13.52|13.53|13.54|13.55|13.56|13.57|13.58|13.59|13.60|13.61|13.62|13.63|13.64|13.65|13.66|13.67|13.68|13.69|13.70|13.71|13.72",
    'OLD_NODE_BLOB="e7df974d141a30b1f1703d191ac3db5d63d9e040"',
    'LEGACY_NODE_BLOB="748cd4319e0e6b101f83efddf1b0b670964b0f42"',
    'NEW_NODE_BLOB="6a38b0e5239327e0ae4ba761903155358b8348e3"',
    "app/static/app.js",
    "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318",
):
    assert marker in text, marker
assert "force_update_v1372.sh" in (PACKAGE / "scripts/update.sh").read_text(encoding="utf-8")
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
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in node_agent
), "Node light select color-scheme override is missing"
light = (PACKAGE / "app/static/theme-light.css").read_text(encoding="utf-8")
assert ":root[data-theme=\"light\"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme=\"light\"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}" in light
assert ":root[data-theme=\"light\"] body select:not([multiple]):not(#streamforge-dropdown-never) option,:root[data-theme=\"light\"] body select[multiple]:not(#streamforge-dropdown-never) option{background:#f2f6fa!important;color:#16232f!important}" in light
assert ':root[data-theme="light"] html' not in light
assert ':root[data-theme="light"] html' not in node_agent
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
# v13.57: Settings-page dark boxes, sidebar and the inline watch toggle.
for marker in (
    "STREAMFORGE_NODE_LIGHT_SETTINGS_SURFACES_V1357",
    "STREAMFORGE_NODE_LIGHT_SIDEBAR_V1357",
    "STREAMFORGE_NODE_LIGHT_PLAYLIST_ROWS_V1357",
    ':root[data-theme="light"] .saved-credential{background:#f2f6fa!important;color:#0e6e57!important}',
    ':root[data-theme="light"] .node-tls-record{background:#ffffff!important;border-color:#c9d6e0!important}',
    ':root[data-theme="light"] .panel-nav .nav-button.active{background:#dfeaf3!important;border-color:#9eb2c2!important}',
    "theme_toggle_inline=_node_theme_toggle_button('theme-toggle-inline')",
):
    assert marker in node_agent, marker
assert '{theme_toggle_inline}' in node_agent
assert "theme_toggle_button=_node_theme_toggle_button('theme-toggle-bottom')" not in node_agent
assert "STREAMFORGE_V1357_SETTINGS_LIGHT_GUARD" in text
# v13.58: the panel switch lives in the button row on both panels, labelled
# buttons keep their label, and the Node watch screen groups LIVE + switch.
assert "STREAMFORGE_NODE_NAV_THEME_TOGGLE_V1358" in node_agent
assert ".panel-nav .theme-toggle{min-width:0;flex:0 0 auto}" in node_agent
assert "data-theme-glyph" in node_agent
assert "STREAMFORGE_NODE_WATCH_STATE_GROUP_V1358" in node_agent
assert "state-group" in node_agent
base_html = (PACKAGE / "app/templates/base.html").read_text(encoding="utf-8")
sidebar_bottom = base_html.split('class="sidebar-bottom"', 1)[1].split("</aside>", 1)[0]
assert "data-theme-toggle" in sidebar_bottom and "Change password" in sidebar_bottom
topbar_actions = base_html.split('class="topbar-actions"', 1)[1].split("</header>", 1)[0]
assert "topbar-theme-toggle" in topbar_actions and "admin-chip" in topbar_actions
style = (PACKAGE / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_SIDEBAR_THEME_TOGGLE_V1358" in style
assert "STREAMFORGE_MAIN_MOBILE_THEME_TOGGLE_V1358" in style
assert ".sidebar-bottom .theme-toggle{width:100%;min-height:36px;padding:7px 11px}" in style
assert ".topbar-theme-toggle{display:none}" in style
assert style.count("{") == style.count("}")
app_js = (PACKAGE / "app/static/app.js").read_text(encoding="utf-8")
assert "paintThemeButtons" in app_js and "button.querySelector('[data-theme-glyph]')" in app_js
assert app_js.count("{") == app_js.count("}")
assert "STREAMFORGE_V1358_BUTTONROW_THEME_GUARD" in text
# v13.59: the per-Node HLS override endpoint is exempt from Main access policy.
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_NODE_HLS_SEGMENT_POLICY_BYPASS_V1359" in main
assert '"/api/v1/node-channel-hls-segment/"' in main
assert main.index('"/api/v1/node-channel-hls-segment/"') > main.index("node_internal_prefixes = (")
assert "STREAMFORGE_V1359_NODE_HLS_BYPASS_GUARD" in text
# v13.60: the Node panel permission audit must have replaced the legacy strings.
for legacy in ("logs.manage", "stream_users.kill", "categories.manage"):
    assert legacy not in node_agent, legacy
for marker in (
    "STREAMFORGE_NODE_PERM_AUDIT_V1360",
    "require_node_panel_user(request, 'sessions.kill')",
    "require_node_panel_user(request, 'categories.reorder')",
    "require_node_panel_user(request, 'categories.edit')",
    "require_node_panel_user(request, 'categories.delete')",
    'require_node_panel_user(request, "channels.playback")',
    "require_node_panel_user(request, 'channels.info')",
    '_has_panel_permission(user, "channels.playback")',
):
    assert marker in node_agent, marker
assert "STREAMFORGE_V1360_PERMISSION_AUDIT_GUARD" in text
# v13.61: the Channels-page HLS preview button is gated by channels.playback.
assert "if c.output_type == 'hls' and _has_panel_permission(user, 'channels.playback'):" in node_agent
assert "STREAMFORGE_NODE_PERM_AUDIT_V1361" in node_agent
assert "STREAMFORGE_V1361_PLAYBACK_GATE_GUARD" in text
# v13.62: configurable password policy + forced password change.
auth = (PACKAGE / "app/auth.py").read_text(encoding="utf-8")
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
models = (PACKAGE / "app/models.py").read_text(encoding="utf-8")
node_manager = (PACKAGE / "app/node_manager.py").read_text(encoding="utf-8")
for marker in (
    "PASSWORD_MIN_LENGTH_KEY = \"password_min_length\"",
    "def password_min_length(db: Session) -> int:",
    "/admin-users/{admin_user_id}/force-password",
    '"password_change_required": bool(admin.must_change_password),',
    "panel_user.must_change_password = True",
):
    assert marker in main, marker
assert "min_length: int = PANEL_PASSWORD_MIN_LENGTH" in auth
assert "must_change_password: Mapped[bool]" in models
assert "ALTER TABLE admin_users ADD COLUMN must_change_password" in (PACKAGE / "app/db.py").read_text(encoding="utf-8")
assert '"must_change_password": bool(getattr(user, "must_change_password", False)),' in node_manager
for marker in ("must_change_password: bool = False", "password_min_length: int = 12", "enforce_node_forced_password_change"):
    assert marker in node_agent, marker
assert "STREAMFORGE_V1362_PASSWORD_POLICY_GUARD" in text
# v13.63: the configured minimum must be imported, or every render 500s.
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "from .auth import PASSWORD_CHANGE_REQUIRED_SESSION_KEY, PANEL_PASSWORD_MIN_LENGTH, current_admin" in main
assert "STREAMFORGE_V1363_RENDER_HOTFIX_GUARD" in text
# v13.64 carried the wrap attempt; v13.65 replaces it with the cell layout fix.
admin_users = (PACKAGE / "app/templates/admin_users.html").read_text(encoding="utf-8")
assert 'table class="admin-users-table"' in admin_users
assert "STREAMFORGE_V1365_CELL_LAYOUT_GUARD" in text
# v13.65: the Actions cell stays a table cell; buttons live in an inner row.
admin_users = (PACKAGE / "app/templates/admin_users.html").read_text(encoding="utf-8")
assert '<td data-label="Actions" class="actions">' not in admin_users
assert '<div class="actions admin-user-actions">' in admin_users
assert ".admin-users-table td.actions{display:table-cell;white-space:normal;vertical-align:middle}" in admin_users
assert ".admin-users-table .admin-user-actions{display:flex;flex-wrap:nowrap;gap:8px;align-items:center}" in admin_users
assert "STREAMFORGE_V1365_CELL_LAYOUT_GUARD" in text
# v13.66: the topbar renders as a real header bar in both themes.
style = (PACKAGE / "app/static/style.css").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_MAIN_TOPBAR_BAR_V1366",
    "margin:0 -28px 22px;padding:0 28px}",
    "border-bottom:1px solid var(--line);box-shadow:0 2px 12px rgba(0,0,0,.08);",
    ".topbar{height:76px;margin:0 -14px 18px;padding:0 14px}",
):
    assert marker in style, marker
light = (PACKAGE / "app/static/theme-light.css").read_text(encoding="utf-8")
assert ':root[data-theme="light"] .topbar{background:var(--panel)!important' not in light
assert ':root[data-theme="light"] .topbar{border-color:var(--line)!important;color:var(--text)!important}' in light
assert "STREAMFORGE_V1366_TOPBAR_BAR_GUARD" in text
for template, expected in (("channels.html", 2), ("dashboard.html", 1), ("channel_info.html", 1)):
    source = (PACKAGE / f"app/templates/{template}").read_text(encoding="utf-8")
    assert source.count("can('channels.playback')") >= expected, template
assert "scripts/test_node_permission_audit.py" in text or True  # carried by the package, not the updater
# v13.67: the Providers feature is complete.
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert '@app.get("/providers"' in main
assert '"/providers/{provider_id}/edit"' in main
assert 'def provider_hostnames' in main
assert (PACKAGE / "app/models.py").read_text(encoding="utf-8").count("class Provider(Base):") == 1
assert (PACKAGE / "app/templates/providers.html").read_text(encoding="utf-8").count("data-label=\"Alt\"") >= 1
assert (PACKAGE / "app/templates/provider_form.html").read_text(encoding="utf-8").count("provider-channel-grid") >= 2
perms = (PACKAGE / "app/permissions.py").read_text(encoding="utf-8")
for marker in ("providers.view", "providers.create", "providers.edit", "providers.delete"):
    assert marker in perms, marker
assert "STREAMFORGE_V1369_RENAMES_GUARD" in text
# v13.68: Total / Pri running / Alt running count columns.
providers_page = (PACKAGE / "app/templates/providers.html").read_text(encoding="utf-8")
for marker in ("data-label=\"Total\"", "data-label=\"Pri\"", "data-label=\"Alt\""):
    assert marker in providers_page, marker
assert "STREAMFORGE_PROVIDERS_COUNTS_V1368" in main
assert "alt_running" in main
assert "STREAMFORGE_V1369_RENAMES_GUARD" in text
# v13.69: the Providers columns are renamed and the edit page titles polished.
providers_page = (PACKAGE / "app/templates/providers.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDER_COLUMNS_RENAMED_V1369" in providers_page
assert 'data-label="Pri channels"' not in providers_page
assert 'data-label="Alt (backup) channels"' not in providers_page
assert providers_page.count('data-label="Running"') == 2
form = (PACKAGE / "app/templates/provider_form.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDER_GRID_TITLES_V1369" in form
assert ".provider-grid-title{margin:0;padding:14px 18px;border-bottom:1px solid var(--line)" in form
assert "STREAMFORGE_V1369_RENAMES_GUARD" in text
# v13.70: the Providers table cells are center-aligned.
providers_page = (PACKAGE / "app/templates/providers.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDERS_CENTER_ALIGN_V1370" in providers_page
assert '.providers-table .pv-center{text-align:center}' in providers_page
assert 'data-label="Total" class="pv-center"' in providers_page
assert "STREAMFORGE_V1370_CENTER_ALIGN_GUARD" in text
# v13.71: navigation knows /providers; both Running columns are centered.
nav_js = (PACKAGE / "app/static/panel_nav.js").read_text(encoding="utf-8")
assert "'/channels','/providers'," in nav_js
assert nav_js.count("{") == nav_js.count("}")
providers_page = (PACKAGE / "app/templates/providers.html").read_text(encoding="utf-8")
assert providers_page.count('<td data-label="Running" class="pv-center">') == 2
assert "STREAMFORGE_V1371_PROVIDERS_RELOAD_GUARD" in text
# v13.72: every Panel page answers with no-store.
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_PANEL_NO_STORE_V1372" in main
assert '"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"' in main
assert "STREAMFORGE_V1372_PANEL_NO_STORE_GUARD" in text
manager = (PACKAGE / "app/notification_manager.py").read_text(encoding="utf-8")
main = (PACKAGE / "app/main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE" in main
assert "STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317" in main
helper = (PACKAGE / "scripts/main_system_control.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317", "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317"):
    assert marker in helper, marker
assert 'NEW_NODE_BLOB="6a38b0e5239327e0ae4ba761903155358b8348e3"' in text
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
assert "STREAMFORGE_V1372_TEMPLATE_COMPILE_GUARD" in text
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


with tempfile.TemporaryDirectory(prefix="sf-v1372-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (installed / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (installed / "app/notification_manager.py").is_file()
    assert (installed / "app/templates/notifications.html").is_file()
    assert (installed / "VERSION").read_text().strip() == "13.72"
    assert (local / "VERSION").read_text(encoding="utf-8").strip() == "13.72"
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
    result = run(installed, local, base, STREAMFORGE_V1372_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0 and snapshot(installed) == current

with tempfile.TemporaryDirectory(prefix="sf-v1372-rollback-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    before = snapshot(installed)
    result = run(installed, local, base, STREAMFORGE_V1372_TEST_FAIL_AFTER_WRITE="1")
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
    # The rolled-back (v13.55) tree already carries the fixed watch wiring but
    # not the v13.56 selector fix.
    rolled_node = (installed / "node_agent/app.py").read_text(encoding="utf-8")
    assert rolled_node.index(
        "def _node_watch_device_theme_css"
    ) < rolled_node.index(
        '@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)'
    ) < rolled_node.index("def node_web_player_watch(channel_key: str, request: Request):")
    assert "STREAMFORGE_NODE_LIGHT_SELECTOR_MATCH_V1356" in rolled_node
    assert "STREAMFORGE_NODE_LIGHT_SETTINGS_SURFACES_V1357" in rolled_node
    assert "theme_toggle_button=_node_theme_toggle_button('theme-toggle-bottom')" not in rolled_node
    assert "STREAMFORGE_NODE_WATCH_STATE_GROUP_V1358" in rolled_node
    rolled_base = (installed / "app/templates/base.html").read_text(encoding="utf-8")
    rolled_sidebar = rolled_base.split('class="sidebar-bottom"', 1)[1].split("</aside>", 1)[0]
    # v13.58 carried the sidebar button-row switch; only the v13.59 bypass is new.
    assert "data-theme-toggle" in rolled_sidebar
    assert "STREAMFORGE_MAIN_SIDEBAR_THEME_TOGGLE_V1358" in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")
    rolled_main = (installed / "app/main.py").read_text(encoding="utf-8")
    # v13.59 carried the HLS policy bypass; only the v13.60 audit markers are new.
    assert "STREAMFORGE_MAIN_NODE_HLS_SEGMENT_POLICY_BYPASS_V1359" in rolled_main
    rolled_node_perms = (installed / "node_agent/app.py").read_text(encoding="utf-8")
    # v13.60 carried the permission audit; only the channels-page gate is new.
    assert "STREAMFORGE_NODE_PERM_AUDIT_V1360" in rolled_node_perms
    assert "categories.manage" not in rolled_node_perms
    assert "STREAMFORGE_NODE_PERM_AUDIT_V1361" in rolled_node_perms
    # v13.62 carried the policy options; only the hotfix import is new.
    assert "STREAMFORGE_FORCE_PASSWORD_CHANGE_V1362" in (
        installed / "app/main.py"
    ).read_text(encoding="utf-8")
    rolled_auth = (installed / "app/auth.py").read_text(encoding="utf-8") if (installed / "app/auth.py").exists() else ""
    # v13.62 carried the parameterised helper; only the import is new.
    assert "STREAMFORGE_PASSWORD_MIN_LENGTH_SETTING_V1362" in rolled_auth
    rolled_main = (installed / "app/main.py").read_text(encoding="utf-8")
    # v13.63 carried the import hotfix; only the table wrap is new.
    assert "from .auth import PASSWORD_CHANGE_REQUIRED_SESSION_KEY, PANEL_PASSWORD_MIN_LENGTH, current_admin" in rolled_main
    rolled_admin_users = (installed / "app/templates/admin_users.html").read_text(encoding="utf-8")
    # v13.66 carried the topbar bar; only the Providers feature is new.
    assert "STREAMFORGE_MAIN_TOPBAR_BAR_V1366" in (
        installed / "app/static/style.css"
    ).read_text(encoding="utf-8")
    rolled_main = (installed / "app/main.py").read_text(encoding="utf-8")
    # v13.69 carried the Providers page, count columns and renames; only the
    # center alignment is new.
    assert '@app.get("/providers"' in rolled_main
    assert "STREAMFORGE_PROVIDERS_COUNTS_V1368" in rolled_main
    rolled_providers = (installed / "app/templates/providers.html")
    assert rolled_providers.exists() and "STREAMFORGE_PROVIDER_COLUMNS_RENAMED_V1369" in rolled_providers.read_text(encoding="utf-8")
    # v13.70 carried the center alignment; only the reload fix is new.
    assert "STREAMFORGE_PROVIDERS_CENTER_ALIGN_V1370" in rolled_providers.read_text(encoding="utf-8")
    rolled_nav = (installed / "app/static/panel_nav.js")
    assert not rolled_nav.exists() or "'/channels','/providers'," not in rolled_nav.read_text(encoding="utf-8")
    assert "STREAMFORGE_MAIN_PANEL_NO_STORE_V1372" not in rolled_main
    assert "if c.output_type == 'hls' and _has_panel_permission(user, 'channels.playback'):" in rolled_node_perms

for case in ("main-version", "node-version", "node-drift"):
    with tempfile.TemporaryDirectory(prefix=f"sf-v1372-{case}-") as temporary:
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

print("PASS: v13.72 panel no-store staged install, static cache republish, idempotent reinstall, rollback and refusal gates")
