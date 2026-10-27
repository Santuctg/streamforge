#!/usr/bin/env python3
"""Negative proof: each Batch 2B (v12.83) fix must be load-bearing.

Reverts one change at a time and requires scripts/test_v1283_batch2b.py to
FAIL, then restores the original file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get("SF_PY") or sys.executable
TEST = "scripts/test_v1283_batch2b.py"

CASES = [
    (
        "shared helper picks rightmost hop",
        "app/client_ip.py",
        "    try:\n        parsed = ipaddress.ip_address(hops[-1])",
        "    try:\n        parsed = ipaddress.ip_address(hops[0])",
    ),
    (
        "shared helper loopback-backend gate",
        "app/client_ip.py",
        "    if not loopback_backend(request):\n        return None\n    header",
        "    header",
    ),
    (
        "access_control rightmost client IP",
        "app/access_control.py",
        "    forwarded = forwarded_client_ip(request)\n    if forwarded:\n        return forwarded\n    peer",
        "    peer",
    ),
    (
        "load_balancer rightmost client IP",
        "app/load_balancer.py",
        "    forwarded = forwarded_client_ip(request)\n    value = forwarded if forwarded else (request.client.host if request.client else \"\")",
        "    value = request.client.host if request.client else \"\"",
    ),
    (
        "viewer_tracking rightmost client IP",
        "app/viewer_tracking.py",
        "        forwarded = forwarded_client_ip(request)\n        if forwarded:\n            return forwarded\n        return request.client.host if request.client else \"unknown\"",
        "        return request.client.host if request.client else \"unknown\"",
    ),
    (
        "connection-tracker rightmost client IP",
        "app/main.py",
        "        forwarded = forwarded_client_ip(request)\n        if forwarded:\n            return forwarded\n        return request.client.host if request.client else \"unknown\"",
        "        return request.client.host if request.client else \"unknown\"",
    ),
    (
        "node TLS verification always on",
        "app/node_manager.py",
        "        return ssl.create_default_context()",
        "        return ssl._create_unverified_context()  # noqa: SLF001",
    ),
    (
        "verify_tls form option removed",
        "app/templates/node_form.html",
        "      <label class=\"check\"><input type=\"checkbox\" name=\"save_ssh_password\">",
        "      <label class=\"check\"><input type=\"checkbox\" name=\"verify_tls\" checked><span>Verify HTTPS certificate</span></label>\n      <label class=\"check\"><input type=\"checkbox\" name=\"save_ssh_password\">",
    ),
    (
        "secret-key startup hard fail",
        "app/main.py",
        "        problem = secret_key_problem()\n        if problem:\n            raise RuntimeError(problem)",
        "        problem = \"\"",
    ),
    (
        "Nginx loopback real_ip trust",
        "deploy/nginx.conf",
        "set_real_ip_from 127.0.0.1;\n",
        "",
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
    if not run_test():
        failures.append("restored tree no longer passes")
    print("negative proof:", "FAIL" if failures else "PASS")
    if failures:
        print("  " + "\n  ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
