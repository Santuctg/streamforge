"""v13.90 Node channels page-size label contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
assert "STREAMFORGE_NODE_SHOW_LABEL_HIDDEN_V1390" in s
assert '<label class=node-channel-limit-filter>Show<select' not in s
assert '<label class=node-channel-limit-filter><select data-node-channel-page-size aria-label="Channels per page">' in s
print("PASS: v13.90 node Show label contract")
