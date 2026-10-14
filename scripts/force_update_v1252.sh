#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="12.52"
NODE_TARGET_VERSION="12.52"
APP_ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BASE_WRAPPER="$SOURCE_DIR/scripts/force_update_v1248.sh"

log(){ echo "[StreamForge v${TARGET_VERSION}] $*"; }
die(){ echo "[StreamForge v${TARGET_VERSION}] ERROR: $*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$SOURCE_DIR/VERSION" ]] || die "package VERSION missing"
[[ -f "$SOURCE_DIR/node_agent/VERSION" ]] || die "package Node Agent VERSION missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "$TARGET_VERSION" ]] || die "wrong package version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] || die "wrong Node Agent version"
[[ -f "$BASE_WRAPPER" ]] || die "v12.48 full-update bridge missing"
[[ -f "$SOURCE_DIR/scripts/apply_v1251_node_recovery_probe_fix.py" ]] || die "V1251 Node patcher missing"
[[ -f "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" ]] || die "V1251 Node verifier missing"

log "Verifying bundled Node Agent single-owner relay health recovery."
python3 "$SOURCE_DIR/scripts/apply_v1251_node_recovery_probe_fix.py" "$SOURCE_DIR/node_agent/app.py"
# STREAMFORGE_V1252_NO_SOURCE_BYTECODE:
# Do not use py_compile against SOURCE_DIR before staging. py_compile writes
# __pycache__, which makes the inherited package-hygiene guard reject the
# updater's own generated metadata. compile() validates syntax without writes.
python3 - "$SOURCE_DIR/node_agent/app.py" <<'PYV1252SYNTAX'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
compile(source, str(path), "exec")
print("PASS: Node Agent Python syntax verified without bytecode output")
PYV1252SYNTAX
python3 "$SOURCE_DIR/scripts/verify_node_relay_probe_dedupe_v1251.py" "$SOURCE_DIR/node_agent/app.py"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$SOURCE_DIR/node_agent/app.py" \
  || die "V1251 Node relay health-probe marker missing"
grep -Fq 'STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250' "$SOURCE_DIR/app/main.py" \
  || die "V1250 Main relay DB-close guard missing from package"

# The updater must leave the release source tree clean before any nested staging.
if find "$SOURCE_DIR" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '.cache' \) -print -quit | grep -q .; then
  die "pre-staging verification polluted package source with cache metadata"
fi
if find "$SOURCE_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit | grep -q .; then
  die "pre-staging verification polluted package source with bytecode"
fi

STAGE="$(mktemp -d /tmp/streamforge-v1252-XXXXXXXX)"
cleanup(){ rm -rf -- "$STAGE"; }
trap cleanup EXIT
cp -a "$SOURCE_DIR/." "$STAGE/"

# STREAMFORGE_V1252_FULL_UPDATE_BRIDGE:
# Reuse the proven v12.48 full-deployment wrapper in an isolated staging copy.
# The V1251 Node recovery fix is already carried in source; this release only
# repairs updater hygiene so a clean release cannot dirty itself before deploy.
python3 - "$STAGE/scripts/force_update_v1248.sh" <<'PYV1252'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if '"12.48"' not in text or '12.36", "12.48' not in text:
    raise SystemExit("v12.52 bridge could not identify the v12.48 version guards")
text = text.replace("12.48", "12.52")
path.write_text(text, encoding="utf-8")
PYV1252
chmod 0755 "$STAGE/scripts/force_update_v1248.sh"

log "Running proven full updater bridge; Remote Nodes are not pushed automatically."
bash "$STAGE/scripts/force_update_v1248.sh" "$@"

[[ "$(tr -d '[:space:]' < "$APP_ROOT/VERSION")" == "$TARGET_VERSION" ]] \
  || die "installed Main VERSION is not $TARGET_VERSION"
[[ "$(tr -d '[:space:]' < "$APP_ROOT/node_agent/VERSION")" == "$NODE_TARGET_VERSION" ]] \
  || die "installed bundled Node Agent VERSION is not $NODE_TARGET_VERSION"
python3 "$APP_ROOT/scripts/verify_node_relay_probe_dedupe_v1251.py" "$APP_ROOT/node_agent/app.py"
grep -Fq 'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251' "$APP_ROOT/node_agent/app.py" \
  || die "installed bundled Node Agent V1251 marker missing"

log "SUCCESS — Main v${TARGET_VERSION} and bundled Node Agent v${NODE_TARGET_VERSION} are installed."
log "Push the Node update from the Main panel after Main verification; no Remote Node was updated automatically."
