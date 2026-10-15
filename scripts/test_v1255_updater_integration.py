#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/force_update_v1255.sh"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if not UPDATER.is_file():
        raise SystemExit("scripts/force_update_v1255.sh is missing")

    source_node_hash = digest(ROOT / "node_agent/app.py")

    with tempfile.TemporaryDirectory(prefix="streamforge-v1255-test-") as tmp:
        base = Path(tmp)
        app_root = base / "opt/streamforge"
        cache_root = base / "var/cache/streamforge/main-static"
        backup_root = base / "var/backups/streamforge"

        (app_root / "app").mkdir(parents=True)
        shutil.copytree(ROOT / "app/static", app_root / "app/static")
        shutil.copytree(ROOT / "node_agent", app_root / "node_agent")
        (app_root / "VERSION").write_text("12.54\n", encoding="utf-8")
        (app_root / "node_agent/VERSION").write_text("12.52\n", encoding="utf-8")

        shutil.copytree(ROOT / "app/static", cache_root)
        sentinel = cache_root / "cache-only.keep"
        sentinel.write_text("preserve me\n", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "STREAMFORGE_ROOT": str(app_root),
                "STREAMFORGE_BACKUP_ROOT": str(backup_root),
                "STREAMFORGE_MAIN_STATIC_CACHE_ROOT": str(cache_root),
                "STREAMFORGE_HOTFIX_TEST_MODE": "1",
            }
        )
        proc = subprocess.run(
            ["bash", str(UPDATER)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"v12.55 updater integration failed:\n{proc.stdout}")

        assert (app_root / "VERSION").read_text(encoding="utf-8").strip() == "12.55"
        assert (app_root / "node_agent/VERSION").read_text(encoding="utf-8").strip() == "12.55"
        assert digest(app_root / "node_agent/app.py") == source_node_hash
        assert digest(app_root / "app/static/app.js") == digest(ROOT / "app/static/app.js")
        assert digest(cache_root / "app.js") == digest(ROOT / "app/static/app.js")
        assert sentinel.read_text(encoding="utf-8") == "preserve me\n"

    print("PASS: v12.55 updater aligns versions without changing Node behavior")


if __name__ == "__main__":
    main()
