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
    "STREAMFORGE_HELP_CONTAINER_CAPTION_MOUNT_V1326",
    "STREAMFORGE_HELP_EXPLICIT_CONTAINER_CAPTION_MOUNT_V1327",
    "STREAMFORGE_HELP_STANDALONE_CAPTION_RESOLUTION_V1328",
):
    assert marker in js, marker
for marker in (
    "STREAMFORGE_HELP_BUTTON_WRAPPED_TITLE_LAYOUT_V1321",
    "sf-help-caption-text",
    "sf-help-title-wrapped{display:inline-block!important",
    "STREAMFORGE_CHANNEL_FAILBACK_COMPACT_BOX_V1323",
    "STREAMFORGE_HELP_BUTTON_LABEL_ROW_AUTO_V1324",
    "STREAMFORGE_CHANNEL_FAILBACK_COMPACT_BOX_V1325",
    "STREAMFORGE_CATEGORY_FIELD_SAME_ROW_V1329",
    "STREAMFORGE_HLS_SEGMENT_LABEL_ONE_LINE_V1329",
    "STREAMFORGE_ENCODING_LABEL_ROW_AUTO_V1329",
    "STREAMFORGE_CATEGORY_PICKER_COMPACT_WIDTH_V1330",
):
    assert marker in css, marker

subprocess.run(["python3", "-m", "py_compile", str(ROOT / "app/main.py")], check=True)
assert css.count("{") == css.count("}")
print("PASS: v13.21 access actors and wrapped help-button layout contract")
