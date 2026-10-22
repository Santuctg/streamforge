#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1272_NODE_LOGIN_DARK_UI_UPDATER:
# Bundled Node Agent login HTML/CSS only; Main auth and running Remote Nodes untouched.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.72"
OLD_NODE_BLOB="b023c2fd96c46bbeec1c16aa42c786bcf7a5544e"
log(){ echo "[StreamForge v12.72] $*"; }
fail(){ echo "[StreamForge v12.72] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || fail "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || fail "package versions must be 12.72"
for file in VERSION node_agent/VERSION node_agent/app.py; do
 [[ -f "$SRC/$file" && -f "$ROOT/$file" ]] || fail "required file missing: $file"
done
case "$(< "$ROOT/VERSION")" in 12.70|12.71|12.72) ;; *) fail "requires installed Main 12.70/12.71/12.72" ;; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.70|12.71|12.72) ;; *) fail "requires bundled Node 12.70/12.71/12.72" ;; esac

# Node payload baseline is SHA-pinned: decline overwriting locally changed Node code.
python3 - "$SRC/node_agent" "$ROOT/node_agent" "$OLD_NODE_BLOB" <<'PY'
from pathlib import Path
import hashlib,sys
source,installed=map(Path,sys.argv[1:3])
previous=sys.argv[3]
def blob_sha(p):
    content=p.read_bytes()
    return hashlib.sha1(b"blob "+str(len(content)).encode()+b"\0"+content).hexdigest()
current=blob_sha(installed/"app.py")
if current not in (previous,blob_sha(source/"app.py")):
    raise SystemExit("Unexpected bundled Node app.py; refusing overwrite")
def unaffected(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name not in ("VERSION","app.py")
            and "__pycache__" not in p.parts and p.suffix not in (".pyc",".pyo")}
a,b=unaffected(source),unaffected(installed)
if a!=b:
    changed=sorted(k for k in set(a)|set(b) if a.get(k)!=b.get(k))
    raise SystemExit("Unrelated Node runtime changed: "+",".join(changed[:12]))
print("STREAMFORGE_V1272_NODE_LOGIN_ONLY_PARITY: unchanged Node runtime verified")
PY
python3 - "$SRC/node_agent/app.py" <<'PY'
from pathlib import Path
import sys
source=Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source,sys.argv[1],"exec")
assert "STREAMFORGE_V1272_NODE_LOGIN_DARK_RESPONSIVE" in source
assert "STREAMFORGE_NODE_FIXED_LOGIN_ADDRESS_BAR_V3059" in source
print("PASS: v12.72 Node login Python syntax and canonical address-bar guard")
PY
python3 "$SRC/scripts/test_v1272_node_login.py"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1272-$STAMP"
mkdir -p "$BACKUP/node_agent"
for file in VERSION node_agent/VERSION node_agent/app.py; do
 cp -a "$ROOT/$file" "$BACKUP/$file"
done
log "Backup created: $BACKUP"
NEEDS_ROLLBACK=1
rollback(){
 local code="${1:-1}"
 trap - ERR
 if [[ "$NEEDS_ROLLBACK" == 1 ]]; then
  log "Rolling back from $BACKUP"
  for file in VERSION node_agent/VERSION node_agent/app.py; do cp -a "$BACKUP/$file" "$ROOT/$file" || true; done
  if [[ "$TEST" != 1 ]]; then
   systemctl restart streamforge >/dev/null 2>&1 || true
   systemctl restart streamforge-public >/dev/null 2>&1 || true
  fi
 fi
 exit "$code"
}
trap 'rollback $?' ERR
tmp="$(mktemp "$ROOT/node_agent/.v1272.XXXXXX")"
cp "$SRC/node_agent/app.py" "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$ROOT/node_agent/app.py"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1272_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
 log "Controlled failure after write"
 rollback 1
fi
cmp -s "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py" || rollback 1
[[ "$(< "$ROOT/VERSION")" == "$TARGET" && "$(< "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || rollback 1
if [[ "$TEST" != 1 ]]; then
 log "Restarting Main HTTP services only; Remote Nodes will update separately."
 systemctl restart streamforge
 systemctl restart streamforge-public
 systemctl is-active --quiet streamforge || rollback 1
 systemctl is-active --quiet streamforge-public || rollback 1
fi
NEEDS_ROLLBACK=0
trap - ERR
log "SUCCESS: Main/bundled Node v12.72. Use Node Updates for remote login redesign."
