#!/usr/bin/env python3
"""v12.84 staged upgrade/reinstall, rollback, version gates and Node parity."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.84":
    print("SKIP: v12.84 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get('STREAMFORGE_V1284_PACKAGE_ROOT', str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1284.sh"
FILES = ('app/main.py', 'app/permissions.py', 'VERSION', 'node_agent/VERSION', 'node_agent/app.py')
text = SCRIPT.read_text()
assert not re.search(r'systemctl\s+(restart|reload)\s+(nginx|streamforge-channel-supervisor)', text)
assert 'if [[ "$TEST" != 1 ]]; then\n  RESTARTED=1\n  systemctl restart streamforge\n  systemctl restart streamforge-public' in text
assert 'systemctl is-active --quiet streamforge-public' in text
assert 'force_update_v1284.sh' in (PACKAGE / 'scripts/update.sh').read_text()


def tagged(relative):
    return subprocess.check_output(['git', 'show', f'v12.83:{relative}'], cwd=ROOT)


def prepare(base):
    app = base / 'installed'
    for relative in FILES:
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tagged(relative))
        path.chmod(0o640)
    (app / 'unrelated.txt').write_text('keep this user file')
    return app


def snapshot(app):
    return {str(path.relative_to(app)): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in app.rglob('*') if path.is_file()}


def run(app, base, **extra):
    env = dict(os.environ, STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(base / 'backups'),
               STREAMFORGE_HOTFIX_TEST_MODE='1')
    env.update(extra)
    return subprocess.run(['bash', str(SCRIPT)], env=env, cwd=ROOT, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix='sf-v1284-install-') as tmp:
    base = Path(tmp)
    app = prepare(base)
    result = run(app, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (app / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
        assert (app / relative).stat().st_mode & 0o777 == 0o640
    before = snapshot(app)
    again = run(app, base)
    assert again.returncode == 0, again.stdout + again.stderr
    assert snapshot(app) == before
    assert len(list((base / 'backups').glob('v1284-*'))) == 2, 'reinstall must not overwrite backup'
    # Rollback must restore the state preceding this reinstall too.
    result = run(app, base, STREAMFORGE_V1284_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(app) == before

for case in ('rollback', 'anchor', 'main-version', 'node-version', 'missing-file', 'node-drift'):
    with tempfile.TemporaryDirectory(prefix=f'sf-v1284-{case}-') as tmp:
        base = Path(tmp)
        app = prepare(base)
        if case == 'anchor':
            (app / 'app/permissions.py').write_text('# locally modified\n')
        elif case == 'main-version':
            (app / 'VERSION').write_text('12.82\n')
        elif case == 'node-version':
            (app / 'node_agent/VERSION').write_text('12.82\n')
        elif case == 'missing-file':
            (app / 'app/main.py').unlink()
        elif case == 'node-drift':
            (app / 'node_agent/app.py').write_text('# drifted node payload\n')
        before = snapshot(app)
        extra = {'STREAMFORGE_V1284_TEST_FAIL_AFTER_WRITE': '1'} if case == 'rollback' else {}
        result = run(app, base, **extra)
        if case == 'node-drift':
            assert result.returncode == 0, result.stdout + result.stderr
            assert (app / 'node_agent/app.py').read_bytes() == (PACKAGE / 'node_agent/app.py').read_bytes()
        else:
            assert result.returncode != 0, case
            assert snapshot(app) == before, case

# A failure after repairing a drifted Node must restore that original too.
with tempfile.TemporaryDirectory(prefix='sf-v1284-drift-rollback-') as tmp:
    base = Path(tmp)
    app = prepare(base)
    (app / 'node_agent/app.py').write_text('# drifted\n')
    before = snapshot(app)
    result = run(app, base, STREAMFORGE_V1284_TEST_FAIL_AFTER_WRITE='1')
    assert result.returncode != 0 and snapshot(app) == before

print('PASS: v12.84 upgrade/reinstall, exact parity, mode preservation, unique backups, rollback and refusals')
