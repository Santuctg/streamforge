#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1274_INTEGRATED_MAIN_SOURCE_UPDATER:
# v12.74 integrates the v12.64-v12.68 in-place Main patches into the release
# app/main.py so fresh installs start. Installed v12.73 Main already runs that
# code, so only version metadata changes; installed app/main.py is verified,
# never replaced. No Node runtime, Nginx, Channel Supervisor or streaming changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.74"
log(){ echo "[StreamForge v12.74] $*"; }
die(){ echo "[StreamForge v12.74] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.73|12.74) ;; *) die "requires installed Main v12.73 or v12.74";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.73|12.74) ;; *) die "requires bundled Node v12.73 or v12.74";; esac
FILES=(VERSION node_agent/VERSION)
for f in "${FILES[@]}" app/main.py; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
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
print("STREAMFORGE_V1274_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE
python3 -B - "$ROOT/app/main.py" <<'PYMAIN'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text(encoding="utf-8")
compile(source, str(p), "exec")
missing = [marker for marker in (
    "STREAMFORGE_V1264_IP_GUARD",
    "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER",
    "STREAMFORGE_V1266_AUTO_VAULT",
    "STREAMFORGE_V1268_PW_MODAL_CSRF",
    "STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY",
    "\nimport shutil\n",
) if marker not in source]
if missing or "backup_rclone_status()" in source:
    raise SystemExit("Installed Main is missing earlier patches: " + ", ".join(missing or ["branding fix"]))
print("STREAMFORGE_V1274_INSTALLED_MAIN_VERIFIED: installed Main already carries integrated patches")
PYMAIN

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1274-$STAMP"
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
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1274_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
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
log "SUCCESS: Main v12.74 (release source now matches installed Main). Running Remote Nodes unchanged."
