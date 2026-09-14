#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN=""
PORT="80"
PUBLIC_PORT="80"
HOSTNAME_VALUE=""
DNS_ONLY="0"
PLAYLIST_HOST=""
PLAYLIST_DNS_ONLY="0"
BIND_ADDRESS="0.0.0.0"
PANEL_URL=""
NODE_SLUG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2;;
    --port) PORT="${2:-80}"; shift 2;;
    --public-port) PUBLIC_PORT="${2:-80}"; shift 2;;
    --hostname) HOSTNAME_VALUE="${2:-}"; shift 2;;
    --dns-only) DNS_ONLY="1"; shift;;
    --playlist-host) PLAYLIST_HOST="${2:-}"; shift 2;;
    --playlist-dns-only) PLAYLIST_DNS_ONLY="1"; shift;;
    --bind) BIND_ADDRESS="${2:-0.0.0.0}"; shift 2;;
    --panel-url) PANEL_URL="${2:-}"; shift 2;;
    --node-slug) NODE_SLUG="${2:-}"; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 1;;
  esac
done
[[ ${EUID} -eq 0 ]] || { echo "Run with sudo/root" >&2; exit 1; }
COLOCATED_MAIN=0
[[ -f /opt/streamforge/app/main.py ]] && COLOCATED_MAIN=1
# STREAMFORGE_NODE_PUBLIC_SYSTEMD_ENV_V79: standalone Remote Nodes get a
# dedicated public service; a co-located Node keeps the Main-proxy behavior.
PUBLIC_SYSTEMD_MANAGED=1
[[ "$COLOCATED_MAIN" -eq 1 ]] && PUBLIC_SYSTEMD_MANAGED=0
# STREAMFORGE_NODE_FRESH_DEPENDENCY_BOOTSTRAP_V107:
# Fresh Debian/Ubuntu Nodes may not have Python, FFmpeg, curl or iproute2.
# Bootstrap required OS packages before validating commands so both manual and
# Main-over-SSH installs work on a clean server.
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null
  apt-get install -y python3 python3-pip python3-venv ffmpeg curl unzip iproute2 geoipupdate ca-certificates gunicorn certbot dnsutils redis-server redis-tools >/dev/null
fi
bash "$SOURCE_DIR/scripts/bootstrap_youtube_runtime.sh" || true
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd/systemctl is required" >&2; exit 1; }
# STREAMFORGE_NODE_TARGET_PYTHON_COMPILE_V108:
# Parse every packaged Node Python source with the Remote Node's own Python
# interpreter before stopping/replacing any existing runtime. This catches
# Python-version-specific syntax incompatibilities (for example Python 3.10
# f-string grammar) without writing bytecode or touching the live service.
python3 - "$SOURCE_DIR/node_agent" <<'PY_NODE_SOURCE_CHECK'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
checked = 0
for path in sorted(root.glob("*.py")):
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    checked += 1
if checked == 0:
    raise SystemExit("No Node Python sources found to validate")
print(f"Remote Python syntax preflight OK ({checked} Node source files)")
PY_NODE_SOURCE_CHECK
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "Invalid control port" >&2; exit 1; }
[[ "$PUBLIC_PORT" =~ ^[0-9]+$ ]] && (( PUBLIC_PORT >= 1 && PUBLIC_PORT <= 65535 )) || { echo "Invalid Playlist/API port" >&2; exit 1; }
if [[ "$DNS_ONLY" == "1" ]]; then
  [[ -n "$HOSTNAME_VALUE" ]] || { echo "--dns-only requires --hostname" >&2; exit 1; }
fi
if [[ "$PLAYLIST_DNS_ONLY" == "1" ]]; then
  [[ -n "$PLAYLIST_HOST" ]] || { echo "--playlist-dns-only requires --playlist-host" >&2; exit 1; }
fi
[[ -n "$PLAYLIST_HOST" ]] || PLAYLIST_HOST="$HOSTNAME_VALUE"
[[ -n "$TOKEN" ]] || TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
id streamforge-node >/dev/null 2>&1 || useradd --system --home /var/lib/streamforge-node --shell /usr/sbin/nologin streamforge-node
# STREAMFORGE_NODE_YOUTUBE_COOKIE_OWNER_SELFHEAL_V1014:
if [[ -f /var/lib/streamforge-node/youtube-cookies.txt ]]; then
  chown streamforge-node:streamforge-node /var/lib/streamforge-node/youtube-cookies.txt || true
  chmod 0600 /var/lib/streamforge-node/youtube-cookies.txt || true
