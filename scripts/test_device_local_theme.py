#!/usr/bin/env python3
"""Device-local Dark/Light theme contracts across all browser surfaces."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
base = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
main_css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
webplayer = (ROOT / "app/templates/web_player.html").read_text(encoding="utf-8")

for source, name in ((base, "Main template"), (app_js, "Main JS"), (node, "Node"), (webplayer, "Web Player")):
    assert "streamforge.theme" in source, f"{name} does not use the shared device theme key"

assert "data-theme-toggle" in base, "Main theme toggle markup is missing"
assert "dataset.theme='light'" in base or 'dataset.theme="light"' in base, "Main light theme bootstrap is missing"
assert "dataset.theme=t" in node, "Node light theme bootstrap is missing"
assert "data-theme-toggle" in node, "Node theme toggle is missing"
assert "data-theme-toggle" in webplayer, "Web Player theme toggle is missing"
assert "[data-theme=\"light\"]" in main_css, "Main light variable overrides are missing"

print("PASS: device-local theme contract")
