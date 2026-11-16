#!/usr/bin/env python3
"""Regression contract for inherited versus explicit HLS node profiles."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "app/main.py").read_text(encoding="utf-8")
manager = (ROOT / "app/node_manager.py").read_text(encoding="utf-8")
template = (ROOT / "app/templates/channel_form.html").read_text(encoding="utf-8")

assert '"effective_hls_segment_time"' in source
assert "int(hls_segment_time) if hls_segment_time is not None else int(channel.hls_segment_time or 1)" in source
assert '"hls_segment_time": profile.get("hls_segment_time") or channel.hls_segment_time' in manager
assert 'value="inherit"' in template
assert 'name="node_hls_segment_times"' in template
assert 'Use channel default' in template

print("PASS: inherited and explicit per-Node HLS profile contract")
