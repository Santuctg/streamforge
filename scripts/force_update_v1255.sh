#!/usr/bin/env bash
set -Eeuo pipefail

# STREAMFORGE_VERSION_ALIGNMENT_V1255:
# Final alignment release: Main and bundled Remote Node Agent both report
# v12.55. Node runtime behavior is intentionally unchanged from the proven
# v12.52 payload; this updater verifies payload parity before changing VERSION.
#
# STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254:
# Carry forward the v12.54 atomic Main static-cache synchronization so Nginx's
# direct /static/ cache cannot drift from /opt/streamforge/app/static.
#
# STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:
# Carry forward the v12.53 persistent Waiting clock and 60-second retry policy.
#
# STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251:
# Carry forward the proven Node relay-health probe single-owner guard.
#
# STREAMFORGE_V1255_ROLLBACK_GUARD:
# Once backup is complete, both implicit command failures and explicit
# post-write validation failures route through rollback().

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.55"
NODE_TARGET_VERSION="12.55"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"
TEST_MODE="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
MAIN_SERVICE="streamforge"
PUBLIC_SERVICE="streamforge-public"

log(){ echo "[StreamForge v${TARGET_VERSION} Version Alignment] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION} Version Alignment] ERROR: $*" >&2; exit 1; }

[[ "$TEST_MODE" == "1" || ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package Main version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong package Node Agent version"
[[ -d "$SOURCE_DIR/app/static" ]] || die "package app/static directory missing"
[[ -f "$SOURCE_DIR/scripts/sync_main_static_cache.py" ]] || die "v12.54 static sync helper missing"
[[ -f "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" ]] || die "V1253 verifier missing"
[[ -f "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" ]] || die "V1251 Node verifier missing"

grep -Fq 'STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254' "$SOURCE_DIR/scripts/sync_main_static_cache.py" \
  || die "V1254 static-cache sync marker missing"
grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$SOURCE_DIR/app/static/app.js" \
  || die "V1253 Waiting UI marker missing from package app.js"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" \
  || die "V1250 Main DB-close guard missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" \
  || die "V1251 Node relay-health guard missing"

python3 "$SOURCE_DIR/scripts/verify_main_waiting_retry_v1253.py" "$SOURCE_DIR"
python3 "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" "$SOURCE_DIR/node_agent/app.py"
python3 "$SOURCE_DIR/scripts/test_main_static_cache_sync_v1254.py"

# Syntax checks are performed in-memory so the release source tree is never
# dirtied with __pycache__ before deployment.
python3 - "$SOURCE_DIR/node_agent/app.py" "$SOURCE_DIR/scripts/sync_main_static_cache.py" <<'PYV1255SYNTAX'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS: v12.55 Python syntax verified without bytecode output")
PYV1255SYNTAX

[[ -f "$APP_ROOT/VERSION" ]] || die "installed Main VERSION missing"
INSTALLED_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/VERSION")"
case "$INSTALLED_VERSION" in
  12.54|12.55) ;;
  *) die "v12.55 alignment requires installed Main v12.54 or v12.55; found $INSTALLED_VERSION" ;;
esac

[[ -f "$APP_ROOT/node_agent/VERSION" ]] || die "installed bundled Node Agent VERSION missing"
INSTALLED_NODE_VERSION="$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")"
case "$INSTALLED_NODE_VERSION" in
  12.52|12.55) ;;
  *) die "v12.55 alignment requires installed bundled Node Agent v12.52 or v12.55; found $INSTALLED_NODE_VERSION" ;;
esac

# This release must not silently relabel different Node code. Every packaged
# Node payload file except VERSION must already match the installed v12.52
# payload byte-for-byte. If it does not, stop before modifying production.
python3 - "$SOURCE_DIR/node_agent" "$APP_ROOT/node_agent" <<'PYV1255NODEPARITY'
from pathlib import Path
import hashlib
import sys

