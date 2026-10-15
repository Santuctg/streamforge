#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
template = (ROOT / "app/templates/node_updates.html").read_text(encoding="utf-8")
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
node_version = (ROOT / "node_agent/VERSION").read_text(encoding="utf-8").strip()

errors: list[str] = []

def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)

# Historical v12.59 UI behavior must remain valid on later releases. Exact
# updater/package tests are gated in the v12.59 workflow itself.
require(version_key(version) >= (12, 59), "Main VERSION must be v12.59 or later")
require(node_version == version, "bundled Node VERSION must stay aligned with Main")
require("STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259" in template, "v12.59 UI spacing marker missing")

# Toolbar controls should read as one intentional control bar, not loose text and
# a button floating directly against the table/header.
require('class="node-update-bulk-selection"' in template, "bulk selection controls must be grouped")
require('class="node-update-bulk-actions"' in template, "bulk action controls must be grouped")
require(
    re.search(r"\.node-update-bulk-toolbar\{[^}]*padding:[^;}]+", template) is not None,
    "bulk toolbar must have internal padding",
)
require(
    re.search(r"\.node-update-bulk-toolbar\{[^}]*border:1px solid var\(--border\)", template) is not None,
    "bulk toolbar must have a visible border",
)
require(
    re.search(r"\.node-update-bulk-toolbar\{[^}]*border-radius:[^;}]+", template) is not None,
    "bulk toolbar must have rounded container styling",
)
require(
    re.search(r"\.node-update-bulk-toolbar\{[^}]*justify-content:space-between", template) is not None,
    "bulk toolbar must keep selection and action groups separated",
)

# The expanded Advanced row must not visually touch the parent Node row.
advanced = re.search(r"\.node-update-advanced-row>td\{([^}]*)\}", template)
require(advanced is not None, "advanced-row spacing rule missing")
if advanced:
    css = advanced.group(1)
    require(
        re.search(r"padding:(?:[1-9]\d*px|[1-9]\d*\.\d+(?:rem|em))\s+", css) is not None
        or re.search(r"padding-top:(?:[1-9]\d*px|[1-9]\d*\.\d+(?:rem|em))", css) is not None,
        "Advanced panel must have positive top spacing from its parent Node row",
    )

# v12.58 behavior must remain intact.
for marker in (
    'form="node-bulk-update-form"',
    'data-select-all',
    'data-update-selected',
    'data-advanced-toggle',
    'data-advanced-panel',
    'data.current_version',
):
    require(marker in template, f"v12.58 behavior missing after UI fix: {marker}")

if errors:
    raise SystemExit("FAIL: " + "; ".join(errors))
print("PASS: v12.59 Node Update Center toolbar and Advanced spacing contract verified")
