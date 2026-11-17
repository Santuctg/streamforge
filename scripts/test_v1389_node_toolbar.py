"""v13.89 Node channels toolbar tidy contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
assert "STREAMFORGE_NODE_CHANNEL_TOOLBAR_TIDY_V1389" in s
assert '<div class="toolbar node-channels-toolbar">' in s
assert '<div class=node-channels-heading>' in s
# Add link lives in the heading row, not in the filter row
assert "</small></div>{add_link}</div><div class=\"actions node-channel-filter-actions\">" in s
assert "</span>{add_link}</div></div>" not in s
assert ".node-channels-toolbar .node-filter-count{{border:0!important" in s
assert ".node-channels-toolbar+.node-bulk-toolbar{{justify-content:flex-end}}" in s
print("PASS: v13.89 node channels toolbar contract")
