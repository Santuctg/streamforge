#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_static_tree(source: Path, cache: Path) -> int:
    if not source.is_dir():
        raise SystemExit(f"source static directory missing: {source}")

    files = [path for path in sorted(source.rglob("*")) if path.is_file()]
    if not files:
        raise SystemExit(f"source static directory is empty: {source}")

    cache.mkdir(parents=True, exist_ok=True)
    synced = 0
    for source_file in files:
        rel = source_file.relative_to(source)
        destination = cache / rel
        destination.parent.mkdir(parents=True, exist_ok=True)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.v1254-",
            dir=str(destination.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as output, source_file.open("rb") as input_handle:
                shutil.copyfileobj(input_handle, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp_path, 0o644)

            source_hash = sha256(source_file)
            staged_hash = sha256(temp_path)
            if source_hash != staged_hash:
                raise SystemExit(f"staged SHA256 mismatch for {rel}")

            os.replace(temp_path, destination)
            if sha256(destination) != source_hash:
                raise SystemExit(f"installed SHA256 mismatch for {rel}")
            synced += 1
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    print(f"{MARKER}: synced {synced} static assets with atomic replace + SHA256 verification")
    return synced


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} SOURCE_STATIC_DIR CACHE_STATIC_DIR", file=sys.stderr)
        return 2
    sync_static_tree(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
