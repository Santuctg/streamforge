#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.53"
NODE_TARGET_VERSION="12.52"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
SUPERVISOR_SERVICE="streamforge-channel-supervisor"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"

log(){ echo "[StreamForge v${TARGET_VERSION} Main Waiting Recovery] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Main Waiting Recovery] ERROR: $*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "v12.53 is Main-only and requires bundled Node Agent v12.52"
[[ -f "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" ]] || die "V1253 verifier missing"

FILES=(
  app/models.py
  app/db.py
  app/recovery_policy.py
  app/ffmpeg.py
  app/main_channel_supervisor.py
  app/main.py
  app/static/app.js
  app/templates/channels.html
)
for rel in "${FILES[@]}"; do
  [[ -f "$SOURCE_DIR/$rel" ]] || die "package file missing: $rel"
done

log "Verifying package source before deployment."
python3 "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" "$SOURCE_DIR"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" \
  || die "V1250 Main DB-close guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" \
  || die "V1251 Node relay-health guard missing"
# STREAMFORGE_V1253_NO_SOURCE_BYTECODE: syntax validation must not dirty the release tree.
python3 - "$SOURCE_DIR" <<'PYV1253SYNTAX'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for rel in (
    "app/models.py", "app/db.py", "app/recovery_policy.py", "app/ffmpeg.py",
    "app/main_channel_supervisor.py", "app/main.py",
):
    path = root / rel
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: V1253 Python syntax verified without bytecode output")
PYV1253SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.52|12.53) ;;
  *) die "v12.53 Main hotfix requires installed v12.52 or v12.53; found $INSTALLED_VERSION" ;;
esac
[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] \
  || die "installed Node Agent must remain v12.52 for this Main-only update"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1253-$STAMP"
mkdir -p "$BACKUP_DIR"
for rel in "${FILES[@]}"; do
  mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
  cp -a "$APP_ROOT/$rel" "$BACKUP_DIR/$rel"
done
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Main Waiting Recovery] Rolling back from $BACKUP_DIR" >&2
    for rel in "${FILES[@]}"; do
      cp -a "$BACKUP_DIR/$rel" "$APP_ROOT/$rel" || true
    done
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" || true
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$SUPERVISOR_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR

log "Installing Main waiting/retry source. Remote Node Agent is unchanged."
for rel in "${FILES[@]}"; do
  install -D -m 0644 "$SOURCE_DIR/$rel" "$APP_ROOT/$rel"
done
mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/apply_v1253_main_waiting_retry_fix.py" "$APP_ROOT/scripts/apply_v1253_main_waiting_retry_fix.py"
install -m 0755 "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" "$APP_ROOT/scripts/verify_main_waiting_retry_v1253.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1253.sh" "$APP_ROOT/scripts/force_update_v1253.sh"
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"

log "Applying additive waiting_since/next_retry_at schema."
PYTHONPATH="$APP_ROOT" python3 - <<'PYV1253SCHEMA'
from app.db import ensure_runtime_schema
ensure_runtime_schema()
print("PASS: V1253 runtime schema ensured")
PYV1253SCHEMA

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main channel supervisor and HTTP workers to load v12.53."
  systemctl restart "$SUPERVISOR_SERVICE"
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$SUPERVISOR_SERVICE" || die "$SUPERVISOR_SERVICE is not active"
  systemctl is-active --quiet "$MAIN_SERVICE" || die "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || die "$PUBLIC_SERVICE is not active"
fi

python3 "$APP_ROOT/scripts/verify_main_waiting_retry_v1253.py" "$APP_ROOT"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || die "Main VERSION did not update"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "Node Agent version changed unexpectedly"

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.53 waiting recovery is installed; bundled Node Agent remains v12.52."
log "Waiting duration resets only after true Up/HLS-ready delivery; full failed input cycles retry every 60 seconds."
