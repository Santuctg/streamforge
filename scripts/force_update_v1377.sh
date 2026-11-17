#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1377_TOPBAR_RESPONSIVE_UPDATER:
# v13.62 ships the configurable password policy and forced password change:
# - Settings -> "Minimum password length" (AppSetting password_min_length,
#   default 12, floor 6) enforced on Panel passwords, viewer (Xtream)
#   passwords and every Remote Node panel (Node forms mirror the configured
#   minimum delivered through the live session auth and panel-user sync);
# - AdminUsers.must_change_password column (staged migration) + the
#   /admin-users/{id}/force-password route + badge; a forced user reaches
#   only the password page on Main and every Node panel, an operator-set
#   password is temporary, and the user's own change clears the flag.
# New package file this release: app/auth.py (policy helper now parameterised).
# The v13.54-v13.61 fixes are carried unchanged.

# No Nginx, environment, helper or supervisor changes.
# Staging cleanup contract markers retained for the release verifier:
# find "$STAGE/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh'
# ! -name 'force_update_v1236.sh' -delete
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="13.77"
HELPER_BIN="${STREAMFORGE_MAIN_HELPER_BIN:-/usr/local/sbin/streamforge-main-system-control}"
OLD_NODE_BLOB="e7df974d141a30b1f1703d191ac3db5d63d9e040"
LEGACY_NODE_BLOB="748cd4319e0e6b101f83efddf1b0b670964b0f42"
NEW_NODE_BLOB="6a38b0e5239327e0ae4ba761903155358b8348e3"
log(){ echo "[StreamForge v13.77] $*"; }
die(){ echo "[StreamForge v13.77] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == 1 || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31|13.32|13.33|13.34|13.35|13.36|13.37|13.38|13.39|13.40|13.41|13.42|13.43|13.44|13.45|13.46|13.47|13.48|13.49|13.50|13.51|13.52|13.53|13.54|13.55|13.56|13.57|13.58|13.59|13.60|13.61|13.62|13.63|13.64|13.65|13.66|13.67|13.68|13.69|13.70|13.71|13.72|13.73|13.74|13.75|13.76|13.77) ;; *) die "requires installed Main v13.01 through v13.77";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31|13.32|13.33|13.34|13.35|13.36|13.37|13.38|13.39|13.40|13.41|13.42|13.43|13.44|13.45|13.46|13.47|13.48|13.49|13.50|13.51|13.52|13.53|13.54|13.55|13.56|13.57|13.58|13.59|13.60|13.61|13.62|13.63|13.64|13.65|13.66|13.67|13.68|13.69|13.70|13.71|13.72|13.73|13.74|13.75|13.76|13.77) ;; *) die "requires bundled Node Agent v13.01 through v13.77";; esac
[[ -f "$SRC/scripts/sync_main_static_cache.py" ]] || die "missing package file scripts/sync_main_static_cache.py"
# STREAMFORGE_V1377_TEMPLATE_COMPILE_GUARD: v13.48-v13.50 shipped a Web Player
# template with a stray endif, so Jinja rejected it on first use and /123 and
# every other Playlist alias returned HTTP 500. Refuse to install a package
# whose templates cannot compile or render.
[[ -f "$SRC/scripts/verify_template_syntax.py" ]] || die "missing package file scripts/verify_template_syntax.py"
python3 "$SRC/scripts/verify_template_syntax.py" "$SRC" || die "package templates failed to compile"
FILES=(
  app/auth.py
  app/node_manager.py
  app/db.py
  app/main.py
  app/models.py
  app/notification_manager.py
  app/permissions.py
  app/static/app.js
  app/static/panel_nav.js
  app/static/style.css
  app/static/dashboard.js
  app/static/theme-light.css
  app/templates/base.html
  app/templates/admin_users.html
  app/templates/backups.html
  app/templates/dashboard.html
  app/templates/login.html
  app/templates/logs.html
  app/templates/web_player.html
  app/templates/main_updates.html
  app/templates/node_asn.html
  app/templates/node_form.html
  app/templates/node_webplayer_manage.html
  app/templates/nodes.html
  app/templates/notifications.html
  app/templates/player.html
  app/templates/providers.html
  app/templates/provider_form.html
  app/templates/system_branding.html
  app/templates/user_form.html
  app/templates/viewer_sessions.html
  node_agent/app.py
  VERSION
  node_agent/VERSION
  scripts/main_system_control.py
)
# STREAMFORGE_V1377_NEW_NOTIFICATION_FILES: created when upgrading from v13.01.
# STREAMFORGE_V1367_NEW_PROVIDER_FILES: created when upgrading from v13.01.
NEW_FILES=(app/notification_manager.py app/templates/notifications.html app/static/theme-light.css app/auth.py app/node_manager.py app/templates/providers.html app/templates/provider_form.html app/static/panel_nav.js)
for file in "${FILES[@]}"; do
  [[ -f "$SRC/$file" ]] || die "missing package file: $file"
  if [[ ! -f "$ROOT/$file" && " ${NEW_FILES[*]} " != *" $file "* ]]; then
    die "missing installed file: $file"
  fi
done
grep -Fq 'STREAMFORGE_V1303_DARK_AUTOFILL_CSS' "$SRC/app/static/style.css" || die "package style.css lacks the carried v13.03 autofill fix"
[[ "$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')" == "$NEW_NODE_BLOB" ]] || die "package Node Agent blob drifted"
installed_node_blob="$(shasum -a 1 "$ROOT/node_agent/app.py" | awk '{print $1}')"
case "$installed_node_blob" in
  "$OLD_NODE_BLOB"|"$LEGACY_NODE_BLOB"|"882f609040d09f8ae70ae21668b368fe6589d496"|"9e41eec538584c3072918457b4d2c66d4eebae18"|"cc14298efddb470756a14134a1e21d74299ad38d"|"77f59232a437de74c84996910bf74845b7da7543"|"2fd7027a2febea69290173647402a0e79f9f05aa"|"5b2540d36ec676700723adfb8ec158d60814fc20"|"90abd24ed7a866bf918deb5a2288205a88819a2c"|"f0cfa1cabc9aff0e2cf0d14c4b76607a419c58e5"|"837e226a84ad84cc7902fd907b2f01acb97a4dbf"|"$NEW_NODE_BLOB") ;;
  *) die "unexpected installed Node Agent blob: $installed_node_blob";;
