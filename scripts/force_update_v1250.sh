#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.50"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
PUBLIC_SERVICE="streamforge-public"
MAIN_SERVICE="streamforge"

log(){ echo "[StreamForge v${TARGET_VERSION} Main Hotfix] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Main Hotfix] ERROR: $*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package version"
[[ -f "$SOURCE_DIR/scripts/apply_v1250_main_fix.py" ]] || die "V1250 patcher missing"
[[ -f "$SOURCE_DIR/scripts/verify_local_relay_db_close_v1250.py" ]] || die "V1250 verifier missing"

TARGET="$APP_ROOT/app/main.py"
INSTALLED_VERSION_FILE="$APP_ROOT/VERSION"
[[ -f "$TARGET" ]] || die "Main source missing: $TARGET"
[[ -f "$INSTALLED_VERSION_FILE" ]] || die "installed VERSION missing: $INSTALLED_VERSION_FILE"

INSTALLED_VERSION="$(tr -d '[:space:]' < "$INSTALLED_VERSION_FILE")"
case "$INSTALLED_VERSION" in
  12.48|12.49|12.50) ;;
  *) die "supported installed versions are 12.48, 12.49, or 12.50; found $INSTALLED_VERSION" ;;
esac

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1250-$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$TARGET" "$BACKUP_DIR/main.py"
cp -a "$INSTALLED_VERSION_FILE" "$BACKUP_DIR/VERSION"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Main Hotfix] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/main.py" "$TARGET" || true
    cp -a "$BACKUP_DIR/VERSION" "$INSTALLED_VERSION_FILE" || true
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR

log "Installed version: $INSTALLED_VERSION"
log "Backup created: $BACKUP_DIR"

python3 "$SOURCE_DIR/scripts/apply_v1250_main_fix.py" "$TARGET"
python3 -m py_compile "$TARGET"
python3 "$SOURCE_DIR/scripts/verify_local_relay_db_close_v1250.py" "$TARGET"

mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/apply_v1250_main_fix.py" "$APP_ROOT/scripts/apply_v1250_main_fix.py"
install -m 0755 "$SOURCE_DIR/scripts/verify_local_relay_db_close_v1250.py" "$APP_ROOT/scripts/verify_local_relay_db_close_v1250.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1250.sh" "$APP_ROOT/scripts/force_update_v1250.sh"
printf '%s\n' "$TARGET_VERSION" > "$INSTALLED_VERSION_FILE"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers only; channel supervisor/FFmpeg processes are not restarted."
  systemctl restart "$PUBLIC_SERVICE"
  systemctl restart "$MAIN_SERVICE"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || die "$PUBLIC_SERVICE is not active"
  systemctl is-active --quiet "$MAIN_SERVICE" || die "$MAIN_SERVICE is not active"
fi

python3 "$APP_ROOT/scripts/verify_local_relay_db_close_v1250.py" "$TARGET"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$TARGET" || die "V1250 marker missing after update"
[[ "$(tr -d '[:space:]' < "$INSTALLED_VERSION_FILE")" == "$TARGET_VERSION" ]] || die "VERSION did not update"

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — V1250 local relay DB checkout release is installed; StreamForge version is $TARGET_VERSION."
log "Node Agent was not modified by this Main-only hotfix."
