#!/usr/bin/env python3
"""Current release round-trip: payment notice banner + backdate allowance (v12.92)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.92":
    print("SKIP: v12.92 release round-trip")
    raise SystemExit(0)

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
form = (ROOT / "app/templates/node_form.html").read_text(encoding="utf-8")

compile(main, str(ROOT / "app/main.py"), "exec")
compile(node, str(ROOT / "node_agent/app.py"), "exec")

# Prior behaviour stays intact.
assert "STREAMFORGE_NODE_EXPIRY_HEALTHY" not in node  # no stray marker
assert "STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289" in main
assert "STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291" in main
assert "STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291" in node
assert "STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290" in node
assert node.count("parsed_expiry.replace(tzinfo=BANGLADESH_TZ)") == 2
assert "def enforce_node_expiry" in node
assert "target=_node_expiry_enforcement_loop" in node

# v12.92: the expired-lock banner is a payment-focused notice.
assert "STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1292" in node
assert "Payment pending &#8212; Node service is off" in node
assert "all channels are stopped until the payment is made" in node
assert 'class="node-expiry-banner"' in node
assert "node-expiry-banner-title" in node
assert "node-expiry-banner-sub" in node
assert ".node-expiry-banner{{" in node
# Spacing so the banner never glues to the cards below it.
assert "margin:2px 0 16px" in node
# The old raw warning wording is gone.
assert "&#9888; Node subscription expired" not in node

# v12.92: backdating is allowed in the node edit form.
assert "STREAMFORGE_NODE_EXPIRY_BACKDATE_ALLOWED_V1292" in form
assert "field.min = minNow;" not in form
# Shortcuts still work without the min clamp (fallback base kept).
assert "const base = field.value || minNow;" in form

print("PASS: v12.92 payment notice banner + backdate allowance round-trip")
