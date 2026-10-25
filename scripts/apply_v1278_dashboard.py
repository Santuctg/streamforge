#!/usr/bin/env python3
"""Apply the v12.78 Overview and SSH payload changes to an installed StreamForge Main tree.

Installed Main sources are patched in place by the updaters, so v12.78 ships
its changes as exact-text hunks (scripts/apply_v1278_dashboard.json) for:

  app/ssh_installer.py, app/templates/dashboard.html,
  app/templates/node_updates.html, app/templates/node_update.html,
  app/static/app.js, app/static/dashboard.js

Each file is skipped when its v12.78 marker is already present. Every hunk's
original text must occur exactly once, otherwise the file is refused and
nothing is written.

Usage: apply_v1278_dashboard.py /path/to/streamforge-root [relative/file ...]
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
        source = source.replace(original, updated)
    if relative.endswith(".py"):
        compile(source, relative, "exec")
    return source


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    root = Path(argv[1])
    patch = load_patch()
    files = argv[2:] or list(patch["files"])
    results: dict[Path, tuple[str, str]] = {}
    for relative in files:
        path = root / relative
        before = path.read_text(encoding="utf-8")
        results[path] = (before, transform(relative, before, patch))
    # Write only after every file transformed cleanly.
    for path, (before, after) in results.items():
        if before != after:
            path.write_text(after, encoding="utf-8")
    print("PASS: v12.78 dashboard patch installed or already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
