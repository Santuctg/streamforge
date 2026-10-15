#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_NODE_INLINE_UPDATE_LOG_V1260:
# Show each Remote Node's established background update log inside that Node's
# Advanced panel. Node runtime bytes are unchanged from v12.59.
# Main channel supervisor and Nginx are deliberately not restarted.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.60"
NODE_TARGET_VERSION="12.60"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_NODE_INLINE_UPDATE_LOG_V1260"

log(){ echo "[StreamForge v${TARGET_VERSION} Inline Node Update Log] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Inline Node Update Log] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
[[ -f "$SOURCE_DIR/app/templates/node_updates.html" ]] || die "package node_updates.html missing"
[[ -f "$SOURCE_DIR/scripts/verify_node_inline_update_log_v1260.py" ]] || die "v12.60 verifier missing"
grep -Fq "$MARKER" "$SOURCE_DIR/app/templates/node_updates.html" || die "v12.60 inline-log marker missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259' "$SOURCE_DIR/app/templates/node_updates.html" || die "V1259 Node Update UI spacing guard missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258' "$SOURCE_DIR/app/main.py" || die "V1258 Node Update Center guard missing"
grep -Fq 'STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257' "$SOURCE_DIR/app/main.py" || die "V1257 bulk-update guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256' "$SOURCE_DIR/node_agent/app.py" || die "V1256 Node Web Player guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
python3 "$SOURCE_DIR/scripts/verify_node_inline_update_log_v1260.py"
python3 - "$SOURCE_DIR/scripts/verify_node_inline_update_log_v1260.py" "$SOURCE_DIR/scripts/test_v1260_updater_integration.py" <<'PYV1260SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.60 Python syntax verified without bytecode output")
PYV1260SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.59|12.60) ;;
  *) die "v12.60 inline Node update log requires installed Main v12.59 or v12.60; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.59|12.60) ;;
  *) die "v12.60 requires bundled Node Agent v12.59 or v12.60; found $INSTALLED_NODE_VERSION" ;;
esac

# STREAMFORGE_V1260_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT:
# v12.60 is a Main template-only UI release. Any Node runtime byte change is a
# packaging regression and stops the update before production files are touched.
python3 - "$SOURCE_DIR/node_agent" "$APP_ROOT/node_agent" <<'PYV1260NODE'
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
print(f"STREAMFORGE_V1260_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT: verified unchanged Node payload parity for {len(s)} files")
PYV1260NODE

[[ -f "$APP_ROOT/app/templates/node_updates.html" ]] || die "installed node_updates.html missing"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1260-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/templates/node_updates.html" "$BACKUP_DIR/app/templates/node_updates.html"
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Inline Node Update Log] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 2>/dev/null || true
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){ echo "[StreamForge v${TARGET_VERSION} Inline Node Update Log] ERROR: $*" >&2; rollback 1; }
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1" dst="$2" mode="$3" dir tmp
  dir="$(dirname "$dst")"; mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1260.XXXXXX")"
  cp "$src" "$tmp"; chmod "$mode" "$tmp"; sync -f "$tmp" 2>/dev/null || true; mv -f "$tmp" "$dst"
}

log "Installing per-Node live update logs inside Advanced panels."
atomic_copy "$SOURCE_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 0644
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/verify_node_inline_update_log_v1260.py" "$APP_ROOT/scripts/verify_node_inline_update_log_v1260.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1260_updater_integration.py" "$APP_ROOT/scripts/test_v1260_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1260.sh" "$APP_ROOT/scripts/force_update_v1260.sh"

if [[ "${STREAMFORGE_V1260_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.60"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.60"
cmp -s "$SOURCE_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" || abort_after_write "installed node_updates.html differs from package"
python3 "$APP_ROOT/scripts/verify_node_inline_update_log_v1260.py" || abort_after_write "installed inline Node update log verification failed"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.60 template/version metadata."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.60 and bundled Node Agent v12.60 are installed."
log "Channel supervisor and Nginx were not restarted. Per-Node live update logs are active inside Advanced."
