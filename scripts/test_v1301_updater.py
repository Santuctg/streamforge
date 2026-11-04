#!/usr/bin/env python3
"""v13.01 staged Main Updates UI installer and rollback tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.01":
    print("SKIP: v13.01 updater test")
    raise SystemExit(0)
PACKAGE = Path(os.environ.get("STREAMFORGE_V1301_PACKAGE_ROOT", str(ROOT)))
SCRIPT = PACKAGE / "scripts/force_update_v1301.sh"
FILES = ("app/templates/main_updates.html", "node_agent/app.py", "VERSION", "node_agent/VERSION")
text = SCRIPT.read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1301_FULLY_RESPONSIVE_MAIN_UPDATES_UPDATER",
    "STREAMFORGE_V1301_LOCAL_NODE_AGENT_OPTIONAL",
    "STREAMFORGE_V1301_TEST_FAIL_AFTER_WRITE",
    "13.00|13.01",
):
    assert marker in text
assert "force_update_v1301.sh" in (PACKAGE / "scripts/update.sh").read_text(encoding="utf-8")


def tagged(relative: str) -> bytes:
    return subprocess.check_output(["git", "show", f"v13.00:{relative}"], cwd=ROOT)


def prepare(base: Path) -> tuple[Path, Path]:
    installed = base / "installed"
    for relative in FILES:
        path = installed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tagged(relative))
        path.chmod(0o640)
    local = base / "local-node"
    local.mkdir()
    (local / "app.py").write_bytes(tagged("node_agent/app.py"))
    (local / "VERSION").write_text("13.00\n", encoding="utf-8")
    return installed, local


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in root.rglob("*") if path.is_file()
    }


def run(installed: Path, local: Path, base: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        STREAMFORGE_ROOT=str(installed),
        STREAMFORGE_BACKUP_ROOT=str(base / "backups"),
        STREAMFORGE_LOCAL_NODE_ROOT=str(local),
        STREAMFORGE_HOTFIX_TEST_MODE="1",
    )
    env.update(extra)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix="sf-v1301-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in FILES:
        assert (installed / relative).read_bytes() == (PACKAGE / relative).read_bytes(), relative
    assert (local / "VERSION").read_text(encoding="utf-8").strip() == "13.01"
    current = snapshot(installed)
    result = run(installed, local, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(installed) == current
    result = run(installed, local, base, STREAMFORGE_V1301_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0 and snapshot(installed) == current

with tempfile.TemporaryDirectory(prefix="sf-v1301-rollback-") as temporary:
    base = Path(temporary)
    installed, local = prepare(base)
    before = snapshot(installed)
    result = run(installed, local, base, STREAMFORGE_V1301_TEST_FAIL_AFTER_WRITE="1")
    assert result.returncode != 0
    assert snapshot(installed) == before

for case in ("main-version", "node-version", "node-drift"):
    with tempfile.TemporaryDirectory(prefix=f"sf-v1301-{case}-") as temporary:
        base = Path(temporary)
        installed, local = prepare(base)
        if case == "main-version":
            (installed / "VERSION").write_text("12.99\n", encoding="utf-8")
        elif case == "node-version":
            (installed / "node_agent/VERSION").write_text("12.99\n", encoding="utf-8")
        else:
            (installed / "node_agent/app.py").write_text("# drifted", encoding="utf-8")
        before = snapshot(installed)
        assert run(installed, local, base).returncode != 0
        assert snapshot(installed) == before

print("PASS: v13.01 fully responsive Main Updates staged install, rollback, reinstall and refusal gates")
