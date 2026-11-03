#!/usr/bin/env python3
"""Current release round-trip: Node dashboard expiry heartbeat payload (v12.89)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.89":
    print("SKIP: v12.89 release round-trip")
    raise SystemExit(0)

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

compile(main, str(ROOT / "app/main.py"), "exec")
compile(node, str(ROOT / "node_agent/app.py"), "exec")

# Main heartbeat must publish the Node subscription expiry.
assert '"node_expires_at": node.expires_at.isoformat() if node.expires_at else ""' in main
assert "STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289" in main

# The Node Agent must receive, persist and render it on the Node dashboard.
assert "STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289" in node
assert "def apply_main_node_expires" in node
assert "self.apply_main_node_expires(data.get(\"node_expires_at\"))" in node
assert '"node_expires_at": self.node_expires_at' in node
assert "Subscription expires" in node
assert "node-expiry-card-expired" in node and "node-expiry-card-active" in node
assert "self.node_expires_at = str(raw_panel_users.get(\"node_expires_at\") or \"\")" in node

# Main Panel dashboard must NOT show node expiry (v12.88 decision: Node pages only).
dashboard = (ROOT / "app/templates/dashboard.html").read_text(encoding="utf-8")
assert "dashboard_node_expiry" not in dashboard
assert "Node expiry" not in dashboard

print("PASS: v12.89 Node dashboard expiry heartbeat round-trip (Main publishes, Node persists and renders)")
