#!/usr/bin/env python3
"""v12.96 staged updater safety and Main dashboard node summary tests."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.96':
    print('SKIP: v12.96 updater test')
    raise SystemExit(0)
PACKAGE = Path(os.environ.get('STREAMFORGE_V1296_PACKAGE_ROOT', str(ROOT)))
SCRIPT = PACKAGE / 'scripts/force_update_v1296.sh'
FILES = ('app/main.py', 'app/templates/dashboard.html', 'app/templates/node_form.html',
         'app/templates/nodes.html', 'node_agent/app.py', 'VERSION', 'node_agent/VERSION')
text = SCRIPT.read_text()
assert 'STREAMFORGE_V1296_EXPIRED_BRANDING_UPDATER' in text
assert 'force_update_v1296.sh' in (PACKAGE / 'scripts/update.sh').read_text()
assert not re.search(r'systemctl\s+(restart|reload)\s+(nginx|streamforge-channel-supervisor)', text)


def tagged(relative, tag='v12.95'):
    return subprocess.check_output(['git', 'show', f'{tag}:{relative}'], cwd=ROOT)


def prepare(base):
    root = base / 'installed'
    for relative in FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tagged(relative))
        path.chmod(0o640)
    local = base / 'local-node'
    local.mkdir()
    (local / 'app.py').write_bytes(tagged('node_agent/app.py'))
    (local / 'VERSION').write_text('12.95\n')
    return root, local


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file()}


def run(root, local, base, **extra):
    env = dict(os.environ, STREAMFORGE_ROOT=str(root),
               STREAMFORGE_BACKUP_ROOT=str(base / 'backups'),
               STREAMFORGE_LOCAL_NODE_ROOT=str(local), STREAMFORGE_HOTFIX_TEST_MODE='1')
    env.update(extra)
    return subprocess.run(['bash', str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix='sf-v1296-upgrade-') as tmp:
    base = Path(tmp)
    root, local = prepare(base)
    result = run(root, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (root / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
        assert (root / relative).stat().st_mode & 0o777 == 0o640
    before = snapshot(root)
    assert run(root, local, base).returncode == 0
    assert snapshot(root) == before
    result = run(root, local, base, STREAMFORGE_V1296_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(root) == before

for case in ('main-version', 'node-version', 'missing-file', 'node-drift'):
    with tempfile.TemporaryDirectory(prefix=f'sf-v1296-{case}-') as tmp:
        base = Path(tmp)
        root, local = prepare(base)
        if case == 'main-version':
            (root / 'VERSION').write_text('12.94\n')
        elif case == 'node-version':
            (root / 'node_agent/VERSION').write_text('12.94\n')
        elif case == 'missing-file':
            (root / 'app/templates/dashboard.html').unlink()
        else:
            (root / 'node_agent/app.py').write_text('# drifted')
        before = snapshot(root)
        result = run(root, local, base)
        assert result.returncode != 0, case + result.stdout
        assert snapshot(root) == before, case

print('PASS: v12.96 updater install, exact parity, modes, reinstall, rollback and version/drift refusals')
