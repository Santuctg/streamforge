#!/usr/bin/env python3
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.57":
    print("SKIP: v12.57 updater test runs only while VERSION is 12.57")
    raise SystemExit(0)

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/force_update_v1257.sh"


def digest(path: Path) -> str:
    h = hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()


def prepare_install(base: Path) -> tuple[Path, Path]:
    app_root = base / "opt/streamforge"
    backup_root = base / "var/backups/streamforge"
    (app_root / "app/templates").mkdir(parents=True)
    (app_root / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "node_agent", app_root / "node_agent", dirs_exist_ok=True)
    (app_root / "VERSION").write_text("12.56\n", encoding="utf-8")
    (app_root / "node_agent/VERSION").write_text("12.56\n", encoding="utf-8")
    (app_root / "app/main.py").write_text("OLD_MAIN\n", encoding="utf-8")
    (app_root / "app/templates/base.html").write_text("OLD_BASE\n", encoding="utf-8")
    (app_root / "app/templates/nodes.html").write_text("OLD_NODES\n", encoding="utf-8")
    return app_root, backup_root


def run_updater(app_root: Path, backup_root: Path, *, fail_after_write: bool = False):
    env = os.environ.copy()
    env.update({
        "STREAMFORGE_ROOT": str(app_root),
        "STREAMFORGE_BACKUP_ROOT": str(backup_root),
        "STREAMFORGE_HOTFIX_TEST_MODE": "1",
    })
    if fail_after_write:
        env["STREAMFORGE_V1257_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(UPDATER)], cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def main() -> None:
    if not UPDATER.is_file():
        raise SystemExit("scripts/force_update_v1257.sh is missing")
    marker = "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257"
    assert marker in (ROOT / "app/main.py").read_text(encoding="utf-8")
    source_files = [
        "app/main.py", "app/templates/base.html", "app/templates/nodes.html", "app/templates/node_updates.html"
    ]

    with tempfile.TemporaryDirectory(prefix="streamforge-v1257-success-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        node_before = digest(app_root / "node_agent/app.py")
        proc = run_updater(app_root, backup_root)
        if proc.returncode != 0:
            raise SystemExit(f"v12.57 updater integration failed:\n{proc.stdout}")
        assert (app_root / "VERSION").read_text().strip() == "12.57"
        assert (app_root / "node_agent/VERSION").read_text().strip() == "12.57"
        for rel in source_files:
            assert digest(app_root / rel) == digest(ROOT / rel), rel
        assert digest(app_root / "node_agent/app.py") == node_before
        assert marker in (app_root / "app/main.py").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1257-rollback-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        snapshots = {rel: (app_root / rel).read_bytes() for rel in ["app/main.py", "app/templates/base.html", "app/templates/nodes.html"]}
        proc = run_updater(app_root, backup_root, fail_after_write=True)
        if proc.returncode == 0:
            raise SystemExit("v12.57 rollback test expected controlled post-write failure")
        assert (app_root / "VERSION").read_text().strip() == "12.56"
        assert (app_root / "node_agent/VERSION").read_text().strip() == "12.56"
        for rel, data in snapshots.items():
            assert (app_root / rel).read_bytes() == data, rel
        assert not (app_root / "app/templates/node_updates.html").exists()

    print("PASS: v12.57 updater installs bulk Node update center, preserves Node runtime, and rolls back cleanly")


if __name__ == "__main__":
    main()
