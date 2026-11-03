#!/usr/bin/env python3
"""v12.93 staged upgrade/reinstall, rollback, version gates and Node parity."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.93":
    print("SKIP: v12.93 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get('STREAMFORGE_V1293_PACKAGE_ROOT', str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1293.sh"
FILES = ('app/main.py', 'app/templates/node_form.html', 'app/templates/nodes.html',
         'node_agent/app.py', 'VERSION', 'node_agent/VERSION')
text = SCRIPT.read_text()
assert not re.search(r'systemctl\s+(restart|reload)\s+(nginx|streamforge-channel-supervisor)', text)
assert 'if [[ "$TEST" != 1 ]]; then\n  RESTARTED=1\n  systemctl restart streamforge\n  systemctl restart streamforge-public' in text
assert 'systemctl is-active --quiet streamforge-public' in text
assert 'force_update_v1293.sh' in (PACKAGE / 'scripts/update.sh').read_text()
assert 'STREAMFORGE_V1293_PAYMENT_NOTICE_UPDATER' in text
assert 'app/templates/nodes.html' in text
assert 'STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1293' in text


def tagged(relative, tag='v12.92'):
    return subprocess.check_output(['git', 'show', f'{tag}:{relative}'], cwd=ROOT)


def prepare(base):
    app = base / 'installed'
    for relative in FILES:
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tagged(relative))
        path.chmod(0o640)
    (app / 'unrelated.txt').write_text('keep this user file')
    local = base / 'local-node'
    local.mkdir(parents=True, exist_ok=True)
    (local / 'app.py').write_bytes(tagged('node_agent/app.py'))
    (local / 'VERSION').write_text('12.92\n')
    return app, local


def snapshot(app):
    return {str(path.relative_to(app)): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in app.rglob('*') if path.is_file()}


def run(app, local, base, **extra):
    env = dict(os.environ, STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(base / 'backups'),
               STREAMFORGE_LOCAL_NODE_ROOT=str(local), STREAMFORGE_HOTFIX_TEST_MODE='1')
    env.update(extra)
    return subprocess.run(['bash', str(SCRIPT)], env=env, cwd=ROOT, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix='sf-v1293-install-') as tmp:
    base = Path(tmp)
    app, local = prepare(base)
    result = run(app, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (app / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
        assert (app / relative).stat().st_mode & 0o777 == 0o640
    assert (local / 'app.py').read_bytes() == (PACKAGE / 'node_agent/app.py').read_bytes()
    assert (local / 'VERSION').read_text() == '12.93\n'
    before = snapshot(app)
    again = run(app, local, base)
    assert again.returncode == 0, again.stdout + again.stderr
    assert snapshot(app) == before
    assert len(list((base / 'backups').glob('v1293-*'))) == 2
    result = run(app, local, base, STREAMFORGE_V1293_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(app) == before

for case in ('rollback', 'main-version', 'node-version', 'missing-file', 'node-drift', 'local-drift'):
    with tempfile.TemporaryDirectory(prefix=f'sf-v1293-{case}-') as tmp:
        base = Path(tmp)
        app, local = prepare(base)
        if case == 'main-version':
            (app / 'VERSION').write_text('12.91\n')
        elif case == 'node-version':
            (app / 'node_agent/VERSION').write_text('12.91\n')
        elif case == 'missing-file':
            (app / 'app/templates/nodes.html').unlink()
        elif case == 'node-drift':
            (app / 'node_agent/app.py').write_text('# drifted node payload\n')
        elif case == 'local-drift':
            (local / 'app.py').write_text('# unknown local agent\n')
        before = snapshot(app)
        local_before = (local / 'app.py').read_bytes()
        extra = {'STREAMFORGE_V1293_TEST_FAIL_AFTER_WRITE': '1'} if case == 'rollback' else {}
        result = run(app, local, base, **extra)
        if case == 'local-drift':
            assert result.returncode == 0, result.stdout + result.stderr
            assert (local / 'app.py').read_bytes() == local_before
            assert (app / 'VERSION').read_text() == '12.93\n'
            assert 'WARNING: local Node Agent' in result.stdout
        else:
            assert result.returncode != 0, case + result.stdout
            assert snapshot(app) == before, case

with tempfile.TemporaryDirectory(prefix='sf-v1293-local-rollback-') as tmp:
    base = Path(tmp)
    app, local = prepare(base)
    local_before = (local / 'app.py').read_bytes()
    result = run(app, local, base, STREAMFORGE_V1293_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0
    assert (local / 'app.py').read_bytes() == local_before

print('PASS: v12.93 upgrade/reinstall, parity, mode preservation, backups, rollback and refusal cases')
