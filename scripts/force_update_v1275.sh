#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1275_SECURE_SESSION_COOKIE_UPDATER:
# Main-only staged patch of the installed app/main.py: mark the Panel session
# cookie Secure on HTTPS requests (scripts/apply_main_hotfixes.py fix 4).
# No Node runtime, Nginx, Channel Supervisor or streaming changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.75"
log(){ echo "[StreamForge v12.75] $*"; }
die(){ echo "[StreamForge v12.75] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.74|12.75) ;; *) die "requires installed Main v12.74 or v12.75";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.74|12.75) ;; *) die "requires bundled Node v12.74 or v12.75";; esac
FILES=(VERSION node_agent/VERSION app/main.py)
for f in "${FILES[@]}"; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
[[ -f "$SRC/scripts/apply_main_hotfixes.py" ]] || die "missing scripts/apply_main_hotfixes.py"
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
print("STREAMFORGE_V1275_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE

# Patch a staged copy of the installed main.py; the repo main.py is never
# copied over it because installed sources carry earlier in-place patches.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -a "$ROOT/app/main.py" "$STAGING/main.py"
python3 -B "$SRC/scripts/apply_main_hotfixes.py" "$STAGING/main.py" || die "Main hotfix patcher preflight failed"
python3 -B - "$STAGING/main.py" <<'PYMAIN'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text(encoding="utf-8")
compile(source, str(p), "exec")
assert "\nimport shutil\n" in source
assert "backup_rclone_status()" not in source
assert "STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY" in source
assert "STREAMFORGE_MAIN_SESSION_COOKIE_SECURE_ON_HTTPS" in source
assert "app.add_middleware(SecureSessionCookieMiddleware)" in source
print("PASS: preflight staged Main hotfixes and Python syntax")
PYMAIN

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1275-$STAMP"
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
  temporary="$(mktemp "$(dirname "$2")/.v1275.XXXXXX")"
  cp "$1" "$temporary"
  chmod 0644 "$temporary"
  mv -f "$temporary" "$2"
}
atomic_copy "$STAGING/main.py" "$ROOT/app/main.py"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1275_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
cmp -s "$STAGING/main.py" "$ROOT/app/main.py" || rollback 1
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
log "SUCCESS: Main v12.75 Panel session cookie is Secure on HTTPS. Running Remote Nodes unchanged."
