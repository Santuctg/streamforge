#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1267_NODE_SELF_PASSWORD_SESSION_ONLY:
# Update bundled Node Agent only, preserving Main auth/RBAC and runtime.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.67"
PREVIOUS_NODE_BLOB="a071e851db49804c413a6d4db9de792a20adb69a"

fail(){ echo "[StreamForge v12.67] ERROR: $*" >&2; exit 1; }
info(){ echo "[StreamForge v12.67] $*"; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || fail "root required"
[[ "$(tr -d '[:space:]' < "$SRC/VERSION")" == "$TARGET" ]] || fail "package Main version mismatch"
[[ "$(tr -d '[:space:]' < "$SRC/node_agent/VERSION")" == "$TARGET" ]] || fail "package Node version mismatch"
for f in VERSION node_agent/VERSION node_agent/app.py; do
  [[ -f "$ROOT/$f" ]] || fail "missing installed $f"
done
case "$(tr -d '[:space:]' < "$ROOT/VERSION")" in
  12.66|12.67) ;; *) fail "upgrade requires installed Main v12.66 or v12.67" ;;
esac
case "$(tr -d '[:space:]' < "$ROOT/node_agent/VERSION")" in
  12.66|12.67) ;; *) fail "upgrade requires installed bundled Node v12.66 or v12.67" ;;
esac

[[ -f "$SRC/scripts/test_v1267_node_self_password.py" ]] || fail "missing security regression test"
python3 "$SRC/scripts/test_v1267_node_self_password.py"
python3 - "$SRC/node_agent" "$ROOT/node_agent" "$PREVIOUS_NODE_BLOB" <<'PYNODE'
from pathlib import Path
import hashlib
import sys

source, installed=map(Path,sys.argv[1:3])
previous_blob=sys.argv[3]
def blob_sha(file):
    contents=file.read_bytes()
    return hashlib.sha1(b"blob "+str(len(contents)).encode()+b"\0"+contents).hexdigest()
actual=blob_sha(installed/"app.py")
if actual not in (previous_blob,blob_sha(source/"app.py")):
    raise SystemExit("Installed Node app.py differs from v12.66/v12.67 baseline; refusing overwrite")
def runtime(root):
    return {f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in root.rglob("*") if f.is_file() and f.name not in ("VERSION","app.py")
            and "__pycache__" not in f.parts and f.suffix not in (".pyc",".pyo")}
old,new=runtime(installed),runtime(source)
if old!=new:
    changed=sorted(k for k in set(old)|set(new) if old.get(k)!=new.get(k))
    raise SystemExit("Unrelated Node runtime changed: "+",".join(changed[:12]))
print("STREAMFORGE_V1267_NODE_APP_ONLY_PARITY: verified unrelated Node files")
PYNODE

python3 - "$SRC/node_agent/app.py" <<'PYCHECK'
from pathlib import Path
import sys
code=Path(sys.argv[1]).read_text()
compile(code,sys.argv[1],"exec")
assert "STREAMFORGE_V1267_SELF_PASSWORD_PERMISSION_FIX" in code
assert "if permission and not _has_panel_permission(user, permission):" in code
assert code.count('require_node_panel_user(request, "")') == 2
print("PASS: Node Agent syntax and personal-session-only permission contract")
PYCHECK

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1267-$STAMP"
mkdir -p "$BACKUP_DIR/node_agent"
for f in VERSION node_agent/VERSION node_agent/app.py; do
  cp -a "$ROOT/$f" "$BACKUP_DIR/$f"
done
info "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" == "1" ]]; then
    info "Rolling back from $BACKUP_DIR"
    for f in VERSION node_agent/VERSION node_agent/app.py; do
      cp -a "$BACKUP_DIR/$f" "$ROOT/$f" || true
    done
    if [[ "$TEST" != "1" ]]; then
      systemctl restart streamforge >/dev/null 2>&1 || true
      systemctl restart streamforge-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR

tmp="$(mktemp "$ROOT/node_agent/.v1267.XXXXXX")"
cp "$SRC/node_agent/app.py" "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$ROOT/node_agent/app.py"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1267_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  info "Controlled failure after write"
  rollback 1
fi
cmp -s "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py" || rollback 1
[[ "$(tr -d '[:space:]' < "$ROOT/VERSION")" == "$TARGET" ]] || rollback 1
[[ "$(tr -d '[:space:]' < "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || rollback 1
if [[ "$TEST" != "1" ]]; then
  info "Restarting Main HTTP workers only; Remote Nodes remain unchanged."
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || rollback 1
  systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK_NEEDED=0
trap - ERR
info "SUCCESS: Main/bundled Node v12.67. Upgrade Remote Nodes via Node Updates."
