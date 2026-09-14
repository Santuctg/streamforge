#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install.sh"
  exit 1
fi

APP_DIR=/opt/streamforge
DATA_DIR=/var/lib/streamforge
ENV_FILE=/etc/streamforge.env
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_VERSION="$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo "unknown")"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv ffmpeg nginx rsync openssl curl unzip ca-certificates gunicorn smbclient certbot dnsutils geoipupdate
bash "$SOURCE_DIR/scripts/bootstrap_youtube_runtime.sh" || true
# STREAMFORGE_REDIS_INSTALL_V61: Redis is loopback runtime state for playback/session sharing.
if ! command -v redis-server >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server >/dev/null 2>&1 || echo "WARNING: redis-server install failed; StreamForge will use local fallback state."
fi
if command -v redis-server >/dev/null 2>&1; then
  systemctl enable --now redis-server >/dev/null 2>&1 || true
fi

id -u streamforge >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin streamforge
getent group video >/dev/null 2>&1 && usermod -aG video streamforge || true
getent group render >/dev/null 2>&1 && usermod -aG render streamforge || true
mkdir -p "$APP_DIR" "$DATA_DIR/hls" /opt/streamforge/logo
install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/gunicorn-tmp"
install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/main-access-runtime"
# STREAMFORGE_WEBPLAYER_DOWNLOAD_DURABLE_STORAGE_V1011:
install -d -o streamforge -g streamforge -m 0755 "$DATA_DIR/webplayer-downloads"
install -d -m 0755 "$DATA_DIR/acme-webroot" "$DATA_DIR/tls" "$DATA_DIR/acme-dns"
install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/main-system-runtime"
install -d -o streamforge -g streamforge -m 0700 "$DATA_DIR/restore-inbox"
install -d -o streamforge -g streamforge -m 0700 "$DATA_DIR/.ssh"
touch "$DATA_DIR/.ssh/known_hosts"
chown streamforge:streamforge "$DATA_DIR/.ssh/known_hosts"
chmod 0600 "$DATA_DIR/.ssh/known_hosts"
install -d -o root -g root -m 0750 /var/backups/streamforge
rsync -a --delete --exclude venv --exclude streamforge.db --exclude logo/ --exclude GeoLite2-ASN.mmdb --exclude GeoLite2-Country.mmdb "$SOURCE_DIR/" "$APP_DIR/"
requirements_hash(){
  python3 - "$1" <<'PY_REQ_HASH'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY_REQ_HASH
}

MAIN_REQUIREMENTS_STAMP="$DATA_DIR/.python-requirements.sha256"
MAIN_REQUIREMENTS_HASH="$(requirements_hash "$APP_DIR/requirements.txt")"
CURRENT_MAIN_REQUIREMENTS_HASH="$(cat "$MAIN_REQUIREMENTS_STAMP" 2>/dev/null || true)"
if [[ "$CURRENT_MAIN_REQUIREMENTS_HASH" == "$MAIN_REQUIREMENTS_HASH" ]]; then
  echo "Main Python dependencies unchanged; skipping pip install."
else
  PIP_INSTALL_ARGS=(install --no-cache-dir --ignore-installed --upgrade -r "$APP_DIR/requirements.txt")
  if python3 -m pip help install 2>/dev/null | grep -q -- '--break-system-packages'; then
    PIP_INSTALL_ARGS=(install --break-system-packages --no-cache-dir --ignore-installed --upgrade -r "$APP_DIR/requirements.txt")
  fi
  python3 -m pip "${PIP_INSTALL_ARGS[@]}"
  printf '%s\n' "$MAIN_REQUIREMENTS_HASH" > "$MAIN_REQUIREMENTS_STAMP"
fi
if [[ ! -x /usr/bin/gunicorn ]] && command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall gunicorn >/dev/null
fi
if [[ ! -x /usr/bin/gunicorn ]]; then
  GUNICORN_BIN="$(command -v gunicorn || true)"
  [[ -n "$GUNICORN_BIN" ]] || { echo "Gunicorn executable was not installed" >&2; exit 1; }
  ln -sfn "$GUNICORN_BIN" /usr/bin/gunicorn
