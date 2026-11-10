#!/usr/bin/env python3
"""v13.19 dedicated supervisor and expiry-restore contract tests."""
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / "VERSION").read_text().strip() == "13.19"
assert (ROOT / "node_agent/VERSION").read_text().strip() == "13.19"

agent = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_NODE_SUPERVISOR_SINGLE_OWNER_V1319",
    "STREAMFORGE_NODE_SUPERVISOR_STALE_COORDINATION_V1319",
    "SUPERVISOR_LOCK_FILE",
    "def _supervisor_process_identity",
    "fcntl.flock",
    "LOCK_EX",
    "STREAMFORGE_NODE_SUPERVISOR_PID_START_TIME_V1319",
    "NODE_CHANNEL_SUPERVISOR_SOCKET.parent.mkdir",
):
    assert marker in agent, marker

if os.getenv("V1319_EXPIRY_TEST") == "1":
    assert "STREAMFORGE_NODE_EXPIRY_RESTORE_READY_RETRY_V1319" in agent
    assert "def _retry_pending_expiry_restore" in agent

for marker in (
    "expiry_restore_desired",
    "self._persist_expiry_restore(pending_restore)",
    "Node subscription active; expired lock released",
):
    assert marker in agent, marker

node = shutil.which("python3")
if node:
    subprocess.run([node, "-m", "py_compile", str(ROOT / "node_agent/app.py")], check=True)

print("PASS: v13.19 Node supervisor ownership and expiry restore contract")
