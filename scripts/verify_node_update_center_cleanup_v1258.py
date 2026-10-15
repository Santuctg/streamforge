#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
bulk = (ROOT / "app/templates/node_updates.html").read_text(encoding="utf-8")
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
node_version = (ROOT / "node_agent/VERSION").read_text(encoding="utf-8").strip()

errors: list[str] = []

def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)

require(version == "12.58", "Main VERSION must be 12.58")
require(node_version == "12.58", "bundled Node VERSION must be 12.58")
require("STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258" in main, "v12.58 cleanup marker missing")

# Advanced controls must stay inside Node Updates instead of navigating to the legacy page.
require('href="/nodes/{{ node.id }}/update"' not in bulk, "Advanced must not link to the legacy single-node page")
require('data-advanced-toggle' in bulk, "inline Advanced toggle missing")
require('data-advanced-panel' in bulk, "inline Advanced panel missing")
require('/nodes/{{ node.id }}/update/api' in bulk, "inline per-node API update action missing")
require('/nodes/{{ node.id }}/update/ssh' in bulk, "inline SSH recovery action missing")

# The bulk form must remain structurally independent from per-Node forms, and a
# cancelled confirmation must not leave Update Selected disabled.
require('form="node-bulk-update-form"' in bulk, "bulk checkboxes/button must target the standalone bulk form")
require("bulkForm.addEventListener('submit'" not in bulk, "cancelled bulk confirmation must not disable Update Selected")

# Node names are admin data, not JavaScript literals. Keep inline confirmation
# strings static so quotes in a Node name cannot break an event-handler string.
require("confirm('Update {{ node.name" not in bulk, "API confirmation must not interpolate Node name into inline JavaScript")
require("confirm('Reinstall and restart {{ node.name" not in bulk, "SSH confirmation must not interpolate Node name into inline JavaScript")

# Legacy GET stays compatible but redirects into the unified Update Center.
legacy_get = re.search(
    r'@app\.get\("/nodes/\{node_id\}/update".*?\ndef node_update_page\(.*?\n(?P<body>.*?)(?=\n\ndef |\n@app\.)',
    main,
    re.S,
)
require(legacy_get is not None, "legacy single-node GET route missing")
if legacy_get:
    body = legacy_get.group("body")
    require("RedirectResponse" in body and "/nodes/updates" in body, "legacy GET must redirect to Node Updates")
    require('render(' not in body or '"node_update.html"' not in body, "legacy GET must not render node_update.html")

# All individual update redirects must return to the unified center.
redirect_helper = re.search(
    r'def node_update_redirect\(.*?\n(?P<body>.*?)(?=\n\ndef |\n@app\.)',
    main,
    re.S,
)
require(redirect_helper is not None, "node_update_redirect helper missing")
if redirect_helper:
    body = redirect_helper.group("body")
    require("/nodes/updates" in body, "individual update redirects must return to Node Updates")
    require("/nodes/{node_id}/update" not in body, "redirect helper must not return to legacy update page")

# Live polling must be able to refresh the displayed installed Node version.
status_route = re.search(
    r'@app\.get\("/nodes/\{node_id\}/update/status".*?\ndef node_update_status\(.*?\n(?P<body>.*?)(?=\n\ndef |\n@app\.)',
    main,
    re.S,
)
require(status_route is not None, "Node update status route missing")
if status_route:
    require("current_version" in status_route.group("body"), "status JSON must expose current_version")
require("data-current-version" in bulk, "current-version cell missing")
require("data.current_version" in bulk, "bulk polling must refresh current version")

if errors:
    raise SystemExit("FAIL: " + "; ".join(errors))
print("PASS: v12.58 Node Update Center cleanup contract verified")
