#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_PANEL_PASSWORD_VAULT_LOGIN_GUARD_V1263:
# Protect Main/Node login attempts and encrypt recoverable future Panel passwords.
# Node runtime bytes are unchanged from v12.62.
# Main channel supervisor and Nginx are deliberately not restarted.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.63"
NODE_TARGET_VERSION="12.63"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_PANEL_PASSWORD_VAULT_LOGIN_GUARD_V1263"

log(){ echo "[StreamForge v${TARGET_VERSION} Panel Vault and Login Guard] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Panel Vault and Login Guard] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
[[ -f "$SOURCE_DIR/app/templates/node_updates.html" ]] || die "package previous Node Updates template missing"
[[ -f "$SOURCE_DIR/app/templates/admin_users.html" ]] || die "package Panel users template missing"
[[ -f "$SOURCE_DIR/app/templates/admin_user_form.html" ]] || die "package Panel user form template missing"
for rel in app/main.py app/db.py app/models.py app/panel_password_security.py app/templates/admin_users.html app/templates/admin_user_form.html; do
  [[ -f "$SOURCE_DIR/$rel" ]] || die "Missing package file: $rel"
done
[[ -f "$SOURCE_DIR/scripts/test_v1263_security.py" ]] || die "v12.63 verifier missing"
grep -Fq "$MARKER" "$SOURCE_DIR/app/main.py" || die "v12.63 security marker missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259' "$SOURCE_DIR/app/templates/node_updates.html" || die "V1259 Node Update UI spacing guard missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258' "$SOURCE_DIR/app/main.py" || die "V1258 Node Update Center guard missing"
grep -Fq 'STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257' "$SOURCE_DIR/app/main.py" || die "V1257 bulk-update guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256' "$SOURCE_DIR/node_agent/app.py" || die "V1256 Node Web Player guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
PYTHONPATH="$SOURCE_DIR" python3 "$SOURCE_DIR/scripts/test_v1263_security.py"
python3 - "$SOURCE_DIR/scripts/test_v1263_security.py" "$SOURCE_DIR/scripts/test_v1263_updater_integration.py" <<'PYV1263SYNTAX'
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
  *) die "v12.63 security release requires installed Main v12.62 or v12.63; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.62|12.63) ;;
  *) die "v12.63 requires bundled Node Agent v12.62 or v12.63; found $INSTALLED_NODE_VERSION" ;;
esac

# STREAMFORGE_V1263_MAIN_SECURITY_NODE_RUNTIME_ALIGNMENT:
# v12.63 updates Main login/security, Panel users, and aligned metadata. Any Node runtime byte change is a
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
for rel in app/main.py app/db.py app/models.py; do
  [[ -f "$APP_ROOT/$rel" ]] || die "installed $rel missing"
done


# Preserve a separate encryption key across all application updates/restores.
# Never overwrite or rotate an existing key: that would make saved passwords undecryptable.
VAULT_KEY_PATH="${STREAMFORGE_PANEL_VAULT_KEY_FILE:-/etc/streamforge-panel-vault.key}"
if [[ "$TEST_MODE" == "1" ]]; then
  VAULT_KEY_PATH="${STREAMFORGE_PANEL_VAULT_KEY_FILE:-$APP_ROOT/test-panel-vault.key}"
fi
python3 - "$VAULT_KEY_PATH" "$TEST_MODE" <<'PYV1263KEY'
from pathlib import Path
import grp, os, secrets, stat, sys
path=Path(sys.argv[1])
test=sys.argv[2]=="1"
if not path.exists():
    if not test and path.parent != Path('/etc'):
        raise SystemExit("Vault key must be stored under /etc")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w',encoding='ascii') as stream:
        stream.write(secrets.token_hex(32)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    if not test:
        os.chown(path,0,grp.getgrnam('streamforge').gr_gid)
        os.chmod(path,0o640)
info=path.stat()
if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode&0o007:
    raise SystemExit("Unsafe vault key path or file permissions")
if len(bytes.fromhex(path.read_text(encoding='ascii').strip()))!=32:
    raise SystemExit("Invalid vault key data")
PYV1263KEY
if [[ "$TEST_MODE" != "1" ]]; then
  runuser -u streamforge -- test -r "$VAULT_KEY_PATH" || die "service user cannot read vault key"
fi
export STREAMFORGE_PANEL_VAULT_KEY_FILE="$VAULT_KEY_PATH"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1263-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/templates/admin_users.html" "$BACKUP_DIR/app/templates/admin_users.html"
cp -a "$APP_ROOT/app/templates/admin_user_form.html" "$BACKUP_DIR/app/templates/admin_user_form.html"
mkdir -p "$BACKUP_DIR/app"
for rel in main.py db.py models.py; do cp -a "$APP_ROOT/app/$rel" "$BACKUP_DIR/app/$rel"; done
if [[ -f "$APP_ROOT/app/panel_password_security.py" ]]; then
  cp -a "$APP_ROOT/app/panel_password_security.py" "$BACKUP_DIR/app/panel_password_security.py"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Panel Vault and Login Guard] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" 2>/dev/null || true
    for rel in main.py db.py models.py; do cp -a "$BACKUP_DIR/app/$rel" "$APP_ROOT/app/$rel" 2>/dev/null || true; done
    if [[ -f "$BACKUP_DIR/app/panel_password_security.py" ]]; then
      cp -a "$BACKUP_DIR/app/panel_password_security.py" "$APP_ROOT/app/panel_password_security.py" 2>/dev/null || true
    else
      rm -f "$APP_ROOT/app/panel_password_security.py"
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){ echo "[StreamForge v${TARGET_VERSION} Panel Vault and Login Guard] ERROR: $*" >&2; rollback 1; }
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1" dst="$2" mode="$3" dir tmp
  dir="$(dirname "$dst")"; mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1263.XXXXXX")"
  cp "$src" "$tmp"; chmod "$mode" "$tmp"; sync -f "$tmp" 2>/dev/null || true; mv -f "$tmp" "$dst"
}

log "Installing Panel users encrypted Panel vault, login guard and per-user reveal."
atomic_copy "$SOURCE_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" 0644
atomic_copy "$SOURCE_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" 0644
for rel in main.py db.py models.py panel_password_security.py; do
  atomic_copy "$SOURCE_DIR/app/$rel" "$APP_ROOT/app/$rel" 0644
done
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/test_v1263_security.py" "$APP_ROOT/scripts/test_v1263_security.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1263_updater_integration.py" "$APP_ROOT/scripts/test_v1263_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1263.sh" "$APP_ROOT/scripts/force_update_v1263.sh"

if [[ "${STREAMFORGE_V1263_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.63"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.63"
cmp -s "$SOURCE_DIR/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" || abort_after_write "installed admin_users.html differs from package"
cmp -s "$SOURCE_DIR/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" || abort_after_write "installed admin_user_form.html differs from package"
for rel in main.py db.py models.py panel_password_security.py; do
  cmp -s "$SOURCE_DIR/app/$rel" "$APP_ROOT/app/$rel" || abort_after_write "installed app/$rel differs"
done
PYTHONPATH="$APP_ROOT" python3 "$APP_ROOT/scripts/test_v1263_security.py" || abort_after_write "installed security verifier failed"

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
log "Channel supervisor and Nginx were not restarted. The Super Admin vault and login rate limits are enabled."
