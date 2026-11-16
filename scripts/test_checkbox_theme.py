#!/usr/bin/env python3
"""Regression contract for unified dark themed Main and Node checkboxes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main_css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
node_source = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

for source, name in ((main_css, "Main"), (node_source, "Node")):
    assert "appearance:none" in source, f"{name} checkbox still uses native appearance"
    assert "input[type=checkbox]:checked" in source, f"{name} checked checkbox theme is missing"
    assert "#101b27" in source, f"{name} unchecked checkbox background is missing"
    assert "transform:rotate(45deg)" in source, f"{name} checkmark alignment is missing"

print("PASS: Main and Node checkbox theme contract")
