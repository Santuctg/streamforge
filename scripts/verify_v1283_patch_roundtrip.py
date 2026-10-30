#!/usr/bin/env python3
"""Historical round-trip: v12.82 sources + hunks == tagged v12.83, exactly.

The updater patches installed trees in place from
scripts/apply_v1283_hardening.json, so every hunk must be anchored uniquely in
the previous release and applying all of them must reproduce the tagged
release byte-for-byte, not later development sources. New changes have their
own patch round-trip (test_role_manager_patch.py).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Historical release check: runs only on its own release.
if (ROOT / "VERSION").read_text().strip() != "12.83":
    print("SKIP: v12.83 patch round-trip runs only while VERSION is 12.83")
    raise SystemExit(0)
sys.path.insert(0, str(ROOT / "scripts"))
from apply_v1283_hardening import transform  # noqa: E402

PREVIOUS = "v12.82"
patch = json.loads((ROOT / "scripts" / "apply_v1283_hardening.json").read_text(encoding="utf-8"))
failures: list[str] = []
hunk_count = 0
for relative, spec in patch["files"].items():
    hunk_count += len(spec["hunks"])
    head = subprocess.run(
        ["git", "show", f"{PREVIOUS}:{relative}"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if head.returncode != 0:
        failures.append(f"{relative}: cannot read {PREVIOUS} (shallow clone?)")
        continue
    old = head.stdout
    release = subprocess.run(
        ["git", "show", f"v12.83:{relative}"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if release.returncode != 0:
        failures.append(f"{relative}: cannot read v12.83 (fetch release tags)")
        continue
    new = release.stdout
    if spec["marker"] in old:
        failures.append(f"{relative}: v12.83 marker already present in {PREVIOUS}")
    if spec["marker"] not in new:
        failures.append(f"{relative}: v12.83 marker missing from this release")
    for number, (original, updated) in enumerate(spec["hunks"], 1):
        if original == updated:
            failures.append(f"{relative}: hunk {number} is a no-op")
        if old.count(original) != 1:
            failures.append(f"{relative}: hunk {number} anchor occurs {old.count(original)} times in {PREVIOUS}")
    try:
        applied = transform(relative, old, patch)
    except ValueError as exc:
        failures.append(str(exc))
        continue
    if applied != new:
        failures.append(f"{relative}: applying the hunks does not reproduce this release")
    if transform(relative, applied, patch) != applied:
        failures.append(f"{relative}: a second pass is not a no-op")

print(f"round-trip over {len(patch['files'])} files, {hunk_count} hunks")
if failures:
    print("FAIL:")
    for failure in failures:
        print(" -", failure)
    raise SystemExit(1)
print("PASS: v12.82 sources + v12.83 hunks reproduce this release exactly")
