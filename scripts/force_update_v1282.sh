#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1282_HARDENING_UPDATER:
# Main: staged patch of installed sources (scripts/apply_v1282_hardening.py)
# for the source-scheme guard, the sessions.kill permission, Node API token
# rotation, the backup env opt-out and the secret-key startup warning, plus
# both VERSION files. Bundled Node Agent: node_agent/app.py is byte-identical
# to v12.81 (verified against the pinned blob) and is restored only when an
# installed tree carries something else. The channel supervisor and Nginx keep
# running; Remote Nodes update separately.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.82"
OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"
log(){ echo "[StreamForge v12.82] $*"; }
die(){ echo "[StreamForge v12.82] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.81|12.82) ;; *) die "requires installed Main v12.81 or v12.82";; esac
PATCH_FILES=(
  app/backup_manager.py
  app/config.py
  app/ffmpeg.py
  app/input_options.py
  app/main.py
  app/permissions.py
  app/ssh_installer.py
  app/stream_info.py
  app/templates/categories.html
  app/templates/node_update.html
  app/templates/nodes.html
  app/templates/roles.html
  app/templates/viewer_sessions.html
)
for file in "${PATCH_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$SRC/$file" ]] || die "package is missing $file"
  [[ -f "$ROOT/$file" ]] || die "installed Main is missing $file; repair it first"
done
[[ "$(< "$ROOT/node_agent/VERSION")" == "12.81" || "$(< "$ROOT/node_agent/VERSION")" == "12.82" ]] || die "requires bundled Node Agent v12.81 or v12.82"
# STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252: compile only inside
# the staged copy so no __pycache__ can be written into the release sources.
STAGING="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING"; }
trap cleanup EXIT
STAGE="$STAGING/tree"
mkdir -p "$STAGE"
cp -a "$ROOT/." "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
for file in "${PATCH_FILES[@]}"; do cp -a "$ROOT/$file" "$STAGE/$file"; done
python3 "$SRC/scripts/apply_v1282_hardening.py" "$STAGE" "${PATCH_FILES[@]}" || die "hardening patch preflight failed"
for file in "${PATCH_FILES[@]}"; do
  grep -Fq 'STREAMFORGE_V1282_' "$STAGE/$file" || die "staged $file is missing the v12.82 marker"
done
python3 - "$STAGE" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in root.rglob('*.py'):
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
PY
mkdir -p "$BACKUP_ROOT"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1282-$STAMP"
FILES=(node_agent/app.py VERSION node_agent/VERSION "${PATCH_FILES[@]}")
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  [[ -f "$ROOT/$file" ]] || continue
  mkdir -p "$BACKUP/$(dirname "$file")"
  cp -a "$ROOT/$file" "$BACKUP/$file"
done
log "Backup created: $BACKUP"
ROLLBACK=1
RESTARTED=0
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == "1" ]]; then
    log "Rolling back from $BACKUP"
    for file in "${FILES[@]}"; do
      [[ -f "$BACKUP/$file" ]] || continue
      mkdir -p "$ROOT/$(dirname "$file")"
      cp -a "$BACKUP/$file" "$ROOT/$file" || true
    done
    find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    # Only reload the workers when they were already restarted once; a
    # pre-write refusal must not disturb the running Panel.
    if [[ "$RESTARTED" == "1" && "$TEST" != 1 ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){ local src="$1" dest="$2"; cp -a "$src" "${dest}.tmp.$$"; mv -f "${dest}.tmp.$$" "$dest"; }
# The Node Agent payload is unchanged from v12.81; restore it only when the
# installed tree carries something else (a repaired or drifted file).
SOURCE_NODE_BLOB="$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')"
INSTALLED_NODE_BLOB="$(shasum -a 1 "$ROOT/node_agent/app.py" | awk '{print $1}')"
[[ "$SOURCE_NODE_BLOB" == "$OLD_NODE_BLOB" ]] || die "package Node Agent blob drifted from the pinned v12.81 blob"
if [[ "$INSTALLED_NODE_BLOB" != "$SOURCE_NODE_BLOB" ]]; then
  log "Restoring the Node Agent payload to the pinned v12.81 bytes"
  atomic_copy "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py"
fi
python3 "$SRC/scripts/apply_v1282_hardening.py" "$ROOT" "${PATCH_FILES[@]}"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
[[ "$(< "$ROOT/VERSION")" == "$TARGET" && "$(< "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || die "version write verification failed"
# Controlled failure for the release test: every contract write already
# happened, so the rollback path must undo all of it.
if [[ "${STREAMFORGE_V1282_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  log "Controlled failure after write"
  rollback 1
fi
find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
# STREAMFORGE_V1282_UPDATER_RESTARTS_MAIN_WORKERS_V1: patching the sources on
# disk is not enough — the running Gunicorn workers keep serving the previous
# release (and the previous security posture) until they reload, which is how
# the Panel kept showing v12.81 after a v12.82 update. Restart only the Main
# HTTP workers: the channel supervisor, Nginx and live streams keep running.
if [[ "$TEST" != 1 ]]; then
  RESTARTED=1
  log "Restarting Main HTTP workers only (channel supervisor and streams keep running)"
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || rollback 1
  systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK=0
trap - ERR
log "Main sources updated in place and Main HTTP workers restarted; the channel supervisor and Nginx keep running."
log "Update Remote Nodes from the Panel: Node Updates."
