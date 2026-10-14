#!/usr/bin/env python3
from pathlib import Path

p = Path('scripts/force_update_v1246.sh')
if not p.exists():
    raise SystemExit('missing scripts/force_update_v1246.sh')
text = p.read_text(encoding='utf-8')
required = [
    'STREAMFORGE_V1246_RELAY_GUARD_COMPAT',
    'STREAMFORGE_RELAY_STATE_LOG_SUPPRESSION_V1245',
    'fresh relay access-log filter missing',
    'dynamic relay access-log filter missing',
    'log(){',
    'die(){',
]
for marker in required:
    if marker not in text:
        raise SystemExit(f'missing expected updater compatibility marker: {marker}')
if "STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036' \"$SOURCE_DIR/deploy/nginx.conf\"" in text:
    raise SystemExit('stale v10.68 fresh relay marker guard still enforced by v12.46 wrapper')
print('OK: v12.46 updater rewrites stale relay marker guards and owns wrapper log helpers')
