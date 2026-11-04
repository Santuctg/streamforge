#!/usr/bin/env python3
"""v12.99 staged Main Web Update installer and rollback tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.99':
    print('SKIP: v12.99 updater test')
    raise SystemExit(0)
PACKAGE = Path(os.environ.get('STREAMFORGE_V1299_PACKAGE_ROOT', str(ROOT)))
SCRIPT = PACKAGE / 'scripts/force_update_v1299.sh'
FILES = ('app/main.py', 'app/permissions.py', 'app/templates/base.html',
         'app/templates/main_updates.html', 'scripts/main_system_control.py',
         'deploy/streamforge-main-system.service', 'node_agent/app.py',
         'VERSION', 'node_agent/VERSION')
OLD_FILES = tuple(item for item in FILES if item != 'app/templates/main_updates.html')
text = SCRIPT.read_text()
for marker in ('STREAMFORGE_V1299_MAIN_WEB_UPDATE_UPDATER',
               'STREAMFORGE_V1299_INSTALL_PRIVILEGED_UPDATE_HELPER',
               'systemctl enable --now streamforge-main-system.path'):
    assert marker in text
assert 'force_update_v1299.sh' in (PACKAGE / 'scripts/update.sh').read_text()


def tagged(relative):
    return subprocess.check_output(['git', 'show', f'v12.98:{relative}'], cwd=ROOT)


def prepare(base):
    root = base / 'installed'
    for relative in OLD_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tagged(relative))
        path.chmod(0o640)
    local = base / 'local-node'
    local.mkdir()
    (local / 'app.py').write_bytes(tagged('node_agent/app.py'))
    (local / 'VERSION').write_text('12.98\n')
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


with tempfile.TemporaryDirectory(prefix='sf-v1299-') as tmp:
    base = Path(tmp)
    root, local = prepare(base)
    result = run(root, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (root / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (root / 'app/templates/main_updates.html').is_file()
    before = snapshot(root)
    result = run(root, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(root) == before
    result = run(root, local, base, STREAMFORGE_V1299_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(root) == before

with tempfile.TemporaryDirectory(prefix='sf-v1299-fresh-rollback-') as tmp:
    base = Path(tmp)
    root, local = prepare(base)
    before = snapshot(root)
    result = run(root, local, base, STREAMFORGE_V1299_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0
    assert snapshot(root) == before
    assert not (root / 'app/templates/main_updates.html').exists()

for case in ('main-version', 'node-version', 'node-drift'):
    with tempfile.TemporaryDirectory(prefix=f'sf-v1299-{case}-') as tmp:
        base = Path(tmp)
        root, local = prepare(base)
        if case == 'main-version':
            (root / 'VERSION').write_text('12.97\n')
        elif case == 'node-version':
            (root / 'node_agent/VERSION').write_text('12.97\n')
        else:
            (root / 'node_agent/app.py').write_text('# drifted')
        before = snapshot(root)
        assert run(root, local, base).returncode != 0
        assert snapshot(root) == before

print('PASS: v12.99 Web Update staged install, new-file rollback, reinstall and refusal gates')