fi
old_env=/etc/streamforge-node.env
read_old(){ local key="$1"; [[ -f "$old_env" ]] && grep -E "^${key}=" "$old_env" | tail -1 | cut -d= -f2- || true; }
GEOIP_AUTO_UPDATE="$(read_old STREAMFORGE_GEOIP_AUTO_UPDATE)"; GEOIP_AUTO_UPDATE="${GEOIP_AUTO_UPDATE:-0}"
MAXMIND_ACCOUNT_ID="$(read_old STREAMFORGE_MAXMIND_ACCOUNT_ID)"
MAXMIND_LICENSE_KEY="$(read_old STREAMFORGE_MAXMIND_LICENSE_KEY)"
ACME_DNS_API="$(read_old STREAMFORGE_ACME_DNS_API)"; ACME_DNS_API="${ACME_DNS_API:-https://auth.acme-dns.io}"
# STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_DURABLE_STORAGE_V1011:
OLD_WEBPLAYER_DOWNLOAD_FILE="$(read_old STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_FILE)"
OLD_WEBPLAYER_DOWNLOAD_FILE="${OLD_WEBPLAYER_DOWNLOAD_FILE:-/var/lib/streamforge-node/webplayer-downloads/managed-download.bin}"
case "$OLD_WEBPLAYER_DOWNLOAD_FILE" in /*) ;; *) OLD_WEBPLAYER_DOWNLOAD_FILE="/opt/streamforge-node/$OLD_WEBPLAYER_DOWNLOAD_FILE" ;; esac
NODE_WEBPLAYER_DOWNLOAD_FILE=/var/lib/streamforge-node/webplayer-downloads/managed-download.bin
mkdir -p /opt/streamforge-node/logo /var/lib/streamforge-node/hls /var/lib/streamforge-node/channel-logos /var/lib/streamforge-node/webplayer-downloads /opt/streamforge /opt/streamforge/scripts
install -d -o streamforge-node -g streamforge-node -m 0755 /var/lib/streamforge-node/webplayer-downloads
# STREAMFORGE_NODE_SELF_HOSTED_HLSJS_INSTALL_V1163:
bash "$SOURCE_DIR/scripts/fetch_hlsjs.sh" /var/lib/streamforge-node/hls.min.js || true
if [[ -f /var/lib/streamforge-node/hls.min.js ]]; then
  chown streamforge-node:streamforge-node /var/lib/streamforge-node/hls.min.js || true
  chmod 0644 /var/lib/streamforge-node/hls.min.js || true
fi
BACKUP_DIR="/var/backups/streamforge-node/pre-gunicorn-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
if [[ -f /opt/streamforge-node/app.py ]]; then
  mkdir -p "$BACKUP_DIR/app"
  tar -C /opt/streamforge-node --exclude='./venv' -cf - . | tar -C "$BACKUP_DIR/app" -xf -
fi
[[ -f /etc/streamforge-node.env ]] && cp -a /etc/streamforge-node.env "$BACKUP_DIR/streamforge-node.env"
if [[ -f "$OLD_WEBPLAYER_DOWNLOAD_FILE" ]]; then
  install -m 0644 "$OLD_WEBPLAYER_DOWNLOAD_FILE" "$BACKUP_DIR/webplayer-managed-download.bin"
fi
[[ -f /etc/systemd/system/streamforge-node.service ]] && cp -a /etc/systemd/system/streamforge-node.service "$BACKUP_DIR/streamforge-node.service"
[[ -f /etc/systemd/system/streamforge-node-public.service ]] && cp -a /etc/systemd/system/streamforge-node-public.service "$BACKUP_DIR/streamforge-node-public.service"
[[ -f /etc/systemd/system/streamforge-node-tls.service ]] && cp -a /etc/systemd/system/streamforge-node-tls.service "$BACKUP_DIR/streamforge-node-tls.service"
[[ -f /etc/systemd/system/streamforge-node-tls.path ]] && cp -a /etc/systemd/system/streamforge-node-tls.path "$BACKUP_DIR/streamforge-node-tls.path"
[[ -f /etc/systemd/system/streamforge-node-tls.timer ]] && cp -a /etc/systemd/system/streamforge-node-tls.timer "$BACKUP_DIR/streamforge-node-tls.timer"
[[ -f /etc/nginx/sites-available/streamforge-node-tls ]] && cp -a /etc/nginx/sites-available/streamforge-node-tls "$BACKUP_DIR/streamforge-node-tls.nginx"
[[ -d /usr/local/libexec/streamforge-node ]] && cp -a /usr/local/libexec/streamforge-node "$BACKUP_DIR/streamforge-node-libexec"
if systemctl is-enabled --quiet streamforge-node >/dev/null 2>&1 || systemctl is-active --quiet streamforge-node >/dev/null 2>&1; then
  touch "$BACKUP_DIR/should-start"
fi
rollback_node_install(){
  local rc=$?
  trap - ERR INT TERM
  echo "Node installation failed; restoring the previous runtime from $BACKUP_DIR" >&2
  systemctl stop streamforge-node-public >/dev/null 2>&1 || true
  systemctl stop streamforge-node >/dev/null 2>&1 || true
  find /opt/streamforge-node -mindepth 1 -maxdepth 1 ! -name venv -exec rm -rf -- {} +
  if [[ -d "$BACKUP_DIR/app" ]]; then
    cp -a "$BACKUP_DIR/app/." /opt/streamforge-node/
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node.env" ]]; then
    install -m 0640 "$BACKUP_DIR/streamforge-node.env" /etc/streamforge-node.env
  else
    rm -f /etc/streamforge-node.env
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node.service" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-node.service" /etc/systemd/system/streamforge-node.service
  else
    rm -f /etc/systemd/system/streamforge-node.service
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node-public.service" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-node-public.service" /etc/systemd/system/streamforge-node-public.service
  else
    rm -f /etc/systemd/system/streamforge-node-public.service
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node-tls.service" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-node-tls.service" /etc/systemd/system/streamforge-node-tls.service
  else
    rm -f /etc/systemd/system/streamforge-node-tls.service
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node-tls.path" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-node-tls.path" /etc/systemd/system/streamforge-node-tls.path
  else
    rm -f /etc/systemd/system/streamforge-node-tls.path
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node-tls.timer" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-node-tls.timer" /etc/systemd/system/streamforge-node-tls.timer
  else
    rm -f /etc/systemd/system/streamforge-node-tls.timer
  fi
  rm -rf /usr/local/libexec/streamforge-node
  if [[ -d "$BACKUP_DIR/streamforge-node-libexec" ]]; then
    cp -a "$BACKUP_DIR/streamforge-node-libexec" /usr/local/libexec/streamforge-node
  fi
  if [[ -f "$BACKUP_DIR/streamforge-node-tls.nginx" ]]; then
    install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
    install -m 0644 "$BACKUP_DIR/streamforge-node-tls.nginx" /etc/nginx/sites-available/streamforge-node-tls
    ln -sfn /etc/nginx/sites-available/streamforge-node-tls /etc/nginx/sites-enabled/streamforge-node-tls
  else
    rm -f /etc/nginx/sites-enabled/streamforge-node-tls /etc/nginx/sites-available/streamforge-node-tls
  fi
  # STREAMFORGE_NODE_LOGO_PATH_V2180: keep Node logo data under the application tree.
install -d -o streamforge-node -g streamforge-node /opt/streamforge-node/logo
for legacy_logo_dir in /var/lib/streamforge-node/node-logos /var/lib/streamforge-node/logo; do
  if [[ -d "$legacy_logo_dir" ]]; then
    find "$legacy_logo_dir" -maxdepth 1 -type f -print0 2>/dev/null | while IFS= read -r -d '' logo_file; do
      base="$(basename "$logo_file")"
      if [[ ! -e "/opt/streamforge-node/logo/$base" ]]; then
        mv "$logo_file" "/opt/streamforge-node/logo/$base"
      fi
    done
  fi
done
chown -R streamforge-node:streamforge-node /opt/streamforge-node/logo

install -m 0755 "$SOURCE_DIR/scripts/uninstall.sh" /usr/local/sbin/streamforge-uninstall
install -m 0755 "$SOURCE_DIR/scripts/cache_clear.sh" /usr/local/sbin/streamforge-cache-clear
systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ -f "$BACKUP_DIR/should-start" ]]; then
    systemctl enable streamforge-node >/dev/null 2>&1 || true
    systemctl restart streamforge-node >/dev/null 2>&1 || true
    if [[ -f "$BACKUP_DIR/streamforge-node-public.service" ]]; then
      systemctl enable streamforge-node-public >/dev/null 2>&1 || true
      systemctl restart streamforge-node-public >/dev/null 2>&1 || true
    fi
  fi
  exit "$rc"
}
trap rollback_node_install ERR INT TERM
# Stop an existing agent before replacing its files or changing its listener.
# `systemctl enable --now` alone does not restart an already-running service,
# which previously left the old STREAMFORGE_NODE_PORT active after SSH update.
# STREAMFORGE_NODE_PUBLIC_SYSTEMD_MIGRATION_V79: stop any existing dedicated
# public unit first.  On pre-v7.9 Nodes the old public pool is a child in the
# control cgroup and is killed when streamforge-node stops.
systemctl stop streamforge-node-public >/dev/null 2>&1 || true
systemctl stop streamforge-node >/dev/null 2>&1 || true
# STREAMFORGE_NODE_NATIVE_TLS_DEPENDENCIES_V39: on a standalone Remote Node,
# Nginx owns TLS ports only. Install it while the native port-80 Agent is
# stopped so Debian's first-start default site cannot collide with Gunicorn.
if [[ "$COLOCATED_MAIN" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
  NGINX_WAS_PRESENT=1
  command -v nginx >/dev/null 2>&1 || NGINX_WAS_PRESENT=0
  if [[ "$NGINX_WAS_PRESENT" -eq 0 ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx >/dev/null
  fi
  # STREAMFORGE_NODE_NGINX_UPDATE_UPTIME_V1032: the current Node architecture
  # binds control/public Python services to loopback backends (8810/8821), so
  # there is no reason to stop an already-working Nginx public frontend during
  # an SSH update.  Keeping it alive avoids Connection refused while services
  # are replaced.  A fresh distro default site is removed and replaced after
  # the backends pass their loopback health checks.
  rm -f /etc/nginx/sites-enabled/default
fi
CONTROL_BACKEND_PORT=8810
if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$CONTROL_BACKEND_PORT" 2>/dev/null | grep -q .; then
  echo "Internal Node control backend port $CONTROL_BACKEND_PORT is already occupied by another service." >&2
  ss -H -ltnp "sport = :$CONTROL_BACKEND_PORT" >&2 2>/dev/null || true
  # The old environment is still intact at this point; restore the previous
  # agent so a failed migration does not leave the Node offline.
  systemctl start streamforge-node >/dev/null 2>&1 || true
  exit 1
fi
# v1.11.125: run the Node Agent directly through /usr/bin/gunicorn with
# one ASGI worker. An existing venv is kept only until the new service passes
# its health check, then it is removed permanently.
find /opt/streamforge-node -mindepth 1 -maxdepth 1 ! -name venv -exec rm -rf -- {} +
cp -a "$SOURCE_DIR/node_agent/." /opt/streamforge-node/
# Preserve a previously uploaded Web Player app even when its legacy/custom path lived under /opt.
if [[ ! -f "$NODE_WEBPLAYER_DOWNLOAD_FILE" && -f "$BACKUP_DIR/webplayer-managed-download.bin" ]]; then
  install -o streamforge-node -g streamforge-node -m 0644 "$BACKUP_DIR/webplayer-managed-download.bin" "$NODE_WEBPLAYER_DOWNLOAD_FILE"
fi
requirements_hash(){
  python3 - "$1" <<'PY_REQ_HASH'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY_REQ_HASH
}

install_global_requirements(){
  local requirements_file="$1"
  local stamp_file="/var/lib/streamforge-node/.python-requirements.sha256"
  local wanted_hash current_stamp
  wanted_hash="$(requirements_hash "$requirements_file")"
  current_stamp="$(cat "$stamp_file" 2>/dev/null || true)"
  if [[ "$current_stamp" == "$wanted_hash" ]]; then
    echo "Node Python dependencies unchanged; skipping pip install."
    return 0
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -y >/dev/null
      apt-get install -y python3-pip >/dev/null
    else
      echo "python3-pip is required for the system Gunicorn Node runtime" >&2
      return 1
    fi
  fi
  local -a pip_args=(install --no-cache-dir --ignore-installed --upgrade -r "$requirements_file")
  if python3 -m pip help install 2>/dev/null | grep -q -- '--break-system-packages'; then
    pip_args=(install --break-system-packages --no-cache-dir --ignore-installed --upgrade -r "$requirements_file")
  fi
  python3 -m pip "${pip_args[@]}"
  printf '%s\n' "$wanted_hash" > "$stamp_file"
}
install_global_requirements /opt/streamforge-node/requirements.txt
# STREAMFORGE_NODE_ISOLATED_CERTBOT_INSTALL_V1030:
# StreamForge application dependencies are intentionally installed globally for
# the system Gunicorn runtime.  Never run distro Certbot in that same Python
# import path: Ubuntu/Debian may ship an older Certbot/acme/josepy stack whose
# dependencies conflict with StreamForge's modern cryptography/pyOpenSSL.  Keep
# the ACME client in a dedicated virtualenv and make the TLS helper prefer it.
STREAMFORGE_CERTBOT_VENV=/opt/streamforge-certbot
STREAMFORGE_CERTBOT_VERSION=5.7.0
ensure_streamforge_certbot(){
  local certbot_bin="$STREAMFORGE_CERTBOT_VENV/bin/certbot"
  local version_line=""
  if [[ -x "$certbot_bin" ]]; then
    version_line="$($certbot_bin --version 2>/dev/null || true)"
  fi
  if [[ "$version_line" != "certbot $STREAMFORGE_CERTBOT_VERSION" ]]; then
    if ! python3 -m venv --help >/dev/null 2>&1; then
      if command -v apt-get >/dev/null 2>&1; then
        apt-get update -y >/dev/null
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null
      fi
    fi
    rm -rf "$STREAMFORGE_CERTBOT_VENV"
    python3 -m venv "$STREAMFORGE_CERTBOT_VENV"
    "$STREAMFORGE_CERTBOT_VENV/bin/python" -m pip install --no-cache-dir --upgrade pip >/dev/null
    "$STREAMFORGE_CERTBOT_VENV/bin/python" -m pip install --no-cache-dir "certbot==$STREAMFORGE_CERTBOT_VERSION"
  fi
  [[ -x "$certbot_bin" ]] || { echo "StreamForge isolated Certbot executable was not installed" >&2; return 1; }
  version_line="$($certbot_bin --version 2>&1)" || { echo "StreamForge isolated Certbot compatibility check failed: $version_line" >&2; return 1; }
  [[ "$version_line" == "certbot $STREAMFORGE_CERTBOT_VERSION" ]] || { echo "Unexpected StreamForge Certbot version: $version_line" >&2; return 1; }
  echo "StreamForge isolated Certbot ready: $version_line"
}
ensure_streamforge_certbot
# Any backoff written by the broken pre-v10.30 system-Certbot import is local
# state, not a CA-issued rate-limit token.  The first root repair may clear it;
# the normal helper immediately re-applies CA-derived backoff on a real failure.
rm -f /var/lib/streamforge-node/tls/*.retry-after 2>/dev/null || true
if [[ ! -x /usr/bin/gunicorn ]] && command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall gunicorn >/dev/null
fi
if [[ ! -x /usr/bin/gunicorn ]]; then
  GUNICORN_BIN="$(command -v gunicorn || true)"
  [[ -n "$GUNICORN_BIN" ]] || { echo "Gunicorn executable was not installed" >&2; exit 1; }
  ln -sfn "$GUNICORN_BIN" /usr/bin/gunicorn
fi
/usr/bin/gunicorn --version
python3 - <<'PY_GLOBAL_RUNTIME'
import cryptography, fastapi, gunicorn, maxminddb, multipart, pydantic, redis, uvicorn, uvicorn_worker, yt_dlp, yt_dlp_ejs
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
print('system Gunicorn/ASGI Node dependencies ok')
PY_GLOBAL_RUNTIME
# STREAMFORGE_NODE_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025:
if command -v deno >/dev/null 2>&1 && python3 -c 'import yt_dlp, yt_dlp_ejs' >/dev/null 2>&1; then
  echo "YouTube runtime: READY ($(deno --version 2>/dev/null | head -1))"
else
  echo "WARNING: YouTube runtime is incomplete (Deno/yt-dlp/EJS); normal streams remain available."
fi
printf 'system-gunicorn-asgi\n' > /opt/streamforge-node/RUNTIME_MODE
cat > /etc/streamforge-node.env <<EOF
STREAMFORGE_NODE_TOKEN=$TOKEN
STREAMFORGE_NODE_PORT=$PORT
STREAMFORGE_NODE_CONTROL_BACKEND_PORT=8810
STREAMFORGE_NODE_PUBLIC_PORT=$PUBLIC_PORT
STREAMFORGE_NODE_PUBLIC_BACKEND_PORT=8821
STREAMFORGE_NODE_PUBLIC_WORKERS=auto
STREAMFORGE_NODE_PUBLIC_WORKERS_MAX=auto
STREAMFORGE_NODE_PUBLIC_MANAGED_BY_SYSTEMD=$PUBLIC_SYSTEMD_MANAGED
STREAMFORGE_NODE_REDIS_ENABLED=1
STREAMFORGE_NODE_REDIS_URL=redis://127.0.0.1:6379/1
STREAMFORGE_NODE_MEDIA_AUTH_CACHE_SECONDS=60
STREAMFORGE_NODE_MEDIA_HEARTBEAT_TTL=150
STREAMFORGE_NODE_MODE=control
STREAMFORGE_NODE_BIND=$BIND_ADDRESS
STREAMFORGE_NODE_ALLOWED_HOST=$HOSTNAME_VALUE
STREAMFORGE_NODE_DNS_ONLY=$DNS_ONLY
STREAMFORGE_NODE_STREAM_HOST=$PLAYLIST_HOST
STREAMFORGE_NODE_STREAM_DNS_ONLY=$PLAYLIST_DNS_ONLY
STREAMFORGE_NODE_FFMPEG=/usr/bin/ffmpeg
STREAMFORGE_NODE_NVENC_FFMPEG=/opt/ffmpeg-nvenc470/bin/ffmpeg
STREAMFORGE_NODE_GUNICORN=/usr/bin/gunicorn
STREAMFORGE_NODE_HLS_ROOT=/var/lib/streamforge-node/hls
STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_FILE=/var/lib/streamforge-node/webplayer-downloads/managed-download.bin
STREAMFORGE_NODE_STATE_FILE=/var/lib/streamforge-node/state.json
STREAMFORGE_NODE_USERS_FILE=/var/lib/streamforge-node/users.json
STREAMFORGE_NODE_PANEL_FILE=/var/lib/streamforge-node/panel.json
STREAMFORGE_NODE_PANEL_USERS_FILE=/var/lib/streamforge-node/panel-users.json
STREAMFORGE_NODE_ACCESS_FILE=/var/lib/streamforge-node/access.json
STREAMFORGE_NODE_LOG_FILE=/var/lib/streamforge-node/agent.log
STREAMFORGE_NODE_PUBLIC_LOG_FILE=/var/lib/streamforge-node/public-gateway.log
STREAMFORGE_NODE_VIEWERS_FILE=/var/lib/streamforge-node/viewers.json
STREAMFORGE_NODE_CHANNEL_LOGO_ROOT=/var/lib/streamforge-node/channel-logos
STREAMFORGE_NODE_LOGO_ROOT=/opt/streamforge-node/logo
STREAMFORGE_NODE_ASN_DB_PATH=/opt/streamforge/GeoLite2-ASN.mmdb
STREAMFORGE_NODE_COUNTRY_DB_PATH=/opt/streamforge/GeoLite2-Country.mmdb
STREAMFORGE_GEOIP_SETTINGS_FILE=/var/lib/streamforge-node/geoip-settings.json
STREAMFORGE_GEOIP_AUTO_UPDATE=$GEOIP_AUTO_UPDATE
STREAMFORGE_MAXMIND_ACCOUNT_ID=$MAXMIND_ACCOUNT_ID
STREAMFORGE_MAXMIND_LICENSE_KEY=$MAXMIND_LICENSE_KEY
STREAMFORGE_ACME_DNS_API=$ACME_DNS_API
STREAMFORGE_NODE_PANEL_URL=$PANEL_URL
STREAMFORGE_NODE_SLUG=$NODE_SLUG
STREAMFORGE_NODE_VIEWER_TTL=90
STREAMFORGE_NODE_PANEL_CACHE_SECONDS=8
STREAMFORGE_NODE_CPU_THREADS=2
STREAMFORGE_NODE_PROGRESS_INTERVAL=3
STREAMFORGE_NODE_VAAPI_DEVICE=/dev/dri/renderD128
STREAMFORGE_NODE_ACME_WEBROOT=/var/lib/streamforge-node/acme-webroot
STREAMFORGE_NODE_TLS_REQUEST_FILE=/var/lib/streamforge-node/tls-reconcile.request
STREAMFORGE_NODE_TLS_STATUS_FILE=/var/lib/streamforge-node/tls/status.json
EOF
if [[ ! -f /opt/streamforge/GeoLite2-ASN.mmdb ]]; then
  for legacy_asn in /var/lib/streamforge-node/GeoLite2-ASN.mmdb /var/lib/streamforge/GeoLite2-ASN.mmdb; do
    if [[ -f "$legacy_asn" ]]; then
      install -m 0644 "$legacy_asn" /opt/streamforge/GeoLite2-ASN.mmdb
      break
    fi
  done
fi
if [[ ! -f /opt/streamforge/GeoLite2-Country.mmdb ]]; then
  for legacy_country in /var/lib/streamforge-node/GeoLite2-Country.mmdb /var/lib/streamforge/GeoLite2-Country.mmdb; do
    if [[ -f "$legacy_country" ]]; then
      install -m 0644 "$legacy_country" /opt/streamforge/GeoLite2-Country.mmdb
      break
    fi
  done
fi
install -d -o root -g root -m 0755 /usr/local/libexec/streamforge-node
install -o root -g root -m 0755 "$SOURCE_DIR/node_agent/apply_node_tls.py" /usr/local/libexec/streamforge-node/apply_node_tls.py
install -o root -g root -m 0644 "$SOURCE_DIR/node_agent/acme_dns_client.py" /usr/local/libexec/streamforge-node/acme_dns_client.py
install -o root -g root -m 0755 "$SOURCE_DIR/node_agent/acme_dns_hook.py" /usr/local/libexec/streamforge-node/acme_dns_hook.py
install -m 0644 "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" /etc/systemd/system/streamforge-node.service
if [[ "$COLOCATED_MAIN" -eq 0 ]]; then
  # STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_INSTALL_V79: public traffic gets its
  # own lifecycle/cgroup and cannot be killed/restarted by control-plane work.
  install -m 0644 "$SOURCE_DIR/node_agent/deploy/streamforge-node-public.service" /etc/systemd/system/streamforge-node-public.service
  install -m 0644 "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.service" /etc/systemd/system/streamforge-node-tls.service
  install -m 0644 "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.path" /etc/systemd/system/streamforge-node-tls.path
  install -m 0644 "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.timer" /etc/systemd/system/streamforge-node-tls.timer
  install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy /var/lib/letsencrypt /var/log/letsencrypt
  cat > /etc/letsencrypt/renewal-hooks/deploy/streamforge-node-nginx-reload <<'HOOK'
#!/bin/sh
systemctl reload nginx >/dev/null 2>&1 || true
HOOK
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/streamforge-node-nginx-reload
else
  systemctl disable --now streamforge-node-public streamforge-node-tls.path streamforge-node-tls.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/streamforge-node-public.service /etc/systemd/system/streamforge-node-tls.service /etc/systemd/system/streamforge-node-tls.path /etc/systemd/system/streamforge-node-tls.timer
fi
install -m 0755 "$SOURCE_DIR/scripts/update_geoip_databases.sh" /opt/streamforge/scripts/update_geoip_databases.sh
install -m 0755 "$SOURCE_DIR/scripts/tune_high_concurrency.py" /usr/local/libexec/streamforge-node/tune_high_concurrency.py
if command -v redis-server >/dev/null 2>&1; then
  systemctl enable --now redis-server >/dev/null 2>&1 || true
fi
if command -v nginx >/dev/null 2>&1; then
  python3 /usr/local/libexec/streamforge-node/tune_high_concurrency.py >/dev/null 2>&1 || true
fi
install -m 0644 "$SOURCE_DIR/deploy/streamforge-geoip-update.service" /etc/systemd/system/streamforge-geoip-update.service
install -m 0644 "$SOURCE_DIR/deploy/streamforge-geoip-update.timer" /etc/systemd/system/streamforge-geoip-update.timer
usermod -aG video streamforge-node 2>/dev/null || true
getent group render >/dev/null && usermod -aG render streamforge-node || true
install -d -o streamforge-node -g streamforge-node -m 0755 /var/lib/streamforge-node/acme-webroot/.well-known/acme-challenge /var/lib/streamforge-node/tls /var/lib/streamforge-node/acme-dns
chown -R streamforge-node:streamforge-node /opt/streamforge-node /var/lib/streamforge-node
# Root executes the immutable copies under /usr/local/libexec/streamforge-node.
# The /opt copies are package sources only and are never executed by the root TLS service.
chmod 0755 /opt/streamforge-node/apply_node_tls.py /opt/streamforge-node/acme_dns_hook.py
chmod 0640 /etc/streamforge-node.env
systemctl daemon-reload
systemctl enable --now streamforge-geoip-update.timer >/dev/null 2>&1 || true
systemctl enable streamforge-node >/dev/null
systemctl restart streamforge-node
for attempt in $(seq 1 40); do
  if curl -fsS --max-time 2 -H "X-Node-Token: $TOKEN" "http://127.0.0.1:$CONTROL_BACKEND_PORT/api/v1/health" >/tmp/streamforge-node-health.json 2>/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 40 ]]; then
    journalctl -u streamforge-node -n 120 --no-pager >&2 || true
    exit 1
  fi
  sleep 1
done
# STREAMFORGE_NODE_DEPLOYED_VERSION_VERIFY_V1034: an SSH/root update must not
# report success while an older Node app is still serving. Verify the packaged
# Node version immediately after the authenticated control health check.
EXPECTED_NODE_VERSION="$(cat "$SOURCE_DIR/node_agent/VERSION" 2>/dev/null || true)"
LIVE_NODE_VERSION="$(python3 - <<'PY2'
import json
try:
    with open('/tmp/streamforge-node-health.json','r',encoding='utf-8') as handle:
        print(str((json.load(handle) or {}).get('version') or '').strip())
except Exception:
    print('')
PY2
)"
if [[ -z "$EXPECTED_NODE_VERSION" || "$LIVE_NODE_VERSION" != "$EXPECTED_NODE_VERSION" ]]; then
  echo "Node deployed-version mismatch: expected $EXPECTED_NODE_VERSION, control backend reported ${LIVE_NODE_VERSION:-unknown}" >&2
  exit 1
fi
if [[ "$COLOCATED_MAIN" -eq 0 ]]; then
  systemctl enable streamforge-node-public >/dev/null
  systemctl restart streamforge-node-public
  # STREAMFORGE_NODE_PUBLIC_HEALTH_READY_V80: app.py explicitly permits this
  # authenticated loopback health request in public mode. v7.9 rejected it as
  # a control route with HTTP 404 even though the 8821 workers were healthy.
  for attempt in $(seq 1 40); do
    if curl -fsS --max-time 2 -H "X-Node-Token: $TOKEN" "http://127.0.0.1:8821/api/v1/health" >/tmp/streamforge-node-public-health.json 2>/dev/null; then
      break
    fi
    if [[ "$attempt" -eq 40 ]]; then
      journalctl -u streamforge-node-public -n 120 --no-pager >&2 || true
      echo "Node Public service did not become ready on 127.0.0.1:8821" >&2
      false
    fi
    sleep 1
  done
  LIVE_PUBLIC_VERSION="$(python3 - <<'PY2'
import json
try:
    with open('/tmp/streamforge-node-public-health.json','r',encoding='utf-8') as handle:
        print(str((json.load(handle) or {}).get('version') or '').strip())
except Exception:
    print('')
PY2
)"
  if [[ "$LIVE_PUBLIC_VERSION" != "$EXPECTED_NODE_VERSION" ]]; then
    echo "Node deployed-version mismatch: expected $EXPECTED_NODE_VERSION, public backend reported ${LIVE_PUBLIC_VERSION:-unknown}" >&2
    exit 1
  fi
  echo "Node deployed version verified: $EXPECTED_NODE_VERSION"
  # STREAMFORGE_NODE_HTTP_FRONT_READY_BEFORE_TLS_V1032: ACME/DNS work must never
  # gate the native HTTP frontend.  First make Nginx serve port $PORT and verify
  # the authenticated health route through Nginx, then start best-effort TLS.
  if ! /usr/bin/python3 /usr/local/libexec/streamforge-node/apply_node_tls.py --bootstrap-http; then
    echo "Node Nginx HTTP frontend bootstrap failed" >&2
    nginx -t >&2 || true
    systemctl status nginx --no-pager -l >&2 2>/dev/null || true
    journalctl -u nginx -n 120 --no-pager >&2 2>/dev/null || true
    exit 1
  fi
  HTTP_FRONT_READY=0
  for attempt in $(seq 1 20); do
    if curl -fsS --max-time 2 -H "X-Node-Token: $TOKEN" "http://127.0.0.1:$PORT/api/v1/health" >/tmp/streamforge-node-http-front-health.json 2>/dev/null; then
      HTTP_FRONT_READY=1
      break
    fi
    sleep 1
  done
  if [[ "$HTTP_FRONT_READY" != "1" ]]; then
    echo "Node Nginx HTTP frontend did not become ready on 127.0.0.1:$PORT" >&2
    ss -lntp >&2 2>/dev/null || true
    nginx -t >&2 || true
    systemctl status nginx --no-pager -l >&2 2>/dev/null || true
    journalctl -u nginx -n 120 --no-pager >&2 2>/dev/null || true
    exit 1
  fi
  echo "Node Nginx HTTP frontend ready on port $PORT"

  # STREAMFORGE_NODE_NATIVE_TLS_TRIGGER_V39: TLS is best-effort and never
  # takes the authenticated native HTTP Agent offline when DNS/ACME is pending.
  systemctl enable --now streamforge-node-tls.path streamforge-node-tls.timer >/dev/null 2>&1 || true
  # STREAMFORGE_NODE_DISABLE_DISTRO_CERTBOT_TIMER_V1032: isolated Certbot is
  # renewed by streamforge-node-tls.timer; do not run the distro Certbot timer
  # that may still import an incompatible system Python dependency stack.
  systemctl disable --now certbot.timer >/dev/null 2>&1 || true
  # STREAMFORGE_NODE_TLS_RECONCILE_TRIGGER_RACE_FIX_V78: systemd .path may
  # immediately run apply_node_tls.py, which intentionally unlinks the request.
  # Own a temporary file first and publish it atomically; do nothing to the
  # destination after rename, so an immediate unlink cannot abort installation.
  tls_request_tmp="$(mktemp /var/lib/streamforge-node/.tls-reconcile.request.XXXXXX)"
  printf '%s\n' "$(date +%s)" > "$tls_request_tmp"
  chown streamforge-node:streamforge-node "$tls_request_tmp"
  chmod 0644 "$tls_request_tmp"
  mv -f "$tls_request_tmp" /var/lib/streamforge-node/tls-reconcile.request
  systemctl start streamforge-node-tls.service >/dev/null 2>&1 || true
  curl -fsS --max-time 2 -H "X-Node-Token: $TOKEN" "http://127.0.0.1:$PORT/api/v1/health" >/tmp/streamforge-node-health.json 2>/dev/null || true
fi
if [[ "$PUBLIC_PORT" != "$PORT" ]]; then
  read -r READY GATEWAY_MODE < <(python3 - <<'PY2'
import json
try:
    with open('/tmp/streamforge-node-health.json','r',encoding='utf-8') as handle:
        data=json.load(handle)
    gateway=data.get('public_gateway',{}) or {}
    print('1' if gateway.get('ready') else '0', str(gateway.get('mode') or ''))
except Exception:
    print('0 unknown')
PY2
)
  if [[ "$READY" != "1" ]]; then
    if [[ "$GATEWAY_MODE" == "managed-node-tls-proxy" || "$GATEWAY_MODE" == "managed-node-tls-public-pool" ]]; then
      echo "Managed TLS on port $PUBLIC_PORT is pending; native HTTP control remains online." >&2
      [[ -f /var/lib/streamforge-node/tls/status.json ]] && cat /var/lib/streamforge-node/tls/status.json >&2 || true
    else
      echo "Playlist/API listener did not start on port $PUBLIC_PORT" >&2
      tail -n 40 /var/lib/streamforge-node/public-gateway.log >&2 2>/dev/null || true
      exit 1
    fi
  fi
fi
rm -f /tmp/streamforge-node-health.json /tmp/streamforge-node-public-health.json /tmp/streamforge-node-http-front-health.json
systemctl is-active --quiet streamforge-node
if [[ "$COLOCATED_MAIN" -eq 0 ]]; then
  systemctl is-active --quiet streamforge-node-public
fi
rm -rf /opt/streamforge-node/venv
trap - ERR INT TERM
printf '\nStreamForge Node Agent installed.\n'
if [[ -n "$HOSTNAME_VALUE" ]]; then
  printf 'API URL: http://%s:%s\n' "$HOSTNAME_VALUE" "$PORT"
else
  printf 'API URL: http://%s:%s\n' "$(hostname -I | awk '{print $1}')" "$PORT"
fi
printf 'Playlist/API port: %s\n' "$PUBLIC_PORT"
printf 'API token: %s\n' "$TOKEN"
printf 'Panel DNS-only access: %s\n' "$DNS_ONLY"
printf 'Playlist/App host: %s\n' "${PLAYLIST_HOST:-same as panel}"
printf 'Playlist/App DNS-only access: %s\n' "$PLAYLIST_DNS_ONLY"
if [[ "$COLOCATED_MAIN" -eq 0 ]]; then
  printf 'Managed Node TLS: enabled (certificate provisioning is best-effort; see /var/lib/streamforge-node/tls/status.json)\n'
fi
printf 'Add these values in StreamForge → Nodes.\n'
