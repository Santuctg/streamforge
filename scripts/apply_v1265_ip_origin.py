#!/usr/bin/env python3
"""Bounded Main patch: record which Main/Remote Node request caused an IP block.

Apply to the already-v12.64-patched installed Main (the repository source
may still contain v12.63 anchors, so the updater stages both patchers).
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER"

def transform(source: str) -> str:
    if MARKER in source:
        return source
    if "STREAMFORGE_V1264_IP_GUARD" not in source:
        raise ValueError("Expected v12.64 IP guard integration")
    anchor = "async def node_panel_live_auth("
    if source.count(anchor) != 1:
        raise ValueError("Expected exactly one live Node authentication handler")
    left, right = source.split(anchor, 1)
    original = "record_ip_failure(source_ip)"
    if left.count(original) != 1 or right.count(original) != 2:
        raise ValueError("Unexpected number of Main/Remote Node IP failure calls")
    left = left.replace(
        original, 'record_ip_failure(source_ip, origin="main")'
    )
    # Place the marker on a separate line; never comment out the 'if:' colon.
    hook = '            if record_ip_failure(source_ip, origin="main"):'
    if left.count(hook) != 1:
        raise ValueError("Expected one Main auth origin call")
    left = left.replace(hook, '            # ' + MARKER + '\n' + hook)
    right = right.replace(
        original,
        'record_ip_failure(source_ip, origin="node", node_id=node.id, node_name=node.name)'
    )
    updated = left + anchor + right
    compile(updated, "app/main.py", "exec")
    return updated

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_v1265_ip_origin.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    old = path.read_text(encoding="utf-8")
    new = transform(old)
    if old != new:
        path.write_text(new, encoding="utf-8")
    print("PASS: bounded IP block source-server patch applied or already present")

if __name__ == "__main__":
    main()
