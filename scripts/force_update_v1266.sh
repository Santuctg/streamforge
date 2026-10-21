#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1266_BOUNDED_AUTO_VAULT:
# Main-managed password escrow + disclosed updated-Node opt-in protocol.
# Main/Node agent source staged and backed up; remote Node services untouched.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET_VERSION="12.66"
NODE_TARGET_VERSION="12.66"
OLD_NODE_GIT_BLOB_SHA="4683d9740def533a9d384b5a0115c61a94942d8d"

log(){ echo "[StreamForge v12.66] $*"; }
die(){ echo "[StreamForge v12.66] ERROR: $*" >&2; exit 1; }
[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET_VERSION" ]] || die "Main package version mismatch"
[[ "$(< "$SRC/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "Node package version mismatch"
for file in \
    scripts/apply_v1264_ip_lock.py scripts/apply_v1265_ip_origin.py \
    scripts/apply_v1266_auto_vault.py scripts/test_v1266_auto_vault.py \
    app/main.py app/panel_login_guard.py app/panel_password_vault.py \
    app/models.py app/permissions.py app/templates/admin_users.html \
    app/templates/admin_user_form.html app/templates/account_password.html \
    node_agent/app.py; do
  [[ -f "$SRC/$file" ]] || die "missing package file $file"
done
python3 "$SRC/scripts/test_v1266_auto_vault.py"
python3 "$SRC/scripts/verify_panel_credential_vault_v1263.py"

[[ -f "$APP_ROOT/VERSION" && -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed version missing"
case "$(< "$APP_ROOT/VERSION")" in 12.64|12.65|12.66) ;; *) die "requires installed Main 12.64/12.65/12.66" ;; esac
case "$(< "$APP_ROOT/node_agent/VERSION")" in 12.64|12.65|12.66) ;; *) die "requires bundled Node 12.64/12.65/12.66" ;; esac

# STREAMFORGE_V1266_NODE_SOURCE_PARITY: only app.py changes.
python3 - "$SRC/node_agent" "$APP_ROOT/node_agent" "$OLD_NODE_GIT_BLOB_SHA" <<'PYNODE'
import hashlib
import sys
from pathlib import Path
source, installed = (Path(p) for p in sys.argv[1:3])
old_sha = sys.argv[3]
def digest(p):
    raw = p.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
# Exact baseline or already-installed new source; refuse local Node runtime drift.
old_file = installed / "app.py"
if not old_file.exists() or digest(old_file) not in (old_sha, digest(source/"app.py")):
    raise SystemExit("Node app.py differs from known baseline and package; refusing overwrite")
def runtime(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name not in ("VERSION", "app.py")
            and "__pycache__" not in p.parts and p.suffix not in (".pyc",".pyo")}
if runtime(source) != runtime(installed):
    bad = sorted(k for k in set(runtime(source))|set(runtime(installed))
                 if runtime(source).get(k) != runtime(installed).get(k))
    raise SystemExit("Unchanged Node runtime differs: " + ",".join(bad[:12]))
print("STREAMFORGE_V1266_NODE_SOURCE_PARITY: only bundled Node app.py changes")
PYNODE

STAGING="$(mktemp -d -t sf-v1266-preflight-XXXXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
cp "$APP_ROOT/app/main.py" "$STAGING/main.py"
python3 "$SRC/scripts/apply_v1264_ip_lock.py" "$STAGING/main.py"
python3 "$SRC/scripts/apply_v1265_ip_origin.py" "$STAGING/main.py"
python3 "$SRC/scripts/apply_v1266_auto_vault.py" "$STAGING/main.py"
python3 - "$STAGING/main.py" "$SRC/node_agent/app.py" <<'PYCOMPILE'
from pathlib import Path
import sys
for name in sys.argv[1:]:
    path=Path(name)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: staged Main and Node Python syntax")
PYCOMPILE
grep -Fq STREAMFORGE_V1266_AUTO_VAULT "$STAGING/main.py" || die "Main escrow patch missing"
grep -Fq STREAMFORGE_V1266_NODE_VAULT_DISCLOSURE "$SRC/node_agent/app.py" || die "Node disclosure marker missing"
grep -Fq STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER "$STAGING/main.py" || die "v12.65 block provenance missing"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1266-$STAMP"
FILES=(
  VERSION node_agent/VERSION node_agent/app.py
  app/main.py app/models.py app/permissions.py
  app/panel_password_vault.py app/panel_login_guard.py
  app/templates/admin_users.html app/templates/admin_user_form.html
  app/templates/account_password.html
)
for file in "${FILES[@]}"; do
  [[ -f "$APP_ROOT/$file" ]] || die "missing installed $file"
  mkdir -p "$BACKUP_DIR/$(dirname "$file")"
  cp -a "$APP_ROOT/$file" "$BACKUP_DIR/$file"
done
log "Backup created: $BACKUP_DIR"
ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" == "1" ]]; then
    log "Rolling back from $BACKUP_DIR"
    for file in "${FILES[@]}"; do cp -a "$BACKUP_DIR/$file" "$APP_ROOT/$file" || true; done
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
  local dest="$2" tmp
  tmp="$(mktemp "$(dirname "$dest")/.v1266.XXXXXX")"
  cp "$1" "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
}
for file in app/models.py app/permissions.py app/panel_password_vault.py app/panel_login_guard.py \
            app/templates/admin_users.html app/templates/admin_user_form.html \
            app/templates/account_password.html node_agent/app.py; do
  atomic_copy "$SRC/$file" "$APP_ROOT/$file"
done
atomic_copy "$STAGING/main.py" "$APP_ROOT/app/main.py"
printf '12.66\n' > "$APP_ROOT/VERSION"
printf '12.66\n' > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
for file in apply_v1264_ip_lock.py apply_v1265_ip_origin.py apply_v1266_auto_vault.py; do
  install -m 0755 "$SRC/scripts/$file" "$APP_ROOT/scripts/$file"
done
if [[ "${STREAMFORGE_V1266_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  log "Controlled failure after write"; rollback 1
fi
[[ "$(< "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || { log "Version mismatch"; rollback 1; }
cmp -s "$STAGING/main.py" "$APP_ROOT/app/main.py" || { log "Main differs from staged"; rollback 1; }
cmp -s "$SRC/node_agent/app.py" "$APP_ROOT/node_agent/app.py" || { log "Node differs from staged"; rollback 1; }

if [[ "$TEST_MODE" != "1" ]]; then
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || { log "Main service failed"; rollback 1; }
  systemctl is-active --quiet streamforge-public || { log "Public service failed"; rollback 1; }
fi
ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS: Main and bundled Node updated to 12.66; remote Nodes must upgrade separately."
log "Automatic recovery requires Main encryption key and updated Node disclosure form."
