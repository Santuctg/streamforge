#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1284_ROLE_MANAGER_UPDATER: bounded staged role patch only.
# No Nginx, environment, database schema, static assets or supervisor changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.84"
OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"
log(){ echo "[StreamForge v12.84] $*"; }
die(){ echo "[StreamForge v12.84] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == 1 || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.83|12.84) ;; *) die "requires installed Main v12.83 or v12.84";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.83|12.84) ;; *) die "requires bundled Node Agent v12.83 or v12.84";; esac
FILES=(app/main.py app/permissions.py VERSION node_agent/VERSION node_agent/app.py)
for file in "${FILES[@]}"; do
  [[ -f "$SRC/$file" && -f "$ROOT/$file" ]] || die "missing package or installed file: $file"
done
[[ -f "$SRC/scripts/apply_role_manager_fix.py" ]] || die "missing role patcher"
[[ "$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')" == "$OLD_NODE_BLOB" ]] || die "package Node Agent blob drifted"
# STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252: compile only stages.
STAGING="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING"; }
trap cleanup EXIT
STAGE="$STAGING/tree"
for file in "${FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$file")"
  cp -a "$ROOT/$file" "$STAGE/$file"
done
python3 "$SRC/scripts/apply_role_manager_fix.py" "$STAGE" || die "role patch preflight failed"
# The Node payload is unchanged; repair drift using the pinned release bytes.
cp -a "$SRC/node_agent/app.py" "$STAGE/node_agent/app.py"
printf '%s\n' "$TARGET" > "$STAGE/VERSION"
printf '%s\n' "$TARGET" > "$STAGE/node_agent/VERSION"
python3 - "$STAGE" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for relative in ('app/main.py', 'app/permissions.py', 'node_agent/app.py'):
    path = root / relative
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
PY
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/v1284-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
for file in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$file")"
  cp -a "$ROOT/$file" "$BACKUP/$file"
done
log "Backup created: $BACKUP"
ROLLBACK=1
RESTARTED=0
clear_bytecode(){
  # Delete only bytecode for files this updater can change.
  for module in main permissions; do
    if [[ -d "$ROOT/app/__pycache__" ]]; then
      find "$ROOT/app/__pycache__" -maxdepth 1 -type f -name "$module.*.pyc" -delete
    fi
  done
  if [[ -d "$ROOT/node_agent/__pycache__" ]]; then
    find "$ROOT/node_agent/__pycache__" -maxdepth 1 -type f -name 'app.*.pyc' -delete
  fi
}
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for file in "${FILES[@]}"; do
      cp -a "$BACKUP/$file" "$ROOT/$file" || true
    done
    clear_bytecode || true
    if [[ "$RESTARTED" == 1 && "$TEST" != 1 ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
  local source="$1" dest="$2"
  # Retain the installed file's ownership/mode, not the package extract's.
  cp -a "$dest" "${dest}.tmp.$$"
  cat "$source" > "${dest}.tmp.$$"
  mv -f "${dest}.tmp.$$" "$dest"
}
for file in "${FILES[@]}"; do
  if ! cmp -s "$STAGE/$file" "$ROOT/$file"; then
    atomic_copy "$STAGE/$file" "$ROOT/$file"
  fi
done
if [[ "${STREAMFORGE_V1284_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
clear_bytecode
# STREAMFORGE_V1284_RESTART_MAIN_WORKERS: never restart Nginx or supervisor.
if [[ "$TEST" != 1 ]]; then
  RESTARTED=1
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge
  systemctl is-active --quiet streamforge-public
fi
ROLLBACK=0
trap - ERR
log "SUCCESS: Main v12.84 role fixes installed; Main workers restarted."
log "Nginx, channel supervisor and Remote Node runtime unchanged; no Remote Node update required."
