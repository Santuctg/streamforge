#!/usr/bin/env python3
"""v13.03 dark autofill CSS release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.03":
    print("SKIP: v13.03 release round-trip")
    raise SystemExit(0)

# --- Dark autofill CSS fix ------------------------------------------------
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1303_DARK_AUTOFILL_CSS",
    "input:-webkit-autofill",
    "textarea:-webkit-autofill",
    "-webkit-text-fill-color:var(--text)",
    "-webkit-box-shadow:0 0 0 1000px #0b1118 inset",
):
    assert marker in style, marker
assert style.count("{") == style.count("}"), "style.css braces unbalanced"

# --- Version and updater wiring -------------------------------------------
for path in ("VERSION", "node_agent/VERSION"):
    assert (ROOT / path).read_text().strip() == "13.03", path

updater = (ROOT / "scripts" / "force_update_v1303.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1303_DARK_AUTOFILL_CSS_UPDATER",
    "app/static/style.css",
    "STREAMFORGE_V1303_STATIC_CACHE",
    "sync_main_static_cache.py",
    "STREAMFORGE_V1303_TEST_FAIL_AFTER_WRITE",
    "13.01|13.02|13.03",
    'OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
):
    assert marker in updater, marker
assert "systemctl restart nginx" not in updater
assert "force_update_v1303.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

staging = (ROOT / "scripts" / "verify_release_updater_staging.py").read_text(encoding="utf-8")
assert "STREAMFORGE_V1303_DARK_AUTOFILL_CSS_UPDATER" in staging
assert "v13.03 contract missing" in staging

# --- Release notes, changelog and docs ------------------------------------
notes = (ROOT / "release-notes" / "v13.03.md").read_text(encoding="utf-8")
assert "streamforge-update --force /root/streamforge-13.03.zip" in notes

changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert changelog.startswith("## 13.03 - ")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "releases/tag/v13.03" in readme
assert "streamforge-13.03.zip" in readme

installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
assert "VERSION=13.03\n" in installation

api = (ROOT / "docs" / "API_AND_TESTING.md").read_text(encoding="utf-8")
for name in ("test_v1303_updater.py", "verify_v1303_patch_roundtrip.py"):
    assert name in api, name

# --- v13.02 Notification Center contract still intact ----------------------
page = (ROOT / "app" / "templates" / "notifications.html").read_text(encoding="utf-8")
Environment().parse(page)
for marker in (
    'action="/system/notifications/settings"',
    'action="/system/notifications/test"',
    "notification-summary-grid",
    "notification-recipient-grid",
    "@media(max-width:700px)",
):
    assert marker in page, marker

main_updates = (ROOT / "app" / "templates" / "main_updates.html").read_text(encoding="utf-8")
assert "confirm('Update Main Server to v{{" not in main_updates
assert "confirm('Update the Main Server now?" in main_updates

print("PASS: v13.03 dark autofill CSS, static cache republish, updater, release notes and Notification Center contract")
