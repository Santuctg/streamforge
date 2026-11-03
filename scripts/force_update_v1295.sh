#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1295_DASHBOARD_NODE_SUMMARY_UPDATER: bounded staged patch for v12.95.
# Cosmetic + operator-experience release: the expired-lock banner becomes a
# friendly payment-focused notice, and the Main node form stops blocking
# deliberate backdating. Expiry semantics are unchanged.

# No Nginx, environment, static assets or supervisor changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.95"
OLD_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"
NEW_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"
log(){ echo "[StreamForge v12.95] $*"; }
die(){ echo "[StreamForge v12.95] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == 1 || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.94|12.95) ;; *) die "requires installed Main v12.94 or v12.95";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.94|12.95) ;; *) die "requires bundled Node Agent v12.94 or v12.95";; esac
FILES=(app/main.py app/templates/dashboard.html app/templates/node_form.html app/templates/nodes.html node_agent/app.py VERSION node_agent/VERSION)
for file in "${FILES[@]}"; do
  [[ -f "$SRC/$file" && -f "$ROOT/$file" ]] || die "missing package or installed file: $file"
done
[[ "$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')" == "$NEW_NODE_BLOB" ]] || die "package Node Agent blob drifted"
installed_node_blob="$(shasum -a 1 "$ROOT/node_agent/app.py" | awk '{print $1}')"
case "$installed_node_blob" in
  "$OLD_NODE_BLOB"|"$NEW_NODE_BLOB") ;;
  *) die "unexpected installed Node Agent blob: $installed_node_blob";;
esac
# STREAMFORGE_V1295_LOCAL_NODE_AGENT_OPTIONAL: when this server also runs a
# local Node Agent from /opt/streamforge-node, update it from the same package
# bytes. Unknown agent versions are left untouched with a visible warning.
LOCAL_NODE_ROOT="${STREAMFORGE_LOCAL_NODE_ROOT:-/opt/streamforge-node}"
LOCAL_FILES=()
LOCAL_ACTION="absent"
if [[ -f "$LOCAL_NODE_ROOT/app.py" ]]; then
  local_node_blob="$(shasum -a 1 "$LOCAL_NODE_ROOT/app.py" | awk '{print $1}')"
  case "$local_node_blob" in
    "$OLD_NODE_BLOB"|"$NEW_NODE_BLOB")
      LOCAL_ACTION="update"
      LOCAL_FILES=(app.py)
      [[ -f "$LOCAL_NODE_ROOT/VERSION" ]] && LOCAL_FILES+=(VERSION)
      ;;
    *)
      LOCAL_ACTION="skip"
      log "WARNING: local Node Agent at $LOCAL_NODE_ROOT has an unexpected version; skipped. Update it with scripts/install_node_agent.sh from this package."
      ;;
  esac
fi
# STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252: compile only stages.
STAGING="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING"; }
trap cleanup EXIT
STAGE="$STAGING/tree"
for file in "${FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$file")"
  # Copy from package source ($SRC), NOT from installed root ($ROOT)
  cp -a "$SRC/$file" "$STAGE/$file"
done
printf '%s\n' "$TARGET" > "$STAGE/VERSION"
printf '%s\n' "$TARGET" > "$STAGE/node_agent/VERSION"
python3 - "$STAGE" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
node = root / 'node_agent' / 'app.py'
text = node.read_text(encoding='utf-8')
compile(text, str(node), 'exec')
for marker in ('STREAMFORGE_NODE_WEBPLAYER_SUBSCRIPTION_EXPIRED_V1294',
               'STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1293',
               'STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291',
               'STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290', 'BANGLADESH_TZ',
               '_node_expiry_enforcement_loop', 'node_expiry_bd_display'):
    if marker not in text:
        raise SystemExit(f'node_agent/app.py missing {marker}')
main = root / 'app' / 'main.py'
main_text = main.read_text(encoding='utf-8')
compile(main_text, str(main), 'exec')
form = root / 'app' / 'templates' / 'node_form.html'
form_text = form.read_text(encoding='utf-8')
if 'STREAMFORGE_NODE_EXPIRY_BACKDATE_ALLOWED_V1292' not in form_text:
    raise SystemExit('node_form.html missing STREAMFORGE_NODE_EXPIRY_BACKDATE_ALLOWED_V1292')
