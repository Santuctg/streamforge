#!/usr/bin/env python3
"""v12.82 updater: staged hardening patch, rollback, idempotence and version gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.82":
    print("SKIP: v12.82 updater test runs only while VERSION is 12.82")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1282.sh"
PATCH = json.loads((ROOT / "scripts/apply_v1282_hardening.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])

# STREAMFORGE_V1282_UPDATER_RESTARTS_MAIN_WORKERS_V1: patching the sources on
# disk is not enough — the running workers keep serving the previous release
# until they reload (the Panel showed v12.81 after a v12.82 update without
# this). The restart must live inside the test-mode guard so tests never call
# systemctl, and must not touch Nginx or the channel supervisor.
SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
assert re.search(
    r'if \[\[ "\$TEST" != 1 \]\]; then\n  RESTARTED=1\n  log "Restarting Main HTTP workers',
    SCRIPT_TEXT,
), "restart block missing from the test-mode guard"
# Exactly the two worker restart lines at block indentation, so neither can
# be satisfied by the rollback's redirected copies or by each other.
assert re.search(r"^  systemctl restart streamforge$", SCRIPT_TEXT, re.M), "streamforge restart missing"
assert re.search(r"^  systemctl restart streamforge-public$", SCRIPT_TEXT, re.M), "streamforge-public restart missing"
assert not re.search(
    r"systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)", SCRIPT_TEXT
), "updater must not restart Nginx or the channel supervisor"


def tagged(relative: str, ref: str) -> bytes:
    """File content straight from a release tag, so later branches stay proof."""
    result = subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, capture_output=True)
    assert result.returncode == 0, (relative, ref, "shallow clone?")
    return result.stdout


def v1281_source(relative: str) -> str:
    """The installed v12.81 content, straight from the previous release tag."""
    source = tagged(relative, "v12.81").decode("utf-8")
    assert PATCH["files"][relative]["marker"] not in source
    return source


def prepare(base: Path) -> Path:
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "app/static", app / "app/static")
    for relative in PATCH_FILES:
        (app / relative).parent.mkdir(parents=True, exist_ok=True)
        (app / relative).write_text(v1281_source(relative), encoding="utf-8")
    (app / "VERSION").write_text("12.81\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.81\n", encoding="utf-8")
    return app


def run(app: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1282_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path):
    return {f: (app / f).read_bytes() for f in PATCH_FILES + ["VERSION", "node_agent/VERSION", "node_agent/app.py"]}


with tempfile.TemporaryDirectory(prefix="sf-v1282-install-") as d:
    base = Path(d)
    app = prepare(base)
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "VERSION").read_text().strip() == "12.82"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.82"
    for relative in PATCH_FILES:
        assert (app / relative).read_bytes() == tagged(relative, "v12.82"), relative
        assert PATCH["files"][relative]["marker"] in (app / relative).read_text(encoding="utf-8"), relative
    # A second run on an already-updated tree changes nothing and succeeds.
    again = run(app, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1282-rollback-") as d:
    base = Path(d)
    app = prepare(base)
    before = snapshot(app)
    result = run(app, base / "backups", fail=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert snapshot(app) == before, "controlled failure must roll every file back"

with tempfile.TemporaryDirectory(prefix="sf-v1282-anchor-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "app/permissions.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "hardening patch preflight failed" in result.stderr, result.stdout + result.stderr
    assert snapshot(app) == before, "a refused anchor must leave the tree untouched"

with tempfile.TemporaryDirectory(prefix="sf-v1282-node-") as d:
    base = Path(d)
    app = prepare(base)
    # The Node payload is unchanged in v12.82: a drifted file is restored to
    # the pinned v12.81 bytes rather than refused.
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "node_agent/app.py").read_bytes() == tagged("node_agent/app.py", "v12.82")

with tempfile.TemporaryDirectory(prefix="sf-v1282-old-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "VERSION").write_text("12.80\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.81" in result.stderr, result.stdout + result.stderr
    assert snapshot(app) == before

print("PASS: v12.82 staged hardening patch, reinstall, rollback, anchor, Node restore and version gates")
