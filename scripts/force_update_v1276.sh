#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1276_SECURITY_HARDENING_UPDATER:
# Main-only staged patch of installed sources (scripts/apply_v1276_security.py):
# stable stream/relay keys, encrypted destination backups, SSH host-key pinning,
# security headers, session revocation on password change, Xtream auth limits.
# The channel supervisor keeps running (its relay keys are unchanged), so live
# streams are not interrupted. No Node runtime or Nginx config changes.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
SBIN="${STREAMFORGE_SBIN_DIR:-/usr/local/sbin}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.76"
log(){ echo "[StreamForge v12.76] $*"; }
die(){ echo "[StreamForge v12.76] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.75|12.76) ;; *) die "requires installed Main v12.75 or v12.76";; esac
case "$(< "$ROOT/node_agent/VERSION")" in 12.75|12.76) ;; *) die "requires bundled Node v12.75 or v12.76";; esac
PATCH_FILES=(app/main.py app/auth.py app/node_manager.py app/ssh_installer.py app/backup_manager.py app/templates/backups.html scripts/apply_main_access.py)
NEW_FILES=(app/stream_keys.py app/xtream_guard.py app/backup_crypto.py)
for f in "${PATCH_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$ROOT/$f" ]] || die "missing installed $f"
done
for f in "${NEW_FILES[@]}" scripts/apply_v1276_security.py scripts/apply_v1276_security.json; do
  [[ -f "$SRC/$f" ]] || die "missing package file $f"
done
HELPER="$SBIN/streamforge-apply-main-access"
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
print("STREAMFORGE_V1276_MAIN_ONLY_NODE_PARITY: unchanged Node runtime verified")
PYNODE

# Stage copies of the installed files, patch and verify them before touching
# production; the repository files are never copied over installed sources.
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
for f in "${PATCH_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp -a "$ROOT/$f" "$STAGING/$f"
done
for f in "${NEW_FILES[@]}"; do
  mkdir -p "$STAGING/$(dirname "$f")"
  cp "$SRC/$f" "$STAGING/$f"
done
python3 -B "$SRC/scripts/apply_v1276_security.py" "$STAGING" "${PATCH_FILES[@]}" || die "security patch preflight failed"
python3 -B - "$STAGING" <<'PYSTAGE'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for path in root.rglob("*.py"):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
checks = {
    "app/main.py": ["STREAMFORGE_MAIN_SECURITY_HEADERS_V1276", "STREAMFORGE_XTREAM_AUTH_GUARD_V1276",
                    "ensure_stream_link_secret(db)", "stamp_admin_session(request, admin)",
                    "prepare_restore_archive(staged, backup_passphrase)"],
    "app/auth.py": ["STREAMFORGE_MAIN_SESSION_AUTH_VERSION_V1276"],
    "app/node_manager.py": ["return stream_link_hmac(message)"],
    "app/ssh_installer.py": ["STREAMFORGE_SSH_HOST_KEY_PINNING_V1276"],
    "app/backup_manager.py": ["STREAMFORGE_BACKUP_ARCHIVE_ENCRYPTION_V1276", "STREAMFORGE_SSH_HOST_KEY_PINNING_V1276"],
    "app/templates/backups.html": ["STREAMFORGE_BACKUP_ARCHIVE_ENCRYPTION_V1276"],
    "scripts/apply_main_access.py": ["STREAMFORGE_STREAM_LINK_SECRET_V1276"],
}
for relative, markers in checks.items():
    text = (root / relative).read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{relative} missing {missing}")
print("PASS: preflight staged v12.76 security patch, markers and Python syntax")
PYSTAGE

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1276-$STAMP"
mkdir -p "$BACKUP"
: > "$BACKUP/absent-files.txt"
for f in "${PATCH_FILES[@]}" "${NEW_FILES[@]}" VERSION node_agent/VERSION; do
  if [[ -e "$ROOT/$f" ]]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -a "$ROOT/$f" "$BACKUP/$f"
  else
    echo "$f" >> "$BACKUP/absent-files.txt"
  fi
done
[[ -f "$HELPER" ]] && cp -a "$HELPER" "$BACKUP/streamforge-apply-main-access"
log "Backup created: $BACKUP"
ROLLBACK=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == 1 ]]; then
    log "Rolling back from $BACKUP"
    for f in "${PATCH_FILES[@]}" "${NEW_FILES[@]}" VERSION node_agent/VERSION; do
      if [[ -e "$BACKUP/$f" ]]; then cp -a "$BACKUP/$f" "$ROOT/$f" || true; fi
    done
    while IFS= read -r f; do [[ -n "$f" ]] && rm -f "$ROOT/$f"; done < "$BACKUP/absent-files.txt"
    [[ -f "$BACKUP/streamforge-apply-main-access" ]] && cp -a "$BACKUP/streamforge-apply-main-access" "$HELPER" || true
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
  temporary="$(mktemp "$(dirname "$2")/.v1276.XXXXXX")"
  cp "$1" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$2"
}
for f in "${PATCH_FILES[@]}"; do
  mode="$(stat -c '%a' "$ROOT/$f")"
  atomic_copy "$STAGING/$f" "$ROOT/$f" "$mode"
done
for f in "${NEW_FILES[@]}"; do
  atomic_copy "$STAGING/$f" "$ROOT/$f" 0644
done
if [[ -f "$HELPER" ]]; then
  atomic_copy "$STAGING/scripts/apply_main_access.py" "$HELPER" 0755
fi
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
if [[ "${STREAMFORGE_V1276_TEST_FAIL_AFTER_WRITE:-0}" == 1 ]]; then
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
log "SUCCESS: Main v12.76 security hardening installed. Running Remote Nodes unchanged."
log "Next: set a backup passphrase under Backups so destination copies are encrypted."