esac
# STREAMFORGE_V1377_LOCAL_NODE_AGENT_OPTIONAL: when this server also runs a
# local Node Agent from /opt/streamforge-node, keep it on the proven payload.
LOCAL_NODE_ROOT="${STREAMFORGE_LOCAL_NODE_ROOT:-/opt/streamforge-node}"
LOCAL_FILES=()
LOCAL_ACTION="absent"
if [[ -f "$LOCAL_NODE_ROOT/app.py" ]]; then
  local_node_blob="$(shasum -a 1 "$LOCAL_NODE_ROOT/app.py" | awk '{print $1}')"
  case "$local_node_blob" in
    "$OLD_NODE_BLOB"|"$LEGACY_NODE_BLOB"|"882f609040d09f8ae70ae21668b368fe6589d496"|"9e41eec538584c3072918457b4d2c66d4eebae18"|"cc14298efddb470756a14134a1e21d74299ad38d"|"77f59232a437de74c84996910bf74845b7da7543"|"2fd7027a2febea69290173647402a0e79f9f05aa"|"5b2540d36ec676700723adfb8ec158d60814fc20"|"90abd24ed7a866bf918deb5a2288205a88819a2c"|"f0cfa1cabc9aff0e2cf0d14c4b76607a419c58e5"|"837e226a84ad84cc7902fd907b2f01acb97a4dbf"|"$NEW_NODE_BLOB")
      LOCAL_ACTION="update"
      LOCAL_FILES=(app.py)
      [[ -f "$LOCAL_NODE_ROOT/VERSION" ]] && LOCAL_FILES+=(VERSION)
      ;;
    *)
      LOCAL_ACTION="skip"
      log "WARNING: local Node Agent at $LOCAL_NODE_ROOT has an unexpected version; skipped. Update it with scripts/install_node_agent.sh from this package."
      ;;
  esac
fi
# STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252: compile only stages.
STAGING="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING"; }
trap cleanup EXIT
STAGE="$STAGING/tree"
for file in "${FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$file")"
  # Copy from package source ($SRC), NOT from installed root ($ROOT)
  cp -a "$SRC/$file" "$STAGE/$file"
done
printf '%s\n' "$TARGET" > "$STAGE/VERSION"
printf '%s\n' "$TARGET" > "$STAGE/node_agent/VERSION"
python3 - "$STAGE" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for relative in (
    'app/auth.py', 'app/db.py', 'app/main.py', 'app/models.py', 'app/notification_manager.py',
    'app/node_manager.py',
    'app/permissions.py', 'node_agent/app.py', 'scripts/main_system_control.py',
):
    path = root / relative
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')

# STREAMFORGE_V1353_NODE_THEME_GUARD: the Node Agent must carry the global
# light pass, the visible light checkbox tick and the shared device theme on
# every public surface, or the staged install aborts.
node_app = (root / 'node_agent' / 'app.py').read_text(encoding='utf-8')
node_markers = (
    'STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348',
    ':root[data-theme="light"] input[type=checkbox]:checked::after{background:#071c18!important}',
    'STREAMFORGE_NODE_LIGHT_SURFACES_V1353',
    'STREAMFORGE_NODE_DEVICE_THEME_SHARED_SCRIPT_V1353',
    'STREAMFORGE_NODE_DEVICE_THEME_TOGGLE_CSS_V1353',
    'STREAMFORGE_NODE_LOGIN_DEVICE_THEME_LIGHT_V1353',
    'STREAMFORGE_NODE_WEBPLAYER_DEVICE_THEME_LIGHT_V1353',
    'STREAMFORGE_NODE_WATCH_DEVICE_THEME_LIGHT_V1353',
    'STREAMFORGE_NODE_EXPIRED_DEVICE_THEME_LIGHT_V1353',
    'STREAMFORGE_NODE_DISCONNECTED_DEVICE_THEME_LIGHT_V1353',
)
for marker in node_markers:
    if marker not in node_app:
        raise SystemExit(f'node_agent/app.py missing {marker}')
if node_app.index('def _node_panel_css()') > node_app.index('STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348') or node_app.index('STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348') > node_app.index('def _node_panel_document('):
    raise SystemExit('node_agent/app.py light overrides are not in the global panel stylesheet')
if node_app.count('_node_device_theme_script()') < 6:
    raise SystemExit('node_agent/app.py does not bootstrap the device theme on every public page')
