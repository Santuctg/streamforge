#!/usr/bin/env python3
from pathlib import Path

BR='hotfix/v12.45-relay-log-protection'

# 1) Fresh Nginx template
p=Path('deploy/nginx.conf'); t=p.read_text()
old='''# STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036: relay HLS is high-frequency
# internal Node/FFmpeg traffic. Suppress successful 2xx/3xx relay requests from
# the normal Nginx access log while preserving 4xx/5xx relay failures.
map $status $streamforge_relay_access_loggable {
    ~^[45] 1;
    default 0;
}
'''
new='''# STREAMFORGE_RELAY_STATE_LOG_SUPPRESSION_V1245: relay HLS is high-frequency.
# 409 (restarting/waiting) and 410 (stopped) are expected channel-state replies,
# so never write them to the normal access log. Preserve other 4xx/5xx failures.
map $status $streamforge_relay_access_loggable {
    ~^409$ 0;
    ~^410$ 0;
    ~^[45] 1;
    default 0;
}
'''
if old not in t: raise SystemExit('deploy/nginx.conf old relay map not found')
p.write_text(t.replace(old,new,1))

# 2) Runtime renderer
p=Path('scripts/apply_main_access.py'); t=p.read_text()
old='''        "# STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036: keep only 4xx/5xx relay requests in the normal access log.\\n"
        "map $status $streamforge_relay_access_loggable {\\n"
        "    ~^[45] 1;\\n"
        "    default 0;\\n}\\n\\n"
'''
new='''        "# STREAMFORGE_RELAY_STATE_LOG_SUPPRESSION_V1245: suppress expected 409/410 relay states; keep other 4xx/5xx.\\n"
        "map $status $streamforge_relay_access_loggable {\\n"
        "    ~^409$ 0;\\n"
        "    ~^410$ 0;\\n"
        "    ~^[45] 1;\\n"
        "    default 0;\\n}\\n\\n"
'''
if old not in t: raise SystemExit('apply_main_access relay map not found')
p.write_text(t.replace(old,new,1))

# 3) Versions
Path('VERSION').write_text('12.45\n')
Path('node_agent/VERSION').write_text('12.45\n')

# 4) Direct updater: carry v12.44 schema fix, then provision bounded nginx log rotation.
base=Path('scripts/force_update_v1244.sh').read_text()
base=base.replace('v12.44','v12.45').replace('12.44','12.45').replace('v1244','v1245').replace('V1244','V1245').replace('PYV1244','PYV1245')
base=base.replace('bash "$STAGE/scripts/force_update_v1236.sh" "$@"\necho "[StreamForge v12.45] Update completed."', '''bash "$STAGE/scripts/force_update_v1236.sh" "$@"

# STREAMFORGE_NGINX_LOGROTATE_V1245: make high-volume Nginx logs bounded even
# on hosts where the distro logrotate package was previously absent.
if ! command -v logrotate >/dev/null 2>&1; then
    log "Installing logrotate for bounded Nginx log retention."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y logrotate
fi
cat >/etc/logrotate.d/nginx <<'EOFV1245'
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
EOFV1245
logrotate -d /etc/logrotate.d/nginx >/dev/null 2>&1 || die "Nginx logrotate configuration validation failed"
log "Nginx log rotation protection ready (daily/max 100M, 7 rotations)."

echo "[StreamForge v12.45] Update completed."''')
Path('scripts/force_update_v1245.sh').write_text(base)
Path('scripts/update.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/env bash "$SCRIPT_DIR/force_update_v1245.sh" "$@"
''')

# 5) Release notes
Path('release-notes/v12.45.md').write_text('''# StreamForge v12.45\n\n## Relay log flood protection\n\n- Stops expected relay `409` (restarting/waiting) and `410` (stopped) responses from filling `/var/log/nginx/access.log`.\n- Continues logging other 4xx and all 5xx relay failures for diagnostics.\n- Applies the policy to the fresh Nginx template and the Main runtime Nginx renderer.\n- Installs `logrotate` automatically when missing and writes bounded Nginx rotation: daily, `maxsize 100M`, 7 rotations, compression.\n- Carries forward the v12.44 dead-input schema-order updater protection and dashboard date/time changes.\n\n## Update\n\n```bash\nstreamforge-update --force /root/streamforge-12.45.zip\n```\n''')

# 6) Release sanity bump
p=Path('.github/workflows/release-sanity.yml'); t=p.read_text()
t=t.replace('12.44','12.45').replace('v12.44','v12.45').replace('v1244','v1245')
anchor='      - name: Verify Python syntax\n'
check='''      - name: Verify v12.45 relay log protection\n        run: python3 scripts/verify_relay_log_protection_v1245.py\n'''
if check not in t:
    if anchor not in t: raise SystemExit('release sanity anchor missing')
    t=t.replace(anchor,check+anchor,1)
p.write_text(t)

print('materialized v12.45 relay log protection release')
