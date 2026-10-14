#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGACY_UPDATER="$SOURCE_DIR/scripts/force_update_v1236.sh"
log(){ echo "[StreamForge v12.47] $*"; }
die(){ echo "[StreamForge v12.47] ERROR: $*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run with sudo/root"
[[ -f "$LEGACY_UPDATER" ]] || die "base updater force_update_v1236.sh missing"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")" == "12.47" ]] || die "wrong package version"
[[ "$(tr -d '[:space:]' < "$SOURCE_DIR/node_agent/VERSION")" == "12.47" ]] || die "wrong Node Agent version"

log "Preparing update-safe v12.47 staging runtime..."
STAGE="$(mktemp -d /tmp/streamforge-v1247-XXXXXXXX)"
cleanup(){ rm -rf -- "$STAGE"; }
trap cleanup EXIT

cp -a "$SOURCE_DIR/." "$STAGE/"

# STREAMFORGE_RELEASE_STAGING_SINGLE_FORCE_UPDATER_V1247:
find "$STAGE/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh' \
  ! -name 'force_update_v1236.sh' -delete

python3 - "$STAGE/scripts/force_update_v1236.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8").replace("12.36", "12.47")

# STREAMFORGE_V1247_RELAY_GUARD_COMPAT:
# v12.45 intentionally replaced the v10.68 relay logging marker with the
# state-aware v12.45 marker. Rewrite the inherited preflight marker checks in
# the staged updater so it validates the new policy instead of rejecting it.
stale = "STREAMFORGE_RELAY_" + "ERROR_ONLY_ACCESS_LOG_V1036"
modern = "STREAMFORGE_RELAY_ACCESS_LOG_POLICY_V1247"
if text.count(stale) < 2:
    raise SystemExit("v12.47 relay-guard patch expected inherited v10.68 marker guards")
text = text.replace(stale, modern)

lines = text.splitlines(keepends=True)

# STREAMFORGE_V1247_DEAD_INPUT_SCHEMA_BEFORE_VERIFY:
indices = [
    i for i, line in enumerate(lines)
    if "verify_main_install.py" in line and "python3" in line
]
if len(indices) != 1:
    raise SystemExit(
        f"v12.47 schema-order patch expected one Main verifier invocation, found {len(indices)}"
    )

