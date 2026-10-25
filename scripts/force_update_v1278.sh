#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1278_DASHBOARD_UPDATER:
# Main-only staged patch of installed sources (scripts/apply_v1278_dashboard.py):
# Overview cards complete on first paint, current values beside the chart
# legends, a loading note on the charts, hls.js helper in the SSH Node payload
# and a clearer SSH update confirmation. The channel supervisor, Nginx and the
# Remote Nodes keep running; the bundled Node runtime is unchanged.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.78"
log(){ echo "[StreamForge v12.78] $*"; }
die(){ echo "[StreamForge v12.78] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.77|12.78) ;; *) die "requires installed Main v12.77 or v12.78";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.77|12.78) ;; *) die "requires bundled Node v12.77 or v12.78";; esac
PATCH_FILES=(app/ssh_installer.py app/templates/dashboard.html app/templates/node_updates.html app/templates/node_update.html app/static/app.js app/static/dashboard.js)
for f in "${PATCH_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
for f in scripts/apply_v1278_dashboard.py scripts/apply_v1278_dashboard.json scripts/fetch_hlsjs.sh; do
  [[ -f "$SRC/$f" ]] || die "missing package file $f"
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
print("STREAMFORGE_V1278_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE

# Stage copies of the installed files, patch and verify them before touching
# production; the repository files are never copied over installed sources.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
for f in "${PATCH_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp -a "$ROOT/$f" "$STAGING/$f"
done
python3 -B "$SRC/scripts/apply_v1278_dashboard.py" "$STAGING" "${PATCH_FILES[@]}" || die "dashboard patch preflight failed"
python3 -B - "$STAGING" <<'PYSTAGE'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for path in root.rglob("*.py"):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
checks = {
    "app/ssh_installer.py": ["STREAMFORGE_NODE_SSH_HLSJS_PAYLOAD_V1278", 'arcname="streamforge_mvp/scripts/fetch_hlsjs.sh"'],
    "app/templates/dashboard.html": ["STREAMFORGE_MAIN_DASHBOARD_INSTANT_METRICS_V1278", "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278"],
    "app/templates/node_updates.html": ["Update this Node over SSH?"],
    "app/templates/node_update.html": ["Update this Node over SSH?"],
    "app/static/app.js": ["STREAMFORGE_MAIN_DASHBOARD_INSTANT_METRICS_V1278", "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278"],
    "app/static/dashboard.js": ["STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278", "chartMessage('Loading…')"],
}
for relative, markers in checks.items():
    text = (root / relative).read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{relative} missing {missing}")
print("PASS: preflight staged v12.78 dashboard patch, markers and Python syntax")
PYSTAGE
if command -v node >/dev/null 2>&1; then
  for f in app/static/app.js app/static/dashboard.js; do
    node --check "$STAGING/$f" || die "staged $f failed the JavaScript syntax check"
  done
fi
# The SSH payload adds scripts/fetch_hlsjs.sh when present; install it if an
# older tree lacks it (it is a standalone helper, so the package copy is used).
NEW_FILES=()
[[ -f "$ROOT/scripts/fetch_hlsjs.sh" ]] || NEW_FILES+=(scripts/fetch_hlsjs.sh)
for f in "${NEW_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp "$SRC/$f" "$STAGING/$f"
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1278-$STAMP"
mkdir -p "$BACKUP"
: > "$BACKUP/absent-files.txt"
FILES=("${PATCH_FILES[@]}" VERSION node_agent/VERSION)
for f in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$f")"
  cp -a "$ROOT/$f" "$BACKUP/$f"
done
for f in "${NEW_FILES[@]}"; do echo "$f" >> "$BACKUP/absent-files.txt"; done
log "Backup created: $BACKUP"
ROLLBACK=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for f in "${FILES[@]}"; do cp -a "$BACKUP/$f" "$ROOT/$f" || true; done
    while IFS= read -r f; do [[ -n "$f" ]] && rm -f "$ROOT/$f"; done < "$BACKUP/absent-files.txt"
    if [[ "$TEST" != 1 ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
  local temporary mode
  mode="${3:-0644}"
  temporary="$(mktemp "$(dirname "$2")/.v1278.XXXXXX")"
  cp "$1" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$2"
}
for f in "${PATCH_FILES[@]}"; do
  mode="$(stat -c '%a' "$ROOT/$f")"
  atomic_copy "$STAGING/$f" "$ROOT/$f" "$mode"
done
for f in "${NEW_FILES[@]}"; do
  mkdir -p "$ROOT/$(dirname "$f")"
  atomic_copy "$STAGING/$f" "$ROOT/$f" 0644
done
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1278_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
for f in "${PATCH_FILES[@]}" "${NEW_FILES[@]}"; do
  cmp -s "$STAGING/$f" "$ROOT/$f" || rollback 1
done
[[ "$(< "$ROOT/VERSION")" == "$TARGET" && "$(< "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || rollback 1
if [[ "$TEST" != 1 ]]; then
  log "Restarting Main HTTP workers only (channel supervisor and streams keep running)"
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || rollback 1
  systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK=0
trap - ERR
log "SUCCESS: Main v12.78 Overview and SSH payload fixes installed. Running Remote Nodes unchanged."
log "Next: reload the Overview page (the new version busts the browser cache automatically)."