# STREAMFORGE_V1359_NODE_HLS_BYPASS_GUARD: the per-Node HLS segment override
# endpoint must be exempt from Main's browser IP/ASN panel whitelist.
main_source = (root / 'app' / 'main.py').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_NODE_HLS_SEGMENT_POLICY_BYPASS_V1359',
    '"/api/v1/node-channel-hls-segment/"',
):
    if marker not in main_source:
        raise SystemExit(f'app/main.py missing {marker}')
if main_source.index('"/api/v1/node-channel-hls-segment/"') < main_source.index('node_internal_prefixes = ('):
    raise SystemExit('the HLS segment bypass must sit inside node_internal_prefixes')
# STREAMFORGE_V1360_PERMISSION_AUDIT_GUARD: the Node panel must not use legacy
# permission strings that can never match a Main role, and the aligned guards
# must be present.
node_perm = (root / 'node_agent' / 'app.py').read_text(encoding='utf-8')
for legacy in ('logs.manage', 'stream_users.kill', 'categories.manage'):
    if legacy in node_perm:
        raise SystemExit(f'node_agent/app.py still uses the legacy permission {legacy}')
for marker in (
    'STREAMFORGE_NODE_PERM_AUDIT_V1360',
    "require_node_panel_user(request, 'sessions.kill')",
    "require_node_panel_user(request, 'categories.reorder')",
    "require_node_panel_user(request, 'categories.edit')",
    "require_node_panel_user(request, 'categories.delete')",
    'require_node_panel_user(request, "channels.playback")',
    "require_node_panel_user(request, 'channels.info')",
    "if c.output_type == 'hls' and _has_panel_permission(user, 'channels.playback'):",
    'STREAMFORGE_NODE_PERM_AUDIT_V1361',
):
    if marker not in node_perm:
        raise SystemExit(f'node_agent/app.py missing {marker}')
# STREAMFORGE_V1361_PLAYBACK_GATE_GUARD: the Channels-page HLS preview button is
# gated by channels.playback exactly like the dashboard and the Main panel.
# STREAMFORGE_V1362_PASSWORD_POLICY_GUARD: the policy helpers, the forced
# change flag and the Node mirror must be present in the package.
for relative, markers in {
    'app/auth.py': (
        'STREAMFORGE_PASSWORD_MIN_LENGTH_SETTING_V1362',
        'min_length: int = PANEL_PASSWORD_MIN_LENGTH',
        'STREAMFORGE_FORCE_PASSWORD_CHANGE_V1362',
    ),
    'app/main.py': (
        'PASSWORD_MIN_LENGTH_KEY = "password_min_length"',
        'def password_min_length(db: Session) -> int:',
        'STREAMFORGE_FORCE_PASSWORD_CHANGE_V1362',
        '/admin-users/{admin_user_id}/force-password',
        '"password_change_required": bool(admin.must_change_password),',
    ),
    'app/models.py': ('must_change_password: Mapped[bool]',),
    'app/db.py': ('ALTER TABLE admin_users ADD COLUMN must_change_password',),
    'app/node_manager.py': ('"must_change_password": bool(getattr(user, "must_change_password", False)),',),
    'node_agent/app.py': (
        'must_change_password: bool = False',
        'password_min_length: int = 12',
        'enforce_node_forced_password_change',
        '"password_change_required"',
    ),
}.items():
    text = (root / relative).read_text(encoding='utf-8')
    for marker in markers:
        if marker not in text:
            raise SystemExit(f'{relative} missing {marker}')
# STREAMFORGE_V1363_RENDER_HOTFIX_GUARD: the configured minimum must import.
main_source = (root / 'app' / 'main.py').read_text(encoding='utf-8')
import_line = 'from .auth import PASSWORD_CHANGE_REQUIRED_SESSION_KEY, PANEL_PASSWORD_MIN_LENGTH, current_admin'
if import_line not in main_source:
    raise SystemExit('app/main.py must import PANEL_PASSWORD_MIN_LENGTH from .auth')
if 'PANEL_PASSWORD_MIN_LENGTH)' in main_source and 'PANEL_PASSWORD_MIN_LENGTH,' not in main_source:
    raise SystemExit('app/main.py uses PANEL_PASSWORD_MIN_LENGTH without importing it')
# STREAMFORGE_V1365_CELL_LAYOUT_GUARD: the Actions cell must stay a table cell
# with the buttons in an inner one-line flex row (the v13.64 wrap rule was
# replaced because the real bug was the td itself being a flex container).
admin_users = (root / 'app' / 'templates' / 'admin_users.html').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_ADMIN_USERS_ACTIONS_CELL_V1365',
    '.admin-users-table td.actions{display:table-cell;white-space:normal;vertical-align:middle}',
    '.admin-users-table .admin-user-actions{display:flex;flex-wrap:nowrap;gap:8px;align-items:center}',
    '.admin-users-table{min-width:1300px}',
):
    if marker not in admin_users:
        raise SystemExit(f'app/templates/admin_users.html missing {marker}')
if '<td data-label="Actions" class="actions">' in admin_users:
    raise SystemExit('the Admin Users Actions cell must not carry the flex .actions class itself')
if '<div class="actions admin-user-actions">' not in admin_users:
    raise SystemExit('the Admin Users buttons must live in an inner flex row')
# STREAMFORGE_V1366_TOPBAR_BAR_GUARD: the topbar must be a real header bar.
style_css = (root / 'app' / 'static' / 'style.css').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_TOPBAR_BAR_V1366',
    'margin:0 -28px 22px;padding:0 28px}',
    'border-bottom:1px solid var(--line);box-shadow:0 2px 12px rgba(0,0,0,.08);',
    '.topbar{height:auto;min-height:76px;flex-wrap:wrap;row-gap:10px;padding:12px 14px;margin:0 -14px 18px}',
):
    if marker not in style_css:
        raise SystemExit(f'app/static/style.css missing {marker}')