schema_block = r'''# STREAMFORGE_V1247_DEAD_INPUT_SCHEMA_BEFORE_VERIFY:
log "Applying dead-input recovery schema before Main install verification."
python3 - "$DATA_DIR/streamforge.db" <<'PYV1247'
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
        print("v12.47 schema migration: added channels.dead_input_recovery_interval default 30")
    else:
        print("v12.47 schema migration: channels.dead_input_recovery_interval already present")
finally:
    conn.close()
PYV1247
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

# STREAMFORGE_V1247_RELAY_LOG_POLICY: enforce the policy in the active Nginx file after the inherited deploy.
SITE=/etc/nginx/sites-available/streamforge
[[ -f "$SITE" ]] || die "active StreamForge Nginx site is missing"
SITE_BACKUP="$(mktemp /tmp/streamforge-nginx-site-v1247.XXXXXX)"
cp -a "$SITE" "$SITE_BACKUP"
if ! python3 - "$SITE" <<'PYV1247'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
policy = """# STREAMFORGE_RELAY_ACCESS_LOG_POLICY_V1247: suppress high-volume expected relay states.
# Keep playlist 503s and non-segment 404s visible for diagnostics; stale segment 404s
# and client disconnect 499s are normal during live-edge movement/channel switching.
map "$status:$uri" $streamforge_relay_access_loggable {
    ~^409: 0;
    ~^410: 0;
    ~^404:/relay/[0-9a-f]{32}/[A-Za-z0-9_-]+/segment_[A-Za-z0-9_.-]+\\.ts$ 0;
    ~^499:/relay/ 0;
    ~^[45][0-9][0-9]: 1;
    default 0;
}"""

lines = text.splitlines()
map_indices = [i for i, line in enumerate(lines)
               if line.lstrip().startswith("map ") and "$streamforge_relay_access_loggable" in line]
if len(map_indices) != 1:
    raise SystemExit(f"expected one relay access-log map in active site, found {len(map_indices)}")
map_start = map_indices[0]
map_end = next((i for i in range(map_start, len(lines)) if lines[i].strip() == "}"), None)
if map_end is None:
    raise SystemExit("relay access-log map has no closing brace")
marker_start = map_start
for i in range(map_start - 1, -1, -1):
    if lines[i].startswith("# STREAMFORGE_RELAY_"):
        marker_start = i
        break
    if lines[i].strip() and not lines[i].lstrip().startswith("#"):
        break
policy_lines = policy.splitlines()
lines[marker_start:map_end + 1] = policy_lines

access = "        access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;"
out = []
i = 0
fallback_count = 0
while i < len(lines):
    line = lines[i]
    if line.strip() == "location @streamforge_relay_fallback {":
        fallback_count += 1
        out.append(line)
        i += 1
        end = i
        while end < len(lines) and lines[end].strip() != "}":
            end += 1
        if end >= len(lines):
            raise SystemExit("unterminated @streamforge_relay_fallback block")
        body = lines[i:end]
        if not any(x.strip() == access.strip() for x in body):
            out.append(access)
        out.extend(body)
        out.append(lines[end])
        i = end + 1
        continue
    out.append(line)
    i += 1

if fallback_count < 1:
    raise SystemExit("no @streamforge_relay_fallback location found")
updated = "\n".join(out) + "\n"
if updated != text:
    path.write_text(updated, encoding="utf-8")
print(f"relay policy applied; fallback blocks verified: {fallback_count}")
PYV1247
then
    cp -a "$SITE_BACKUP" "$SITE"
    rm -f "$SITE_BACKUP"
    die "could not apply v12.47 relay logging policy"
fi
if ! nginx -t; then
    cp -a "$SITE_BACKUP" "$SITE"
    nginx -t || true
    rm -f "$SITE_BACKUP"
    die "Nginx validation failed after v12.47 relay policy"
fi
systemctl reload nginx || {
    cp -a "$SITE_BACKUP" "$SITE"
    nginx -t && systemctl reload nginx || true
    rm -f "$SITE_BACKUP"
    die "Nginx reload failed after v12.47 relay policy"
}
rm -f "$SITE_BACKUP"
log "Relay log policy applied: suppress 409/410, stale segment 404 and relay 499; retain playlist 503 diagnostics."

# STREAMFORGE_NGINX_LOGROTATE_HOURLY_V1247: maxsize is checked hourly, not only daily.
if ! command -v logrotate >/dev/null 2>&1; then
    log "Installing missing logrotate package."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y logrotate
fi
cat >/etc/logrotate.d/nginx <<'EOFV1247'
/var/log/nginx/*.log {
        daily
        maxsize 100M
        rotate 7
        compress
        delaycompress
        missingok
        notifempty
        create 0640 www-data adm
        sharedscripts
        postrotate
                if [ -s /run/nginx.pid ]; then
                        kill -USR1 "$(cat /run/nginx.pid)"
                fi
        endscript
}
EOFV1247
cat >/etc/cron.hourly/streamforge-logrotate <<'EOFHOURV1247'
#!/bin/sh
# STREAMFORGE_NGINX_LOGROTATE_HOURLY_V1247
[ -x /usr/sbin/logrotate ] || exit 0
exec /usr/sbin/logrotate /etc/logrotate.d/nginx
EOFHOURV1247
chmod 0755 /etc/cron.hourly/streamforge-logrotate
logrotate -d /etc/logrotate.d/nginx >/dev/null 2>&1 || die "Nginx logrotate configuration validation failed"
log "Nginx log rotation protection ready (hourly size check, 100M maxsize, 7 rotations)."
log "SUCCESS — StreamForge v12.47 is live."
