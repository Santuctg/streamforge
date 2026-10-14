#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_main_static_cache.py"
MARKER = "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not SYNC.is_file():
        raise SystemExit(f"FAIL: missing sync helper: {SYNC}")

    text = SYNC.read_text(encoding="utf-8")
    if MARKER not in text:
        raise SystemExit(f"FAIL: missing marker {MARKER}")

    with tempfile.TemporaryDirectory(prefix="sf-v1254-static-") as tmp_raw:
        tmp = Path(tmp_raw)
        source = tmp / "source"
        cache = tmp / "cache"
        (source / "nested").mkdir(parents=True)
        (cache / "nested").mkdir(parents=True)

        expected = {
            Path("app.js"): b"console.log('v12.54');\n",
            Path("style.css"): b"body{font-family:sans-serif}\n",
            Path("nested/panel.js"): b"window.sf=1254;\n",
        }
        for rel, payload in expected.items():
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        stale_targets = [cache / "app.js", cache / "style.css", cache / "nested/panel.js"]
        for path in stale_targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"stale\n")
        (cache / "cache-only.txt").write_text("preserve me\n", encoding="utf-8")

        old_inodes = {path.relative_to(cache): path.stat().st_ino for path in stale_targets}

        result = subprocess.run(
            [sys.executable, str(SYNC), str(source), str(cache)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                "FAIL: sync helper returned non-zero\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        for rel, payload in expected.items():
            source_file = source / rel
            cache_file = cache / rel
            if cache_file.read_bytes() != payload:
                raise SystemExit(f"FAIL: cache content mismatch for {rel}")
            if sha256(source_file) != sha256(cache_file):
                raise SystemExit(f"FAIL: SHA256 mismatch for {rel}")
            if old_inodes.get(rel) == cache_file.stat().st_ino:
                raise SystemExit(f"FAIL: {rel} was overwritten in-place instead of atomically replaced")
            if (cache_file.stat().st_mode & 0o777) != 0o644:
                raise SystemExit(f"FAIL: unexpected mode for {rel}: {oct(cache_file.stat().st_mode & 0o777)}")

        if (cache / "cache-only.txt").read_text(encoding="utf-8") != "preserve me\n":
            raise SystemExit("FAIL: unrelated cache-only file was removed or modified")

        if MARKER not in result.stdout:
            raise SystemExit("FAIL: sync helper did not report the v12.54 marker")

    print("PASS: v12.54 Main static cache sync replaces packaged assets atomically and verifies SHA256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
