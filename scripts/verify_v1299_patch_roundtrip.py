#!/usr/bin/env python3
"""v12.99 authenticated GitHub Main Web Update security contract."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.99':
    print('SKIP: v12.99 release round-trip')
    raise SystemExit(0)

main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
permissions = (ROOT / 'app/permissions.py').read_text(encoding='utf-8')
base = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
page = (ROOT / 'app/templates/main_updates.html').read_text(encoding='utf-8')
helper_text = (ROOT / 'scripts/main_system_control.py').read_text(encoding='utf-8')
unit = (ROOT / 'deploy/streamforge-main-system.service').read_text(encoding='utf-8')
compile(main, str(ROOT / 'app/main.py'), 'exec')
compile(helper_text, str(ROOT / 'scripts/main_system_control.py'), 'exec')

for marker in ('STREAMFORGE_MAIN_WEB_GITHUB_UPDATE_V1299',
               '@app.get("/system/updates"', '@app.post("/system/updates/apply"',
               'permission_required("main_system.update")',
               '_write_main_system_request(', 'target_version=latest["version"]'):
    assert marker in main
assert 'main_system.update' in permissions
assert 'Main Updates' in base and '/system/updates' in base
assert '/system/updates/apply' in page and 'SHA256-verified' in page

for marker in ('STREAMFORGE_MAIN_WEB_GITHUB_UPDATE_HELPER_V1299',
               'GITHUB_RELEASES_API', 'browser_download_url',
               'sha256:[0-9a-f]{64}', 'hashlib.sha256()',
               '/usr/local/bin/streamforge-update', 'update_release'):
    assert marker in helper_text
assert 'TimeoutStartSec=3600' in unit
assert '/usr/local/bin /usr/local/sbin /etc/systemd/system' in unit

# Exercise release ordering and trust checks without network access.
namespace = runpy.run_path(str(ROOT / 'scripts/main_system_control.py'))


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _limit=-1): return self.payload


def release(version):
    name = f'streamforge-{version}.zip'
    return {
        'tag_name': f'v{version}', 'draft': False, 'prerelease': False,
        'assets': [{
            'name': name, 'digest': 'sha256:' + ('a' * 64),
            'browser_download_url': f'https://github.com/Santuctg/streamforge/releases/download/v{version}/{name}',
        }],
    }

original = namespace['_github_update_plan'].__globals__['urllib'].request.urlopen
namespace['_github_update_plan'].__globals__['urllib'].request.urlopen = lambda *_a, **_k: Response([release('12.100'), release('12.99')])
try:
    plan = namespace['_github_update_plan']('12.98', '12.100')
    assert [item['version'] for item in plan] == ['12.99', '12.100']
finally:
    namespace['_github_update_plan'].__globals__['urllib'].request.urlopen = original

print('PASS: v12.99 Web Update routes, permission, GitHub digest trust, sequential plan and root helper sandbox')
