#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256:
# Prefer HLS.js/MSE over Chromium's ambiguous native-HLS "maybe" response in
# both Main and Node Web Players, while retaining native HLS as fallback.
#
# STREAMFORGE_V1256_BOUNDED_PLAYER_UPDATE:
# This release changes only the Main player template, bundled Node app.py,
# version metadata, and release helper scripts. It does not restart the Main
# channel supervisor, reload Nginx, or alter FFmpeg/transcode policy.
#
# STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:
# STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251:
# STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254:
# Carry-forward guards remain mandatory.
#
# STREAMFORGE_V1256_ROLLBACK_GUARD:
# After backup, implicit failures and explicit validation failures restore the
# previous Main/Node player files and both VERSION files.

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.56"
NODE_TARGET_VERSION="12.56"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"
MARKER="STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256"

log(){ echo "[StreamForge v${TARGET_VERSION} Browser Playback] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Browser Playback] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
[[ -f "$SOURCE_DIR/app/templates/player.html" ]] || die "package Main player template missing"
[[ -f "$SOURCE_DIR/node_agent/app.py" ]] || die "package Node app.py missing"
[[ -f "$SOURCE_DIR/scripts/verify_webplayer_hlsjs_first_v1256.py" ]] || die "v12.56 Web Player verifier missing"
[[ -f "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" ]] || die "V1253 verifier missing"
[[ -f "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" ]] || die "V1251 Node verifier missing"

grep -Fq "$MARKER" "$SOURCE_DIR/app/templates/player.html" || die "v12.56 marker missing from Main player"
grep -Fq "$MARKER" "$SOURCE_DIR/node_agent/app.py" || die "v12.56 marker missing from Node player"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" || die "V1250 Main DB-close guard missing"
grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$SOURCE_DIR/app/static/app.js" || die "V1253 Waiting UI marker missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" || die "V1251 Node relay-health guard missing"
grep -Fq 'STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254' "$SOURCE_DIR/scripts/sync_main_static_cache.py" || die "V1254 static-cache helper missing"

python3 "$SOURCE_DIR/scripts/verify_webplayer_hlsjs_first_v1256.py" "$SOURCE_DIR"
python3 "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" "$SOURCE_DIR"
python3 "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" "$SOURCE_DIR/node_agent/app.py"

python3 - "$SOURCE_DIR/node_agent/app.py" "$SOURCE_DIR/scripts/verify_webplayer_hlsjs_first_v1256.py" <<'PYV1256SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.56 Python syntax verified without bytecode output")
PYV1256SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.55|12.56) ;;
  *) die "v12.56 browser compatibility update requires installed Main v12.55 or v12.56; found $INSTALLED_VERSION" ;;
esac

[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.55|12.56) ;;
  *) die "v12.56 browser compatibility update requires bundled Node Agent v12.55 or v12.56; found $INSTALLED_NODE_VERSION" ;;
esac

[[ -f "$APP_ROOT/app/templates/player.html" ]] || die "installed Main player template missing"
[[ -f "$APP_ROOT/node_agent/app.py" ]] || die "installed bundled Node app.py missing"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1256-$STAMP"
mkdir -p "$BACKUP_DIR/app/templates" "$BACKUP_DIR/node_agent"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node_agent/VERSION"
cp -a "$APP_ROOT/app/templates/player.html" "$BACKUP_DIR/app/templates/player.html"
cp -a "$APP_ROOT/node_agent/app.py" "$BACKUP_DIR/node_agent/app.py"
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Browser Playback] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/app/templates/player.html" "$APP_ROOT/app/templates/player.html" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node_agent/app.py" "$APP_ROOT/node_agent/app.py" 2>/dev/null || true
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){
  echo "[StreamForge v${TARGET_VERSION} Browser Playback] ERROR: $*" >&2
  rollback 1
}
trap 'rollback $?' ERR

atomic_copy(){
  local src="$1"
  local dst="$2"
  local mode="$3"
  local dir tmp
  dir="$(dirname "$dst")"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.v1256.XXXXXX")"
  cp "$src" "$tmp"
  chmod "$mode" "$tmp"
  sync -f "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$dst"
}

log "Installing HLS.js-first Main and bundled Node Web Player code."
atomic_copy "$SOURCE_DIR/app/templates/player.html" "$APP_ROOT/app/templates/player.html" 0644
atomic_copy "$SOURCE_DIR/node_agent/app.py" "$APP_ROOT/node_agent/app.py" 0644
printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"

mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/apply_v1256_webplayer_hlsjs_first.py" "$APP_ROOT/scripts/apply_v1256_webplayer_hlsjs_first.py"
install -m 0755 "$SOURCE_DIR/scripts/verify_webplayer_hlsjs_first_v1256.py" "$APP_ROOT/scripts/verify_webplayer_hlsjs_first_v1256.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1256_updater_integration.py" "$APP_ROOT/scripts/test_v1256_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1256.sh" "$APP_ROOT/scripts/force_update_v1256.sh"

if [[ "${STREAMFORGE_V1256_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi

[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not update to v12.56"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not update to v12.56"
cmp -s "$SOURCE_DIR/app/templates/player.html" "$APP_ROOT/app/templates/player.html" || abort_after_write "installed Main player differs from package"
cmp -s "$SOURCE_DIR/node_agent/app.py" "$APP_ROOT/node_agent/app.py" || abort_after_write "installed bundled Node app.py differs from package"
python3 "$APP_ROOT/scripts/verify_webplayer_hlsjs_first_v1256.py" "$APP_ROOT" || abort_after_write "installed Web Player HLS.js-first verification failed"
python3 "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" "$APP_ROOT/node_agent/app.py" || abort_after_write "installed Node lost V1251 relay-health guard"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.56 player/version metadata."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.56 and bundled Node Agent v12.56 are installed."
log "Channel supervisor and Nginx were not restarted. Push Node v12.56 from the Main panel after Main verification."