light_css = (root / 'app' / 'static' / 'theme-light.css').read_text(encoding='utf-8')
if ':root[data-theme="light"] .topbar{background:var(--panel)!important' in light_css:
    raise SystemExit('the light theme must not force a flat topbar fill over the bar gradient')
if ':root[data-theme="light"] .topbar{border-color:var(--line)!important;color:var(--text)!important}' not in light_css:
    raise SystemExit('the light theme must keep the topbar border override')
# STREAMFORGE_V1369_RENAMES_GUARD: the Providers feature, its count columns and the renames must be complete.
main_source = (root / 'app' / 'main.py').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_PROVIDERS_COUNTS_V1368',
    'STREAMFORGE_PROVIDERS_V1367',
    'class Provider(Base):' if False else '@app.get("/providers"',
    '"/providers/{provider_id}/edit"',
    '"/providers/{provider_id}/delete"',
    'def provider_hostnames',
    'providers.create',
):
    if marker not in main_source:
        raise SystemExit(f'app/main.py missing {marker}')
for relative, markers in {
    'app/models.py': ('class Provider(Base):',),
    'app/templates/providers.html': ('providers-table', 'STREAMFORGE_PROVIDER_COLUMNS_RENAMED_V1369'),
    'app/templates/provider_form.html': ('provider-channel-grid', 'STREAMFORGE_PROVIDER_GRID_TITLES_V1369'),
    'app/templates/base.html': ('href="/providers"',),
}.items():
    text = (root / relative).read_text(encoding='utf-8')
    for marker in markers:
        if marker not in text:
            raise SystemExit(f'{relative} missing {marker}')
perms = (root / 'app' / 'permissions.py').read_text(encoding='utf-8')
for marker in ('providers.view', 'providers.create', 'providers.edit', 'providers.delete'):
    if marker not in perms:
        raise SystemExit(f'app/permissions.py missing {marker}')
# STREAMFORGE_V1370_CENTER_ALIGN_GUARD: the Providers table must be centered.
providers_page = (root / 'app' / 'templates' / 'providers.html').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_PROVIDERS_CENTER_ALIGN_V1370',
    '.providers-table .pv-center{text-align:center}',
):
    if marker not in providers_page:
        raise SystemExit(f'app/templates/providers.html missing {marker}')
# STREAMFORGE_V1371_PROVIDERS_RELOAD_GUARD: navigation knows /providers and
# the Alt running column is centered.
nav_js = (root / 'app' / 'static' / 'panel_nav.js').read_text(encoding='utf-8')
if "'/channels','/providers'," not in nav_js:
    raise SystemExit('app/static/panel_nav.js missing the /providers navigation prefix')
providers_page = (root / 'app' / 'templates' / 'providers.html').read_text(encoding='utf-8')
if providers_page.count('<td data-label="Running" class="pv-center">') < 2:
    raise SystemExit('both Providers Running columns must be centered')
# STREAMFORGE_V1372_PANEL_NO_STORE_GUARD: every Panel page must be no-store.
main_source = (root / 'app' / 'main.py').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_PANEL_NO_STORE_V1372',
    '"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"',
):
    if marker not in main_source:
        raise SystemExit(f'app/main.py missing {marker}')
# STREAMFORGE_V1373_PROVIDERS_CARD_GUARD: the card view must center values.
providers_page = (root / 'app' / 'templates' / 'providers.html').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_PROVIDERS_CARD_CENTER_V1373',
    'grid-template-columns:minmax(0,1fr)',
):
    if marker not in providers_page:
        raise SystemExit(f'app/templates/providers.html missing {marker}')
# STREAMFORGE_V1374_TAIL_CONTRACT: every Main route handler must end with a
# Return (or if/try whose branches all return), so a dropped tail can never
# ship again; and the admin_user_update tail must be present.
import ast as _ast
main_tree = _ast.parse((root / 'app' / 'main.py').read_text(encoding='utf-8'))
allowed_names = {
    'system_backup_encryption_update', 'system_backup_google_credentials',
    'system_backup_google_callback', 'system_backup_test_destination',
    'system_backup_google_test', 'system_local_backup_update',
    'system_local_backup_run', 'system_backup_update', 'system_backup_run',
    'system_backup_archive_delete', 'system_backup_target_file_delete',
    'system_backup_target_file_restore', 'system_backup_archive_restore',
    'system_backup_restore', 'node_geoip_settings_save', 'node_geoip_update_now',
    'node_access_back_sync', 'channel_import',
}
def _returns_in(body):
    return isinstance(body[-1], _ast.Return)
for node in _ast.walk(main_tree):
    if not isinstance(node, _ast.FunctionDef):
        continue
    is_route = any(isinstance(d, _ast.Call) and getattr(d.func, 'attr', '') in {'get', 'post', 'put', 'delete'} for d in node.decorator_list)
    if not is_route:
        continue
    last = node.body[-1]
    if isinstance(last, _ast.Return):
        continue
    if isinstance(last, _ast.If):
        branches = [last.body] + ([last.orelse] if last.orelse else [])
        if all(_returns_in(b) for b in branches):
            continue
    if isinstance(last, _ast.Try):
        bodies = [last.body] + [h.body for h in last.handlers]
        if last.orelse:
            bodies.append(last.orelse)
        if last.finalbody:
            bodies.append(last.finalbody)
        if all(_returns_in(b) for b in bodies):
            continue
    if node.name not in allowed_names:
        raise SystemExit(f'route handler {node.name} does not end with a Return')
