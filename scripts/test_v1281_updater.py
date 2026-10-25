#!/usr/bin/env python3
"""v12.81 updater: pinned bundled Node app.py replacement, reinstall, rollback and gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.81":
    print("SKIP: v12.81 updater test runs only while VERSION is 12.81")
    raise SystemExit(0)
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1281.sh"
OLD_NODE_BLOB = "83521def749210e025af9a979a77405983f0a43e"


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def old_node_app() -> bytes:
    for ref in ("v12.80", "origin/main", "HEAD~1"):
        try:
            content = subprocess.run(["git", "show", f"{ref}:node_agent/app.py"], cwd=ROOT,
                                     capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        if blob_sha(content) == OLD_NODE_BLOB:
            return content
    raise SystemExit("v12.80 node_agent/app.py baseline not found; fetch the v12.80 tag (git fetch --tags)")


OLD_NODE = old_node_app()


def prepare(base: Path) -> Path:
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "node_agent/app.py").write_bytes(OLD_NODE)
    (app / "VERSION").write_text("12.80\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.80\n", encoding="utf-8")
    return app


def run(app: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1281_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path):
    return {f: (app / f).read_bytes() for f in ("node_agent/app.py", "VERSION", "node_agent/VERSION")}


with tempfile.TemporaryDirectory(prefix="sf-v1281-install-") as d:
    base = Path(d)
    app = prepare(base)
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STREAMFORGE_V1281_NODE_APP_ONLY_PARITY" in result.stdout
    assert (app / "VERSION").read_text().strip() == "12.81"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.81"
    assert (app / "node_agent/app.py").read_bytes() == (ROOT / "node_agent/app.py").read_bytes()
    again = run(app, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1281-rollback-") as d:
    base = Path(d)
    app = prepare(base)
    before = snapshot(app)
    result = run(app, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1281-node-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "Unexpected bundled Node app.py" in (result.stdout + result.stderr)
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1281-runtime-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "node_agent/source_resolver.py").write_text("# locally modified\n", encoding="utf-8")
    result = run(app, base / "backups")
    assert result.returncode != 0 and "Unrelated Node runtime changed" in (result.stdout + result.stderr)

with tempfile.TemporaryDirectory(prefix="sf-v1281-old-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "VERSION").write_text("12.79\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.80" in result.stderr
    assert snapshot(app) == before
print("PASS: v12.81 pinned bundled Node app.py, reinstall, rollback, baseline/runtime and version gates")
