#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1302_EMAIL_NOTIFICATION_CENTER_UPDATER: Stream User and Node
# Server owner email notifications. Adds contact fields, the Notification
# Center page, permissions, campaign/delivery persistence and the expiry
# reminder worker. SMTP stays disabled until an operator configures it.

# No Nginx, environment, static asset, helper or supervisor changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="13.02"
OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"
NEW_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"
log(){ echo "[StreamForge v13.02] $*"; }
die(){ echo "[StreamForge v13.02] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == 1 || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 13.01|13.02) ;; *) die "requires installed Main v13.01 or v13.02";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 13.01|13.02) ;; *) die "requires bundled Node Agent v13.01 or v13.02";; esac
FILES=(
  app/db.py
  app/main.py
  app/models.py
  app/notification_manager.py
  app/permissions.py
  app/templates/base.html
  app/templates/main_updates.html
  app/templates/node_form.html
  app/templates/notifications.html
  app/templates/user_form.html
  node_agent/app.py
  VERSION
  node_agent/VERSION
)
# STREAMFORGE_V1302_NEW_NOTIFICATION_FILES: created when upgrading from v13.01.
NEW_FILES=(app/notification_manager.py app/templates/notifications.html)
for file in "${FILES[@]}"; do
  [[ -f "$SRC/$file" ]] || die "missing package file: $file"
  if [[ ! -f "$ROOT/$file" && " ${NEW_FILES[*]} " != *" $file "* ]]; then
    die "missing installed file: $file"
  fi
done
[[ "$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')" == "$NEW_NODE_BLOB" ]] || die "package Node Agent blob drifted"
installed_node_blob="$(shasum -a 1 "$ROOT/node_agent/app.py" | awk '{print $1}')"
case "$installed_node_blob" in
  "$OLD_NODE_BLOB"|"$NEW_NODE_BLOB") ;;
  *) die "unexpected installed Node Agent blob: $installed_node_blob";;
esac
# STREAMFORGE_V1302_LOCAL_NODE_AGENT_OPTIONAL: when this server also runs a
# local Node Agent from /opt/streamforge-node, keep it on the proven payload.
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
for relative in (
    'app/db.py', 'app/main.py', 'app/models.py', 'app/notification_manager.py',
    'app/permissions.py', 'node_agent/app.py',
):
    path = root / relative
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')

checks = {
    'app/notification_manager.py': (
        'notification_worker', 'queue_expiry_reminders', 'queue_manual_campaign',
        'process_delivery_queue', 'cancel_stale_expiry_deliveries', 'EmailConfig',
    ),
    'app/models.py': (
        'notification_campaigns', 'notification_deliveries', 'contact_email',
        'email_notifications', 'dedupe_key',
    ),
    'app/db.py': (
        'ALTER TABLE nodes ADD COLUMN contact_email', 'ix_stream_users_contact_email',
        'ALTER TABLE stream_users ADD COLUMN email_notifications',
    ),
    'app/permissions.py': (
        'notifications.view', 'notifications.send', 'notifications.manage',
    ),
    'app/main.py': (
        'STREAMFORGE_EMAIL_NOTIFICATIONS_V1302', '/system/notifications',
        'notification_worker.start()', 'cancel_stale_expiry_deliveries',
    ),
    'app/templates/base.html': ('/system/notifications', 'notifications.view'),
    'app/templates/user_form.html': ('name="contact_email"', 'name="email_notifications"'),
    'app/templates/node_form.html': ('name="contact_name"', 'name="contact_email"'),
    'app/templates/notifications.html': (
        '/system/notifications/settings', '/system/notifications/campaign',
        'notification-recipient-grid', 'notification-summary-grid',
        'data-notification-campaign', 'notification-history',
    ),
    'app/templates/main_updates.html': (
        'main-updates-page', '/system/updates/apply',
        "confirm('Update the Main Server now?",
    ),
}
for relative, markers in checks.items():
    text = (root / relative).read_text(encoding='utf-8')
    for marker in markers:
        if marker not in text:
            raise SystemExit(f'{relative} missing {marker}')
page = (root / 'app' / 'templates' / 'main_updates.html').read_text(encoding='utf-8')
if "confirm('Update Main Server to v{{" in page:
    raise SystemExit('main_updates.html still interpolates remote data into confirm()')
PY
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/v1302-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
for file in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$file")"
  if [[ -f "$ROOT/$file" ]]; then
    cp -a "$ROOT/$file" "$BACKUP/$file"
  else
    printf '%s\n' "$file" >> "$BACKUP/created-files.txt"
  fi
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
  for dir in "$ROOT/app" "$ROOT/node_agent"; do
    if [[ -d "$dir/__pycache__" ]]; then
      find "$dir/__pycache__" -maxdepth 1 -type f \( -name 'main.*.pyc' -o -name 'models.*.pyc' -o -name 'db.*.pyc' -o -name 'permissions.*.pyc' -o -name 'notification_manager.*.pyc' -o -name 'app.*.pyc' \) -delete
    fi
  done
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
      if [[ -f "$BACKUP/$file" ]]; then
        cp -a "$BACKUP/$file" "$ROOT/$file" || true
      else
        rm -f "$ROOT/$file" || true
      fi
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
  if [[ ! -f "$ROOT/$file" ]]; then
    mkdir -p "$ROOT/$(dirname "$file")"
    install -m 0644 "$STAGE/$file" "$ROOT/$file"
  elif ! cmp -s "$STAGE/$file" "$ROOT/$file"; then
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
if [[ "${STREAMFORGE_V1302_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
clear_bytecode
# STREAMFORGE_V1302_RESTART_MAIN_WORKERS: never restart Nginx or supervisor.
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
log "SUCCESS: Main v13.02 Notification Center for Stream Users and Node Server owners installed; Main workers restarted."
if [[ "$LOCAL_ACTION" == update ]]; then
  log "Local Node Agent at $LOCAL_NODE_ROOT updated; local node services try-restarted."
fi
log "Nginx, privileged update helper and channel supervisor unchanged. Remote Nodes do not require an update for this Main-only release."
