#!/usr/bin/env python3
"""Negative proof: each batch-2A fix must be load-bearing.

Reverts one change at a time in a scratch copy of the repo and requires
scripts/test_v1282_batch2a.py to FAIL, then restores the original file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get("SF_PY") or sys.executable
TEST = "scripts/test_v1282_batch2a.py"

CASES = [
    (
        "viewer-kill permission (sessions.kill)",
        "app/main.py",
        'dependencies=[Depends(permission_required("sessions.kill"))]',
        'dependencies=[Depends(permission_required("dashboard.view"))]',
    ),
    (
        "save-time source scheme guard",
        "app/main.py",
        "        ensure_safe_input_target(cleaned)\n        raw_program =",
        "        raw_program =",
    ),
    (
        "run-time scheme choke point",
        "app/input_options.py",
        "    ensure_safe_input_target(target)\n    scheme = urlsplit(base).scheme.lower()",
        "    scheme = urlsplit(base).scheme.lower()",
    ),
    (
        "node token rotation command",
        "app/ssh_installer.py",
        'if action_key == "token_rotate":',
        'if action_key == "token_rotat":',
    ),
    (
        "backup env opt-out switch",
        "app/backup_manager.py",
        'return os.getenv("STREAMFORGE_BACKUP_INCLUDE_ENV", "1").strip().lower() in {"1", "true", "yes", "on"}',
        'return True',
    ),
    (
        "secret-key startup hard fail",
        "app/main.py",
        'raise RuntimeError(problem)',
        'pass  # disabled secret-key refusal',
    ),
    (
        "access-denied page context",
        "app/main.py",
        'response = render(request, "access_denied.html", db, permission=exc.permission)',
        'response = TEMPLATES.TemplateResponse(request=request, name="access_denied.html", context={"permission": exc.permission, "can": lambda key: False}, status_code=403)',
    ),
]


def run_test() -> bool:
    env = dict(os.environ)
    env["PYTHONPATH"] = "."
    result = subprocess.run(
        [PY, TEST],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode == 0


def main() -> int:
    failures = []
    for label, relpath, old, new in CASES:
        target = ROOT / relpath
        original = target.read_text(encoding="utf-8")
        if old not in original:
            failures.append(f"{label}: anchor text missing in {relpath}")
            continue
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        try:
            passed = run_test()
        finally:
            target.write_text(original, encoding="utf-8")
        status = "OK (test failed as required)" if not passed else "BROKEN (test still passed!)"
        print(f"  revert {label:38s} -> {status}")
        if passed:
            failures.append(label)
    # Final confirmation: restored tree is green again.
    if not run_test():
        failures.append("restored tree no longer passes")
    print("negative proof:", "FAIL" if failures else "PASS")
    if failures:
        print("  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
