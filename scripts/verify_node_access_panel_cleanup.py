#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "node_agent" / "app.py").read_text(encoding="utf-8")
main_template = (root / "app" / "templates" / "node_form.html").read_text(encoding="utf-8")

settings_anchor = "content = alert + f'''<div class=\"form-layout node-settings-layout\">"
start = source.find(settings_anchor)
if start < 0:
    raise SystemExit("FAIL: Node Settings HTML anchor missing")
settings_html = source[start:start + 16000]

removed_from_node_ui = (
    "Node Panel/API access URLs",
    "Playlist/App access URLs",
    "Panel access rules",
    "Playback & catalog access rules",
)

errors = []
for label in removed_from_node_ui:
    if label in settings_html:
        errors.append(f"Node Agent access panel still contains: {label}")

# This change is Node-only. The Main server's node form must remain intact.
for label in removed_from_node_ui:
    if label not in main_template:
        errors.append(f"Main server node form was unexpectedly changed: {label}")

# Removing the controls must not erase values synchronized from Main. The
# Node-side Settings POST path must carry the current manager values forward.
required_preservation = (
    "STREAMFORGE_NODE_ACCESS_PANEL_PRESERVE_CONFIG_V1240",
    "panel_urls=list(manager.panel_urls)",
    "stream_urls=list(manager.stream_urls)",
    "panel_ip_whitelist=manager.panel_ip_whitelist",
    "panel_ip_blacklist=manager.panel_ip_blacklist",
    "panel_asn_whitelist=manager.panel_asn_whitelist",
    "panel_asn_blacklist=manager.panel_asn_blacklist",
    "ip_whitelist=manager.ip_whitelist",
    "ip_blacklist=manager.ip_blacklist",
    "asn_whitelist=manager.asn_whitelist",
    "asn_blacklist=manager.asn_blacklist",
)
for marker in required_preservation:
    if marker not in source:
        errors.append(f"Node Agent preservation logic missing: {marker}")

if errors:
    raise SystemExit("\n".join(f"FAIL: {item}" for item in errors))

print("Node Access Panel cleanup regression guard: PASS")
