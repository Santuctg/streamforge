#!/usr/bin/env python3
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.54":
    print("SKIP: v12.54 updater test runs only while VERSION is 12.54")
    raise SystemExit(0)

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "force_update_v1254.sh"
MARKER = "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not UPDATER.is_file():
        raise SystemExit(f"FAIL: missing v12.54 updater: {UPDATER}")

    updater_text = UPDATER.read_text(encoding="utf-8")
    required = (
        MARKER,
        'TARGET_VERSION="12.54"',
        'NODE_TARGET_VERSION="12.52"',
        'STATIC_CACHE_ROOT="${STREAMFORGE_MAIN_STATIC_CACHE_ROOT:-/var/cache/streamforge/main-static}"',
        'sync_main_static_cache.py',
        'Remote Node Agent is unchanged',
    )
    for snippet in required:
        if snippet not in updater_text:
            raise SystemExit(f"FAIL: updater missing {snippet}")

    if 'python3 -m py_compile "$SOURCE_DIR/' in updater_text:
        raise SystemExit("FAIL: updater can dirty release source with py_compile")

    if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "12.54":
        raise SystemExit("FAIL: package VERSION is not 12.54")
    if (ROOT / "node_agent" / "VERSION").read_text(encoding="utf-8").strip() != "12.52":
        raise SystemExit("FAIL: bundled Node Agent must remain 12.52")

    with tempfile.TemporaryDirectory(prefix="sf-v1254-updater-") as tmp_raw:
        tmp = Path(tmp_raw)
        app_root = tmp / "installed"
        backup_root = tmp / "backups"
        cache_root = tmp / "main-static"

        (app_root / "app" / "static").mkdir(parents=True)
        (app_root / "node_agent").mkdir(parents=True)
        cache_root.mkdir(parents=True)

        (app_root / "VERSION").write_text("12.53\n", encoding="utf-8")
        (app_root / "node_agent" / "VERSION").write_text("12.52\n", encoding="utf-8")
        (app_root / "app" / "static" / "app.js").write_text("installed stale\n", encoding="utf-8")
        (cache_root / "app.js").write_text("nginx stale\n", encoding="utf-8")
        (cache_root / "cache-only.txt").write_text("keep\n", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "STREAMFORGE_ROOT": str(app_root),
                "STREAMFORGE_BACKUP_ROOT": str(backup_root),
                "STREAMFORGE_MAIN_STATIC_CACHE_ROOT": str(cache_root),
                "STREAMFORGE_HOTFIX_TEST_MODE": "1",
            }
        )
        result = subprocess.run(
            ["bash", str(UPDATER)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                "FAIL: v12.54 updater test-mode execution failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        if (app_root / "VERSION").read_text(encoding="utf-8").strip() != "12.54":
            raise SystemExit("FAIL: installed Main VERSION did not become 12.54")
        if (app_root / "node_agent" / "VERSION").read_text(encoding="utf-8").strip() != "12.52":
            raise SystemExit("FAIL: installed Node Agent VERSION changed")

        source_static = ROOT / "app" / "static"
        for source_file in sorted(path for path in source_static.rglob("*") if path.is_file()):
            rel = source_file.relative_to(source_static)
            installed_file = app_root / "app" / "static" / rel
            cache_file = cache_root / rel
            expected = sha256(source_file)
            if not installed_file.is_file() or sha256(installed_file) != expected:
                raise SystemExit(f"FAIL: installed static mismatch for {rel}")
            if not cache_file.is_file() or sha256(cache_file) != expected:
                raise SystemExit(f"FAIL: Nginx static cache mismatch for {rel}")

        if (cache_root / "cache-only.txt").read_text(encoding="utf-8") != "keep\n":
            raise SystemExit("FAIL: updater removed unrelated cache-only file")
        if MARKER not in result.stdout:
            raise SystemExit("FAIL: updater output did not confirm v12.54 static cache sync")
        backups = list(backup_root.glob("v1254-*"))
        if not backups:
            raise SystemExit("FAIL: updater did not create a v12.54 backup")
        if not any((backup / "main-static" / "app.js").is_file() for backup in backups):
            raise SystemExit("FAIL: updater backup does not contain previous Nginx static cache")

    print("PASS: v12.54 updater refreshes installed/static cache atomically and preserves Node v12.52")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
