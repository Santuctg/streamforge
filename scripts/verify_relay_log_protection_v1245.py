#!/usr/bin/env python3
from pathlib import Path

files = {
    'fresh nginx': Path('deploy/nginx.conf').read_text(encoding='utf-8'),
    'main renderer': Path('scripts/apply_main_access.py').read_text(encoding='utf-8'),
    'updater': Path('scripts/force_update_v1245.sh').read_text(encoding='utf-8') if Path('scripts/force_update_v1245.sh').exists() else '',
}

errors=[]
for name,text in files.items():
    if name == 'updater':
        continue
    if 'STREAMFORGE_RELAY_ACCESS_LOG_POLICY_V1247' in text:
        required = (
            '~^409:',
            '~^410:',
            '~^404:/relay/',
            'segment_',
            '~^499:/relay/',
            '~^[45][0-9][0-9]: 1;',
        )
        if any(token not in text for token in required):
            errors.append(f'{name}: v12.47 relay policy must suppress 409/410, stale segment 404 and relay 499 while retaining 503 diagnostics')
    elif '~^409$ 0;' not in text or '~^410$ 0;' not in text or '~^[45] 1;' not in text:
        errors.append(f'{name}: relay map must suppress 409/410 while retaining other 4xx/5xx logging')

updater=files['updater']
for token in ('STREAMFORGE_NGINX_LOGROTATE_V1245','maxsize 100M','rotate 7','compress','command -v logrotate'):
    if token not in updater:
        errors.append(f'updater: missing {token}')

if errors:
    raise SystemExit('v12.45 relay log protection FAILED:\n - '+'\n - '.join(errors))
print('OK: v12.45 suppresses relay 409/410 log flood and provisions bounded nginx log rotation')
