#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGACY_UPDATER="$SOURCE_DIR/scripts/force_update_v1236.sh"

[[ ${EUID} -eq 0 ]] || { echo "[StreamForge v12.43] ERROR: run with sudo/root" >&2; exit 1; }
[[ -f "$LEGACY_UPDATER" ]] || { echo "[StreamForge v12.43] ERROR: base updater force_update_v1236.sh missing" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "12.43" ]] || { echo "[StreamForge v12.43] ERROR: wrong package version" >&2; exit 1; }
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "12.43" ]] || { echo "[StreamForge v12.43] ERROR: wrong Node Agent version" >&2; exit 1; }

echo "[StreamForge v12.43] Preparing update-safe v12.43 staging runtime..."
STAGE="$(mktemp -d /tmp/streamforge-v1243-XXXXXXXX)"
cleanup(){ rm -rf -- "$STAGE"; }
trap cleanup EXIT

cp -a "$SOURCE_DIR/." "$STAGE/"

# STREAMFORGE_RELEASE_STAGING_SINGLE_FORCE_UPDATER_V1243:
find "$STAGE/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh' \
  ! -name 'force_update_v1236.sh' -delete

python3 - "$STAGE/scripts/force_update_v1236.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8").replace("12.36", "12.43")
lines = text.splitlines(keepends=True)

# STREAMFORGE_V1243_DEAD_INPUT_SCHEMA_BEFORE_VERIFY:
# v12.42 introduced a channels column that verify_main_install.py requires,
# but the inherited modern-update path did not run ensure_runtime_schema()
# before install verification. Inject the additive migration immediately
# before the real verifier invocation, inside the inherited updater's existing
# backup/rollback transaction boundary.
indices = [
    i for i, line in enumerate(lines)
    if "verify_main_install.py" in line and "python3" in line
]
if len(indices) != 1:
    raise SystemExit(
        f"v12.43 schema-order patch expected one Main verifier invocation, found {len(indices)}"
    )

schema_block = r'''# STREAMFORGE_V1243_DEAD_INPUT_SCHEMA_BEFORE_VERIFY:
log "Applying v12.42 dead-input recovery schema before Main install verification."
python3 - "$DATA_DIR/streamforge.db" <<'PYV1243'
import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path, timeout=30)
try:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(channels)")}
    if 'dead_input_recovery_interval' not in columns:
        conn.execute(
            "ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30"
        )
        conn.commit()
        print("v12.43 schema migration: added channels.dead_input_recovery_interval default 30")
    else:
        print("v12.43 schema migration: channels.dead_input_recovery_interval already present")
finally:
    conn.close()
PYV1243
'''

lines.insert(indices[0], schema_block)
path.write_text("".join(lines), encoding="utf-8")
PY
chmod 0755 "$STAGE/scripts/force_update_v1236.sh"

printf '%s\n' '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' \
  'exec /usr/bin/env bash "$SCRIPT_DIR/force_update_v1236.sh" "$@"' \
  > "$STAGE/scripts/update.sh"
chmod 0755 "$STAGE/scripts/update.sh"

bash "$STAGE/scripts/force_update_v1236.sh" "$@"
echo "[StreamForge v12.43] Update completed."
