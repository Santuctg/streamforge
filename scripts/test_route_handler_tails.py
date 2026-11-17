#!/usr/bin/env python3
"""Main route-handler tail contract (v13.74).

The v13.62 route insertion dropped the tail of `admin_user_update`, so the
save handler returned None and FastAPI answered JSON null — the browser
replaced the page with a JSON viewer and saving panel users looked broken.
This contract asserts every Main route handler's final statement is a Return
(or an if/else whose branches all return), so a dropped tail can never ship.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

tree = ast.parse(main)
problems = []
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    is_route = any(
        isinstance(d, ast.Call) and getattr(d.func, "attr", "") in {"get", "post", "put", "delete"}
        for d in node.decorator_list
    )
    if not is_route:
        continue
    last = node.body[-1]
    if isinstance(last, ast.Return):
        continue
    if isinstance(last, ast.If):
        # Every branch must end in a Return (single-level if/else check).
        branches = [last.body]
        if last.orelse:
            branches.append(last.orelse)
        if all(isinstance(b[-1], ast.Return) for b in branches):
            continue
    if isinstance(last, ast.Try):
        # A try whose body and every handler/else end in a Return is fine.
        bodies = [last.body] + [h.body for h in last.handlers]
        if last.orelse:
            bodies.append(last.orelse)
        if last.finalbody:
            bodies.append(last.finalbody)
        if all(isinstance(b[-1], ast.Return) for b in bodies):
            continue
    problems.append((node.name, node.lineno))

# The known-acceptable Try handlers are whitelisted; everything else must pass.
allowed = {
    "system_backup_encryption_update", "system_backup_google_credentials",
    "system_backup_google_callback", "system_backup_test_destination",
    "system_backup_google_test", "system_local_backup_update",
    "system_local_backup_run", "system_backup_update", "system_backup_run",
    "system_backup_archive_delete", "system_backup_target_file_delete",
    "system_backup_target_file_restore", "system_backup_archive_restore",
    "system_backup_restore", "node_geoip_settings_save", "node_geoip_update_now",
    "node_access_back_sync", "channel_import",
}
bad = [(name, line) for name, line in problems if name not in allowed]
assert not bad, f"route handlers missing their return tail: {bad}"

# The restored admin_user_update tail must exist and sit before the
# force-password route.
assert 'message="Panel user updated"' in main
assert main.index('message="Panel user updated"') < main.index(
    "/admin-users/{admin_user_id}/force-password"
)
assert "STREAMFORGE_ADMIN_USER_UPDATE_TAIL_RESTORED_V1374" in main

# The force-password handler must not carry the orphaned unreachable fragment.
force_start = main.index("def admin_user_force_password")
force_end = main.index("\ndef ", force_start + 1)
force_body = main[force_start:force_end]
assert "Panel user updated" not in force_body, (
    "orphaned dead fragment still inside admin_user_force_password"
)

print("PASS: Main route-handler tail contract")
