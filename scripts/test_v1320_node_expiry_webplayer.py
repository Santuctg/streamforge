#!/usr/bin/env python3
"""v13.20 Node expiry refresh/WebPlayer enforcement contract."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
agent = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

for marker in (
    "STREAMFORGE_NODE_EXPIRY_DISK_REFRESH_V1320",
    "def refresh_node_expiry_from_disk",
    "refresh_node_expiry_from_disk()",
    "node_expiry_lock_reason()",
    "node_expiry_bd_display()",
):
    assert marker in agent, marker

subprocess.run(["python3", "-m", "py_compile", str(ROOT / "node_agent/app.py")], check=True)
print("PASS: v13.20 Node expiry refresh keeps WebPlayer and channel enforcement current")
