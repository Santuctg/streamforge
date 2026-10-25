#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1281_NODE_LIVE_CHARTS_UPDATER:
# Bundled Node Agent only: node_agent/app.py with live Node Panel charts (values
# beside the legends, a loading note, the newest point from the 3s status
# refresh and a quiet history re-read per sample interval), installed only over
# the pinned v12.80 blob with every other Node runtime file byte-identical.
# No Main source, static asset or Nginx change; the channel supervisor and
# Nginx keep running. Remote Nodes pick it up from Node Updates.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.81"
OLD_NODE_BLOB="83521def749210e025af9a979a77405983f0a43e"
log(){ echo "[StreamForge v12.81] $*"; }
die(){ echo "[StreamForge v12.81] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.80|12.81) ;; *) die "requires installed Main v12.80 or v12.81";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.80|12.81) ;; *) die "requires bundled Node v12.80 or v12.81";; esac
for f in VERSION node_agent/VERSION node_agent/app.py; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
[[ -f "$SRC/node_agent/app.py" ]] || die "missing package file node_agent/app.py"

# Bundled Node payload: app.py must be the pinned v12.80 baseline (or already
# this release); every other Node runtime file must be byte-identical.
python3 - "$SRC/node_agent" "$ROOT/node_agent" "$OLD_NODE_BLOB" <<'PYNODE'
from pathlib import Path
import hashlib, sys
src, inst = map(Path, sys.argv[1:3])
previous = sys.argv[3]
def blob_sha(path):
    content = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
if blob_sha(inst / "app.py") not in (previous, blob_sha(src / "app.py")):
    raise SystemExit("Unexpected bundled Node app.py; refusing overwrite")
def unaffected(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name not in ("VERSION", "app.py")
            and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")}
a, b = unaffected(src), unaffected(inst)
if a != b:
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    raise SystemExit("Unrelated Node runtime changed: " + ",".join(changed[:12]))
print("STREAMFORGE_V1281_NODE_APP_ONLY_PARITY: pinned Node app.py baseline and unchanged Node runtime verified")
PYNODE

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/node_agent"
cp "$SRC/node_agent/app.py" "$STAGING/node_agent/app.py"
python3 -B - "$STAGING/node_agent/app.py" <<'PYSTAGE'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
compile(text, str(path), "exec")
for marker in ("STREAMFORGE_NODE_DASHBOARD_LIVE_CHARTS_V1281", "STREAMFORGE_NODE_SECURITY_HEADERS_V1277",
               "STREAMFORGE_NODE_REAL_CPU_HISTORY_V1148"):
    if marker not in text:
        raise SystemExit(f"node_agent/app.py missing {marker}")
print("PASS: preflight staged v12.81 Node app.py, markers and Python syntax")
PYSTAGE

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1281-$STAMP"
FILES=(node_agent/app.py VERSION node_agent/VERSION)
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
  local temporary mode
  mode="${3:-0644}"
  temporary="$(mktemp "$(dirname "$2")/.v1281.XXXXXX")"
  cp "$1" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$2"
}
mode="$(stat -c '%a' "$ROOT/node_agent/app.py")"
atomic_copy "$STAGING/node_agent/app.py" "$ROOT/node_agent/app.py" "$mode"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1281_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
cmp -s "$STAGING/node_agent/app.py" "$ROOT/node_agent/app.py" || rollback 1
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
log "SUCCESS: bundled Node Agent v12.81 (live Node Panel charts) installed."
log "Next: update Remote Nodes from Node Updates, then reload a Node Panel dashboard."
