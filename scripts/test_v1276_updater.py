#!/usr/bin/env python3
"""v12.76 security updater: staged multi-file patch, new modules, helper, reinstall and rollback."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.76":
    print("SKIP: v12.76 updater test runs only while VERSION is 12.76")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1276.sh"
sys.path.insert(0, str(ROOT / "scripts"))
PATCH = json.loads((ROOT / "scripts/apply_v1276_security.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])
NEW_FILES = ["app/stream_keys.py", "app/xtream_guard.py", "app/backup_crypto.py"]


def v1275_source(relative: str) -> str:
    """The repo file with every v12.76 hunk reverted (installed v12.75 content)."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    for original, updated in reversed(PATCH["files"][relative]["hunks"]):
        assert source.count(updated) == 1, (relative, updated[:60])
        source = source.replace(updated, original)
    assert PATCH["files"][relative]["marker"] not in source
    return source


def prepare(base: Path):
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "VERSION").write_text("12.75\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.75\n", encoding="utf-8")
    for relative in PATCH_FILES:
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(v1275_source(relative), encoding="utf-8")
    (app / "scripts/apply_main_access.py").chmod(0o755)
    sbin = base / "sbin"
    sbin.mkdir()
    shutil.copy(app / "scripts/apply_main_access.py", sbin / "streamforge-apply-main-access")
    return app, sbin


def run(app: Path, sbin: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_SBIN_DIR=str(sbin),
               STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1276_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path, sbin: Path):
    files = {f: (app / f).read_bytes() for f in PATCH_FILES + ["VERSION", "node_agent/VERSION"]}
    files["helper"] = (sbin / "streamforge-apply-main-access").read_bytes()
    files["new"] = tuple((app / f).exists() for f in NEW_FILES)
    return files


with tempfile.TemporaryDirectory(prefix="sf-v1276-install-") as d:
    base = Path(d)
    app, sbin = prepare(base)
    node_before = {p.relative_to(app / "node_agent").as_posix(): p.read_bytes()
                   for p in (app / "node_agent").rglob("*") if p.is_file() and p.name != "VERSION"}
    result = run(app, sbin, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "VERSION").read_text().strip() == "12.76"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.76"
    for relative in PATCH_FILES + NEW_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
    helper = sbin / "streamforge-apply-main-access"
    assert helper.read_bytes() == (ROOT / "scripts/apply_main_access.py").read_bytes()
    assert oct(helper.stat().st_mode & 0o777) == "0o755"
    assert oct((app / "scripts/apply_main_access.py").stat().st_mode & 0o777) == "0o755", "file mode kept"
    node_after = {p.relative_to(app / "node_agent").as_posix(): p.read_bytes()
                  for p in (app / "node_agent").rglob("*") if p.is_file() and p.name != "VERSION"}
    assert node_before == node_after
    again = run(app, sbin, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr
    for relative in PATCH_FILES + NEW_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative

with tempfile.TemporaryDirectory(prefix="sf-v1276-rollback-") as d:
    base = Path(d)
    app, sbin = prepare(base)
    before = snapshot(app, sbin)
    result = run(app, sbin, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app, sbin) == before, "rollback restores patched files, helper and removes new modules"

with tempfile.TemporaryDirectory(prefix="sf-v1276-anchor-") as d:
    base = Path(d)
    app, sbin = prepare(base)
    # An installed file whose anchor text differs must stop the update untouched.
    main = app / "app/main.py"
    main.write_text(main.read_text(encoding="utf-8").replace("def channel_stream_key(", "def channel_stream_key_local("), encoding="utf-8")
    before = snapshot(app, sbin)
    result = run(app, sbin, base / "backups")
    assert result.returncode != 0 and "preflight failed" in result.stderr, result.stdout + result.stderr
    assert snapshot(app, sbin) == before

with tempfile.TemporaryDirectory(prefix="sf-v1276-old-") as d:
    base = Path(d)
    app, sbin = prepare(base)
    (app / "VERSION").write_text("12.74\n", encoding="utf-8")
    before = snapshot(app, sbin)
    result = run(app, sbin, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.75" in result.stderr
    assert snapshot(app, sbin) == before
print("PASS: v12.76 staged security upgrade (7 patched files, 3 new modules, root helper), reinstall, rollback, anchor and version gates, Node parity")
