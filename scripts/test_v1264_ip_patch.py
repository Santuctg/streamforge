#!/usr/bin/env python3
"""Bounded migration from 12.63 actor lockout to 12.64 IP lockout."""
from pathlib import Path
import runpy
import ast

root = Path(__file__).resolve().parents[1]
apply = runpy.run_path(str(root/"scripts/apply_v1264_ip_lock.py"))["transform"]
source = (root/"app/main.py").read_text(encoding="utf-8")
result = apply(source)
# Later releases integrate this patch and the v12.64-v12.68 patches into app/main.py;
# the pre-integration transform is covered by this test in its own release ZIP.
if "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" in source:
    assert result == source, "integrated release source must stay unchanged"
    print("PASS: v12.64 IP patch is a no-op on integrated Main source")
    raise SystemExit(0)
assert result != source or "STREAMFORGE_V1264_IP_GUARD" in source
assert apply(result) == result, "idempotence"
compile(result, "app/main.py", "exec")
for token in (
    "STREAMFORGE_V1264_IP_GUARD",
    "main_panel_client_ip(request)",
    "normalize_client_ip(submitted_client_ip)",
    "ip_locked(source_ip)",
    "record_ip_failure(source_ip)",
    "clear_ip_failures(source_ip)",
    "if not admin:",
    "if not allowed or not verify_password",
    "Vault role permissions changed",
    "vault_discard(db, admin.id)",
    "panel_vault_router",
):
    assert token in result, token
assert "login_locked(admin.id)" not in result
assert "record_login_failure(admin.id)" not in result
assert "clear_login_failures(admin.id)" not in result
node = result[result.index("async def node_panel_live_auth("):]
assert node.index("if ip_locked(source_ip)") < node.index("admin = db.scalar(")
assert node.index("record_ip_failure(source_ip)") < node.index("if action == \"login\":", node.index("auth_version = _panel_admin_auth_version(admin)"))
print("PASS: Main/Node IP login patch, unknown-user protection, no account locking, compile + idempotence")
