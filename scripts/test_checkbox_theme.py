#!/usr/bin/env python3
"""Regression contract for unified dark themed Main and Node checkboxes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main_css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
light_css = (ROOT / "app/static/theme-light.css").read_text(encoding="utf-8")
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
assert "left:50%" in main_css and "top:50%" in main_css
assert "transform:translate(-50%,-50%)" in main_css
assert "left:50%" in node_source and "top:50%" in node_source
assert "transform:translate(-50%,-50%)" in node_source
assert "transform:none" in main_css
assert "transform:none" in node_source

# In the light theme a checked box must stay visible: accent fill + dark tick.
assert "STREAMFORGE_LIGHT_CHECKBOX_TICK_V1350" in light_css, "light checkbox tick fix is missing"
assert 'input[type=checkbox]:checked{background:#37c7a2!important;border-color:#37c7a2!important}' in light_css
assert 'input[type=checkbox]:checked::after{background:#071c18!important}' in light_css
assert 'input[type=checkbox]:checked::after{background:#f2f6fa' not in light_css, (
    "light theme paints the checkmark white, hiding it"
)

print("PASS: Main and Node checkbox theme contract")
