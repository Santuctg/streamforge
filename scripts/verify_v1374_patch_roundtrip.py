#!/usr/bin/env python3
"""v13.74 panel save hotfix release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.74":
    print("SKIP: v13.74 release round-trip")
    raise SystemExit(0)

# --- Carried help-text info buttons (v13.18) --------------------------------
MARKER = "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318"
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}"), "style.css braces unbalanced"
for marker in (
    MARKER,
    "STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER",
    "position:fixed!important", "left:9.5px!important", "width:56px!important",
    "body .sf-help-source{display:none!important}",
    "body .sf-help-bubble.sf-help-bubble{",
    "body .sf-help-btn.sf-help-btn{",
    "min-height:15px",
    "z-index:20000",
):
    assert marker in style, marker
for marker in (
    "STREAMFORGE_THEME_COLOR_TOKENS_V1349",
    ":root[data-theme=\"light\"]{",
    "--sf-bg-0b1118:#0b1118",
    "--sf-bd-304156:#304156",
    "--sf-fg-edf4fb:#edf4fb",
    "--sf-bg-0b1118:#ffffff",
    "--sf-fg-edf4fb:#172535",
):
    assert marker in style, marker
app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
for marker in (
    MARKER,
    "btn.type = 'button'",
    "event.preventDefault()",
    "event.stopPropagation()",
    "SOURCE_CLASS = 'sf-help-source'",
    "BUBBLE_CLASS = 'sf-help-bubble'",
    "sf-help-standalone",
    "role', 'tooltip'",
    "data-destination-help",
):
    assert marker in app_js, marker

# --- SMS integration still intact ------------------------------------------
manager = (ROOT / "app" / "notification_manager.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_EXACT_UI_TEMPLATE_FIX", "contact_phone", "sms.maestro.com.bd", "DEFAULT_SMS_USER_BODY", "DEFAULT_SMS_NODE_BODY"):
    assert marker in manager, marker
for path in ("app/models.py", "app/db.py", "app/main.py", "app/templates/notifications.html"):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "sms_notifications" in text or "SMS" in text, path
node_form = (ROOT / "app/templates/node_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_NODE_OWNER_SINGLE_ROW", "node-owner-notification-row"):
    assert marker in node_form, marker
assert 'render_template(sms.user_body, values)' in manager

# --- Version and updater wiring -------------------------------------------
for path in ("VERSION", "node_agent/VERSION"):
    assert (ROOT / path).read_text().strip() == "13.74", path

updater = (ROOT / "scripts" / "force_update_v1374.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1374_PANEL_SAVE_HOTFIX_UPDATER",
    "app/static/app.js",
    "app/static/style.css",
    "STREAMFORGE_V1374_STATIC_CACHE",
    "sync_main_static_cache.py",
    "STREAMFORGE_V1374_TEST_FAIL_AFTER_WRITE",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31|13.32|13.33|13.34|13.35|13.36|13.37|13.38|13.39|13.40|13.41|13.42|13.43|13.44|13.45|13.46|13.47|13.48|13.49|13.50|13.51|13.52|13.53|13.54|13.55|13.56|13.57|13.58|13.59|13.60|13.61|13.62|13.63|13.64|13.65|13.66|13.67|13.68|13.69|13.70|13.71|13.72|13.73|13.74",
    "contact_phone",
    "sms_notifications",
    "sms.maestro.com.bd",
    'OLD_NODE_BLOB="e7df974d141a30b1f1703d191ac3db5d63d9e040"',
    'LEGACY_NODE_BLOB="748cd4319e0e6b101f83efddf1b0b670964b0f42"',
    'NEW_NODE_BLOB="6a38b0e5239327e0ae4ba761903155358b8348e3"',
    MARKER,
):
    assert marker in updater, marker
assert "systemctl restart nginx" not in updater
assert "force_update_v1374.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

staging = (ROOT / "scripts" / "verify_release_updater_staging.py").read_text(encoding="utf-8")

# --- Release notes, changelog and docs ------------------------------------
notes = (ROOT / "release-notes" / "v13.74.md").read_text(encoding="utf-8")
assert "streamforge-update --force /root/streamforge-13.74.zip" in notes

changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert changelog.startswith("## 13.74 - ")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "releases/tag/v13.74" in readme
assert "streamforge-13.74.zip" in readme

installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
assert "VERSION=13.74\n" in installation

api = (ROOT / "docs" / "API_AND_TESTING.md").read_text(encoding="utf-8")
for name in ("test_v1374_updater.py", "verify_v1374_patch_roundtrip.py", "verify_template_syntax.py", "test_v1318_help_buttons.py", "test_node_permission_audit.py", "test_password_policy_options.py", "test_route_handler_tails.py"):
    assert name in api, name

# --- v13.11 Notification Center contract still intact ----------------------
page = (ROOT / "app" / "templates" / "notifications.html").read_text(encoding="utf-8")
Environment().parse(page)
for marker in (
    'action="/system/notifications/settings"',
    'action="/system/notifications/test"',
    "notification-summary-grid",
    "notification-recipient-grid",
    "@media(max-width:700px)",
):
    assert marker in page, marker

main_updates = (ROOT / "app" / "templates" / "main_updates.html").read_text(encoding="utf-8")
assert "confirm('Update Main Server to v{{" not in main_updates
assert "confirm('Update the Main Server now?" in main_updates

main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
user_form = (ROOT / "app" / "templates" / "user_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE", '"status": "scheduled"', '"request_id": request_id'):
    assert marker in main, marker
for marker in ("data-message-modal", "modal.showModal()"):
    assert marker in page, marker
assert '/system/notifications/timezone' not in page
for marker in ("user-notification-settings", "<b>Email notifications</b>", "<b>SMS notifications</b>"):
    assert marker in user_form, marker
settings_page = (ROOT / "app" / "templates" / "system_branding.html").read_text(encoding="utf-8")
for marker in ('name="system_timezone"', "System time zone"):
    assert marker in settings_page, marker

# --- v13.16 Node WebPlayer duplicate stream_id contract --------------------
node_app = (ROOT / "node_agent" / "app.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_NODE_WEBPLAYER_UNIQUE_STREAM_REF_V1316",
    "STREAMFORGE_NODE_WEBPLAYER_KEY_REF_V1316",
    "STREAMFORGE_NODE_PLAYBACK_RESOLVE_KEY_FIRST_V1316",
    "urllib.parse.quote(channel.key, safe='')}/",
    "urllib.parse.quote(item.key, safe='')}/",
    "authorized_channel_map.setdefault(stream_ref, authorized_channel.key)",
):
    assert marker in node_app, marker
assert "urllib.parse.quote(str(item_stream_id), safe='')/" not in node_app
import hashlib
node_digest = hashlib.sha1((ROOT / "node_agent" / "app.py").read_bytes()).hexdigest()
assert node_digest == "6a38b0e5239327e0ae4ba761903155358b8348e3", node_digest

# --- v13.17 GitHub rate-limit contract carried unchanged -------------------
helper = (ROOT / "scripts" / "main_system_control.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317",
    "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317",
    "STREAMFORGE_GITHUB_TOKEN",
    "/releases/tags/",
):
    assert marker in helper, marker
for marker in ("STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317", "STREAMFORGE_GITHUB_TOKEN"):
    assert marker in main, marker
release_credit = main.split("def _github_latest_release", 1)[1].split("try:", 1)[0]
assert "< 300:" in release_credit

# --- Carried v13.18 help-button contract -----------------------------------
import subprocess
subprocess.run(["python3", str(ROOT / "scripts" / "test_v1321_access_tooltip.py")], check=True)

# --- v13.49 token light-theme contract -------------------------------------
base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
assert "STREAMFORGE_LIGHT_THEME_LATE_STYLESHEET_V1348" in base
assert "theme-light.css" in base
assert base.index("STREAMFORGE_LIGHT_THEME_LATE_STYLESHEET_V1348") > base.index("{% block content %}")
light = (ROOT / "app" / "static" / "theme-light.css").read_text(encoding="utf-8")
assert "STREAMFORGE_LIGHT_THEME_OVERRIDES_V1348" in light
assert ':root[data-theme="light"] .metrics-legend [data-legend-value]' in light
assert "theme-light.css" in updater
assert "app/templates/viewer_sessions.html" in updater
assert "app/templates/player.html" in updater
web_player = (ROOT / "app" / "templates" / "web_player.html").read_text(encoding="utf-8")
assert "querySelectorAll('[data-theme-toggle]')" in web_player
dashboard = (ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
assert "themeLight" in dashboard and "streamforge:themechange" in dashboard
assert "STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348" in node_app

# --- v13.50 name-card theme switch and light checkbox ----------------------
assert "STREAMFORGE_MAIN_USER_CARD_THEME_TOGGLE_V1350" in style
assert "STREAMFORGE_LIGHT_CHECKBOX_TICK_V1350" in light
assert "input[type=checkbox]:checked::after{background:#071c18!important}" in light
assert "input[type=checkbox]:checked::after{background:#f2f6fa" not in light, (
    "light theme paints the checkmark white again"
)
assert 'input[type=checkbox]:checked{background:#37c7a2!important;border-color:#37c7a2!important}' in light
card = base.split('class="topbar-actions"', 1)[1].split("</header>", 1)[0]
assert "admin-chip" in card and "admin-chip-id" in card and "topbar-theme-toggle" in card
assert ".topbar .admin-chip-id{display:none}" in style
# v13.58: the signed-in switch lives in the sidebar button row on desktop.
sidebar_bottom = base.split('class="sidebar-bottom"', 1)[1].split("</aside>", 1)[0]
assert "data-theme-toggle" in sidebar_bottom and "Change password" in sidebar_bottom
assert "STREAMFORGE_MAIN_SIDEBAR_THEME_TOGGLE_V1358" in style
assert "STREAMFORGE_MAIN_MOBILE_THEME_TOGGLE_V1358" in style
assert ".sidebar-bottom .theme-toggle{width:100%;min-height:36px;padding:7px 11px}" in style
assert "STREAMFORGE_NODE_NAV_THEME_TOGGLE_V1358" in node_app
assert ".panel-nav .theme-toggle{min-width:0;flex:0 0 auto}" in node_app
assert "STREAMFORGE_NODE_WATCH_STATE_GROUP_V1358" in node_app
assert "data-theme-glyph" in node_app
app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
assert "paintThemeButtons" in app_js and "button.querySelector('[data-theme-glyph]')" in app_js
assert "STREAMFORGE_V1358_BUTTONROW_THEME_GUARD" in updater

# --- v13.51 Web Player template hotfix (carried) --------------------------
assert "STREAMFORGE_WEBPLAYER_TEMPLATE_SYNTAX_V1351" in web_player
assert "</style>\n{% endif %}\n</head>" not in web_player
assert "STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344" in web_player
verify_script = ROOT / "scripts" / "verify_template_syntax.py"
assert "STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351" in verify_script.read_text(encoding="utf-8")
assert "STREAMFORGE_V1374_TEMPLATE_COMPILE_GUARD" in updater
assert "verify_template_syntax.py" in updater
publish_workflow = (
    ROOT / ".github" / "workflows" / "publish-release.yml"
).read_text(encoding="utf-8")
assert "python3 scripts/verify_template_syntax.py" in publish_workflow

# --- v13.52 Web Player watch-screen device theme --------------------------
player = (ROOT / "app" / "templates" / "player.html").read_text(encoding="utf-8")
assert "STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344" in player
assert "STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352" in player
assert "STREAMFORGE_DEVICE_LOCAL_THEME_TOGGLE_V1344" in player
assert 'data-theme-toggle' in player
assert 'html[data-theme="light"] body.player-body' in player
assert 'class="player-body"' in player
assert "app/templates/player.html" in updater
assert "STREAMFORGE_V1374_PANEL_SAVE_HOTFIX_UPDATER" in updater
assert "STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352" in (
    ROOT / "scripts" / "verify_template_syntax.py"
).read_text(encoding="utf-8")

# --- v13.53 Node Dark/Light device theme ---------------------------------
assert "STREAMFORGE_NODE_DEVICE_THEME_SHARED_SCRIPT_V1353" in node_app
assert "STREAMFORGE_NODE_DEVICE_THEME_TOGGLE_CSS_V1353" in node_app
assert "STREAMFORGE_NODE_LOGIN_DEVICE_THEME_LIGHT_V1353" in node_app
assert "STREAMFORGE_NODE_WEBPLAYER_DEVICE_THEME_LIGHT_V1353" in node_app
assert "STREAMFORGE_NODE_WATCH_DEVICE_THEME_LIGHT_V1353" in node_app
assert "STREAMFORGE_NODE_EXPIRED_DEVICE_THEME_LIGHT_V1353" in node_app
assert "STREAMFORGE_NODE_DISCONNECTED_DEVICE_THEME_LIGHT_V1353" in node_app
assert "STREAMFORGE_NODE_LIGHT_SURFACES_V1353" in node_app
assert node_app.index("def _node_panel_css()") < node_app.index(
    "STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348"
) < node_app.index("def _node_panel_document("), (
    "the late Node light overrides must live in the global panel stylesheet"
)
assert (
    ':root[data-theme="light"] input[type=checkbox]:checked::after{background:#f2f6fa' not in node_app
), "the Node light theme paints the checkbox tick white again"
assert node_app.count("_node_device_theme_script()") >= 6, (
    "every Node public page must bootstrap the device theme"
)
assert node_app.count("_node_theme_toggle_button(") >= 7, (
    "every Node public page must offer the theme toggle"
)
assert node_app.count("STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344") >= 1

# --- v13.54 light select popups and root-static HLS.js --------------------
light = (ROOT / "app" / "static" / "theme-light.css").read_text(encoding="utf-8")
assert "STREAMFORGE_LIGHT_SELECT_POPUP_V1354" in light
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in light
), "Main light theme must force light color-scheme on selects"
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never) option,:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never) option{background:#f2f6fa!important;color:#16232f!important}' in light
), "Main light theme must recolour select options"
assert "STREAMFORGE_NODE_LIGHT_SELECT_POPUP_V1354" in node_app
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in node_app
), "Node light theme must force light color-scheme on selects"
player_src = (ROOT / "app" / "templates" / "player.html").read_text(encoding="utf-8")
hls_lines = [line for line in player_src.splitlines() if "static/vendor/hls.min.js" in line]
assert len(hls_lines) >= 2 and all("prefix" not in line for line in hls_lines), (
    "the watch screen must load self-hosted HLS.js from the root /static/ cache"
)
assert "STREAMFORGE_V1374_PANEL_SAVE_HOTFIX_UPDATER" in updater
assert "STREAMFORGE_LIGHT_SELECT_POPUP_V1354" in updater

# --- v13.55 watch-route wiring -------------------------------------------
node_lines = node_app.splitlines()
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
assert node_app.index("def _node_watch_device_theme_css") < node_app.index(
    '@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)'
) < node_app.index("def node_web_player_watch(channel_key: str, request: Request):"), (
    "the watch route decorator must bind node_web_player_watch"
)
assert "STREAMFORGE_V1355_ROUTE_WIRING_GUARD" in updater

# --- v13.56 light selector match ------------------------------------------
assert "STREAMFORGE_LIGHT_SELECTOR_MATCH_V1356" in light
assert "STREAMFORGE_NODE_LIGHT_SELECTOR_MATCH_V1356" in node_app
assert ':root[data-theme="light"] html' not in light, (
    "theme-light.css still carries the impossible :root html light selector"
)
assert ':root[data-theme="light"] html' not in node_app, (
    "the Node panel CSS still carries the impossible :root html light selector"
)
assert "STREAMFORGE_V1374_PANEL_SAVE_HOTFIX_UPDATER" in updater

# --- v13.57 settings/sidebar light surfaces + inline watch toggle ---------
for marker in (
    "STREAMFORGE_NODE_LIGHT_SETTINGS_SURFACES_V1357",
    "STREAMFORGE_NODE_LIGHT_SIDEBAR_V1357",
    "STREAMFORGE_NODE_LIGHT_PLAYLIST_ROWS_V1357",
    ':root[data-theme="light"] .saved-credential{background:#f2f6fa!important;color:#0e6e57!important}',
    ':root[data-theme="light"] .node-tls-record{background:#ffffff!important;border-color:#c9d6e0!important}',
    ':root[data-theme="light"] .panel-nav .nav-button.active{background:#dfeaf3!important;border-color:#9eb2c2!important}',
    "theme_toggle_inline=_node_theme_toggle_button('theme-toggle-inline')",
):
    assert marker in node_app, marker
assert '{theme_toggle_inline}' in node_app
assert "theme_toggle_button=_node_theme_toggle_button('theme-toggle-bottom')" not in node_app
assert "STREAMFORGE_V1357_SETTINGS_LIGHT_GUARD" in updater

# --- v13.59 node HLS override access-policy bypass ------------------------
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_NODE_HLS_SEGMENT_POLICY_BYPASS_V1359" in main
assert '"/api/v1/node-channel-hls-segment/"' in main
assert main.index('"/api/v1/node-channel-hls-segment/"') > main.index("node_internal_prefixes = ("), (
    "the HLS segment bypass must sit inside node_internal_prefixes"
)
assert "STREAMFORGE_V1359_NODE_HLS_BYPASS_GUARD" in updater

# --- v13.60 Node panel permission audit -----------------------------------
for legacy in ("logs.manage", "stream_users.kill", "categories.manage"):
    assert legacy not in node_app, f"legacy permission {legacy} still used by the Node panel"
for marker in (
    "STREAMFORGE_NODE_PERM_AUDIT_V1360",
    "require_node_panel_user(request, 'sessions.kill')",
    "require_node_panel_user(request, 'categories.reorder')",
    "require_node_panel_user(request, 'categories.edit')",
    "require_node_panel_user(request, 'categories.delete')",
    'require_node_panel_user(request, "channels.playback")',
    "require_node_panel_user(request, 'channels.info')",
):
    assert marker in node_app, marker
assert "STREAMFORGE_V1360_PERMISSION_AUDIT_GUARD" in updater
# --- v13.61 channels-page playback gate ------------------------------------
assert "if c.output_type == 'hls' and _has_panel_permission(user, 'channels.playback'):" in node_app
assert "STREAMFORGE_NODE_PERM_AUDIT_V1361" in node_app
for template, expected in (("channels.html", 2), ("dashboard.html", 1), ("channel_info.html", 1)):
    source = (ROOT / "app" / "templates" / template).read_text(encoding="utf-8")
    assert source.count("can('channels.playback')") >= expected, template
assert "STREAMFORGE_V1361_PLAYBACK_GATE_GUARD" in updater

# --- v13.62 password policy options ----------------------------------------
auth = (ROOT / "app" / "auth.py").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
models = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
node_manager = (ROOT / "app" / "node_manager.py").read_text(encoding="utf-8")
assert "PASSWORD_MIN_LENGTH_KEY = \"password_min_length\"" in main
assert "def password_min_length(db: Session) -> int:" in main
assert "/admin-users/{admin_user_id}/force-password" in main
assert '"password_change_required": bool(admin.must_change_password),' in main
assert "panel_user.must_change_password = True" in main
assert "min_length: int = PANEL_PASSWORD_MIN_LENGTH" in auth
assert "must_change_password: Mapped[bool]" in models
assert "ALTER TABLE admin_users ADD COLUMN must_change_password" in (ROOT / "app" / "db.py").read_text(encoding="utf-8")
assert '"must_change_password": bool(getattr(user, "must_change_password", False)),' in node_manager
for marker in ("must_change_password: bool = False", "password_min_length: int = 12", "enforce_node_forced_password_change"):
    assert marker in node_app, marker
assert "STREAMFORGE_V1362_PASSWORD_POLICY_GUARD" in updater

# --- v13.63 render NameError hotfix ----------------------------------------
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
assert "from .auth import PASSWORD_CHANGE_REQUIRED_SESSION_KEY, PANEL_PASSWORD_MIN_LENGTH, current_admin" in main, (
    "PANEL_PASSWORD_MIN_LENGTH must be imported or every render raises NameError"
)
assert "STREAMFORGE_V1363_RENDER_HOTFIX_GUARD" in updater

# --- v13.64 admin users table wrap -----------------------------------------
admin_users = (ROOT / "app" / "templates" / "admin_users.html").read_text(encoding="utf-8")
assert 'table class="admin-users-table"' in admin_users
assert ".admin-users-table td.actions{display:table-cell;white-space:normal;vertical-align:middle}" in admin_users
assert ".admin-users-table .admin-user-actions{display:flex;flex-wrap:nowrap;gap:8px;align-items:center}" in admin_users
assert "STREAMFORGE_ADMIN_USERS_ACTIONS_CELL_V1365" in admin_users
assert "STREAMFORGE_V1365_CELL_LAYOUT_GUARD" in updater

# --- v13.65 admin users cell layout ----------------------------------------
admin_users = (ROOT / "app" / "templates" / "admin_users.html").read_text(encoding="utf-8")
assert '<td data-label="Actions" class="actions">' not in admin_users, (
    "the Actions cell must not be a flex container itself"
)
assert '<div class="actions admin-user-actions">' in admin_users
assert ".admin-users-table td.actions{display:table-cell;white-space:normal;vertical-align:middle}" in admin_users
assert ".admin-users-table .admin-user-actions{display:flex;flex-wrap:nowrap;gap:8px;align-items:center}" in admin_users
assert "STREAMFORGE_ADMIN_USERS_ACTIONS_CELL_V1365" in admin_users
assert "STREAMFORGE_V1365_CELL_LAYOUT_GUARD" in updater

# --- v13.66 topbar header bar ----------------------------------------------
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_TOPBAR_BAR_V1366" in style
assert "margin:0 -28px 22px;padding:0 28px}" in style
assert "border-bottom:1px solid var(--line);box-shadow:0 2px 12px rgba(0,0,0,.08);" in style
light = (ROOT / "app" / "static" / "theme-light.css").read_text(encoding="utf-8")
assert ':root[data-theme="light"] .topbar{background:var(--panel)!important' not in light
assert ':root[data-theme="light"] .topbar{border-color:var(--line)!important;color:var(--text)!important}' in light
assert "STREAMFORGE_V1366_TOPBAR_BAR_GUARD" in updater

# --- v13.67 Providers feature ----------------------------------------------
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
assert '@app.get("/providers"' in main
assert '"/providers/{provider_id}/edit"' in main
assert '"/providers/{provider_id}/delete"' in main
assert "def provider_hostnames" in main
assert '"/providers"' in (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
assert (ROOT / "app" / "models.py").read_text(encoding="utf-8").count("class Provider(Base):") == 1
perms = (ROOT / "app" / "permissions.py").read_text(encoding="utf-8")
for marker in ("providers.view", "providers.create", "providers.edit", "providers.delete"):
    assert marker in perms, marker
assert (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8").count("data-label=\"Alt\"") >= 1
assert (ROOT / "app" / "templates" / "provider_form.html").read_text(encoding="utf-8").count("provider-channel-grid") >= 2
assert "STREAMFORGE_V1369_RENAMES_GUARD" in updater

# --- v13.68 provider count columns -----------------------------------------
providers_page = (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
for marker in ('data-label="Total"', 'data-label="Pri"', 'data-label="Alt"'):
    assert marker in providers_page, marker
assert "STREAMFORGE_PROVIDERS_COUNTS_V1368" in main
assert "alt_running" in main
assert "STREAMFORGE_V1369_RENAMES_GUARD" in updater

# --- v13.69 provider renames -----------------------------------------------
providers_page = (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDER_COLUMNS_RENAMED_V1369" in providers_page
assert 'data-label="Pri channels"' not in providers_page
assert 'data-label="Alt (backup) channels"' not in providers_page
assert providers_page.count('data-label="Running"') == 2
form = (ROOT / "app" / "templates" / "provider_form.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDER_GRID_TITLES_V1369" in form
assert ".provider-grid-title{margin:0;padding:14px 18px;border-bottom:1px solid var(--line)" in form
assert "STREAMFORGE_V1369_RENAMES_GUARD" in updater

# --- v13.70 providers center align -----------------------------------------
providers_page = (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDERS_CENTER_ALIGN_V1370" in providers_page
assert ".providers-table .pv-center{text-align:center}" in providers_page
assert "STREAMFORGE_V1370_CENTER_ALIGN_GUARD" in updater

# --- v13.71 providers reload fix -------------------------------------------
nav_js = (ROOT / "app" / "static" / "panel_nav.js").read_text(encoding="utf-8")
assert "'/channels','/providers'," in nav_js, (
    "the panel navigation runtime must know the /providers prefix"
)
providers_page = (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
assert providers_page.count('<td data-label="Running" class="pv-center">') == 2
assert "STREAMFORGE_V1371_PROVIDERS_RELOAD_GUARD" in updater

# --- v13.72 panel no-store --------------------------------------------------
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_PANEL_NO_STORE_V1372" in main
assert '"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"' in main
assert "STREAMFORGE_V1372_PANEL_NO_STORE_GUARD" in updater

# --- v13.73 providers card center ------------------------------------------
providers_page = (ROOT / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
assert "STREAMFORGE_PROVIDERS_CARD_CENTER_V1373" in providers_page
assert ".providers-table tbody tr td{display:flex!important;flex-direction:column!important;align-items:center!important;gap:4px;text-align:center}" in providers_page
assert "STREAMFORGE_V1373_PROVIDERS_CARD_GUARD" in updater

# --- v13.74 panel save tail contract ----------------------------------------
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
assert "STREAMFORGE_ADMIN_USER_UPDATE_TAIL_RESTORED_V1374" in main
assert 'message="Panel user updated"' in main
force_start = main.index("def admin_user_force_password")
force_body = main[force_start: main.index("\ndef ", force_start + 1)]
assert "Panel user updated" not in force_body
assert "STREAMFORGE_V1374_TAIL_CONTRACT" in updater

# Every template must compile; this is the guard that v13.48 skipped.
import subprocess as _subprocess
_subprocess.run(
    ["python3", str(verify_script), str(ROOT)],
    check=True,
    stdout=_subprocess.DEVNULL,
)

print("PASS: v13.74 panel save hotfix release contract")
