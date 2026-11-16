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
    assert "place-items:center" in source, f"{name} checkmark alignment is missing"

assert "STREAMFORGE_GLOBAL_CHECKBOX_THEME_V1337" in main_css, "Main global checkbox theme is missing"
assert "STREAMFORGE_GLOBAL_CHECKBOX_THEME_V1337" in node_source, "Node global checkbox theme is missing"
assert "clip-path:polygon" in main_css, "Main checkmark needs a font-independent geometric shape"
assert "clip-path:polygon" in node_source, "Node checkmark needs a font-independent geometric shape"
assert "transform:none" in main_css
assert "transform:none" in node_source

print("PASS: Main and Node checkbox theme contract")
