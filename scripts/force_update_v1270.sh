#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1270_REVEAL_TIMEOUT_AND_NODE_COPY
# Scope: Main reveal modal auth-step + exact countdown and bundled Node form copy,
# Main/Node VERSION metadata only. No password/auth logic, Nginx or streams.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.70"
OLD_NODE_BLOB="a65a53a4dc8f071cb4f906d18b242ae11b864a97"

log(){ echo "[StreamForge v12.70] $*"; }
die(){ echo "[StreamForge v12.70] ERROR: $*" >&2; exit 1; }
[[ "$TEST_MODE" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.69|12.70) ;; *) die "requires installed Main v12.69 or v12.70";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.69|12.70) ;; *) die "requires bundled Node v12.69 or v12.70";; esac
FILES=(VERSION node_agent/VERSION node_agent/app.py app/templates/admin_users.html)
for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" && -f "$ROOT/$f" ]] || die "missing $f"
done
python3 - "$SRC/node_agent" "$ROOT/node_agent" "$OLD_NODE_BLOB" <<'PYNODE'
from pathlib import Path
import sys,hashlib
src,inst=map(Path,sys.argv[1:3])
old=sys.argv[3]
def git_sha(path):
    data=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
if git_sha(inst/"app.py") not in (old,git_sha(src/"app.py")):
    raise SystemExit("Node app.py differs from known v12.69 or v12.70 baseline")
def listing(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name not in {"VERSION","app.py"}
            and "__pycache__" not in p.parts and p.suffix not in {".pyc",".pyo"}}
oldfiles,newfiles=listing(inst),listing(src)
if oldfiles!=newfiles:
    bad=sorted(k for k in set(oldfiles)|set(newfiles) if oldfiles.get(k)!=newfiles.get(k))
    raise SystemExit("Unchanged Node runtime payload differs: "+",".join(bad[:12]))
print("STREAMFORGE_V1269_NODE_FORM_ONLY_PARITY: unrelated Node files unchanged")
PYNODE
python3 - "$SRC/node_agent/app.py" "$SRC/app/templates/admin_users.html" <<'PYCHECK'
from pathlib import Path
import sys
node=Path(sys.argv[1]).read_text(encoding="utf-8")
markup=Path(sys.argv[2]).read_text(encoding="utf-8")
compile(node,sys.argv[1],"exec")
assert "STREAMFORGE_V1270_NODE_PASSWORD_FORM_CLEANUP" in node
assert "New passwords are encrypted on Main and can be viewed by authorized administrators." not in node
assert "As a managed account, authorized administrators can recover changed passwords." in node
assert 'payload["managed_password_notice"] = True' in node
assert "STREAMFORGE_V1269_VAULT_REVEAL_TEXT_DARK" in markup
assert "STREAMFORGE_V1270_REVEAL_AUTH_STEP" in markup
assert '.sf-vault-dialog input[data-vault-secret-value]' in markup
assert "authStep.hidden = true;" in markup
assert "const expiresAt = performance.now() + 10000;" in markup
print("PASS: Node disclosure and 10-second modal preflight")
PYCHECK
PYTHONPATH="$SRC" python3 "$SRC/scripts/test_v1270_reveal_ui.py"
STAGING_BACKUP="$BACKUP_ROOT/v1270-$(date +%Y%m%d-%H%M%S)"
for f in "${FILES[@]}"; do
    mkdir -p "$STAGING_BACKUP/$(dirname "$f")"
    cp -a "$ROOT/$f" "$STAGING_BACKUP/$f"
done
log "Backup created: $STAGING_BACKUP"
ROLLBACK_NEEDED=1
rollback(){
    local rc="${1:-1}"
    trap - ERR
    if [[ "$ROLLBACK_NEEDED" == 1 ]]; then
        log "Rolling back from $STAGING_BACKUP"
        for f in "${FILES[@]}"; do cp -a "$STAGING_BACKUP/$f" "$ROOT/$f" || true; done
        if [[ "$TEST_MODE" != 1 ]]; then
            systemctl restart streamforge >/dev/null 2>&1 || true
            systemctl restart streamforge-public >/dev/null 2>&1 || true
        fi
    fi
    exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
    local temporary
    temporary="$(mktemp "$(dirname "$2")/.v1270.XXXXXX")"
    cp "$1" "$temporary"
    chmod 0644 "$temporary"
    mv -f "$temporary" "$2"
}
atomic_copy "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py"
atomic_copy "$SRC/app/templates/admin_users.html" "$ROOT/app/templates/admin_users.html"
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1269_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
    log "Controlled failure after write"
    rollback 1
fi
cmp -s "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py" || rollback 1
cmp -s "$SRC/app/templates/admin_users.html" "$ROOT/app/templates/admin_users.html" || rollback 1
if [[ "$TEST_MODE" != 1 ]]; then
    log "Restarting Main HTTP workers only"
    systemctl restart streamforge
    systemctl restart streamforge-public
    systemctl is-active --quiet streamforge || rollback 1
    systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS: Main v12.70 updated. Deploy v12.70 to each running Remote Node separately."
