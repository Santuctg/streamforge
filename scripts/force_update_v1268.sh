#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1268_MAIN_PASSWORD_MODAL_SCOPED
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
[[ "$TEST_MODE" == "1" || $EUID -eq 0 ]] || { echo "root required"; exit 1; }
[[ "$(< "$SRC/VERSION")" == "12.68" && "$(< "$SRC/node_agent/VERSION")" == "12.68" ]] || { echo "Wrong package version"; exit 1; }
case "$(< "$ROOT/VERSION")" in 12.67|12.68) ;; *) echo "Requires installed Main 12.67 or 12.68"; exit 1;; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.67|12.68) ;; *) echo "Requires bundled Node 12.67 or 12.68"; exit 1;; esac
for path in app/main.py app/panel_password_vault.py app/templates/admin_users.html; do
 [[ -f "$SRC/$path" && -f "$ROOT/$path" ]] || { echo "Missing $path"; exit 1; }
done
python3 - "$SRC/node_agent" "$ROOT/node_agent" <<'PY'
from pathlib import Path
import sys, hashlib
src, dst = map(Path,sys.argv[1:])
def catalog(root):
 return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
         for p in root.rglob("*") if p.is_file() and p.name!="VERSION"
         and "__pycache__" not in p.parts and p.suffix not in {".pyc",".pyo"}}
a,b=catalog(src),catalog(dst)
if a!=b:
 different=sorted(k for k in set(a)|set(b) if a.get(k)!=b.get(k))
 raise SystemExit("Node runtime payload differs for: "+",".join(different[:12]))
print("STREAMFORGE_V1268_MAIN_ONLY_NODE_PARITY: verified unchanged Node runtime")
PY
STAGING="$(mktemp -d -t sf-v1268-stage-XXXXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
cp "$ROOT/app/main.py" "$STAGING/main.py"
python3 "$SRC/scripts/apply_v1268_password_modal.py" "$STAGING/main.py"
python3 - "$STAGING/main.py" "$SRC/app/panel_password_vault.py" <<'PY'
from pathlib import Path
import sys
for name in sys.argv[1:]:
 file=Path(name)
 compile(file.read_text(encoding="utf-8"),str(file),"exec")
print("PASS: Main Python preflight")
PY
grep -Fq STREAMFORGE_V1268_PW_MODAL_CSRF "$STAGING/main.py"
grep -Fq STREAMFORGE_V1268_PASSWORD_RESET_MODAL_API "$SRC/app/panel_password_vault.py"
grep -Fq STREAMFORGE_V1268_PASSWORD_RESET_MODAL "$SRC/app/templates/admin_users.html"
PYTHONPATH="$SRC" python3 "$SRC/scripts/test_v1268_password_modal_patch.py"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1268-$STAMP"
FILES=(VERSION node_agent/VERSION app/main.py app/panel_password_vault.py app/templates/admin_users.html)
for f in "${FILES[@]}"; do
 mkdir -p "$BACKUP/$(dirname "$f")"
 cp -a "$ROOT/$f" "$BACKUP/$f"
done
echo "[StreamForge v12.68] Backup: $BACKUP"
ROLLBACK_NEEDED=1
rollback(){
 local rc="${1:-1}"
 trap - ERR
 if [[ "$ROLLBACK_NEEDED" == 1 ]]; then
  echo "[StreamForge v12.68] Rolling back from $BACKUP"
  for f in "${FILES[@]}"; do cp -a "$BACKUP/$f" "$ROOT/$f" || true; done
  if [[ "$TEST_MODE" != 1 ]]; then
   systemctl restart streamforge >/dev/null 2>&1 || true
   systemctl restart streamforge-public >/dev/null 2>&1 || true
  fi
 fi
 exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){
 local temp
 temp="$(mktemp "$(dirname "$2")/.v1268.XXXXXX")"
 cp "$1" "$temp"
 chmod 0644 "$temp"
 mv -f "$temp" "$2"
}
atomic_copy "$STAGING/main.py" "$ROOT/app/main.py"
atomic_copy "$SRC/app/panel_password_vault.py" "$ROOT/app/panel_password_vault.py"
atomic_copy "$SRC/app/templates/admin_users.html" "$ROOT/app/templates/admin_users.html"
printf '%s\n' 12.68 > "$ROOT/VERSION"
printf '%s\n' 12.68 > "$ROOT/node_agent/VERSION"
mkdir -p "$ROOT/scripts"
install -m 0755 "$SRC/scripts/apply_v1268_password_modal.py" "$ROOT/scripts/apply_v1268_password_modal.py"
if [[ "${STREAMFORGE_V1268_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
 echo "[StreamForge v12.68] Controlled failure after write"
 rollback 1
fi
cmp -s "$STAGING/main.py" "$ROOT/app/main.py" || rollback 1
cmp -s "$SRC/app/panel_password_vault.py" "$ROOT/app/panel_password_vault.py" || rollback 1
cmp -s "$SRC/app/templates/admin_users.html" "$ROOT/app/templates/admin_users.html" || rollback 1
if [[ "$TEST_MODE" != 1 ]]; then
 systemctl restart streamforge
 systemctl restart streamforge-public
 systemctl is-active --quiet streamforge || rollback 1
 systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK_NEEDED=0
trap - ERR
echo "[StreamForge v12.68] SUCCESS: Main password modals updated; no Node/Nginx/channel changes."
