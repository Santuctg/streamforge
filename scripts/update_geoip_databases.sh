#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${STREAMFORGE_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEST_DIR="${STREAMFORGE_GEOIP_DIR:-$APP_ROOT}"
MAIN_ENV="${STREAMFORGE_ENV_FILE:-/etc/streamforge.env}"
NODE_ENV="${STREAMFORGE_NODE_ENV_FILE:-/etc/streamforge-node.env}"
SETTINGS_FILE="${STREAMFORGE_GEOIP_SETTINGS_FILE:-$APP_ROOT/geoip-settings.json}"
if [[ ! -f "$SETTINGS_FILE" && -f /var/lib/streamforge-node/geoip-settings.json ]]; then SETTINGS_FILE=/var/lib/streamforge-node/geoip-settings.json; fi
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

# STREAMFORGE_GEOIP_INHERITED_SECRET_PERMISSION_SAFE_V99R6:
# The web service receives root-owned EnvironmentFile values from systemd, but
# the streamforge service user cannot read /etc/streamforge.env directly.
# Preserve inherited secrets and silently skip env files that are not readable.
read_env_value(){
  local key="$1" file value=""
  for file in "$MAIN_ENV" "$NODE_ENV"; do
    [[ -r "$file" ]] || continue
    value="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    [[ -n "$value" ]] && { printf '%s' "$value"; return 0; }
  done
  printf ''
}

provider="${STREAMFORGE_GEOIP_PROVIDER:-}"
enabled="${STREAMFORGE_GEOIP_AUTO_UPDATE:-}"
account="${STREAMFORGE_GEOIP_ACCOUNT_ID:-}"
license="${STREAMFORGE_GEOIP_LICENSE_KEY:-}"

# Main Panel web settings use encrypted secret storage. Read them with the
# installed application and its secret key when available.
if command -v python3 >/dev/null 2>&1 && [[ -f "$APP_ROOT/app/geoip_config.py" ]]; then
  panel_secret="${STREAMFORGE_SECRET_KEY:-}"
  [[ -n "$panel_secret" ]] || panel_secret="$(read_env_value STREAMFORGE_SECRET_KEY)"
  mapfile -t web_values < <(PYTHONPATH="$APP_ROOT" STREAMFORGE_SECRET_KEY="$panel_secret" STREAMFORGE_GEOIP_SETTINGS_FILE="$SETTINGS_FILE" python3 - <<'PY' 2>/dev/null || true
from app.geoip_config import load_geoip_settings
s=load_geoip_settings(include_secrets=True)
print(s.get('provider','auto'))
print('1' if s.get('auto_update') else '0')
print(s.get('maxmind_account_id',''))
print(s.get('maxmind_license_key',''))
PY
  )
  [[ -n "${web_values[0]:-}" && -z "$provider" ]] && provider="${web_values[0]}"
  [[ -n "${web_values[1]:-}" && -z "$enabled" ]] && enabled="${web_values[1]}"
  [[ -n "${web_values[2]:-}" && -z "$account" ]] && account="${web_values[2]}"
  [[ -n "${web_values[3]:-}" && -z "$license" ]] && license="${web_values[3]}"
fi

# Remote Node settings are stored in a mode-0600 JSON file.
if [[ -f "$SETTINGS_FILE" && ( -z "$account" || -z "$license" || -z "$enabled" || -z "$provider" ) ]]; then
  mapfile -t json_values < <(python3 - "$SETTINGS_FILE" <<'PY' 2>/dev/null || true
import json,sys
try:
    data=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception:
    data={}
print(data.get('provider','auto'))
print('1' if data.get('auto_update') else '0')
print(data.get('maxmind_account_id',''))
print(data.get('maxmind_license_key',''))
PY
  )
  provider="${provider:-${json_values[0]:-auto}}"
  enabled="${enabled:-${json_values[1]:-0}}"
  account="${account:-${json_values[2]:-}}"
  license="${license:-${json_values[3]:-}}"
fi

provider="${provider:-$(read_env_value STREAMFORGE_GEOIP_PROVIDER)}"
provider="${provider:-auto}"
enabled="${enabled:-$(read_env_value STREAMFORGE_GEOIP_AUTO_UPDATE)}"
account="${account:-$(read_env_value STREAMFORGE_MAXMIND_ACCOUNT_ID)}"
license="${license:-$(read_env_value STREAMFORGE_MAXMIND_LICENSE_KEY)}"

if [[ "$FORCE" != "1" ]]; then
  case "${enabled,,}" in 1|true|yes|on) ;; *) echo "GeoIP auto-update is disabled in the web settings"; exit 0;; esac
fi
if [[ "${provider,,}" == "ipinfo" && ( -z "$account" || -z "$license" ) ]]; then
  echo "IPinfo is a live API provider; no MaxMind database download is required."
  exit 0
fi
[[ -n "$account" && -n "$license" ]] || { echo "MaxMind Account ID or License Key is missing" >&2; exit 1; }
# STREAMFORGE_GEOIPUPDATE_DEPENDENCY_V99R7:
command -v geoipupdate >/dev/null 2>&1 || { echo "GeoIP updater dependency is missing. Run StreamForge v9.9 r7 update or install the geoipupdate package." >&2; exit 1; }

mkdir -p "$DEST_DIR"
config="$(mktemp /tmp/streamforge-GeoIP.conf.XXXXXX)"
trap 'rm -f "$config"' EXIT
cat > "$config" <<EOF
AccountID $account
LicenseKey $license
EditionIDs GeoLite2-ASN GeoLite2-Country
DatabaseDirectory $DEST_DIR
EOF
chmod 0600 "$config"
geoipupdate -f "$config" -d "$DEST_DIR" -v
chmod 0644 "$DEST_DIR/GeoLite2-ASN.mmdb" "$DEST_DIR/GeoLite2-Country.mmdb" 2>/dev/null || true
date -u +%FT%TZ > "$DEST_DIR/.geoip-last-update"
chmod 0644 "$DEST_DIR/.geoip-last-update"
systemctl try-restart streamforge.service >/dev/null 2>&1 || true
systemctl try-restart streamforge-node.service >/dev/null 2>&1 || true
echo "GeoIP databases updated in $DEST_DIR"
