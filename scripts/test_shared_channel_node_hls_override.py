#!/usr/bin/env python3
"""Main API regression for shared-channel per-Node HLS overrides."""
from __future__ import annotations

from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
main_source = (ROOT / "app/main.py").read_text(encoding="utf-8")

endpoint = "/api/v1/node-channel-hls-segment/{node_slug}/{channel_key}"
assert endpoint in main_source, "Main per-Node HLS override endpoint is missing"
assert "_node_control_identity(request, db)" in main_source, "Node token identity guard is missing"
assert "hls_segment_time" in main_source and "1 <= hls_segment_time <= 20" in main_source
assert "channel_nodes" in main_source and "node_id=:node_id" in main_source
assert "restart_running=True" in main_source, "Override must target-restart the affected Node channel"
assert '"hls_segment_time": hls_segment_time' in main_source, "Inherit/clear behavior is missing"

print("PASS: shared-channel per-Node HLS override API contract")
