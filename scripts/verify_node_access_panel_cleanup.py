#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "node_agent" / "app.py").read_text(encoding="utf-8")
main_template = (root / "app" / "templates" / "node_form.html").read_text(encoding="utf-8")

removed_from_node_ui = (
    "Node Panel/API access URLs",
    "Playlist/App access URLs",
    "Panel access rules",
    "Playback & catalog access rules",
)

errors = []
for label in removed_from_node_ui:
    if label in source:
        errors.append(f"Node Agent access panel still contains: {label}")

# This change is Node-only. The Main server's node form must remain intact.
for label in removed_from_node_ui:
    if label not in main_template:
        errors.append(f"Main server node form was unexpectedly changed: {label}")

# Hidden/preserved values must remain available to the Node-side save flow so
# removing visible controls cannot wipe existing access configuration.
for marker in (
    "STREAMFORGE_NODE_ACCESS_PANEL_PRESERVE_HIDDEN_V1240",
    "name=\"api_url\"",
    "name=\"playlist_url\"",
    "name=\"panel_ip_whitelist\"",
    "name=\"panel_ip_blacklist\"",
    "name=\"panel_asn_whitelist\"",
    "name=\"panel_asn_blacklist\"",
    "name=\"ip_whitelist\"",
    "name=\"ip_blacklist\"",
    "name=\"asn_whitelist\"",
    "name=\"asn_blacklist\"",
):
    if marker not in source:
        errors.append(f"Node Agent preservation marker/input missing: {marker}")

if errors:
    raise SystemExit("\n".join(f"FAIL: {item}" for item in errors))

print("Node Access Panel cleanup regression guard: PASS")
