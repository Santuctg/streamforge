#!/usr/bin/env python3
"""Regression contracts for Node-initiated HLS updates and channel sorting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

# Main must not call back into the initiating Node before returning its response.
endpoint_start = main.index("def node_channel_hls_segment_override(")
endpoint_end = main.index("def _node_compatible_channels", endpoint_start)
endpoint = main[endpoint_start:endpoint_end]
assert "sync_channel_config(" not in endpoint, "Main override endpoint must not deadlock by syncing back synchronously"
assert '"restarted": False' in endpoint

# Node applies the returned effective value locally after Main persistence.
assert "effective_hls_segment_time" in node
assert "manager.sync_channel(key, rt.config.model_copy" in node
assert "hls_segment_override" in node

# The channel list exposes persistent sorting choices and applies them before paging.
assert "data-node-sort-key" in node, "Node table needs clickable sortable headers"
assert "channel-sort-indicator" in node, "Node sortable headers need the Main-style arrow indicator"
assert "appearance:none" in node and "input[type=checkbox]" in node, "Node checkboxes need the dark themed custom appearance"
assert "data-node-channel-sort" not in node, "Sorting must be in table headers, not a separate dropdown"
assert "data-node-sort-key=\"name\"" in node
assert "channelPage" in node and "sort" in node
assert "data-node-sort-status" in node

print("PASS: shared HLS timeout prevention and channel sort contract")