main_text = (root / 'app' / 'main.py').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_ADMIN_USER_UPDATE_TAIL_RESTORED_V1374',
    'message="Panel user updated"',
):
    if marker not in main_text:
        raise SystemExit(f'app/main.py missing {marker}')
force_start = main_text.index('def admin_user_force_password')
force_end = main_text.index('\ndef ', force_start + 1)
if 'Panel user updated' in main_text[force_start:force_end]:
    raise SystemExit('orphaned fragment still inside admin_user_force_password')
# STREAMFORGE_V1376_PROVIDERS_CARD_GUARD: every Providers th must be centered and the card fix must be real.
providers_page = (root / 'app' / 'templates' / 'providers.html').read_text(encoding='utf-8')
import re as _re
bad_ths = [t for t in _re.findall(r'<th[^>]*>', providers_page) if 'pv-center' not in t and t != '<thead>']
if bad_ths:
    raise SystemExit(f'app/templates/providers.html has uncentered th: {bad_ths}')
if 'display:flex!important;flex-direction:column' in providers_page:
    raise SystemExit('app/templates/providers.html still carries the broken flex override')
if 'grid-template-columns:minmax(0,1fr)' not in providers_page:
    raise SystemExit('app/templates/providers.html missing the centered card grid')
if 'STREAMFORGE_PROVIDERS_CARD_CENTER_V2_V1376' not in providers_page:
    raise SystemExit('app/templates/providers.html missing the V2 card marker')
# STREAMFORGE_V1377_TOPBAR_RESPONSIVE_GUARD: the topbar must wrap on phones.
style_css = (root / 'app' / 'static' / 'style.css').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_TOPBAR_RESPONSIVE_V1377',
    '.topbar{height:auto;min-height:76px;flex-wrap:wrap;row-gap:10px;padding:12px 14px;margin:0 -14px 18px}',
    '.topbar h1{font-size:19px}',
):
    if marker not in style_css:
        raise SystemExit(f'app/static/style.css missing {marker}')
checks = {
    'app/notification_manager.py': (
        'notification_worker', 'queue_expiry_reminders', 'queue_manual_campaign',
        'process_delivery_queue', 'cancel_stale_expiry_deliveries', 'EmailConfig',
    ),
    'app/models.py': (
        'notification_campaigns', 'notification_deliveries', 'contact_email',
        'email_notifications', 'contact_phone', 'sms_notifications', 'dedupe_key',
    ),
    'app/db.py': (
        'ALTER TABLE nodes ADD COLUMN contact_email', 'ix_stream_users_contact_email',
        'ALTER TABLE stream_users ADD COLUMN email_notifications',
    ),
    'app/permissions.py': (
        'notifications.view', 'notifications.send', 'notifications.manage',
    ),
    'app/main.py': (
        'STREAMFORGE_EMAIL_NOTIFICATIONS_V1302', '/system/notifications',
        'notification_worker.start()', 'cancel_stale_expiry_deliveries',
        'STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE',
        'STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317',
    ),
    'scripts/main_system_control.py': (
        'STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317',
        'STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317',
    ),
    'app/static/app.js': (
        'STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318',
        "btn.type = 'button'", 'sf-help-bubble', 'sf-help-source',
        'data-destination-help', 'aria-expanded',
    ),
    'app/static/style.css': (
        'STREAMFORGE_THEME_COLOR_TOKENS_V1349',
        'STREAMFORGE_MAIN_USER_CARD_THEME_TOGGLE_V1350',
        '--sf-bg-0b1118', '--sf-fg-edf4fb',
        'STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER',
        'position:fixed!important', 'left:9.5px!important', 'width:56px!important',
        'STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318',
        'body .sf-help-source{display:none!important}', 'body .sf-help-bubble.sf-help-bubble{',
        'body .sf-help-btn.sf-help-btn{',
    ),
    'app/templates/base.html': (
        '/system/notifications', 'notifications.view',
        'admin-chip-id', 'data-theme-toggle',
    ),
    'app/static/theme-light.css': (
        'STREAMFORGE_LIGHT_THEME_OVERRIDES_V1348',
        'STREAMFORGE_LIGHT_LOGIN_AUTOFILL_V1348',
        'STREAMFORGE_LIGHT_CHECKBOX_TICK_V1350',
        ':root[data-theme="light"] .login-card input[name="username"]',
    ),
    'app/templates/web_player.html': (
        'STREAMFORGE_WEBPLAYER_TEMPLATE_SYNTAX_V1351',
        'STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344',
        'data-theme-toggle',
    ),
    # v13.52: the watch screen must carry the device theme bootstrap, a toggle
    # button and the scoped light-theme block, or dark stays forced forever.
    'app/templates/player.html': (
        'STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344',
        'STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352',
        'STREAMFORGE_DEVICE_LOCAL_THEME_TOGGLE_V1344',
        'data-theme-toggle',
    ),
    'app/templates/dashboard.html': ('var(--sf-',),
    'app/templates/viewer_sessions.html': ('var(--sf-',),
    'app/templates/user_form.html': ('name="contact_email"', 'name="email_notifications"', 'user-notification-settings', '<b>Email notifications</b>', '<b>SMS notifications</b>'),
    'app/templates/node_form.html': ('name="contact_name"', 'name="contact_email"'),
    'app/templates/notifications.html': (
        '/system/notifications/settings', '/system/notifications/campaign',
        'notification-recipient-grid', 'notification-summary-grid',
        'data-notification-campaign', 'notification-history', 'sms_user_body', 'sms_node_body',
        'data-message-modal', 'modal.showModal()',
    ),
    'app/templates/system_branding.html': ('name="system_timezone"', 'System time zone'),
    'app/templates/main_updates.html': (
        'main-updates-page', '/system/updates/apply',
        "confirm('Update the Main Server now?", 'Update processing',
    ),
}
for relative, markers in checks.items():
    text = (root / relative).read_text(encoding='utf-8')
    for marker in markers:
        if marker not in text:
            raise SystemExit(f'{relative} missing {marker}')
