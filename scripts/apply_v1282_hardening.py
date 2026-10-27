#!/usr/bin/env python3
"""Apply the v12.82 hardening to an installed StreamForge Main tree.

Installed Main sources are patched in place by the updaters, so v12.82 ships
its changes as exact-text hunks (scripts/apply_v1282_hardening.json) for the
Main sources, templates and both VERSION files the release changes.

Each file is skipped when its v12.82 marker is already present. Every hunk's
original text must occur exactly once, otherwise the file is refused and
nothing is written.

Usage: apply_v1282_hardening.py /path/to/streamforge-root [relative/file ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PATCH_DATA = Path(__file__).with_suffix(".json")


def load_patch() -> dict:
    return json.loads(PATCH_DATA.read_text(encoding="utf-8"))


def transform(relative: str, source: str, patch: dict | None = None) -> str:
    spec = (patch or load_patch())["files"][relative]
    if spec["marker"] in source:
        return source
    for number, (original, updated) in enumerate(spec["hunks"], 1):
        matches = source.count(original)
        if matches != 1:
            raise ValueError(f"{relative}: hunk {number} expected one anchor; found {matches}")
        source = source.replace(original, updated, 1)
    return source


def apply(root: Path, patch: dict | None = None) -> list[str]:
    patch = patch or load_patch()
    applied: list[str] = []
    for relative in patch["files"]:
        path = root / relative
        if not path.is_file():
            print(f"skip {relative}: not present")
            continue
        source = path.read_text(encoding="utf-8")
        updated = transform(relative, source, patch)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            applied.append(relative)
    return applied


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1])
    patch = load_patch()
    relatives = sys.argv[2:] or list(patch["files"])
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"missing {relative}")
        source = path.read_text(encoding="utf-8")
        path.write_text(transform(relative, source, patch), encoding="utf-8")
        print(f"patched {relative}")


if __name__ == "__main__":
    main()
