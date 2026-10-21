#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_PANEL_USER_SAVED_PASSWORD_V1263:
# Add encrypted recovery storage separate from one-way scrypt authentication.
# Node runtime bytes are unchanged from v12.62; Main DB migration is additive.
# Main channel supervisor and Nginx are deliberately not restarted.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.63"
NODE_TARGET_VERSION="12.63"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_PANEL_USER_SAVED_PASSWORD_V1263"

log(){ echo "[StreamForge v${TARGET_VERSION} Panel User Saved Password Vault] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Panel User Saved Password Vault] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
# STREAMFORGE_V1263_SECRET_PREFLIGHT: never enable recoverable password
# escrow with a default/weak/missing Main secret. Read without displaying it.
if [[ "$TEST_MODE" != "1" ]]; then
  MAIN_PID="$(systemctl show -p MainPID --value "$MAIN_SERVICE" 2>/dev/null || true)"
  [[ "$MAIN_PID" =~ ^[1-9][0-9]*$ ]] || die "Main Panel service must be running to verify vault key safety"
  python3 - "$MAIN_PID" <<'PYV1263KEY' || die "Set a stable 32+ character STREAMFORGE_SECRET_KEY in Main service before v12.63 deployment"
import sys
from pathlib import Path
pid = sys.argv[1]
try:
    data = (Path("/proc")/pid/"environ").read_bytes().split(b"\\0")
    keys = [value.split(b"=", 1)[1] for value in data if value.startswith(b"STREAMFORGE_SECRET_KEY=")]
except OSError:
    sys.exit(1)
if len(keys) != 1 or len(keys[0]) < 32 or keys[0] == b"dev-change-me-now":
    sys.exit(1)
print("PASS: configured Main vault encryption key meets minimum length; value not shown")
PYV1263KEY
fi

[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
[[ -f "$SOURCE_DIR/app/templates/node_updates.html" ]] || die "package previous Node Updates template missing"
[[ -f "$SOURCE_DIR/app/templates/admin_users.html" ]] || die "package Panel users template missing"
[[ -f "$SOURCE_DIR/app/templates/admin_user_form.html" ]] || die "package Panel user form template missing"
for path in app/main.py app/models.py app/db.py app/panel_password_vault.py; do
  [[ -f "$SOURCE_DIR/$path" ]] || die "missing vault source $path"
done
[[ -f "$SOURCE_DIR/scripts/verify_panel_saved_password_v1263.py" ]] || die "v12.63 verifier missing"
grep -Fq "$MARKER" "$SOURCE_DIR/app/main.py" || die "v12.63 vault marker missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259' "$SOURCE_DIR/app/templates/node_updates.html" || die "V1259 Node Update UI spacing guard missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258' "$SOURCE_DIR/app/main.py" || die "V1258 Node Update Center guard missing"
grep -Fq 'STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257' "$SOURCE_DIR/app/main.py" || die "V1257 bulk-update guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256' "$SOURCE_DIR/node_agent/app.py" || die "V1256 Node Web Player guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
python3 "$SOURCE_DIR/scripts/verify_panel_saved_password_v1263.py"
python3 - "$SOURCE_DIR/scripts/verify_panel_saved_password_v1263.py" "$SOURCE_DIR/scripts/test_v1263_updater_integration.py" <<'PYV1263SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.63 Python syntax verified without bytecode output")
PYV1263SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.62|12.63) ;;
  *) die "v12.63 encrypted vault requires installed Main v12.62 or v12.63; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.62|12.63) ;;
  *) die "v12.63 requires bundled Node Agent v12.62 or v12.63; found $INSTALLED_NODE_VERSION" ;;
esac

# STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT:
# v12.63 is a bounded Main API/schema/Panel users release. Any Node runtime byte change is a
# packaging regression and stops the update before production files are touched.
python3 - "$SOURCE_DIR/node_agent" "$APP_ROOT/node_agent" <<'PYV1263NODE'
from pathlib import Path
import hashlib, sys
src, dst = map(Path, sys.argv[1:])
def files(root):
    out = {}
    for p in root.rglob('*'):
        if not p.is_file() or p.name == 'VERSION' or '__pycache__' in p.parts or p.suffix in {'.pyc', '.pyo'}:
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out
s, d = files(src), files(dst)
if s != d:
    names = sorted(set(s) | set(d))
    bad = [name for name in names if s.get(name) != d.get(name)]
    raise SystemExit('Node runtime payload differs for: ' + ', '.join(bad[:20]))
print(f"STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT: verified unchanged Node payload parity for {len(s)} files")
PYV1263NODE

[[ -f "$APP_ROOT/app/templates/admin_users.html" ]] || die "installed admin_users.html missing"
[[ -f "$APP_ROOT/app/templates/admin_user_form.html" ]] || die "installed admin_user_form.html missing"
for path in app/main.py app/models.py app/db.py; do
  [[ -f "$APP_ROOT/$path" ]] || die "installed file missing $path"
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1263-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent" "$BACKUP_DIR/app"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/templates/admin_users.html" "$BACKUP_DIR/app/templates/admin_users.html"
cp -a "$APP_ROOT/app/templates/admin_user_form.html" "$BACKUP_DIR/app/templates/admin_user_form.html"
for path in app/main.py app/models.py app/db.py; do
  cp -a "$APP_ROOT/$path" "$BACKUP_DIR/$path"
done
if [[ -e "$APP_ROOT/app/panel_password_vault.py" ]]; then
  cp -a "$APP_ROOT/app/panel_password_vault.py" "$BACKUP_DIR/app/panel_password_vault.py"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Panel User Saved Password Vault] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" 2>/dev/null || true
    for path in app/main.py app/models.py app/db.py; do
      cp -a "$BACKUP_DIR/$path" "$APP_ROOT/$path" 2>/dev/null || true
    done
    if [[ -e "$BACKUP_DIR/app/panel_password_vault.py" ]]; then
      cp -a "$BACKUP_DIR/app/panel_password_vault.py" "$APP_ROOT/app/panel_password_vault.py" 2>/dev/null || true
    else
      rm -f "$APP_ROOT/app/panel_password_vault.py"
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){ echo "[StreamForge v${TARGET_VERSION} Panel User Saved Password Vault] ERROR: $*" >&2; rollback 1; }
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1" dst="$2" mode="$3" dir tmp
  dir="$(dirname "$dst")"; mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1263.XXXXXX")"
  cp "$src" "$tmp"; chmod "$mode" "$tmp"; sync -f "$tmp" 2>/dev/null || true; mv -f "$tmp" "$dst"
}

log "Installing encrypted password vault module, Main API, schema, and Panel users reveal controls."
atomic_copy "$SOURCE_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" 0644
atomic_copy "$SOURCE_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" 0644
for path in app/main.py app/models.py app/db.py app/panel_password_vault.py; do
  atomic_copy "$SOURCE_DIR/$path" "$APP_ROOT/$path" 0644
done
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/verify_panel_saved_password_v1263.py" "$APP_ROOT/scripts/verify_panel_saved_password_v1263.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1263_updater_integration.py" "$APP_ROOT/scripts/test_v1263_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1263.sh" "$APP_ROOT/scripts/force_update_v1263.sh"

if [[ "${STREAMFORGE_V1263_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.63"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.63"
cmp -s "$SOURCE_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" || abort_after_write "installed admin_users.html differs from package"
cmp -s "$SOURCE_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" || abort_after_write "installed admin_user_form.html differs from package"
for path in app/main.py app/models.py app/db.py app/panel_password_vault.py; do
  cmp -s "$SOURCE_DIR/$path" "$APP_ROOT/$path" || abort_after_write "installed $path differs from package"
done
python3 "$APP_ROOT/scripts/verify_panel_saved_password_v1263.py" || abort_after_write "installed password vault verification failed"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.63 template/version metadata."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.63 and bundled Node Agent v12.63 are installed."
log "Channel supervisor and Nginx were not restarted. Super Admin-only encrypted saved-password reveal is active."
