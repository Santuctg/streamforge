#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1280_LIVE_CHARTS_UPDATER:
# Main-only staged patch of installed app/static/app.js and dashboard.js
# (scripts/apply_v1280_live_charts.py): the Overview charts extend their last
# point from the existing 5s metrics poll and re-read history once per sample
# interval. Nginx serves /static/ from the published cache, so the patched
# files are republished there and verified byte for byte (the v12.78 lesson).
# No application code, Node runtime or Nginx config changes; Nginx and the
# channel supervisor keep running.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.80"
log(){ echo "[StreamForge v12.80] $*"; }
die(){ echo "[StreamForge v12.80] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.79|12.80) ;; *) die "requires installed Main v12.79 or v12.80";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.79|12.80) ;; *) die "requires bundled Node v12.79 or v12.80";; esac
PATCH_FILES=(app/static/app.js app/static/dashboard.js)
for f in "${PATCH_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
for f in scripts/apply_v1280_live_charts.py scripts/apply_v1280_live_charts.json scripts/sync_main_static_cache.py; do
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
print("STREAMFORGE_V1280_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE

# Stage copies of the installed files, patch and verify them before touching
# production; the repository files are never copied over installed sources.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
for f in "${PATCH_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp -a "$ROOT/$f" "$STAGING/$f"
done
python3 -B "$SRC/scripts/apply_v1280_live_charts.py" "$STAGING" "${PATCH_FILES[@]}" || die "live-charts patch preflight failed"
for f in "${PATCH_FILES[@]}"; do
  grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_LIVE_CHARTS_V1280' "$STAGING/$f" || die "staged $f missing the v12.80 marker"
  if command -v node >/dev/null 2>&1; then
    node --check "$STAGING/$f" || die "staged $f failed the JavaScript syntax check"
  fi
done
log "PASS: preflight staged v12.80 live-charts patch"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1280-$STAMP"
FILES=("${PATCH_FILES[@]}" VERSION node_agent/VERSION)
for f in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$f")"
  cp -a "$ROOT/$f" "$BACKUP/$f"
done
if [[ -d "$STATIC_CACHE_ROOT" ]]; then
  mkdir -p "$BACKUP/main-static"
  cp -a "$STATIC_CACHE_ROOT/." "$BACKUP/main-static/"
fi
log "Backup created: $BACKUP"
ROLLBACK=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for f in "${FILES[@]}"; do cp -a "$BACKUP/$f" "$ROOT/$f" || true; done
    if [[ -d "$BACKUP/main-static" ]]; then
      mkdir -p "$STATIC_CACHE_ROOT"
      cp -a "$BACKUP/main-static/." "$STATIC_CACHE_ROOT/" 2>/dev/null || true
    fi
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
  temporary="$(mktemp "$(dirname "$2")/.v1280.XXXXXX")"
  cp "$1" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$2"
}
for f in "${PATCH_FILES[@]}"; do
  mode="$(stat -c '%a' "$ROOT/$f")"
  atomic_copy "$STAGING/$f" "$ROOT/$f" "$mode"
done
log "Refreshing Nginx direct static cache from installed Main assets."
python3 -B "$SRC/scripts/sync_main_static_cache.py" "$ROOT/app/static" "$STATIC_CACHE_ROOT"
[[ "$TEST" == 1 ]] || chown -R root:root "$STATIC_CACHE_ROOT"
python3 -B - "$ROOT/app/static" "$STATIC_CACHE_ROOT" <<'PYVERIFY'
from pathlib import Path
import hashlib, sys
installed, cache = map(Path, sys.argv[1:])
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
stale = [p.relative_to(installed).as_posix() for p in installed.rglob("*")
         if p.is_file() and (not (cache / p.relative_to(installed)).is_file()
                             or digest(p) != digest(cache / p.relative_to(installed)))]
if stale:
    raise SystemExit("static cache still differs for: " + ",".join(sorted(stale)[:12]))
print("STREAMFORGE_V1280_STATIC_CACHE_VERIFIED: Nginx static cache matches installed app/static")
PYVERIFY
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1280_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
for f in "${PATCH_FILES[@]}"; do
  cmp -s "$STAGING/$f" "$ROOT/$f" || rollback 1
  cmp -s "$STAGING/$f" "$STATIC_CACHE_ROOT/${f#app/static/}" || rollback 1
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
log "SUCCESS: Main v12.80 live Overview charts installed. Running Remote Nodes unchanged."
log "Next: reload the Overview page."
