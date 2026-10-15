#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258:
# Unify bulk, per-Node API and SSH recovery controls on Node Updates. The Node
# runtime payload is unchanged from v12.57; only its VERSION metadata advances.
# Main channel supervisor and Nginx are deliberately not restarted.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.58"
NODE_TARGET_VERSION="12.58"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258"

log(){ echo "[StreamForge v${TARGET_VERSION} Node Update Center] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Node Update Center] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
for rel in app/main.py app/templates/node_updates.html; do
  [[ -f "$SOURCE_DIR/$rel" ]] || die "package file missing: $rel"
done
[[ -f "$SOURCE_DIR/scripts/verify_node_update_center_cleanup_v1258.py" ]] || die "v12.58 verifier missing"
grep -Fq "$MARKER" "$SOURCE_DIR/app/main.py" || die "v12.58 Main marker missing"
grep -Fq 'STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257' "$SOURCE_DIR/app/main.py" || die "V1257 bulk-update guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256' "$SOURCE_DIR/node_agent/app.py" || die "V1256 Node Web Player guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
python3 "$SOURCE_DIR/scripts/verify_node_update_center_cleanup_v1258.py"
python3 - "$SOURCE_DIR/app/main.py" "$SOURCE_DIR/scripts/apply_v1258_node_update_center_cleanup.py" "$SOURCE_DIR/scripts/verify_node_update_center_cleanup_v1258.py" "$SOURCE_DIR/scripts/test_v1258_updater_integration.py" <<'PYV1258SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.58 Python syntax verified without bytecode output")
PYV1258SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.57|12.58) ;;
  *) die "v12.58 Node Update Center cleanup requires installed Main v12.57 or v12.58; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.57|12.58) ;;
  *) die "v12.58 requires bundled Node Agent v12.57 or v12.58; found $INSTALLED_NODE_VERSION" ;;
esac

# This is a Main UI/route cleanup release. Any Node runtime delta is a packaging
# regression and must stop the update before production files are touched.
python3 - "$SOURCE_DIR/node_agent" "$APP_ROOT/node_agent" <<'PYV1258NODE'
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
print(f"STREAMFORGE_V1258_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT: verified unchanged Node payload parity for {len(s)} files")
PYV1258NODE

[[ -f "$APP_ROOT/app/main.py" ]] || die "installed app/main.py missing"
[[ -f "$APP_ROOT/app/templates/node_updates.html" ]] || die "installed node_updates.html missing"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1258-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/main.py" "$BACKUP_DIR/app/main.py"
cp -a "$APP_ROOT/app/templates/node_updates.html" "$BACKUP_DIR/app/templates/node_updates.html"
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Node Update Center] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/main.py" "$APP_ROOT/app/main.py" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 2>/dev/null || true
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){ echo "[StreamForge v${TARGET_VERSION} Node Update Center] ERROR: $*" >&2; rollback 1; }
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1" dst="$2" mode="$3" dir tmp
  dir="$(dirname "$dst")"; mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1258.XXXXXX")"
  cp "$src" "$tmp"; chmod "$mode" "$tmp"; sync -f "$tmp" 2>/dev/null || true; mv -f "$tmp" "$dst"
}

log "Installing unified Node Updates routes, live version refresh and inline Advanced controls."
atomic_copy "$SOURCE_DIR/app/main.py" "$APP_ROOT/app/main.py" 0644
atomic_copy "$SOURCE_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 0644
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/apply_v1258_node_update_center_cleanup.py" "$APP_ROOT/scripts/apply_v1258_node_update_center_cleanup.py"
install -m 0755 "$SOURCE_DIR/scripts/verify_node_update_center_cleanup_v1258.py" "$APP_ROOT/scripts/verify_node_update_center_cleanup_v1258.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1258_updater_integration.py" "$APP_ROOT/scripts/test_v1258_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1258.sh" "$APP_ROOT/scripts/force_update_v1258.sh"

if [[ "${STREAMFORGE_V1258_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.58"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.58"
cmp -s "$SOURCE_DIR/app/main.py" "$APP_ROOT/app/main.py" || abort_after_write "installed app/main.py differs from package"
cmp -s "$SOURCE_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" || abort_after_write "installed node_updates.html differs from package"
python3 "$APP_ROOT/scripts/verify_node_update_center_cleanup_v1258.py" || abort_after_write "installed Node Update Center verification failed"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.58 routes/templates."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.58 and bundled Node Agent v12.58 are installed."
log "Channel supervisor and Nginx were not restarted. Advanced Node recovery now stays inside Node Updates."
