#!/usr/bin/env python3
"""v12.78 updater: staged Main-only patch, reinstall, rollback and version/Node gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.78":
    print("SKIP: v12.78 updater test runs only while VERSION is 12.78")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1278.sh"
PATCH = json.loads((ROOT / "scripts/apply_v1278_dashboard.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])


def v1277_source(relative: str) -> str:
    """The repo file with every v12.78 hunk reverted (installed v12.77 content)."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    for original, updated in reversed(PATCH["files"][relative]["hunks"]):
        assert source.count(updated) == 1, (relative, updated[:60])
        source = source.replace(updated, original)
    assert PATCH["files"][relative]["marker"] not in source
    return source


def prepare(base: Path, *, with_fetcher: bool = True) -> Path:
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "VERSION").write_text("12.77\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.77\n", encoding="utf-8")
    for relative in PATCH_FILES:
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(v1277_source(relative), encoding="utf-8")
    if with_fetcher:
        (app / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts/fetch_hlsjs.sh", app / "scripts/fetch_hlsjs.sh")
    return app


def run(app: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1278_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path):
    files = PATCH_FILES + ["VERSION", "node_agent/VERSION", "scripts/fetch_hlsjs.sh"]
    return {f: (app / f).read_bytes() if (app / f).exists() else None for f in files}


with tempfile.TemporaryDirectory(prefix="sf-v1278-install-") as d:
    base = Path(d)
    app = prepare(base)
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STREAMFORGE_V1278_MAIN_ONLY_NODE_PARITY" in result.stdout
    assert (app / "VERSION").read_text().strip() == "12.78"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.78"
    for relative in PATCH_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
    again = run(app, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1278-rollback-") as d:
    base = Path(d)
    app = prepare(base)
    before = snapshot(app)
    result = run(app, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1278-fetcher-") as d:
    # An older tree without the hls.js helper gets it; a rollback removes it again.
    base = Path(d)
    app = prepare(base, with_fetcher=False)
    before = snapshot(app)
    result = run(app, base / "backups", fail=True)
    assert result.returncode != 0 and snapshot(app) == before
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "scripts/fetch_hlsjs.sh").read_bytes() == (ROOT / "scripts/fetch_hlsjs.sh").read_bytes()

with tempfile.TemporaryDirectory(prefix="sf-v1278-node-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "Node runtime payload differs" in (result.stdout + result.stderr)
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1278-old-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "VERSION").write_text("12.76\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.77" in result.stderr
    assert snapshot(app) == before
print("PASS: v12.78 staged Main patch, hls.js helper, reinstall, rollback, Node parity and version gates")
