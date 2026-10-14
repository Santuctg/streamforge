#!/usr/bin/env python3
from pathlib import Path

updater = Path('scripts/force_update_v1247.sh')
if not updater.exists():
    raise SystemExit('missing scripts/force_update_v1247.sh')
text = updater.read_text(encoding='utf-8')
required = [
    'STREAMFORGE_RELAY_FALLBACK_LOG_FILTER_V1247',
    'location @streamforge_relay_fallback',
    'access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;',
    '/opt/streamforge/scripts/apply_main_access.py',
    '/etc/nginx/sites-available/streamforge',
    'nginx -t',
]
for marker in required:
    if marker not in text:
        raise SystemExit(f'missing v12.47 fallback log protection marker: {marker}')
print('OK: v12.47 updater permanently patches relay fallback conditional logging')
