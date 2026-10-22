#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1271_LOGIN_AUTOFILL_BLOCKED_IP_CARD:
# Main-only login autofill CSS and independent Blocked IPs UI card.
# No Node runtime, API, vault, Nginx or streaming changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.71"
log(){ echo "[StreamForge v12.71] $*"; }
die(){ echo "[StreamForge v12.71] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.70|12.71) ;; *) die "requires installed Main v12.70 or v12.71";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.70|12.71) ;; *) die "requires bundled Node v12.70 or v12.71";; esac
FILES=(VERSION node_agent/VERSION app/templates/login.html app/templates/admin_users.html)
for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" && -f "$ROOT/$f" ]] || die "missing $f"
done
python3 - "$SRC/node_agent" "$ROOT/node_agent" <<'PYNODE'
from pathlib import Path
import sys, hashlib
src, inst = map(Path, sys.argv[1:])
def listing(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name != "VERSION"
            and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
old, new = listing(inst), listing(src)
if old != new:
    changed = sorted(k for k in set(old)|set(new) if old.get(k) != new.get(k))
    raise SystemExit("Node runtime payload differs for: "+",".join(changed[:12]))
print("STREAMFORGE_V1271_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE
PYTHONPATH="$SRC" python3 "$SRC/scripts/test_v1271_login_blocked_ui.py"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1271-$STAMP"
for f in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$f")"
  cp -a "$ROOT/$f" "$BACKUP/$f"
done
log "Backup created: $BACKUP"
ROLLBACK=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for f in "${FILES[@]}"; do cp -a "$BACKUP/$f" "$ROOT/$f" || true; done
    if [[ "$TEST" != 1 ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
  local temporary
  temporary="$(mktemp "$(dirname "$2")/.v1271.XXXXXX")"
  cp "$1" "$temporary"
  chmod 0644 "$temporary"
  mv -f "$temporary" "$2"
}
atomic_copy "$SRC/app/templates/login.html" "$ROOT/app/templates/login.html"
atomic_copy "$SRC/app/templates/admin_users.html" "$ROOT/app/templates/admin_users.html"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1271_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
for f in app/templates/login.html app/templates/admin_users.html; do
  cmp -s "$SRC/$f" "$ROOT/$f" || rollback 1
done
[[ "$(< "$ROOT/VERSION")" == "$TARGET" && "$(< "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || rollback 1
if [[ "$TEST" != 1 ]]; then
  log "Restarting Main HTTP workers only"
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || rollback 1
  systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK=0
trap - ERR
log "SUCCESS: Main v12.71 login autofill + separate Blocked IPs card. Running Remote Nodes unchanged."
