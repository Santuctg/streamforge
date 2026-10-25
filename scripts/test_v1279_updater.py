#!/usr/bin/env python3
"""v12.79 updater: refresh the Nginx static cache left stale by v12.78, rollback and gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.79":
    print("SKIP: v12.79 updater test runs only while VERSION is 12.79")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1279.sh"
PATCH = json.loads((ROOT / "scripts/apply_v1278_dashboard.json").read_text(encoding="utf-8"))


def v1277_static(name: str) -> str:
    """The v12.77 asset still published in the cache after the v12.78 update."""
    relative = f"app/static/{name}"
    source = (ROOT / relative).read_text(encoding="utf-8")
    for original, updated in reversed(PATCH["files"][relative]["hunks"]):
        assert source.count(updated) == 1, (relative, updated[:60])
        source = source.replace(updated, original)
    assert "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278" not in source
    return source


def prepare(base: Path) -> tuple[Path, Path]:
    app = base / "installed"
    cache = base / "main-static"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "app/static", app / "app/static")
    shutil.copytree(ROOT / "app/static", cache)
    for name in ("app.js", "dashboard.js"):
        (cache / name).write_text(v1277_static(name), encoding="utf-8")
    (app / "VERSION").write_text("12.78\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.78\n", encoding="utf-8")
    return app, cache


def run(app: Path, cache: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_MAIN_STATIC_CACHE_ROOT=str(cache), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1279_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path, cache: Path):
    files = {f"cache/{p.relative_to(cache)}": p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    files.update({f: (app / f).read_bytes() for f in ("VERSION", "node_agent/VERSION", "app/static/app.js")})
    return files


with tempfile.TemporaryDirectory(prefix="sf-v1279-install-") as d:
    base = Path(d)
    app, cache = prepare(base)
    result = run(app, cache, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STREAMFORGE_V1279_STATIC_CACHE_VERIFIED" in result.stdout
    assert (app / "VERSION").read_text().strip() == "12.79"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.79"
    for path in (ROOT / "app/static").rglob("*"):
        if path.is_file():
            assert (cache / path.relative_to(ROOT / "app/static")).read_bytes() == path.read_bytes(), path
    again = run(app, cache, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1279-rollback-") as d:
    base = Path(d)
    app, cache = prepare(base)
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1279-unpatched-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "app/static/app.js").write_text(v1277_static("app.js"), encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "install v12.78 first" in result.stderr
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1279-node-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "Node runtime payload differs" in (result.stdout + result.stderr)
    assert snapshot(app, cache) == before

with tempfile.TemporaryDirectory(prefix="sf-v1279-old-") as d:
    base = Path(d)
    app, cache = prepare(base)
    (app / "VERSION").write_text("12.77\n", encoding="utf-8")
    before = snapshot(app, cache)
    result = run(app, cache, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.78" in result.stderr
    assert snapshot(app, cache) == before
print("PASS: v12.79 static cache refresh, byte-for-byte verification, reinstall, rollback and version/Node gates")
