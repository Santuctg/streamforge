#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257:
# Move Node updates out of each Nodes card into a dedicated bulk update center.
# Selected Nodes start their established per-Node API update workers in parallel.
#
# STREAMFORGE_V1257_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT:
# Node runtime payload is unchanged from v12.56; only bundled Node VERSION is
# aligned to 12.57 so the new bulk center can roll the fleet to one version.
#
# STREAMFORGE_V1257_ROLLBACK_GUARD:
# After backup, any post-write failure restores Main code/templates and both
# VERSION files. Channel supervisor and Nginx are never restarted here.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.57"
NODE_TARGET_VERSION="12.57"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257"

log(){ echo "[StreamForge v${TARGET_VERSION} Node Bulk Updates] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Node Bulk Updates] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
for rel in app/main.py app/templates/base.html app/templates/nodes.html app/templates/node_updates.html; do
  [[ -f "$SOURCE_DIR/$rel" ]] || die "package file missing: $rel"
done
[[ -f "$SOURCE_DIR/scripts/verify_node_bulk_update_center_v1257.py" ]] || die "v12.57 verifier missing"
grep -Fq "$MARKER" "$SOURCE_DIR/app/main.py" || die "v12.57 Main marker missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256' "$SOURCE_DIR/node_agent/app.py" || die "V1256 Node Web Player guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
python3 "$SOURCE_DIR/scripts/verify_node_bulk_update_center_v1257.py"
python3 - "$SOURCE_DIR/app/main.py" "$SOURCE_DIR/scripts/apply_v1257_node_bulk_update_center.py" "$SOURCE_DIR/scripts/verify_node_bulk_update_center_v1257.py" <<'PYV1257SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.57 Python syntax verified without bytecode output")
PYV1257SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.56|12.57) ;;
  *) die "v12.57 bulk Node update center requires installed Main v12.56 or v12.57; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.56|12.57) ;;
  *) die "v12.57 requires bundled Node Agent v12.56 or v12.57; found $INSTALLED_NODE_VERSION" ;;
esac

# Runtime Node payload must be unchanged. A version-alignment release must never
# silently replace Node runtime code while presenting itself as a Main UI change.
python3 - "$SOURCE_DIR/node_agent" "$APP_ROOT/node_agent" <<'PYV1257NODE'
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
    changed = sorted(set(s) | set(d))
    bad = [name for name in changed if s.get(name) != d.get(name)]
    raise SystemExit('Node runtime payload differs for: ' + ', '.join(bad[:20]))
print(f"STREAMFORGE_V1257_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT: verified unchanged Node payload parity for {len(s)} files")
PYV1257NODE

for rel in app/main.py app/templates/base.html app/templates/nodes.html; do
  [[ -f "$APP_ROOT/$rel" ]] || die "installed file missing: $rel"
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1257-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/main.py" "$BACKUP_DIR/app/main.py"
cp -a "$APP_ROOT/app/templates/base.html" "$BACKUP_DIR/app/templates/base.html"
cp -a "$APP_ROOT/app/templates/nodes.html" "$BACKUP_DIR/app/templates/nodes.html"
if [[ -f "$APP_ROOT/app/templates/node_updates.html" ]]; then
  cp -a "$APP_ROOT/app/templates/node_updates.html" "$BACKUP_DIR/app/templates/node_updates.html"
  printf '1\n' > "$BACKUP_DIR/had-node-updates"
else
  printf '0\n' > "$BACKUP_DIR/had-node-updates"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Node Bulk Updates] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/main.py" "$APP_ROOT/app/main.py" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/base.html" "$APP_ROOT/app/templates/base.html" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/nodes.html" "$APP_ROOT/app/templates/nodes.html" 2>/dev/null || true
    if [[ "$(cat "$BACKUP_DIR/had-node-updates" 2>/dev/null || echo 0)" == "1" ]]; then
      cp -a "$BACKUP_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 2>/dev/null || true
    else
      rm -f "$APP_ROOT/app/templates/node_updates.html" 2>/dev/null || true
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){ echo "[StreamForge v${TARGET_VERSION} Node Bulk Updates] ERROR: $*" >&2; rollback 1; }
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1" dst="$2" mode="$3" dir tmp
  dir="$(dirname "$dst")"; mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1257.XXXXXX")"
  cp "$src" "$tmp"; chmod "$mode" "$tmp"; sync -f "$tmp" 2>/dev/null || true; mv -f "$tmp" "$dst"
}

log "Installing dedicated Node Updates page and parallel bulk update controls."
atomic_copy "$SOURCE_DIR/app/main.py" "$APP_ROOT/app/main.py" 0644
atomic_copy "$SOURCE_DIR/app/templates/base.html" "$APP_ROOT/app/templates/base.html" 0644
atomic_copy "$SOURCE_DIR/app/templates/nodes.html" "$APP_ROOT/app/templates/nodes.html" 0644
atomic_copy "$SOURCE_DIR/app/templates/node_updates.html" "$APP_ROOT/app/templates/node_updates.html" 0644
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/apply_v1257_node_bulk_update_center.py" "$APP_ROOT/scripts/apply_v1257_node_bulk_update_center.py"
install -m 0755 "$SOURCE_DIR/scripts/verify_node_bulk_update_center_v1257.py" "$APP_ROOT/scripts/verify_node_bulk_update_center_v1257.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1257_updater_integration.py" "$APP_ROOT/scripts/test_v1257_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1257.sh" "$APP_ROOT/scripts/force_update_v1257.sh"

if [[ "${STREAMFORGE_V1257_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.57"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.57"
for rel in app/main.py app/templates/base.html app/templates/nodes.html app/templates/node_updates.html; do
  cmp -s "$SOURCE_DIR/$rel" "$APP_ROOT/$rel" || abort_after_write "installed $rel differs from package"
done
python3 "$APP_ROOT/scripts/verify_node_bulk_update_center_v1257.py" || abort_after_write "installed bulk Node update center verification failed"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.57 routes/templates."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.57 and bundled Node Agent v12.57 are installed."
log "Channel supervisor and Nginx were not restarted. Use Node Updates to select and update remote Nodes in parallel."
