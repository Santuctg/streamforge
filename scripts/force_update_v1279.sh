#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1279_STATIC_CACHE_UPDATER:
# v12.78 patched app/static/app.js and app/static/dashboard.js in the installed
# tree but did not refresh the Nginx direct static cache
# (/var/cache/streamforge/main-static), so browsers kept receiving the v12.77
# scripts and the Overview legend values stayed empty. This Main-only updater
# republishes the installed static tree into that cache, verifies it byte for
# byte and bumps the version so browsers request the new asset URLs. No
# application source, Node runtime or Nginx config changes; Nginx and the
# channel supervisor keep running.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.79"
log(){ echo "[StreamForge v12.79] $*"; }
die(){ echo "[StreamForge v12.79] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.78|12.79) ;; *) die "requires installed Main v12.78 or v12.79";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.78|12.79) ;; *) die "requires bundled Node v12.78 or v12.79";; esac
[[ -f "$SRC/scripts/sync_main_static_cache.py" ]] || die "missing package file scripts/sync_main_static_cache.py"
for f in app/static/app.js app/static/dashboard.js; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
  grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278' "$ROOT/$f" || die "installed $f lacks the v12.78 change; install v12.78 first"
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
print("STREAMFORGE_V1279_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1279-$STAMP"
mkdir -p "$BACKUP"
for f in VERSION node_agent/VERSION; do
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
    for f in VERSION node_agent/VERSION; do cp -a "$BACKUP/$f" "$ROOT/$f" || true; done
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
for name in ("app.js", "dashboard.js"):
    if "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278" not in (cache / name).read_text(encoding="utf-8"):
        raise SystemExit(f"static cache {name} lacks the v12.78 Overview change")
print("STREAMFORGE_V1279_STATIC_CACHE_VERIFIED: Nginx static cache matches installed app/static")
PYVERIFY
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1279_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
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
log "SUCCESS: Main v12.79 static cache refreshed; the Overview now loads the v12.78 scripts."
log "Next: reload the Overview page."