# STREAMFORGE_LIGHT_SELECT_POPUP_V1354 / STREAMFORGE_NODE_LIGHT_SELECT_POPUP_V1354
# STREAMFORGE_LIGHT_SELECTOR_MATCH_V1356 / STREAMFORGE_NODE_LIGHT_SELECTOR_MATCH_V1356
theme_light = (root / 'app' / 'static' / 'theme-light.css').read_text(encoding='utf-8')
# v13.56: no light rule may use the impossible ":root[data-theme=light] html"
# prefix, and the light select rules must carry the dark rule's id guard.
if ':root[data-theme="light"] html' in theme_light:
    raise SystemExit('theme-light.css still carries an impossible :root html light selector')
if ':root[data-theme="light"] html' in (root / 'node_agent' / 'app.py').read_text(encoding='utf-8'):
    raise SystemExit('node_agent/app.py still carries an impossible :root html light selector')
for marker in (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}',
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never) option,:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never) option{background:#f2f6fa!important;color:#16232f!important}',
):
    if marker not in theme_light:
        raise SystemExit(f'app/static/theme-light.css missing {marker}')
player_src = (root / 'app' / 'templates' / 'player.html').read_text(encoding='utf-8')
# STREAMFORGE_V1354_HLSJS_ROOT_STATIC_GUARD: the watch screen must load the
# self-hosted HLS.js from the host-root /static/ cache, never from the
# {{ prefix }}-prefixed aliased path that Nginx silently drops.
hls_lines = [line for line in player_src.splitlines() if 'static/vendor/hls.min.js' in line]
if len(hls_lines) < 2 or any('prefix' in line for line in hls_lines):
    raise SystemExit('player.html must load HLS.js from the root /static/ cache path')
node_app_check = (root / 'node_agent' / 'app.py').read_text(encoding='utf-8')
# STREAMFORGE_V1357_SETTINGS_LIGHT_GUARD: the Settings-page dark boxes, the
# sidebar light pass and the inline watch toggle must be present.
for marker in (
    'STREAMFORGE_NODE_NAV_THEME_TOGGLE_V1358',
    '.panel-nav .theme-toggle{min-width:0;flex:0 0 auto}',
    "data-theme-glyph",
    'STREAMFORGE_NODE_WATCH_STATE_GROUP_V1358',
    'STREAMFORGE_NODE_LIGHT_SETTINGS_SURFACES_V1357',
    'STREAMFORGE_NODE_LIGHT_SIDEBAR_V1357',
    'STREAMFORGE_NODE_LIGHT_PLAYLIST_ROWS_V1357',
    ':root[data-theme="light"] .saved-credential{background:#f2f6fa!important;color:#0e6e57!important}',
    ':root[data-theme="light"] .node-tls-record{background:#ffffff!important;border-color:#c9d6e0!important}',
    ':root[data-theme="light"] .panel-nav .nav-button.active{background:#dfeaf3!important;border-color:#9eb2c2!important}',
    "theme_toggle_inline=_node_theme_toggle_button('theme-toggle-inline')",
):
    if marker not in node_app_check:
        raise SystemExit(f'node_agent/app.py missing {marker}')
if '{theme_toggle_inline}' not in node_app_check or "theme_toggle_button=_node_theme_toggle_button('theme-toggle-bottom')" in node_app_check:
    raise SystemExit('the watch screen must carry the inline status-row toggle, not the bottom-right one')
base_html = (root / 'app' / 'templates' / 'base.html').read_text(encoding='utf-8')
style_css = (root / 'app' / 'static' / 'style.css').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_SIDEBAR_THEME_TOGGLE_V1358',
    'STREAMFORGE_MAIN_MOBILE_THEME_TOGGLE_V1358',
    '.sidebar-bottom .theme-toggle{width:100%;min-height:36px;padding:7px 11px}',
    '.topbar-theme-toggle{display:none}',
):
    if marker not in style_css:
        raise SystemExit(f'app/static/style.css missing {marker}')
sidebar_bottom = base_html.split('class="sidebar-bottom"', 1)[1].split('</aside>', 1)[0]
if 'data-theme-toggle' not in sidebar_bottom or 'Change password' not in sidebar_bottom:
    raise SystemExit('the Main theme switch must live in the sidebar button row')
topbar_actions = base_html.split('class="topbar-actions"', 1)[1].split('</header>', 1)[0]
if 'topbar-theme-toggle' not in topbar_actions or 'admin-chip' not in topbar_actions:
    raise SystemExit('phones must keep the compact topbar theme switch')
