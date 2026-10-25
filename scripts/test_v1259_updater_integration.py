#!/usr/bin/env python3
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.59":
    print("SKIP: v12.59 updater test runs only while VERSION is 12.59")
    raise SystemExit(0)

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/force_update_v1259.sh"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def prepare_install(base: Path) -> tuple[Path, Path]:
    app_root = base / "opt/streamforge"
    backup_root = base / "var/backups/streamforge"
    (app_root / "app/templates").mkdir(parents=True)
    (app_root / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "node_agent", app_root / "node_agent", dirs_exist_ok=True)
    (app_root / "VERSION").write_text("12.58\n", encoding="utf-8")
    (app_root / "node_agent/VERSION").write_text("12.58\n", encoding="utf-8")
    (app_root / "app/templates/node_updates.html").write_text("OLD_NODE_UPDATES\n", encoding="utf-8")
    return app_root, backup_root


def run_updater(app_root: Path, backup_root: Path, *, fail_after_write: bool = False):
    env = os.environ.copy()
    env.update({
        "STREAMFORGE_ROOT": str(app_root),
        "STREAMFORGE_BACKUP_ROOT": str(backup_root),
        "STREAMFORGE_HOTFIX_TEST_MODE": "1",
    })
    if fail_after_write:
        env["STREAMFORGE_V1259_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(
        ["bash", str(UPDATER)], cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )


def main() -> None:
    if not UPDATER.is_file():
        raise SystemExit("scripts/force_update_v1259.sh is missing")

    marker = "STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259"
    assert marker in (ROOT / "app/templates/node_updates.html").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1259-success-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        node_before = digest(app_root / "node_agent/app.py")
        proc = run_updater(app_root, backup_root)
        if proc.returncode != 0:
            raise SystemExit(f"v12.59 updater integration failed:\n{proc.stdout}")
        assert (app_root / "VERSION").read_text().strip() == "12.59"
        assert (app_root / "node_agent/VERSION").read_text().strip() == "12.59"
        assert digest(app_root / "app/templates/node_updates.html") == digest(ROOT / "app/templates/node_updates.html")
        assert digest(app_root / "node_agent/app.py") == node_before
        assert marker in (app_root / "app/templates/node_updates.html").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1259-rollback-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        snapshots = {
            "VERSION": (app_root / "VERSION").read_bytes(),
            "node_agent/VERSION": (app_root / "node_agent/VERSION").read_bytes(),
            "app/templates/node_updates.html": (app_root / "app/templates/node_updates.html").read_bytes(),
        }
        proc = run_updater(app_root, backup_root, fail_after_write=True)
        if proc.returncode == 0:
            raise SystemExit("v12.59 rollback test expected controlled post-write failure")
        for rel, data in snapshots.items():
            assert (app_root / rel).read_bytes() == data, rel

    print("PASS: v12.59 updater installs Node Update Center UI spacing, preserves Node runtime, and rolls back cleanly")


if __name__ == "__main__":
    main()