source = Path(sys.argv[1])
installed = Path(sys.argv[2])
skip_dirs = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"}
skip_suffixes = {".pyc", ".pyo"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

count = 0
for src in sorted(p for p in source.rglob("*") if p.is_file()):
    rel = src.relative_to(source)
    if rel.as_posix() == "VERSION":
        continue
    if any(part in skip_dirs for part in rel.parts) or src.suffix in skip_suffixes:
        continue
    dst = installed / rel
    if not dst.is_file():
        raise SystemExit(f"installed Node payload missing file: {rel}")
    if digest(src) != digest(dst):
        raise SystemExit(f"installed Node payload differs from alignment package: {rel}")
    count += 1
print(f"STREAMFORGE_VERSION_ALIGNMENT_V1255: verified unchanged Node payload parity for {count} files")
PYV1255NODEPARITY

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/v1255-$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$APP_ROOT/VERSION" "$BACKUP_DIR/VERSION"
mkdir -p "$BACKUP_DIR/node-agent"
cp -a "$APP_ROOT/node_agent/VERSION" "$BACKUP_DIR/node-agent/VERSION"
if [[ -d "$APP_ROOT/app/static" ]]; then
  mkdir -p "$BACKUP_DIR/app-static"
  cp -a "$APP_ROOT/app/static/." "$BACKUP_DIR/app-static/"
fi
if [[ -d "$STATIC_CACHE_ROOT" ]]; then
  mkdir -p "$BACKUP_DIR/main-static"
  cp -a "$STATIC_CACHE_ROOT/." "$BACKUP_DIR/main-static/"
fi
log "Backup created: $BACKUP_DIR"

ROLLBACK_NEEDED=1
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK_NEEDED" -eq 1 ]]; then
    echo "[StreamForge v${TARGET_VERSION} Version Alignment] Rolling back from $BACKUP_DIR" >&2
    cp -a "$BACKUP_DIR/VERSION" "$APP_ROOT/VERSION" 2>/dev/null || true
    cp -a "$BACKUP_DIR/node-agent/VERSION" "$APP_ROOT/node_agent/VERSION" 2>/dev/null || true
    if [[ -d "$BACKUP_DIR/app-static" ]]; then
      mkdir -p "$APP_ROOT/app/static"
      cp -a "$BACKUP_DIR/app-static/." "$APP_ROOT/app/static/" 2>/dev/null || true
    fi
    if [[ -d "$BACKUP_DIR/main-static" ]]; then
      mkdir -p "$STATIC_CACHE_ROOT"
      cp -a "$BACKUP_DIR/main-static/." "$STATIC_CACHE_ROOT/" 2>/dev/null || true
    fi
    if [[ "$TEST_MODE" != "1" ]]; then
      systemctl restart "$MAIN_SERVICE" >/dev/null 2>&1 || true
      systemctl restart "$PUBLIC_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
abort_after_write(){
  echo "[StreamForge v${TARGET_VERSION} Version Alignment] ERROR: $*" >&2
  rollback 1
}
trap 'rollback $?' ERR

log "Re-applying atomic Main static deployment and Nginx direct-cache parity."
python3 "$SOURCE_DIR/scripts/sync_main_static_cache.py" \
  "$SOURCE_DIR/app/static" "$APP_ROOT/app/static"

mkdir -p "$APP_ROOT/scripts"
install -m 0755 "$SOURCE_DIR/scripts/sync_main_static_cache.py" "$APP_ROOT/scripts/sync_main_static_cache.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1255_version_alignment.py" "$APP_ROOT/scripts/test_v1255_version_alignment.py"
install -m 0755 "$SOURCE_DIR/scripts/test_v1255_updater_integration.py" "$APP_ROOT/scripts/test_v1255_updater_integration.py"
install -m 0755 "$SOURCE_DIR/scripts/force_update_v1255.sh" "$APP_ROOT/scripts/force_update_v1255.sh"

printf '%s\n' "$TARGET_VERSION" > "$APP_ROOT/VERSION"
printf '%s\n' "$NODE_TARGET_VERSION" > "$APP_ROOT/node_agent/VERSION"

# Test-only fault injection proves that explicit failures after production
# writes restore both VERSION files and backed-up static trees.
if [[ "${STREAMFORGE_V1255_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  abort_after_write "controlled post-write rollback test"
fi

python3 "$SOURCE_DIR/scripts/sync_main_static_cache.py" \
  "$APP_ROOT/app/static" "$STATIC_CACHE_ROOT"

python3 - "$SOURCE_DIR/app/static" "$APP_ROOT/app/static" "$STATIC_CACHE_ROOT" <<'PYV1255STATIC'
from pathlib import Path
import hashlib
import sys

source, installed, cache = map(Path, sys.argv[1:4])


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

count = 0
for src in sorted(path for path in source.rglob("*") if path.is_file()):
    rel = src.relative_to(source)
    expected = digest(src)
    for label, root in (("installed", installed), ("nginx-cache", cache)):
        target = root / rel
        if not target.is_file():
            raise SystemExit(f"missing {label} static asset: {rel}")
        if digest(target) != expected:
            raise SystemExit(f"SHA256 mismatch for {label} static asset: {rel}")
    count += 1
print(f"STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254: verified SHA256 parity for {count} static assets")
PYV1255STATIC

[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] || abort_after_write "Main VERSION did not align to v12.55"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || abort_after_write "bundled Node Agent VERSION did not align to v12.55"
grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$APP_ROOT/app/static/app.js" \
  || abort_after_write "installed app.js lost V1253 Waiting UI marker"
grep -Fq 'STREAMFORGE_MAIN_WAITING_RECOVERY_V1253' "$STATIC_CACHE_ROOT/app.js" \
  || abort_after_write "Nginx static cache app.js lost V1253 Waiting UI marker"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$APP_ROOT/node_agent/app.py" \
  || abort_after_write "installed bundled Node Agent lost V1251 relay-health guard"

if [[ "$TEST_MODE" != "1" ]]; then
  log "Restarting Main HTTP workers to load v12.55 version metadata."
  systemctl restart "$MAIN_SERVICE"
  systemctl restart "$PUBLIC_SERVICE"
  systemctl is-active --quiet "$MAIN_SERVICE" || abort_after_write "$MAIN_SERVICE is not active"
  systemctl is-active --quiet "$PUBLIC_SERVICE" || abort_after_write "$PUBLIC_SERVICE is not active"
fi

ROLLBACK_NEEDED=0
trap - ERR
log "SUCCESS — Main v12.55 and bundled Node Agent v12.55 are aligned."
log "Node runtime code is unchanged from v12.52. Push the Node update from the Main panel only after Main verification."
