#!/usr/bin/env python3
"""Current release round-trip: Node expiry service lock + BD 12h display (v12.90)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.90":
    print("SKIP: v12.90 release round-trip")
    raise SystemExit(0)

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

compile(main, str(ROOT / "app/main.py"), "exec")
compile(node, str(ROOT / "node_agent/app.py"), "exec")

# Main heartbeat must keep publishing the Node subscription expiry (v12.89).
assert '"node_expires_at": node.expires_at.isoformat() if node.expires_at else ""' in main
assert "STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289" in main

# Node Agent: heartbeat persists and triggers enforcement.
assert "STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290" in node
assert "def apply_main_node_expires" in node
assert "self.apply_main_node_expires(data.get(\"node_expires_at\"))" in node
assert "if NODE_MODE != \"public\":\n            self.enforce_node_expiry()" in node

# Node Agent: BD timezone, 12-hour display, lock reason and enforcement loop.
assert "BANGLADESH_TZ = timezone(timedelta(hours=6), \"Asia/Dhaka\")" in node
assert ('"%d %b %Y %I:%M %p"' in node), "12-hour BD display format missing"
assert "def node_expiry_lock_reason" in node
assert "def enforce_node_expiry" in node
assert "def _node_expiry_enforcement_loop" in node
assert "expiry_restore_desired" in node
assert "self._expiry_lock_active = False" in node
# start() guard must engage before the delegation branch so supervisor starts
# cannot bypass the lock.
start_guard = node.index("if self.node_expiry_lock_reason():\n            return {\"key\": key, \"status\": \"expired\"")
delegate = node.index("if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:\n            return self._delegate_channel_command(\"start\", key, config, timeout=15.0)", start_guard)
assert start_guard < delegate
# Enforcement loop and control-worker thread wiring.
assert "target=_node_expiry_enforcement_loop" in node
# Corrupt expiry must never lock the service.
assert "A corrupt Main value must not brick the Node service" in node
# Dashboard banner on the Node panel.
assert "Node subscription expired — all channels stopped" in node
assert "expiry_banner" in node

# Live behaviour check of the display + lock logic, extracted via AST.
import ast
import textwrap
import unittest.mock as mock_module

tree = ast.parse(node)
matches = {}
wanted = {"node_expiry_bd_display", "node_expiry_lock_reason"}
for top in tree.body:
    if isinstance(top, ast.ClassDef):
        for item in top.body:
            if isinstance(item, ast.FunctionDef) and item.name in wanted:
                matches[item.name] = ast.FunctionDef(
                    name=item.name, args=item.args, body=item.body,
                    decorator_list=[], returns=item.returns,
                    type_comment=None,
                )
for fn in matches.values():
    ast.fix_missing_locations(fn)

script = "\n".join(
    textwrap.dedent(
        "from datetime import datetime, timedelta, timezone\n"
        "BANGLADESH_TZ = timezone(timedelta(hours=6), 'Asia/Dhaka')\n"
        f"{ast.unparse(fn)}\n"
    )
    for fn in matches.values()
)
namespace: dict = {}
exec(compile(script, "<v1290-logic>", "exec"), namespace)


class _Model:
    node_expires_at = ""


model = _Model()
display, css = namespace["node_expiry_bd_display"](model)
assert display == "Unlimited" and css == "node-expiry-card-active"
assert namespace["node_expiry_lock_reason"](model) == ""

model.node_expires_at = "2026-12-02T19:40:00+00:00"
# 19:40 UTC -> BD (+6) is 01:40 AM the following day, in 12-hour form.
display, css = namespace["node_expiry_bd_display"](model)
assert display == "03 Dec 2026 01:40 AM", display
assert css == "node-expiry-card-active"
assert namespace["node_expiry_lock_reason"](model) == ""

# Corrupt value: display it verbatim in red, and never lock the service.
model.node_expires_at = "garbage-date"
display, css = namespace["node_expiry_bd_display"](model)
assert display == "garbage-date" and css == "node-expiry-card-expired"
assert namespace["node_expiry_lock_reason"](model) == ""

# Naive ISO (Main SQLite format) is treated as UTC: past -> locked.
model.node_expires_at = "2020-01-02T19:40:00"
# 2020-01-02T19:40:00 naive (UTC) -> BD 03 Jan 2020 01:40 AM, expired past.
display, css = namespace["node_expiry_bd_display"](model)
assert display == "03 Jan 2020 01:40 AM · EXPIRED", display
assert css == "node-expiry-card-expired"
assert namespace["node_expiry_lock_reason"](model) == "Node subscription expired"

# Naive ISO without seconds must also parse (FormData normalize variants).
model.node_expires_at = "2020-01-02T19:40"
assert namespace["node_expiry_lock_reason"](model) == "Node subscription expired"

# Main dashboard must still be free of the expiry card (v12.88 decision).
dashboard = (ROOT / "app/templates/dashboard.html").read_text(encoding="utf-8")
assert "dashboard_node_expiry" not in dashboard

print("PASS: v12.90 BD 12-hour expiry display, epoch-accurate lock logic and dashboard banner round-trip")