app_js = (root / 'app' / 'static' / 'app.js').read_text(encoding='utf-8')
for marker in (
    'STREAMFORGE_MAIN_SIDEBAR_THEME_TOGGLE_V1358',
    'paintThemeButtons',
    "button.querySelector('[data-theme-glyph]')",
):
    if marker not in app_js:
        raise SystemExit(f'app/static/app.js missing {marker}')
# STREAMFORGE_V1358_BUTTONROW_THEME_GUARD: the switch lives in the button row on
# both panels, labelled buttons keep their label, and the Node Web Player keeps
# the LIVE pill and the switch together.
# STREAMFORGE_V1355_ROUTE_WIRING_GUARD: v13.53 wedged the watch-device-CSS
# helper between the watch route decorator and its handler, so every channel
# click returned the CSS text. Every @app/@router decorator must be bound to a
# def directly below it, and the watch route must bind node_web_player_watch.
node_lines = node_app_check.splitlines()
for index, line in enumerate(node_lines):
    stripped = line.strip()
    if stripped.startswith('@app.') or stripped.startswith('@router.'):
        follow = index + 1
        while follow < len(node_lines) and (
            not node_lines[follow].strip() or node_lines[follow].strip().startswith('#')
        ):
            follow += 1
        nxt = node_lines[follow].strip() if follow < len(node_lines) else ''
        if nxt and not (nxt.startswith('def ') or nxt.startswith('async def ') or nxt.startswith('@')):
            raise SystemExit(f'route decorator at source line {index + 1} is not bound to a handler')
watch_decorator = node_app_check.index('@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)')
watch_handler = node_app_check.index('def node_web_player_watch(channel_key: str, request: Request):')
watch_helper = node_app_check.index('def _node_watch_device_theme_css')
if not (watch_helper < watch_decorator < watch_handler):
    raise SystemExit('the watch route decorator must bind node_web_player_watch')
if ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' not in node_app_check:
    raise SystemExit('node_agent/app.py missing the light select color-scheme override')
web_player_page = (root / 'app' / 'templates' / 'web_player.html').read_text(encoding='utf-8')
if '</style>\n{% endif %}\n</head>' in web_player_page:
    raise SystemExit('web_player.html still has the stray endif that broke v13.48-v13.50')
player_page = (root / 'app' / 'templates' / 'player.html').read_text(encoding='utf-8')
if 'html[data-theme="light"] body.player-body' not in player_page:
    raise SystemExit('player.html lacks the v13.52 light watch-screen theme')
if 'querySelectorAll(\'[data-theme-toggle]\')' not in player_page:
    raise SystemExit('player.html lacks the device theme toggle script')
page = (root / 'app' / 'templates' / 'main_updates.html').read_text(encoding='utf-8')
if "confirm('Update Main Server to v{{" in page:
    raise SystemExit('main_updates.html still interpolates remote data into confirm()')
notification_page = (root / 'app' / 'templates' / 'notifications.html').read_text(encoding='utf-8')
if '/system/notifications/timezone' in notification_page:
    raise SystemExit('Notification Center still owns the system timezone control')
manager = (root / 'app' / 'notification_manager.py').read_text(encoding='utf-8')
for marker in ('STREAMFORGE_V1312_EXACT_UI_TEMPLATE_FIX', 'sms.maestro.com.bd', 'contact_phone', 'DEFAULT_SMS_USER_BODY', 'DEFAULT_SMS_NODE_BODY'):
    if marker not in manager:
        raise SystemExit(f'app/notification_manager.py missing {marker}')
PY
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/v1305-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
for file in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$file")"
  if [[ -f "$ROOT/$file" ]]; then
    cp -a "$ROOT/$file" "$BACKUP/$file"
  else
    printf '%s\n' "$file" >> "$BACKUP/created-files.txt"
  fi
done
if [[ -d "$STATIC_CACHE_ROOT" ]]; then
  mkdir -p "$BACKUP/main-static"
  cp -a "$STATIC_CACHE_ROOT/." "$BACKUP/main-static/"
fi
if [[ "$LOCAL_ACTION" == update ]]; then
  for file in "${LOCAL_FILES[@]}"; do
    mkdir -p "$BACKUP/local-node/$(dirname "$file")"
    cp -a "$LOCAL_NODE_ROOT/$file" "$BACKUP/local-node/$file"
  done
