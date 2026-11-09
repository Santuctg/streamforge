#!/usr/bin/env python3
"""v13.18 info-button help text contract.

Every inline panel help text (class="field-help" plus sentence-like bare
<small> captions inside labels) is converted at runtime by one IIFE in
app/static/app.js into a 15px info button next to its caption; the original
help element keeps its identity (hidden with .sf-help-source) so dynamic
texts such as data-destination-help keep updating, and its HTML is cloned
into a fixed white bubble with a downward pointer.
"""
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "13.18":
    print("SKIP: v13.18 help buttons contract")
    raise SystemExit(0)

MARKER = "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318"
app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")

# --- markers in both static assets ----------------------------------------
assert MARKER in app_js, "app.js lacks the v13.18 help-button marker"
assert MARKER in style, "style.css lacks the v13.18 help-button marker"
assert style.count("{") == style.count("}"), "style.css braces unbalanced"

# --- conversion rules ------------------------------------------------------
# Buttons are real buttons that can never submit a form or toggle a label's
# checkbox/radio (click handler also stops propagation).
assert "btn.type = 'button'" in app_js
assert "event.preventDefault()" in app_js and "event.stopPropagation()" in app_js
# The source element keeps its identity and is hidden, not removed.
assert "SOURCE_CLASS = 'sf-help-source'" in app_js
assert "body .sf-help-source{display:none!important}" in style
# Single fixed white bubble with a pointer, below-flip and clamped arrow.
for token in (
    "BUBBLE_CLASS = 'sf-help-bubble'",
    "'sf-help-btn'",
    "CAPTION_CLASS = 'sf-help-title'",
    "sf-help-standalone",
    "role', 'tooltip'",
    "aria-expanded",
    "aria-controls",
):
    assert token in app_js, token
for token in (
    "body .sf-help-bubble.sf-help-bubble{",
    "position:fixed;left:0;top:0;z-index:20000",
    "body .sf-help-bubble.sf-help-bubble::after{",
    "body .sf-help-bubble.sf-help-bubble.sf-help-below::after{",
    "body .sf-help-btn.sf-help-btn{",
    "min-height:15px",
    "--sf-help-arrow-x",
):
    assert token in style, token

# --- markup preserved: templates still ship every help text ----------------
total = 0
for path in sorted((ROOT / "app" / "templates").glob("*.html")):
    total += len(re.findall(r'class="[^"]*\bfield-help\b', path.read_text(encoding="utf-8")))
assert total >= 99, f"expected >=99 field-help elements in templates, found {total}"

# --- JavaScript syntax gate ------------------------------------------------
node = shutil.which("node")
if node:
    subprocess.run([node, "--check", str(ROOT / "app" / "static" / "app.js")], check=True)

print(f"PASS: v13.18 info-button help contract ({total} field-help elements preserved in templates)")
