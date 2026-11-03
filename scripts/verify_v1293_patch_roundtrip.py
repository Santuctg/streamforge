#!/usr/bin/env python3
"""Current release round-trip: red expiry and centered payment notice (v12.93)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.93":
    print("SKIP: v12.93 release round-trip")
    raise SystemExit(0)

node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
main_nodes = (ROOT / "app/templates/nodes.html").read_text(encoding="utf-8")
form = (ROOT / "app/templates/node_form.html").read_text(encoding="utf-8")
compile(node, str(ROOT / "node_agent/app.py"), "exec")

# Both expiry surfaces explicitly render expired state in red.
assert '.node-expiry-detail b.node-expired{color:#ff4d5e!important' in main_nodes
assert 'node-expiry-card-expired{{color:#ff4d5e!important' in node
assert 'node-expired-badge' in main_nodes
assert 'node-expiry-card-expired' in node

# Payment notice uses requested wording and is centered with spacing below it.
assert 'STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1293' in node
assert '&#128176; Payment Pending' in node
assert 'The subscription has expired, so all channels are stopped until the payment is made. ' in node
assert 'Once it is paid, everything resumes automatically.' in node
assert 'text-align:center' in node
assert 'margin:2px 0 16px' in node

# Existing backdate support remains present.
assert 'STREAMFORGE_NODE_EXPIRY_BACKDATE_ALLOWED_V1292' in form
assert 'field.min = minNow' not in form

print('PASS: v12.93 red expired expiry on Main/Node and centered payment notice')
