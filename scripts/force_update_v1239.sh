#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGACY_UPDATER="$SOURCE_DIR/scripts/force_update_v1236.sh"

[[ ${EUID} -eq 0 ]] || { echo "[StreamForge v12.39] ERROR: run with sudo/root" >&2; exit 1; }
[[ -f "$LEGACY_UPDATER" ]] || { echo "[StreamForge v12.39] ERROR: base updater force_update_v1236.sh missing" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "12.39" ]] || { echo "[StreamForge v12.39] ERROR: wrong package version" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "12.39" ]] || { echo "[StreamForge v12.39] ERROR: wrong Node Agent version" >&2; exit 1; }

echo "[StreamForge v12.39] Preparing update-safe v12.39 staging runtime..."
STAGE="$(mktemp -d /tmp/streamforge-v1239-XXXXXXXX)"
cleanup(){ rm -rf -- "$STAGE"; }
trap cleanup EXIT

cp -a "$SOURCE_DIR/." "$STAGE/"
rm -f "$STAGE/scripts/force_update_v1239.sh"

# v12.39 carries the proven v12.36 full updater logic, but stages it with the
# new package/version guards. Keeping only the v12.36-named updater in staging
# also satisfies its one-version-specific-updater integrity check.
python3 - "$STAGE/scripts/force_update_v1236.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("12.36", "12.39")
path.write_text(text, encoding="utf-8")
PY
chmod 0755 "$STAGE/scripts/force_update_v1236.sh"

# Preserve the staged updater selector expected by the inherited v12.36 guard.
printf '%s\n' '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' \
  'exec /usr/bin/env bash "$SCRIPT_DIR/force_update_v1236.sh" "$@"' \
  > "$STAGE/scripts/update.sh"
chmod 0755 "$STAGE/scripts/update.sh"

bash "$STAGE/scripts/force_update_v1236.sh" "$@"
echo "[StreamForge v12.39] Update completed."
