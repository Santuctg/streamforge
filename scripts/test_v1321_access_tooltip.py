#!/usr/bin/env python3
"""v13.21 attempted-actor and wrapped-help layout contract."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")

for marker in (
    "STREAMFORGE_ACCESS_LOG_UNAUTHENTICATED_ACTOR_V1321",
    "Unauthenticated",
    "Failed panel login for {username.strip() or 'unknown'}",
):
    assert marker in main, marker
for marker in (
    "STREAMFORGE_HELP_BUTTON_CAPTION_TEXT_V1321",
    "sf-help-caption-text",
):
    assert marker in js, marker
for marker in (
    "STREAMFORGE_HELP_BUTTON_WRAPPED_TITLE_LAYOUT_V1321",
    "sf-help-caption-text",
):
    assert marker in css, marker

subprocess.run(["python3", "-m", "py_compile", str(ROOT / "app/main.py")], check=True)
assert css.count("{") == css.count("}")
print("PASS: v13.21 access actors and wrapped help-button layout contract")
