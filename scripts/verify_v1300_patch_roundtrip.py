#!/usr/bin/env python3
"""v13.00 Main Updates responsive UI release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.00":
    print("SKIP: v13.00 release round-trip")
    raise SystemExit(0)

page_path = ROOT / "app/templates/main_updates.html"
page = page_path.read_text(encoding="utf-8")
Environment().parse(page)
for marker in (
    'action="/system/updates/apply"',
    "View release notes",
    "SHA256-verified",
    "main-updates-page",
    "main-update-version-grid",
    "main-update-security-grid",
    "main-update-job-steps",
    "main-update-install-button",
    "@media(max-width:700px)",
    "@media(max-width:430px)",
    "setTimeout(function(){ location.reload(); }, 5000)",
):
    assert marker in page, marker
for legacy in ('<div class="notice">', '<div class="error">', ".main-update-grid{"):
    assert legacy not in page, legacy
assert page.count('<form method="post" action="/system/updates/apply"') == 1
assert "confirm('Update Main Server to v{{ latest.version }}?" in page

print("PASS: v13.00 responsive Main Updates layout, alerts, actions and refresh contract")