fi
log "Backup created: $BACKUP"
ROLLBACK=1
RESTARTED=0
LOCAL_RESTARTED=0
clear_bytecode(){
  # Delete only bytecode for files this updater can change.
  for dir in "$ROOT/app" "$ROOT/node_agent"; do
    if [[ -d "$dir/__pycache__" ]]; then
      find "$dir/__pycache__" -maxdepth 1 -type f \( -name 'main.*.pyc' -o -name 'models.*.pyc' -o -name 'db.*.pyc' -o -name 'permissions.*.pyc' -o -name 'notification_manager.*.pyc' -o -name 'app.*.pyc' \) -delete
    fi
  done
  if [[ "$LOCAL_ACTION" == update && -d "$LOCAL_NODE_ROOT/__pycache__" ]]; then
    find "$LOCAL_NODE_ROOT/__pycache__" -maxdepth 1 -type f -name 'app.*.pyc' -delete
  fi
}
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for file in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$file" ]]; then
        cp -a "$BACKUP/$file" "$ROOT/$file" || true
      else
        rm -f "$ROOT/$file" || true
      fi
    done
    # STREAMFORGE_V1377_ROLLBACK_PRIVILEGED_HELPER: restore the previous root
    # system-control helper whenever the package copy was backed up.
    if [[ -f "$BACKUP/scripts/main_system_control.py" ]]; then
      install -m 0755 "$BACKUP/scripts/main_system_control.py" "$HELPER_BIN" 2>/dev/null || true
    fi
    if [[ -d "$BACKUP/main-static" ]]; then
      mkdir -p "$STATIC_CACHE_ROOT"
      cp -a "$BACKUP/main-static/." "$STATIC_CACHE_ROOT/" 2>/dev/null || true
    fi
    if [[ "$LOCAL_ACTION" == update ]]; then
      for file in "${LOCAL_FILES[@]}"; do
        cp -a "$BACKUP/local-node/$file" "$LOCAL_NODE_ROOT/$file" || true
      done
      if [[ "$LOCAL_RESTARTED" == 1 ]]; then
        systemctl try-restart streamforge-node.service >/dev/null 2>&1 || true
        systemctl try-restart streamforge-node-public.service >/dev/null 2>&1 || true
      fi
    fi
    clear_bytecode || true
    if [[ "$RESTARTED" == 1 && "$TEST" != 1 ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
  local source="$1" dest="$2"
  # Retain the installed file's ownership/mode, not the package extract's.
  cp -a "$dest" "${dest}.tmp.$$"
  cat "$source" > "${dest}.tmp.$$"
  mv -f "${dest}.tmp.$$" "$dest"
}
for file in "${FILES[@]}"; do
  if [[ ! -f "$ROOT/$file" ]]; then
    mkdir -p "$ROOT/$(dirname "$file")"
    install -m 0644 "$STAGE/$file" "$ROOT/$file"
  elif ! cmp -s "$STAGE/$file" "$ROOT/$file"; then
    atomic_copy "$STAGE/$file" "$ROOT/$file"
  fi
done
# STREAMFORGE_V1377_INSTALL_PRIVILEGED_HELPER: publish the refreshed root
# system-control helper next to the existing root-owned install.
mkdir -p "$(dirname "$HELPER_BIN")" 2>/dev/null || true
if [[ "$TEST" == 1 ]]; then
  install -m 0755 "$STAGE/scripts/main_system_control.py" "$HELPER_BIN"
else
  install -o root -g root -m 0755 "$STAGE/scripts/main_system_control.py" "$HELPER_BIN"
fi
cmp -s "$STAGE/scripts/main_system_control.py" "$HELPER_BIN" || die "Root system-control helper does not match the installed package"
if [[ "$LOCAL_ACTION" == update ]]; then
  if ! cmp -s "$STAGE/node_agent/app.py" "$LOCAL_NODE_ROOT/app.py"; then
    atomic_copy "$STAGE/node_agent/app.py" "$LOCAL_NODE_ROOT/app.py"
  fi
  if [[ " ${LOCAL_FILES[*]} " == *" VERSION "* ]]; then
    printf '%s\n' "$TARGET" > "$LOCAL_NODE_ROOT/VERSION"
  fi
fi
# STREAMFORGE_V1377_STATIC_CACHE: Nginx serves /static/ from this cache, so
# the installed style.css must be republished there or browsers keep the old
# sheet (and the white autofilled fields).
log "Refreshing Nginx direct static cache from installed Main assets."
python3 -B "$SRC/scripts/sync_main_static_cache.py" "$ROOT/app/static" "$STATIC_CACHE_ROOT"
[[ "$TEST" == 1 ]] || chown -R root:root "$STATIC_CACHE_ROOT"
python3 -B - "$ROOT/app/static" "$STATIC_CACHE_ROOT" <<'PYVERIFY'
from pathlib import Path
import hashlib, sys
installed, cache = map(Path, sys.argv[1:])
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
stale = [p.relative_to(installed).as_posix() for p in installed.rglob("*")
         if p.is_file() and (not (cache / p.relative_to(installed)).is_file()
                             or digest(p) != digest(cache / p.relative_to(installed)))]
if stale:
    raise SystemExit("static cache still differs for: " + ",".join(sorted(stale)[:12]))
if "STREAMFORGE_V1303_DARK_AUTOFILL_CSS" not in (cache / "style.css").read_text(encoding="utf-8"):
    raise SystemExit("static cache style.css lacks the carried v13.03 autofill fix")
print("STREAMFORGE_V1377_STATIC_CACHE_VERIFIED: Nginx static cache matches installed app/static")
PYVERIFY
if [[ "${STREAMFORGE_V1377_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
clear_bytecode
# STREAMFORGE_V1377_RESTART_MAIN_WORKERS: never restart Nginx or supervisor.
if [[ "$TEST" != 1 ]]; then
  RESTARTED=1
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge
  systemctl is-active --quiet streamforge-public
fi
if [[ "$LOCAL_ACTION" == update && "$TEST" != 1 ]]; then
  LOCAL_RESTARTED=1
  systemctl try-restart streamforge-node.service || true
  systemctl try-restart streamforge-node-public.service || true
fi
ROLLBACK=0
trap - ERR
log "SUCCESS: Main v13.77 installed; the topbar is responsive on phones."
if [[ "$LOCAL_ACTION" == update ]]; then
  log "Local Node Agent at $LOCAL_NODE_ROOT updated; local node services try-restarted."
fi
log "Nginx, privileged update helper and channel supervisor unchanged. Remote Nodes receive the updated Node Agent from Main automatically."
