#!/usr/bin/env python3
from pathlib import Path

MAIN = Path('app/static/dashboard.js').read_text(encoding='utf-8')
NODE = Path('node_agent/app.py').read_text(encoding='utf-8')

MARKER = 'STREAMFORGE_ADAPTIVE_DASHBOARD_DATETIME_V1244'
REQUIRED = (
    'formatDashboardAxisTime',
    'formatDashboardTooltipTime',
    "month:'short'",
    "year:'numeric'",
)

errors = []
for name, text in [('Main', MAIN), ('Node', NODE)]:
    if MARKER not in text:
        errors.append(f'{name}: missing {MARKER}')
    for token in REQUIRED:
        if token not in text:
            errors.append(f'{name}: missing {token}')

# The old time-only tooltip must no longer be the chart hover title on Main.
old_main = "new Date(Number(points[hover].time)*1000).toLocaleTimeString"
if old_main in MAIN:
    errors.append('Main: hover tooltip is still time-only')

if errors:
    raise SystemExit('dashboard datetime parity guard FAILED:\n - ' + '\n - '.join(errors))

print('dashboard datetime parity guard OK: Main + Node adaptive date/time markers present')
