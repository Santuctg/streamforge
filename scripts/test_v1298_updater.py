#!/usr/bin/env python3
"""v12.98 staged updater safety and Main dashboard node summary tests."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.98':
    print('SKIP: v12.98 updater test')
    raise SystemExit(0)
PACKAGE = Path(os.environ.get('STREAMFORGE_V1298_PACKAGE_ROOT', str(ROOT)))
SCRIPT = PACKAGE / 'scripts/force_update_v1298.sh'
FILES = ('app/main.py', 'app/templates/dashboard.html', 'app/templates/node_form.html',
         'app/templates/nodes.html', 'node_agent/app.py', 'VERSION', 'node_agent/VERSION')
text = SCRIPT.read_text()
assert 'STREAMFORGE_V1298_RETRY_EXPIRY_RESTORE_UPDATER' in text
assert 'STREAMFORGE_NODE_EXPIRY_RESTORE_RETRY_V1298' in text
assert 'force_update_v1298.sh' in (PACKAGE / 'scripts/update.sh').read_text()
assert not re.search(r'systemctl\s+(restart|reload)\s+(nginx|streamforge-channel-supervisor)', text)
agent = (PACKAGE / 'node_agent/app.py').read_text()
restore = agent.split('def enforce_node_expiry(self)', 1)[1].split('\n    def ', 1)[0]
assert 'pending_restore = dict(restore)' in restore
assert 'pending_restore.pop(key, None)' in restore
assert 'self._persist_expiry_restore(pending_restore)' in restore
assert restore.index('result = self.start(key, config)') < restore.index('pending_restore.pop(key, None)') < restore.index('self._persist_expiry_restore(pending_restore)')
assert 'status in {"error", "stopped", "expired"}' in restore


def tagged(relative, tag='v12.97'):
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
    (local / 'VERSION').write_text('12.97\n')
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


with tempfile.TemporaryDirectory(prefix='sf-v1298-upgrade-') as tmp:
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
    result = run(root, local, base, STREAMFORGE_V1298_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(root) == before

for case in ('main-version', 'node-version', 'missing-file', 'node-drift'):
    with tempfile.TemporaryDirectory(prefix=f'sf-v1298-{case}-') as tmp:
        base = Path(tmp)
        root, local = prepare(base)
        if case == 'main-version':
            (root / 'VERSION').write_text('12.95\n')
        elif case == 'node-version':
            (root / 'node_agent/VERSION').write_text('12.95\n')
        elif case == 'missing-file':
            (root / 'app/templates/dashboard.html').unlink()
        else:
            (root / 'node_agent/app.py').write_text('# drifted')
        before = snapshot(root)
        result = run(root, local, base)
        assert result.returncode != 0, case + result.stdout
        assert snapshot(root) == before, case

print('PASS: v12.98 updater install, exact parity, modes, reinstall, rollback and version/drift refusals')