fi
/usr/bin/gunicorn --version
python3 - <<'PY_GLOBAL_MAIN'
import cryptography, fastapi, gunicorn, itsdangerous, jinja2, maxminddb, multipart, paramiko, redis, sqlalchemy, uvicorn, uvicorn_worker, yt_dlp, yt_dlp_ejs
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
print('system Gunicorn/ASGI Main dependencies ok')
PY_GLOBAL_MAIN
# STREAMFORGE_MAIN_ISOLATED_CERTBOT_INSTALL_V1030: keep Certbot and its ACME
# dependencies out of StreamForge's globally-upgraded application site-packages.
STREAMFORGE_CERTBOT_VENV=/opt/streamforge-certbot
STREAMFORGE_CERTBOT_VERSION=5.7.0
ensure_streamforge_certbot(){
  local certbot_bin="$STREAMFORGE_CERTBOT_VENV/bin/certbot"
  local version_line=""
  if [[ -x "$certbot_bin" ]]; then
    version_line="$($certbot_bin --version 2>/dev/null || true)"
  fi
  if [[ "$version_line" != "certbot $STREAMFORGE_CERTBOT_VERSION" ]]; then
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
# STREAMFORGE_MAIN_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025:
if command -v deno >/dev/null 2>&1 && python3 -c 'import yt_dlp, yt_dlp_ejs' >/dev/null 2>&1; then
  echo "YouTube runtime: READY ($(deno --version 2>/dev/null | head -1))"
else
  echo "WARNING: YouTube runtime is incomplete (Deno/yt-dlp/EJS); normal streams remain available."
fi
printf 'system-gunicorn-asgi\n' > "$APP_DIR/RUNTIME_MODE"

# Self-host HLS.js for Chrome/Edge/Firefox playback; browser CDN fallback remains available.
bash "$APP_DIR/scripts/fetch_hlsjs.sh" "$APP_DIR/app/static/vendor/hls.min.js" || true

# STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_PUBLISH_V122:
# Nginx normally runs as www-data and must not depend on traverse permission
# through the private /opt/streamforge application tree. Publish only the
# browser-safe static tree into a root-owned, world-readable cache.
MAIN_STATIC_DIR=/var/cache/streamforge/main-static
install -d -o root -g root -m 0755 "$MAIN_STATIC_DIR"
rsync -a --delete --chmod=D755,F644 "$APP_DIR/app/static/" "$MAIN_STATIC_DIR/"
chown -R root:root "$MAIN_STATIC_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  SECRET=$(openssl rand -hex 32)
  cat > "$ENV_FILE" <<EOF
STREAMFORGE_SECRET_KEY=$SECRET
STREAMFORGE_ADMIN_USER=admin
STREAMFORGE_ADMIN_PASSWORD=ChangeMe123!
STREAMFORGE_DATABASE_URL=sqlite:////var/lib/streamforge/streamforge.db
STREAMFORGE_HLS_ROOT=/var/lib/streamforge/hls
STREAMFORGE_WEBPLAYER_DOWNLOAD_ROOT=/var/lib/streamforge/webplayer-downloads
STREAMFORGE_LOGO_ROOT=/opt/streamforge/logo
STREAMFORGE_NODE_LOGO_ROOT=/opt/streamforge/logo
STREAMFORGE_FFMPEG_BIN=/usr/bin/ffmpeg
STREAMFORGE_NVENC_FFMPEG_BIN=/opt/ffmpeg-nvenc470/bin/ffmpeg
STREAMFORGE_FFPROBE_BIN=/usr/bin/ffprobe
STREAMFORGE_FFPROBE_TIMEOUT=10
STREAMFORGE_FFPROBE_ANALYZEDURATION=8000000
STREAMFORGE_FFPROBE_PROBESIZE=20000000
STREAMFORGE_PUBLIC_BASE_URL=http://$(hostname -I | awk '{print $1}')
STREAMFORGE_RELAY_BASE_URL=http://$(hostname -I | awk '{print $1}')
STREAMFORGE_RELAY_START_WAIT_SECONDS=20
STREAMFORGE_CPU_ENCODE_THREADS=2
STREAMFORGE_PROGRESS_INTERVAL=3
STREAMFORGE_VAAPI_DEVICE=/dev/dri/renderD128
STREAMFORGE_HTTP_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 StreamForge/$APP_VERSION
STREAMFORGE_HTTP_RW_TIMEOUT_US=15000000
STREAMFORGE_HTTP_RECONNECT_DELAY_MAX=10
STREAMFORGE_AUTO_RESTART_STALL_SECONDS=30
STREAMFORGE_AUTO_RESTART_CHECK_INTERVAL=5
STREAMFORGE_FAILBACK_PROBE_TIMEOUT=5
STREAMFORGE_LOG_PAGE_LIMIT=500
STREAMFORGE_VIEWER_KEY_TTL_SECONDS=43200
STREAMFORGE_RESTREAM_KEY_TTL_SECONDS=86400
STREAMFORGE_REDIS_ENABLED=1
STREAMFORGE_REDIS_URL=redis://127.0.0.1:6379/0
STREAMFORGE_REDIS_PREFIX=streamforge
STREAMFORGE_REDIS_TIMEOUT_MS=150
STREAMFORGE_REDIS_TOUCH_INTERVAL_MS=1000
STREAMFORGE_REDIS_FAILURE_BACKOFF_SECONDS=5
STREAMFORGE_PUBLIC_WORKERS=auto
STREAMFORGE_PUBLIC_MAX_WORKERS=auto
STREAMFORGE_ASN_DB_PATH=/opt/streamforge/GeoLite2-ASN.mmdb
STREAMFORGE_COUNTRY_DB_PATH=/opt/streamforge/GeoLite2-Country.mmdb
STREAMFORGE_GEOIP_SETTINGS_FILE=/opt/streamforge/geoip-settings.json
STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR=/var/lib/streamforge/main-access-runtime
STREAMFORGE_TIMEZONE=Asia/Dhaka
STREAMFORGE_GEOIP_AUTO_UPDATE=0
STREAMFORGE_MAXMIND_ACCOUNT_ID=
STREAMFORGE_MAXMIND_LICENSE_KEY=
STREAMFORGE_ACME_DNS_API=https://auth.acme-dns.io
EOF
  chmod 600 "$ENV_FILE"
fi

grep -q '^STREAMFORGE_ACME_DNS_API=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_ACME_DNS_API=https://auth.acme-dns.io' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_ENABLED=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_ENABLED=1' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_URL=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_URL=redis://127.0.0.1:6379/0' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_PREFIX=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_PREFIX=streamforge' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_TIMEOUT_MS=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_TIMEOUT_MS=150' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_TOUCH_INTERVAL_MS=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_TOUCH_INTERVAL_MS=1000' >> "$ENV_FILE"
grep -q '^STREAMFORGE_REDIS_FAILURE_BACKOFF_SECONDS=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_REDIS_FAILURE_BACKOFF_SECONDS=5' >> "$ENV_FILE"
grep -q '^STREAMFORGE_PUBLIC_WORKERS=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_PUBLIC_WORKERS=auto' >> "$ENV_FILE"
grep -q '^STREAMFORGE_PUBLIC_MAX_WORKERS=' "$ENV_FILE" || printf '%s\n' 'STREAMFORGE_PUBLIC_MAX_WORKERS=auto' >> "$ENV_FILE"

chown -R streamforge:streamforge "$APP_DIR" "$DATA_DIR"
chown -R streamforge:streamforge /opt/streamforge/logo
chown streamforge:streamforge "$DATA_DIR/gunicorn-tmp"
chmod 0750 "$DATA_DIR/gunicorn-tmp"

# Verify Gunicorn can use its dedicated worker temp directory before service start.
runuser -u streamforge -- env TMPDIR="$DATA_DIR/gunicorn-tmp" python3 - <<'PY_GUNICORN_TMP'
import os, tempfile
root = os.environ["TMPDIR"]
fd, path = tempfile.mkstemp(prefix="streamforge-worker-", dir=root)
os.close(fd)
os.unlink(path)
print("Gunicorn worker temp directory verified")
PY_GUNICORN_TMP

sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge.service" > /etc/systemd/system/streamforge.service
chmod 0644 /etc/systemd/system/streamforge.service
sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge-channel-supervisor.service" > /etc/systemd/system/streamforge-channel-supervisor.service
chmod 0644 /etc/systemd/system/streamforge-channel-supervisor.service
sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge-public.service" > /etc/systemd/system/streamforge-public.service
chmod 0644 /etc/systemd/system/streamforge-public.service
chmod 0755 "$APP_DIR/scripts/streamforge-public-start"

# Privileged Main web-listener configuration handoff.
install -o root -g root -m 0755 "$APP_DIR/scripts/apply_main_access.py" /usr/local/sbin/streamforge-apply-main-access
install -d -o root -g root -m 0755 /usr/local/libexec/streamforge
install -o root -g root -m 0644 "$APP_DIR/scripts/acme_dns_client.py" /usr/local/libexec/streamforge/acme_dns_client.py
install -o root -g root -m 0755 "$APP_DIR/scripts/acme_dns_hook.py" /usr/local/libexec/streamforge/acme_dns_hook.py
mkdir -p /etc/letsencrypt/renewal-hooks/deploy /var/lib/letsencrypt /var/log/letsencrypt
install -o root -g root -m 0755 "$APP_DIR/scripts/streamforge_tls_renew_hook.sh" /etc/letsencrypt/renewal-hooks/deploy/streamforge-nginx-reload
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
sed -e "s#/var/lib/streamforge#$DATA_DIR#g" -e "s#/etc/streamforge.env#$ENV_FILE#g" "$APP_DIR/deploy/streamforge-main-access.service" > /etc/systemd/system/streamforge-main-access.service
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-access.path" > /etc/systemd/system/streamforge-main-access.path
install -m 0644 "$APP_DIR/deploy/streamforge-main-tls.timer" /etc/systemd/system/streamforge-main-tls.timer
chmod 0644 /etc/systemd/system/streamforge-main-access.service /etc/systemd/system/streamforge-main-access.path /etc/systemd/system/streamforge-main-tls.timer
rm -f /etc/sudoers.d/streamforge-main-access
rm -f "$DATA_DIR/main-access-runtime/request.json" "$DATA_DIR/main-access-runtime/result.json"

# Privileged Main system-control handoff used by restore/restart actions.
install -o root -g root -m 0755 "$APP_DIR/scripts/main_system_control.py" /usr/local/sbin/streamforge-main-system-control
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-system.service" > /etc/systemd/system/streamforge-main-system.service
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-system.path" > /etc/systemd/system/streamforge-main-system.path
chmod 0644 /etc/systemd/system/streamforge-main-system.service /etc/systemd/system/streamforge-main-system.path
rm -f "$DATA_DIR/main-system-runtime/request.json" "$DATA_DIR/main-system-runtime/result.json"

# STREAMFORGE_FRESH_INSTALL_GEOIP_SELF_COPY_V308: APP_DIR is already
# /opt/streamforge after rsync, so copying this file onto itself makes GNU
# install abort a brand-new installation. The payload is already in place.
chmod 0755 "$APP_DIR/scripts/update_geoip_databases.sh"
install -m 0755 "$APP_DIR/scripts/streamforge-capacity-audit" /usr/local/bin/streamforge-capacity-audit
install -m 0644 "$APP_DIR/deploy/streamforge-geoip-update.service" /etc/systemd/system/streamforge-geoip-update.service
install -m 0644 "$APP_DIR/deploy/streamforge-geoip-update.timer" /etc/systemd/system/streamforge-geoip-update.timer

install -m 0644 "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/streamforge
ln -sfn /etc/nginx/sites-available/streamforge /etc/nginx/sites-enabled/streamforge
rm -f /etc/nginx/sites-enabled/default
python3 "$APP_DIR/scripts/tune_high_concurrency.py"
nginx -t

install -m 0755 "$APP_DIR/scripts/uninstall.sh" /usr/local/sbin/streamforge-uninstall
install -m 0755 "$APP_DIR/scripts/cache_clear.sh" /usr/local/sbin/streamforge-cache-clear
# STREAMFORGE_AUTO_UPDATE_COMMAND_INSTALL_V3011:
install -m 0755 "$APP_DIR/scripts/streamforge-update" /usr/local/bin/streamforge-update
# STREAMFORGE_RESETUSER_COMMAND_INSTALL_V3015:
install -m 0755 "$APP_DIR/scripts/streamforge" /usr/local/bin/streamforge
systemctl daemon-reload
systemctl enable --now nginx
systemctl enable --now streamforge-main-access.path streamforge-main-tls.timer
systemctl enable --now streamforge-main-system.path
systemctl enable --now streamforge-geoip-update.timer >/dev/null 2>&1 || true
# STREAMFORGE_MAIN_SUPERVISOR_FRESH_INSTALL_V1115
systemctl enable --now streamforge-channel-supervisor
systemctl enable --now streamforge
nginx -t
systemctl reload nginx
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8800/health >/dev/null 2>&1 || curl -fsS --max-time 2 http://127.0.0.1:8800/login >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    journalctl -u streamforge -n 160 --no-pager >&2 || true
    exit 1
  fi
  sleep 1
done

# STREAMFORGE_PUBLIC_FRESH_INSTALL_V62: start the isolated Public plane only
# after the single Main control worker has initialized the database.
systemctl enable --now streamforge-public
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8811/health 2>/dev/null | grep -qx ok; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    journalctl -u streamforge-public -n 120 --no-pager >&2 || true
    exit 1
  fi
  sleep 1
done

# The first application start creates/migrates the DB and Local Server row.
# Now generate the authoritative Nginx listener config from that live state.
STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR="$DATA_DIR/main-access-runtime" STREAMFORGE_DATABASE_PATH="$DATA_DIR/streamforge.db" /usr/local/sbin/streamforge-apply-main-access
nginx -t
systemctl is-active --quiet nginx
systemctl is-active --quiet streamforge-channel-supervisor
systemctl is-active --quiet streamforge
systemctl is-active --quiet streamforge-public
systemctl is-active --quiet streamforge-main-access.path
systemctl is-active --quiet streamforge-main-system.path

python3 "$APP_DIR/scripts/verify_main_install.py"   --app-dir "$APP_DIR"   --data-dir "$DATA_DIR"   --env-file "$ENV_FILE"   --nginx-site /etc/nginx/sites-available/streamforge

rm -rf "$APP_DIR/venv"

echo
echo "StreamForge installed."
echo "Open: http://$(hostname -I | awk '{print $1}')"
echo "Default login: admin / ChangeMe123!"
echo "Recovery commands: sudo streamforge resetuser | sudo streamforge resetdomain | sudo streamforge resetpanelaccess"
