#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1263_MAIN_ONLY_VAULT_UPDATE:
# Scoped Main model/module/templates patch with backup/rollback. Node runtime
# and channel supervisor/nginx deliberately untouched.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET_VERSION="12.63"
NODE_TARGET_VERSION="12.63"
log(){ echo "[StreamForge v12.63 Vault] $*"; }
die(){ echo "[StreamForge v12.63 Vault] ERROR: $*" >&2; exit 1; }
[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "root required"
[[ "$(cat "$SRC/VERSION" | tr -d '[:space:]')" == "$TARGET_VERSION" ]] || die "package Main VERSION mismatch"
[[ "$(cat "$SRC/node_agent/VERSION" | tr -d '[:space:]')" == "$NODE_TARGET_VERSION" ]] || die "package Node VERSION mismatch"
[[ -f "$SRC/scripts/apply_v1263_vault.py" ]] || die "missing scoped Main patcher"
[[ -f "$SRC/app/panel_password_vault.py" ]] || die "missing vault source"
[[ -f "$SRC/app/models.py" ]] || die "missing model source"
[[ -f "$SRC/app/templates/admin_users.html" ]] || die "missing listing source"
[[ -f "$SRC/app/templates/admin_user_form.html" ]] || die "missing form source"
grep -Fq STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263 "$SRC/app/panel_password_vault.py" || die "missing vault marker"
grep -Fq STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262 "$SRC/app/templates/admin_users.html" || die "v12.62 navigation guard absent"
grep -Fq STREAMFORGE_NODE_INLINE_UPDATE_LOG_V1260 "$SRC/app/templates/node_updates.html" || die "v12.60 Node log guard absent"
python3 "$SRC/scripts/verify_panel_credential_vault_v1263.py"
for path in "$SRC/scripts/apply_v1263_vault.py" "$SRC/app/panel_password_vault.py" "$SRC/app/models.py"; do
  python3 - "$path" <<'PYCOMPILE'
from pathlib import Path
import sys
path=Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"),str(path),"exec")
PYCOMPILE
done
[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
case "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" in
  12.62|12.63) ;;
  *) die "upgrade requires installed Main 12.62 or 12.63" ;;
esac
case "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" in
  12.62|12.63) ;;
  *) die "upgrade requires bundled Node 12.62 or 12.63" ;;
esac
# STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT:
# Compare every Node runtime file before production writes, excluding metadata
# and caches; no separate Node ZIP, no Node service restart.
python3 - "$SRC/node_agent" "$APP_ROOT/node_agent" <<'PYNODE'
import hashlib
from pathlib import Path
import sys
def checks(root):
    result={}
    for p in Path(root).rglob("*"):
        if not p.is_file() or p.name=="VERSION" or "__pycache__" in p.parts or p.suffix in (".pyc",".pyo"):continue
        result[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result
src,dst=(checks(x) for x in sys.argv[1:])
if src!=dst:
    bad=sorted(k for k in set(src)|set(dst) if src.get(k)!=dst.get(k))
    raise SystemExit("Node runtime payload differs for: "+",".join(bad[:12]))
print("STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT: verified",len(src),"unchanged Node files")
PYNODE
for p in app/main.py app/models.py app/templates/admin_users.html app/templates/admin_user_form.html; do
  [[ -f "$APP_ROOT/$p" ]] || die "installed $p missing"
done
STAGING="$(mktemp -d -t sf-v1263-preflight-XXXXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
cp -a "$APP_ROOT/app/main.py" "$STAGING/main.py"
python3 "$SRC/scripts/apply_v1263_vault.py" "$STAGING/main.py" || die "Main patcher preflight failed"
grep -Fq STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263 "$STAGING/main.py" || die "Main patch marker missing"
python3 - "$STAGING/main.py" <<'PYMAIN'
from pathlib import Path
import sys
p=Path(sys.argv[1])
compile(p.read_text(encoding="utf-8"),str(p),"exec")
print("PASS: preflight Main Python syntax")
PYMAIN

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1263-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
for f in VERSION node_agent/VERSION app/main.py app/models.py app/templates/admin_users.html app/templates/admin_user_form.html; do
  mkdir -p "$BACKUP_DIR/$(dirname "$f")"
  cp -a "$APP_ROOT/$f" "$BACKUP_DIR/$f"
done
EXISTING_MODULE=0
if [[ -e "$APP_ROOT/app/panel_password_vault.py" ]]; then
  EXISTING_MODULE=1
  cp -a "$APP_ROOT/app/panel_password_vault.py" "$BACKUP_DIR/app/panel_password_vault.py"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" == "1" ]]; then
    log "Rolling back from $BACKUP_DIR"
    for f in VERSION node_agent/VERSION app/main.py app/models.py app/templates/admin_users.html app/templates/admin_user_form.html; do
      cp -a "$BACKUP_DIR/$f" "$APP_ROOT/$f" 2>/dev/null || true
    done
    if [[ "$EXISTING_MODULE" == "1" ]]; then
      cp -a "$BACKUP_DIR/app/panel_password_vault.py" "$APP_ROOT/app/panel_password_vault.py" 2>/dev/null || true
    else
      rm -f "$APP_ROOT/app/panel_password_vault.py"
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
abort(){ log "ERROR: $*"; rollback 1; }
atomic_copy(){
  local dir tmp
  dir="$(dirname "$2")"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1263.XXXXXX")"
  cp "$1" "$tmp"
  chmod "$3" "$tmp"
  mv -f "$tmp" "$2"
}

log "Installing bounded Main-only encrypted Panel credential vault."
atomic_copy "$SRC/app/panel_password_vault.py" "$APP_ROOT/app/panel_password_vault.py" 0644
atomic_copy "$SRC/app/models.py" "$APP_ROOT/app/models.py" 0644
atomic_copy "$SRC/app/templates/admin_users.html" "$APP_ROOT/app/templates/admin_users.html" 0644
atomic_copy "$SRC/app/templates/admin_user_form.html" "$APP_ROOT/app/templates/admin_user_form.html" 0644
atomic_copy "$STAGING/main.py" "$APP_ROOT/app/main.py" 0644
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SRC/scripts/apply_v1263_vault.py" "$APP_ROOT/scripts/apply_v1263_vault.py"
install -m 0755 "$SRC/scripts/verify_panel_credential_vault_v1263.py" "$APP_ROOT/scripts/verify_panel_credential_vault_v1263.py"
install -m 0755 "$SRC/scripts/force_update_v1263.sh" "$APP_ROOT/scripts/force_update_v1263.sh"

if [[ "${STREAMFORGE_V1263_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort "controlled post-write failure"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort "Main version mismatch"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort "Node version mismatch"
cmp -s "$STAGING/main.py" "$APP_ROOT/app/main.py" || abort "Main patch differs from staged"
cmp -s "$SRC/app/models.py" "$APP_ROOT/app/models.py" || abort "Models differ from package"
cmp -s "$SRC/app/panel_password_vault.py" "$APP_ROOT/app/panel_password_vault.py" || abort "Vault module differs from package"
grep -Fq STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263 "$APP_ROOT/app/main.py" || abort "Main integration missing"
if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers only."
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || abort "streamforge service inactive"
  systemctl is-active --quiet streamforge-public || abort "streamforge-public service inactive"
fi
ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS: Main and bundled Node metadata 12.63 installed. Channel supervisor/Nginx untouched."
log "Vault reveal is disabled until STREAMFORGE_PANEL_PASSWORD_VAULT_KEY is configured for Main."
