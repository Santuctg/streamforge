#!/usr/bin/env python3
"""Node panel permission audit contract.

Every Node panel permission check must reference a permission that exists in
the Main permission catalog, because panel roles are authorised live by Main
and `decode_permissions` intersects with that catalog. The v13.53-era audit
found three legacy strings (logs.manage, stream_users.kill,
categories.manage) that never matched for any non-Super-Admin user.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
node = (ROOT / "node_agent" / "app.py").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
perm_src = (ROOT / "app" / "permissions.py").read_text(encoding="utf-8")

defined = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', perm_src))
assert defined, "permission catalog is empty"

# --- 1. every permission-shaped string used by the Node panel must be defined ---
# Ignore known non-permission strings: file names in the updater payload, the
# shared localStorage key and public endpoint filenames.
ignore = {
    "youtube.com", "youtu.be", "app.py", "redis_state.py", "requirements.txt",
    "cache_clear.sh", "fetch_hlsjs.sh", "get.php", "player_api.php",
    "streamforge.theme",
}
unknown = []
for match in re.finditer(r"['\"]([a-z_]+\.[a-z_]+)['\"]", node):
    permission = match.group(1)
    if permission in defined or permission in ignore:
        continue
    line = node.count("\n", 0, match.start()) + 1
    unknown.append((permission, line))
assert not unknown, f"Node panel uses undefined permissions: {unknown}"

# --- 2. the legacy strings must be gone entirely --------------------------------
for legacy in ("logs.manage", "stream_users.kill", "categories.manage"):
    assert legacy not in node, f"legacy permission {legacy} is still used by the Node panel"

# --- 3. the corrected guards must exist ----------------------------------------
required = (
    'require_node_panel_user(request, "logs.clear")',
    "require_node_panel_user(request, 'sessions.kill')",
    "require_node_panel_user(request, 'categories.reorder')",
    "require_node_panel_user(request, 'categories.edit')",
    "require_node_panel_user(request, 'categories.delete')",
    'require_node_panel_user(request, "channels.playback")',
    "require_node_panel_user(request, 'channels.info')",
    'STREAMFORGE_NODE_PERM_AUDIT_V1360',
)
for marker in required:
    assert marker in node, f"Node panel permission guard missing: {marker}"

assert "_has_panel_permission(user, \"channels.playback\")" in node, (
    "the dashboard HLS preview button must be gated by channels.playback"
)
assert node.count('STREAMFORGE_NODE_PERM_AUDIT_V1360') >= 12, (
    "the audit fix markers must be present on every corrected site"
)

# --- 4. Main still carries the machine-to-machine policy bypass -----------------
assert '"/api/v1/node-channel-hls-segment/"' in main, (
    "the per-Node HLS override must stay exempt from Main's browser IP/ASN policy"
)
assert main.index('"/api/v1/node-channel-hls-segment/"') > main.index("node_internal_prefixes = (")

print("PASS: Node panel permission audit contract")
