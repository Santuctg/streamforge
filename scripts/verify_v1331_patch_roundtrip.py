#!/usr/bin/env python3
"""v13.31 info-button help text release contract."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "13.31":
    print("SKIP: v13.31 release round-trip")
    raise SystemExit(0)

# --- Help-text info buttons (v13.31) ---------------------------------------
MARKER = "STREAMFORGE_HELP_TEXT_INFO_BUTTONS_V1318"
style = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
assert style.count("{") == style.count("}"), "style.css braces unbalanced"
for marker in (
    MARKER,
    "STREAMFORGE_COLLAPSED_FOOTER_DEVICE_INDEPENDENT_CENTER",
    "position:fixed!important", "left:9.5px!important", "width:56px!important",
    "body .sf-help-source{display:none!important}",
    "body .sf-help-bubble.sf-help-bubble{",
    "body .sf-help-btn.sf-help-btn{",
    "min-height:15px",
    "z-index:20000",
):
    assert marker in style, marker
app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
for marker in (
    MARKER,
    "btn.type = 'button'",
    "event.preventDefault()",
    "event.stopPropagation()",
    "SOURCE_CLASS = 'sf-help-source'",
    "BUBBLE_CLASS = 'sf-help-bubble'",
    "sf-help-standalone",
    "role', 'tooltip'",
    "data-destination-help",
):
    assert marker in app_js, marker

# --- SMS integration still intact ------------------------------------------
manager = (ROOT / "app" / "notification_manager.py").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_EXACT_UI_TEMPLATE_FIX", "contact_phone", "sms.maestro.com.bd", "DEFAULT_SMS_USER_BODY", "DEFAULT_SMS_NODE_BODY"):
    assert marker in manager, marker
for path in ("app/models.py", "app/db.py", "app/main.py", "app/templates/notifications.html"):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "sms_notifications" in text or "SMS" in text, path
node_form = (ROOT / "app/templates/node_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1312_NODE_OWNER_SINGLE_ROW", "node-owner-notification-row"):
    assert marker in node_form, marker
assert 'render_template(sms.user_body, values)' in manager

# --- Version and updater wiring -------------------------------------------
for path in ("VERSION", "node_agent/VERSION"):
    assert (ROOT / path).read_text().strip() == "13.31", path

updater = (ROOT / "scripts" / "force_update_v1331.sh").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_V1331_DEAD_INPUT_HELP_BUTTON_ROW_UPDATER",
    "app/static/app.js",
    "app/static/style.css",
    "STREAMFORGE_V1331_STATIC_CACHE",
    "sync_main_static_cache.py",
    "STREAMFORGE_V1331_TEST_FAIL_AFTER_WRITE",
    "13.01|13.02|13.03|13.04|13.05|13.06|13.07|13.08|13.09|13.10|13.11|13.12|13.13|13.14|13.15|13.16|13.17|13.18|13.19|13.20|13.21|13.22|13.23|13.24|13.25|13.26|13.27|13.28|13.29|13.30|13.31",
    "contact_phone",
    "sms_notifications",
    "sms.maestro.com.bd",
    'OLD_NODE_BLOB="43d18ca8056512029e2628ab8314b498b2c9fff4"',
    'NEW_NODE_BLOB="7cf9f6174af1fcfe802d3935e476864ad791139f"',
    MARKER,
):
    assert marker in updater, marker
assert "systemctl restart nginx" not in updater
assert "force_update_v1331.sh" in (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")

staging = (ROOT / "scripts" / "verify_release_updater_staging.py").read_text(encoding="utf-8")

# --- Release notes, changelog and docs ------------------------------------
notes = (ROOT / "release-notes" / "v13.31.md").read_text(encoding="utf-8")
assert "streamforge-update --force /root/streamforge-13.31.zip" in notes

changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert changelog.startswith("## 13.31 - ")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "releases/tag/v13.31" in readme
assert "streamforge-13.31.zip" in readme

installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
assert "VERSION=13.31\n" in installation

api = (ROOT / "docs" / "API_AND_TESTING.md").read_text(encoding="utf-8")
for name in ("test_v1331_updater.py", "verify_v1331_patch_roundtrip.py", "test_v1318_help_buttons.py"):
    assert name in api, name

# --- v13.11 Notification Center contract still intact ----------------------
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

main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
user_form = (ROOT / "app" / "templates" / "user_form.html").read_text(encoding="utf-8")
for marker in ("STREAMFORGE_V1314_GLOBAL_SYSTEM_TIMEZONE", '"status": "scheduled"', '"request_id": request_id'):
    assert marker in main, marker
for marker in ("data-message-modal", "modal.showModal()"):
    assert marker in page, marker
assert '/system/notifications/timezone' not in page
for marker in ("user-notification-settings", "<b>Email notifications</b>", "<b>SMS notifications</b>"):
    assert marker in user_form, marker
settings_page = (ROOT / "app" / "templates" / "system_branding.html").read_text(encoding="utf-8")
for marker in ('name="system_timezone"', "System time zone"):
    assert marker in settings_page, marker

# --- v13.16 Node WebPlayer duplicate stream_id contract --------------------
node_app = (ROOT / "node_agent" / "app.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_NODE_WEBPLAYER_UNIQUE_STREAM_REF_V1316",
    "STREAMFORGE_NODE_WEBPLAYER_KEY_REF_V1316",
    "STREAMFORGE_NODE_PLAYBACK_RESOLVE_KEY_FIRST_V1316",
    "urllib.parse.quote(channel.key, safe='')}/",
    "urllib.parse.quote(item.key, safe='')}/",
    "authorized_channel_map.setdefault(stream_ref, authorized_channel.key)",
):
    assert marker in node_app, marker
assert "urllib.parse.quote(str(item_stream_id), safe='')/" not in node_app
import hashlib
node_digest = hashlib.sha1((ROOT / "node_agent" / "app.py").read_bytes()).hexdigest()
assert node_digest == "7cf9f6174af1fcfe802d3935e476864ad791139f", node_digest

# --- v13.17 GitHub rate-limit contract carried unchanged -------------------
helper = (ROOT / "scripts" / "main_system_control.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317",
    "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317",
    "STREAMFORGE_GITHUB_TOKEN",
    "/releases/tags/",
):
    assert marker in helper, marker
for marker in ("STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317", "STREAMFORGE_GITHUB_TOKEN"):
    assert marker in main, marker
release_credit = main.split("def _github_latest_release", 1)[1].split("try:", 1)[0]
assert "< 300:" in release_credit

# --- v13.31 help buttons contract -----------------------------------------
import subprocess
subprocess.run(["python3", str(ROOT / "scripts" / "test_v1321_access_tooltip.py")], check=True)

print("PASS: v13.31 info-button help text release contract")
