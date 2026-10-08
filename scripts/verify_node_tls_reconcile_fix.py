#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "node_agent" / "apply_node_tls.py").read_text(encoding="utf-8")
installer = (root / "scripts" / "install_node_agent.sh").read_text(encoding="utf-8")

errors = []

if "REQUEST_FILE.unlink(missing_ok=True)" in source:
    errors.append("apply_node_tls.py still deletes tls-reconcile.request")

if 'subprocess.run(["systemctl", "is-enabled", "--quiet", "nginx"]' not in source:
    errors.append("apply_node_tls.py lacks conditional nginx enable guard")

if "STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT" not in source:
    errors.append("persistent TLS request marker missing")

if "STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT" not in installer:
    errors.append("installer does not seed persistent tls-reconcile.request")

if errors:
    raise SystemExit("\n".join(f"FAIL: {item}" for item in errors))

print("Node TLS reconciliation regression guard: PASS")
