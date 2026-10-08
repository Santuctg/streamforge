#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGACY_UPDATER="$SOURCE_DIR/scripts/force_update_v1236.sh"

[[ ${EUID} -eq 0 ]] || { echo "[StreamForge v12.41] ERROR: run with sudo/root" >&2; exit 1; }
[[ -f "$LEGACY_UPDATER" ]] || { echo "[StreamForge v12.41] ERROR: base updater force_update_v1236.sh missing" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "12.41" ]] || { echo "[StreamForge v12.41] ERROR: wrong package version" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "12.41" ]] || { echo "[StreamForge v12.41] ERROR: wrong Node Agent version" >&2; exit 1; }

echo "[StreamForge v12.41] Preparing update-safe v12.41 staging runtime..."
STAGE="$(mktemp -d /tmp/streamforge-v1241-XXXXXXXX)"
cleanup(){ rm -rf -- "$STAGE"; }
trap cleanup EXIT

cp -a "$SOURCE_DIR/." "$STAGE/"

# STREAMFORGE_RELEASE_STAGING_SINGLE_FORCE_UPDATER_V1241:
# The inherited v12.36 updater intentionally requires exactly one versioned
# force updater in the staged package. Remove every historical bridge script
# and keep only the inherited full updater before running its integrity guards.
find "$STAGE/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh' \
  ! -name 'force_update_v1236.sh' -delete

python3 - "$STAGE/scripts/force_update_v1236.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("12.36", "12.41")
path.write_text(text, encoding="utf-8")
PY
chmod 0755 "$STAGE/scripts/force_update_v1236.sh"

printf '%s\n' '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' \
  'exec /usr/bin/env bash "$SCRIPT_DIR/force_update_v1236.sh" "$@"' \
  > "$STAGE/scripts/update.sh"
chmod 0755 "$STAGE/scripts/update.sh"

bash "$STAGE/scripts/force_update_v1236.sh" "$@"
echo "[StreamForge v12.41] Update completed."
