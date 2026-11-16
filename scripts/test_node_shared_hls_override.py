#!/usr/bin/env python3
"""Node panel contract for Main-synced per-Node HLS overrides."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

assert 'value="keep"' in source, "Bulk HLS control needs a safe Keep current option"
assert "Use Main default" in source, "Shared-channel inherit option is missing"
assert "/api/v1/node-channel-hls-segment/" in source, "Node panel does not call Main override API"
assert "panel_control_request(" in source and "'POST'" in source, "Node panel must use authenticated Main control transport"
assert "catalog_owner == 'local'" in source or 'catalog_owner == "local"' in source
assert "hls_segment_time" in source

print("PASS: Node shared-channel HLS override contract")
