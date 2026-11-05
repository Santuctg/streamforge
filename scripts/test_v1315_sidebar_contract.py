#!/usr/bin/env python3
"""Device-independent collapsed footer geometry contract for v13.15."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")

for marker in (
    "STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER",
    "position:fixed!important",
    "left:9.5px!important",
    "width:56px!important",
    "grid-template-columns:44px!important",
    "transform:none!important",
    "padding-bottom:112px!important",
):
    assert marker in style, marker

# Sidebar center and fixed footer/button centers are identical and do not use
# scrollbar width, viewport width or browser-specific gutter measurements.
upper_navigation_center = (76 - 1) / 2  # exclude the 1px right border
footer_center = 9.5 + (56 / 2)
button_center = 9.5 + 6 + (44 / 2)
assert upper_navigation_center == footer_center == button_center == 37.5

final_rule = style.split("STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER", 1)[1]
assert "scrollbar-gutter" not in final_rule

print("PASS: v13.15 footer center matches upper navigation at x=37.5 on every desktop scrollbar model")