dashboard = root / 'app' / 'templates' / 'dashboard.html'
dashboard_text = dashboard.read_text(encoding='utf-8')
if 'offline_node_count' not in dashboard_text or 'expired_node_count' not in dashboard_text:
    raise SystemExit('dashboard.html missing offline/expired Node counts')
main_text = main.read_text(encoding='utf-8')
if 'STREAMFORGE_MAIN_DASHBOARD_NODE_SUMMARY_V1295' not in main_text:
    raise SystemExit('app/main.py missing STREAMFORGE_MAIN_DASHBOARD_NODE_SUMMARY_V1295')
if 'expired_node_count=expired_node_count' not in main_text:
    raise SystemExit('app/main.py missing expired Node count')
nodes = root / 'app' / 'templates' / 'nodes.html'
nodes_text = nodes.read_text(encoding='utf-8')
if 'color:#ff4d5e!important' not in nodes_text:
    raise SystemExit('nodes.html missing expired red style')
if 'field.min = minNow' in form_text:
    raise SystemExit('node_form.html still blocks backdated expiry values')
PY
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/v1295-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
for file in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$file")"
  cp -a "$ROOT/$file" "$BACKUP/$file"
done
if [[ "$LOCAL_ACTION" == update ]]; then
  for file in "${LOCAL_FILES[@]}"; do
    mkdir -p "$BACKUP/local-node/$(dirname "$file")"
    cp -a "$LOCAL_NODE_ROOT/$file" "$BACKUP/local-node/$file"
  done
fi
log "Backup created: $BACKUP"
ROLLBACK=1
RESTARTED=0
LOCAL_RESTARTED=0
clear_bytecode(){
  # Delete only bytecode for files this updater can change.
  for module in main; do
    if [[ -d "$ROOT/app/__pycache__" ]]; then
      find "$ROOT/app/__pycache__" -maxdepth 1 -type f -name "$module.*.pyc" -delete
    fi
  done
  if [[ -d "$ROOT/node_agent/__pycache__" ]]; then
    find "$ROOT/node_agent/__pycache__" -maxdepth 1 -type f -name 'app.*.pyc' -delete
  fi
  if [[ "$LOCAL_ACTION" == update && -d "$LOCAL_NODE_ROOT/__pycache__" ]]; then
    find "$LOCAL_NODE_ROOT/__pycache__" -maxdepth 1 -type f -name 'app.*.pyc' -delete
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
    if [[ "$LOCAL_ACTION" == update ]]; then
      for file in "${LOCAL_FILES[@]}"; do
        cp -a "$BACKUP/local-node/$file" "$LOCAL_NODE_ROOT/$file" || true
      done
      if [[ "$LOCAL_RESTARTED" == 1 ]]; then
        systemctl try-restart streamforge-node.service >/dev/null 2>&1 || true
        systemctl try-restart streamforge-node-public.service >/dev/null 2>&1 || true
      fi
    fi
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
if [[ "$LOCAL_ACTION" == update ]]; then
  if ! cmp -s "$STAGE/node_agent/app.py" "$LOCAL_NODE_ROOT/app.py"; then
    atomic_copy "$STAGE/node_agent/app.py" "$LOCAL_NODE_ROOT/app.py"
  fi
  if [[ " ${LOCAL_FILES[*]} " == *" VERSION "* ]]; then
    printf '%s\n' "$TARGET" > "$LOCAL_NODE_ROOT/VERSION"
  fi
fi
if [[ "${STREAMFORGE_V1295_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
clear_bytecode
# STREAMFORGE_V1295_RESTART_MAIN_WORKERS: never restart Nginx or supervisor.
if [[ "$TEST" != 1 ]]; then
  RESTARTED=1
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge
  systemctl is-active --quiet streamforge-public
fi
if [[ "$LOCAL_ACTION" == update && "$TEST" != 1 ]]; then
  LOCAL_RESTARTED=1
  systemctl try-restart streamforge-node.service || true
  systemctl try-restart streamforge-node-public.service || true
fi
ROLLBACK=0
trap - ERR
log "SUCCESS: Main v12.95 dashboard Node totals installed; Main workers restarted."
if [[ "$LOCAL_ACTION" == update ]]; then
  log "Local Node Agent at $LOCAL_NODE_ROOT updated; local node services try-restarted."
fi
log "Nginx and channel supervisor unchanged. Remote Nodes: run the agent update from Node Updates — the Main dashboard summary has been updated."
