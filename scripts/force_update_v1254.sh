#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_MAIN_ONLY_HOTFIX_V1254:
# This updater fixes Main static-asset deployment only. It never modifies or
# pushes the Remote Node Agent, which remains v12.52.
# STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254:
# Nginx serves /static/ directly from /var/cache/streamforge/main-static, so a
# Main release must refresh that cache after updating app/static.
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.54"
NODE_TARGET_VERSION="12.52"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"

log(){ echo "[StreamForge v${TARGET_VERSION} Main Static Cache] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Main Static Cache] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] \
  || die "v12.54 is Main-only and requires bundled Node Agent v12.52"
[[ -d "$SOURCE_DIR/app/static" ]] || die "package app/static directory missing"
[[ -f "$SOURCE_DIR/scripts/sync_main_static_cache.py" ]] || die "v12.54 static sync helper missing"
[[ -f "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" ]] || die "V1253 verifier missing"

grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$SOURCE_DIR/app/static/app.js" \
  || die "V1253 Waiting UI marker missing from package app.js"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" \
  || die "V1250 Main DB-close guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" \
  || die "V1251 Node relay-health guard missing"
python3 "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" "$SOURCE_DIR"

# STREAMFORGE_V1254_NO_SOURCE_BYTECODE: syntax validation must never dirty the
# release source tree. The sync helper is compiled from text in memory only.
python3 - "$SOURCE_DIR/scripts/sync_main_static_cache.py" <<'PYV1254SYNTAX'
from pathlib import Path
import sys
path = Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: V1254 sync helper syntax verified without bytecode output")
PYV1254SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.53|12.54) ;;
  *) die "v12.54 Main hotfix requires installed v12.53 or v12.54; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] \
  || die "installed Node Agent must remain v12.52 for this Main-only update"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1254-$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
if [[ -d "$APP_ROOT/app/static" ]]; then
  mkdir -p "$BACKUP_DIR/app-static"
  cp -a "$APP_ROOT/app/static/." "$BACKUP_DIR/app-static/"
fi
if [[ -d "$STATIC_CACHE_ROOT" ]]; then
  mkdir -p "$BACKUP_DIR/main-static"
  cp -a "$STATIC_CACHE_ROOT/." "$BACKUP_DIR/main-static/"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Main Static Cache] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    if [[ -d "$BACKUP_DIR/app-static" ]]; then
      mkdir -p "$APP_ROOT/app/static"
      cp -a "$BACKUP_DIR/app-static/." "$APP_ROOT/app/static/" 2>/dev/null || true
    fi
    if [[ -d "$BACKUP_DIR/main-static" ]]; then
      mkdir -p "$STATIC_CACHE_ROOT"
      cp -a "$BACKUP_DIR/main-static/." "$STATIC_CACHE_ROOT/" 2>/dev/null || true
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR

log "Installing packaged Main static assets. Remote Node Agent is unchanged."
python3 "$SOURCE_DIR/scripts/sync_main_static_cache.py" \
  "$SOURCE_DIR/app/static" "$APP_ROOT/app/static"

mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/sync_main_static_cache.py" "$APP_ROOT/scripts/sync_main_static_cache.py"
install -m 0755 "$SOURCE_DIR/scripts/test_main_static_cache_sync_v1254.py" "$APP_ROOT/scripts/test_main_static_cache_sync_v1254.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1254_updater_static_cache_integration.py" "$APP_ROOT/scripts/test_v1254_updater_static_cache_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1254.sh" "$APP_ROOT/scripts/force_update_v1254.sh"
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"

log "Refreshing Nginx direct static cache from installed Main assets."
python3 "$SOURCE_DIR/scripts/sync_main_static_cache.py" \
  "$APP_ROOT/app/static" "$STATIC_CACHE_ROOT"

python3 - "$SOURCE_DIR/app/static" "$APP_ROOT/app/static" "$STATIC_CACHE_ROOT" <<'PYV1254VERIFY'
from pathlib import Path
import hashlib
import sys

source, installed, cache = map(Path, sys.argv[1:4])

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

count = 0
for src in sorted(path for path in source.rglob("*") if path.is_file()):
    rel = src.relative_to(source)
    expected = digest(src)
    for label, root in (("installed", installed), ("nginx-cache", cache)):
        target = root / rel
        if not target.is_file():
            raise SystemExit(f"missing {label} static asset: {rel}")
        if digest(target) != expected:
            raise SystemExit(f"SHA256 mismatch for {label} static asset: {rel}")
    count += 1
print(f"STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254: verified SHA256 parity for {count} static assets")
PYV1254VERIFY

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.54 asset version metadata."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || die "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || die "$PUBLIC_SERVICE is not active"
fi

[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || die "Main VERSION did not update"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "Node Agent version changed unexpectedly"

grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$APP_ROOT/app/static/app.js" \
  || die "installed app.js lost V1253 Waiting UI marker"
grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$STATIC_CACHE_ROOT/app.js" \
  || die "Nginx static cache app.js lost V1253 Waiting UI marker"

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.54 static cache sync is installed; bundled Node Agent remains v12.52."
log "Nginx /static/ assets now match /opt/streamforge/app/static by SHA256."
