#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1277_PASSWORD_POLICY_UPDATER:
# Main: staged patch of installed sources (scripts/apply_v1277_security.py) for
# the 12-character Panel password policy and the forced change after a weak or
# default login. Bundled Node Agent: node_agent/app.py with Node Panel security
# headers and the 12-character Node password form, after a pinned-baseline check.
# The channel supervisor and Nginx keep running; Remote Nodes update separately.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.77"
OLD_NODE_BLOB="0f466c3fc32240997e5acec508a5cf5aa904d8ec"
log(){ echo "[StreamForge v12.77] $*"; }
die(){ echo "[StreamForge v12.77] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.76|12.77) ;; *) die "requires installed Main v12.76 or v12.77";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.76|12.77) ;; *) die "requires bundled Node v12.76 or v12.77";; esac
PATCH_FILES=(app/main.py app/auth.py app/panel_password_vault.py app/templates/account_password.html app/templates/admin_users.html app/templates/admin_user_form.html scripts/reset_admin.py)
for f in "${PATCH_FILES[@]}" VERSION node_agent/VERSION node_agent/app.py; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
for f in scripts/apply_v1277_security.py scripts/apply_v1277_security.json node_agent/app.py; do
  [[ -f "$SRC/$f" ]] || die "missing package file $f"
done

# Bundled Node payload: app.py must be the pinned v12.76 baseline (or already
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
print("STREAMFORGE_V1277_NODE_APP_ONLY_PARITY: pinned Node app.py baseline and unchanged Node runtime verified")
PYNODE

# Stage copies of the installed Main files and patch them; the repository
# Main files are never copied over installed sources.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
for f in "${PATCH_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp -a "$ROOT/$f" "$STAGING/$f"
done
mkdir -p "$STAGING/node_agent"
cp "$SRC/node_agent/app.py" "$STAGING/node_agent/app.py"
python3 -B "$SRC/scripts/apply_v1277_security.py" "$STAGING" "${PATCH_FILES[@]}" || die "password-policy patch preflight failed"
python3 -B - "$STAGING" <<'PYSTAGE'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for path in root.rglob("*.py"):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
checks = {
    "app/main.py": ["STREAMFORGE_PANEL_PASSWORD_POLICY_V1277", "app.add_middleware(PasswordChangeRequiredMiddleware)"],
    "app/auth.py": ["PANEL_PASSWORD_MIN_LENGTH = 12"],
    "app/panel_password_vault.py": ["panel_password_policy_error(password, _streamforge_settings.admin_password)"],
    "app/templates/account_password.html": ["STREAMFORGE_PANEL_PASSWORD_POLICY_V1277"],
    "scripts/reset_admin.py": ["len(password) < 12"],
    "node_agent/app.py": ["STREAMFORGE_NODE_SECURITY_HEADERS_V1277", "STREAMFORGE_PANEL_PASSWORD_POLICY_V1277",
                          "STREAMFORGE_NODE_FIXED_LOGIN_ADDRESS_BAR_V3059"],
}
for relative, markers in checks.items():
    text = (root / relative).read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{relative} missing {missing}")
print("PASS: preflight staged v12.77 Main patch, Node app.py, markers and Python syntax")
PYSTAGE

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1277-$STAMP"
FILES=("${PATCH_FILES[@]}" node_agent/app.py VERSION node_agent/VERSION)
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
  temporary="$(mktemp "$(dirname "$2")/.v1277.XXXXXX")"
  cp "$1" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$2"
}
for f in "${PATCH_FILES[@]}" node_agent/app.py; do
  mode="$(stat -c '%a' "$ROOT/$f")"
  atomic_copy "$STAGING/$f" "$ROOT/$f" "$mode"
done
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1277_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
  log "Controlled failure after write"
  rollback 1
fi
for f in "${PATCH_FILES[@]}" node_agent/app.py; do
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
log "SUCCESS: Main/bundled Node v12.77 password policy and Node Panel headers installed."
log "Next: update Remote Nodes from Node Updates so their Panels send the new headers."
