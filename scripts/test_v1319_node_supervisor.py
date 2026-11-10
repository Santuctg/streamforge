#!/usr/bin/env python3
"""v13.19 dedicated supervisor and expiry-restore contract tests."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
node = shutil.which("python3")
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
    "STREAMFORGE_NODE_SUPERVISOR_OWNED_CLEANUP_V1319",
    "signal.SIGTERM",
    "_supervisor_coordination_owned",
):
    assert marker in agent, marker

if os.getenv("V1319_EXPIRY_TEST") == "1":
    assert "STREAMFORGE_NODE_EXPIRY_RESTORE_READY_RETRY_V1319" in agent
    assert "def _retry_pending_expiry_restore" in agent

if os.getenv("V1319_BEHAVIOR_TEST") == "1" and sys.version_info >= (3, 10):
    with tempfile.TemporaryDirectory(prefix="sf-v1319-supervisor-") as tmp:
        code = """
import os
import app
identity = app._supervisor_process_identity(os.getpid())
assert identity and identity[1] > 0
app.NODE_CHANNEL_SUPERVISOR_PID_FILE.write_text(f'{os.getpid()} {identity[1]}\\n')
assert app._supervisor_coordination_owned()
app.NODE_CHANNEL_SUPERVISOR_PID_FILE.write_text('1 1\\n')
assert not app._supervisor_coordination_owned()
with app._supervisor_lifecycle_lock():
    assert app.SUPERVISOR_LOCK_FILE.is_file()
"""
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(ROOT / "node_agent"),
            "STREAMFORGE_NODE_MODE": "control",
            "STREAMFORGE_NODE_CHANNEL_SUPERVISOR_SOCKET": str(Path(tmp) / "supervisor.sock"),
            "STREAMFORGE_NODE_CHANNEL_SUPERVISOR_PID_FILE": str(Path(tmp) / "supervisor.pid"),
            "STREAMFORGE_NODE_CHANNEL_SUPERVISOR_LOCK_FILE": str(Path(tmp) / "supervisor.lock"),
        })
        subprocess.run([node or "python3", "-c", code], check=True, env=env, cwd=ROOT / "node_agent")

for marker in (
    "expiry_restore_desired",
    "self._persist_expiry_restore(pending_restore)",
    "Node subscription active; expired lock released",
):
    assert marker in agent, marker

if node:
    subprocess.run([node, "-m", "py_compile", str(ROOT / "node_agent/app.py")], check=True)

print("PASS: v13.19 Node supervisor ownership and expiry restore contract")
