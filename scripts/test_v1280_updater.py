#!/usr/bin/env python3
"""v12.80 updater: staged live-charts patch, Nginx static cache refresh, rollback and gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.80":
    print("SKIP: v12.80 updater test runs only while VERSION is 12.80")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1280.sh"
PATCH = json.loads((ROOT / "scripts/apply_v1280_live_charts.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])


def v1279_source(relative: str) -> str:
    """The repo file with every v12.80 hunk reverted (installed v12.79 content)."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    for original, updated in reversed(PATCH["files"][relative]["hunks"]):
        assert source.count(updated) == 1, (relative, updated[:60])
        source = source.replace(updated, original)
    assert PATCH["files"][relative]["marker"] not in source
    return source


def prepare(base: Path) -> tuple[Path, Path]:
    app = base / "installed"
    cache = base / "main-static"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "app/static", app / "app/static")
    for relative in PATCH_FILES:
        (app / relative).write_text(v1279_source(relative), encoding="utf-8")
    shutil.copytree(app / "app/static", cache)
    (app / "VERSION").write_text("12.79\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.79\n", encoding="utf-8")
    return app, cache


def run(app: Path, cache: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_MAIN_STATIC_CACHE_ROOT=str(cache), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1280_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path, cache: Path):
    files = {f"cache/{p.relative_to(cache)}": p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    files.update({f: (app / f).read_bytes() for f in PATCH_FILES + ["VERSION", "node_agent/VERSION"]})
    return files


with tempfile.TemporaryDirectory(prefix="sf-v1280-install-") as d:
    base = Path(d)
    app, cache = prepare(base)
    result = run(app, cache, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STREAMFORGE_V1280_STATIC_CACHE_VERIFIED" in result.stdout
    assert (app / "VERSION").read_text().strip() == "12.80"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.80"
    for relative in PATCH_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
        assert (cache / Path(relative).name).read_bytes() == (ROOT / relative).read_bytes(), relative
    again = run(app, cache, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1280-rollback-") as d:
    base = Path(d)
    app, cache = prepare(base)
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1280-anchor-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "app/static/dashboard.js").write_text("// locally modified\n", encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "live-charts patch preflight failed" in result.stderr
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1280-node-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "Node runtime payload differs" in (result.stdout + result.stderr)
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1280-old-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "VERSION").write_text("12.78\n", encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.79" in result.stderr
    assert snapshot(app, cache) == before
print("PASS: v12.80 staged live-charts patch, static cache refresh, reinstall, rollback, anchor, Node and version gates")
