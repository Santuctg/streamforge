#!/usr/bin/env python3
"""Current release round-trip: node expiry = Bangladesh wall time (v12.91)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.91":
    print("SKIP: v12.91 release round-trip")
    raise SystemExit(0)

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

compile(main, str(ROOT / "app/main.py"), "exec")
compile(node, str(ROOT / "node_agent/app.py"), "exec")

# Main heartbeat must keep publishing the Node subscription expiry (v12.89),
# documented as Bangladesh wall time (v12.91).
assert '"node_expires_at": node.expires_at.isoformat() if node.expires_at else ""' in main
assert "STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289" in main
assert "STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291" in main

# Main knows Bangladesh time for its own Nodes-list badge.
assert 'BANGLADESH_TZ = timezone(timedelta(hours=6), "Asia/Dhaka")' in main
assert 'node_expiry_now=datetime.now(BANGLADESH_TZ).replace(tzinfo=None)' in main

# Main Nodes list must compare the expiry with BD wall time now.
nodes_html = (ROOT / "app/templates/nodes.html").read_text(encoding="utf-8")
assert "node_expiry_now" in nodes_html
assert '<span>Expires (BD)</span>' in nodes_html
assert "{% if node.expires_at < now %}" not in nodes_html

# The Node Agent interprets naive heartbeat values as Asia/Dhaka local time.
assert "STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290" in node
assert "STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291" in node
assert node.count("parsed_expiry.replace(tzinfo=BANGLADESH_TZ)") == 2, "naive must be BD in both display+lock"
lock_body = node.split("def node_expiry_lock_reason")[1].split("\n    def ")[0]
assert "tzinfo=timezone.utc)" not in lock_body, "lock reason must not treat naive as UTC"
assert "def enforce_node_expiry" in node
assert "def _node_expiry_enforcement_loop" in node
assert "expiry_restore_desired" in node
assert "target=_node_expiry_enforcement_loop" in node
# The start() guard must stay in front of the delegation branch.
start_guard = node.index("if self.node_expiry_lock_reason():\n            return {\"key\": key, \"status\": \"expired\"")
delegate = node.index("if not NODE_CHANNEL_OWNER and NODE_DEDICATED_CHANNEL_SUPERVISOR:\n            return self._delegate_channel_command(\"start\", key, config, timeout=15.0)", start_guard)
assert start_guard < delegate
# Display format stays Bangladesh 12-hour.
assert node.count('"%d %b %Y %I:%M %p"') == 1

# Live behaviour check of the display + lock logic, extracted via AST.
import ast
import textwrap

tree = ast.parse(node)
wrapped: dict[str, ast.FunctionDef] = {}
wanted = {"node_expiry_bd_display", "node_expiry_lock_reason"}
for top in tree.body:
    if isinstance(top, ast.ClassDef):
        for item in top.body:
            if isinstance(item, ast.FunctionDef) and item.name in wanted:
                wrapped[item.name] = ast.FunctionDef(
                    name=item.name, args=item.args, body=item.body,
                    decorator_list=[], returns=item.returns, type_comment=None,
                )
                ast.fix_missing_locations(wrapped[item.name])

script = "\n".join(
    textwrap.dedent(
        "from datetime import datetime, timedelta, timezone\n"
        "BANGLADESH_TZ = timezone(timedelta(hours=6), 'Asia/Dhaka')\n"
        f"{ast.unparse(fn)}\n"
    )
    for fn in wrapped.values()
)
namespace: dict = {}
exec(compile(script, "<v1291-logic>", "exec"), namespace)


class _Model:
    node_expires_at = ""


model = _Model()
display, css = namespace["node_expiry_bd_display"](model)
assert display == "Unlimited" and css == "node-expiry-card-active"
assert namespace["node_expiry_lock_reason"](model) == ""

# The operator enters Bangladesh wall time on Main; the card echoes it.
model.node_expires_at = "2026-12-02T12:12:00"
display, css = namespace["node_expiry_bd_display"](model)
assert display == "02 Dec 2026 12:12 PM", display
assert css == "node-expiry-card-active"
assert namespace["node_expiry_lock_reason"](model) == ""

# UTC-aware values remain epoch-accurate: 19:40+00:00 -> BD next day 01:40 AM.
model.node_expires_at = "2026-12-02T19:40:00+00:00"
display, css = namespace["node_expiry_bd_display"](model)
assert display == "03 Dec 2026 01:40 AM", display

# Corrupt value: display it verbatim in red, and never lock the service.
model.node_expires_at = "garbage-date"
assert namespace["node_expiry_bd_display"](model) == ("garbage-date", "node-expiry-card-expired")
assert namespace["node_expiry_lock_reason"](model) == ""

# Naive BD wall time already in the past locks immediately (12:12 BD noon
# passed while now-UTC is past 06:12 gives the correct instant).
model.node_expires_at = "2020-01-02T12:12:00"
assert namespace["node_expiry_lock_reason"](model) == "Node subscription expired"

print("PASS: v12.91 naive expiry = Bangladesh wall time round-trip (insert-what-you-see)")
