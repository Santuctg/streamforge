#!/usr/bin/env python3
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.56":
    print("SKIP: v12.56 updater test runs only while VERSION is 12.56")
    raise SystemExit(0)

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/force_update_v1256.sh"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def prepare_install(base: Path) -> tuple[Path, Path]:
    app_root = base / "opt/streamforge"
    backup_root = base / "var/backups/streamforge"
    (app_root / "app/templates").mkdir(parents=True)
    (app_root / "node_agent").mkdir(parents=True)
    (app_root / "scripts").mkdir(parents=True)
    (app_root / "VERSION").write_text("12.55\n", encoding="utf-8")
    (app_root / "node_agent/VERSION").write_text("12.55\n", encoding="utf-8")
    (app_root / "app/templates/player.html").write_text("OLD_MAIN_PLAYER\n", encoding="utf-8")
    (app_root / "node_agent/app.py").write_text("OLD_NODE_PLAYER\n", encoding="utf-8")
    return app_root, backup_root


def run_updater(app_root: Path, backup_root: Path, *, fail_after_write: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "STREAMFORGE_ROOT": str(app_root),
            "STREAMFORGE_BACKUP_ROOT": str(backup_root),
            "STREAMFORGE_HOTFIX_TEST_MODE": "1",
        }
    )
    if fail_after_write:
        env["STREAMFORGE_V1256_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> None:
    if not UPDATER.is_file():
        raise SystemExit("scripts/force_update_v1256.sh is missing")

    source_main = ROOT / "app/templates/player.html"
    source_node = ROOT / "node_agent/app.py"
    marker = "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256"
    assert marker in source_main.read_text(encoding="utf-8")
    assert marker in source_node.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1256-success-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        proc = run_updater(app_root, backup_root)
        if proc.returncode != 0:
            raise SystemExit(f"v12.56 updater integration failed:\n{proc.stdout}")
        assert (app_root / "VERSION").read_text(encoding="utf-8").strip() == "12.56"
        assert (app_root / "node_agent/VERSION").read_text(encoding="utf-8").strip() == "12.56"
        assert digest(app_root / "app/templates/player.html") == digest(source_main)
        assert digest(app_root / "node_agent/app.py") == digest(source_node)
        assert marker in (app_root / "app/templates/player.html").read_text(encoding="utf-8")
        assert marker in (app_root / "node_agent/app.py").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1256-rollback-") as tmp:
        app_root, backup_root = prepare_install(Path(tmp))
        before_main = (app_root / "app/templates/player.html").read_bytes()
        before_node = (app_root / "node_agent/app.py").read_bytes()
        proc = run_updater(app_root, backup_root, fail_after_write=True)
        if proc.returncode == 0:
            raise SystemExit("v12.56 rollback test expected controlled post-write failure")
        assert (app_root / "VERSION").read_text(encoding="utf-8").strip() == "12.55"
        assert (app_root / "node_agent/VERSION").read_text(encoding="utf-8").strip() == "12.55"
        assert (app_root / "app/templates/player.html").read_bytes() == before_main
        assert (app_root / "node_agent/app.py").read_bytes() == before_node

    print("PASS: v12.56 updater installs HLS.js-first Main/Node players and rolls back cleanly")


if __name__ == "__main__":
    main()
