#!/usr/bin/env python3
"""v12.63 Main integration regression without overwriting in-repo Main source."""
from pathlib import Path
import ast
import json
import runpy
import tempfile

root=Path(__file__).resolve().parents[1]
namespace=runpy.run_path(str(root/"scripts/apply_v1263_vault.py"))
transform=namespace["transform"]
source=(root/"app/main.py").read_text(encoding="utf-8")
updated=transform(source)
if "STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263" in source:
    assert updated == source, "integrated release source must stay unchanged"
else:
    assert updated != source, "pre-release source must receive the patch"
assert transform(updated)==updated, "patch must be idempotent"
compile(updated, "app/main.py", "exec")
for marker in (
    "STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263",
    "app.include_router(panel_vault_router)",
    "vault_active=can_vault_reveal(actor)",
    "vault_active=can_vault_manage(actor)",
    "vault_csrf_token=vault_csrf_token(request)",
    "vault_remember: Optional[str] = Form(None)",
    "vault_store(db, panel_user, password)",
    "vault_discard(db, panel_user.id)",
    "vault_discard(db, admin.id)",
    "Node password changes revoke the saved secret",
    "vault_discard(db, panel_user.id)  # panel-user delete",
    '"panel_users.password_vault_manage"',
    '"panel_users.password_reveal"',
    "Vault role permissions changed",
):
    assert marker in updated, marker
assert source.count("admin.password_hash = hash_password(new_password)")>=1
assert updated.count("if as_bool(vault_remember)")>=2
assert updated.count("vault_active=can_vault_reveal(actor)") == 1
assert updated.count("vault_active=can_vault_manage(actor)") == 4
assert "if key in ALL_PERMISSION_KEYS and key not in" in updated
# All credential storage changes keep the current scrypt verifier.
assert "password_hash = hash_password(password)" in updated
with tempfile.TemporaryDirectory() as t:
    path=Path(t)/"main.py"
    path.write_text(source,encoding="utf-8")
    namespace["main"]() if False else None
    path.write_text(transform(path.read_text()),encoding="utf-8")
    assert path.read_text()==updated
# Compile/execute only the transformed role-grant function, not the full service.
# This checks that delegated roles cannot turn their own reveal permission into
# grant authority, while the unrestricted Super Admin can grant both permissions.
from types import SimpleNamespace
# This static integration test runs before third-party packages are installed.
# Use in-memory role permission fixtures rather than importing the Main ORM.
AdminUser = SimpleNamespace
SUPERUSER_PERMISSION = "*"
PERMISSION_KEYS = {"panel_users.view", "panel_users.password_reveal",
                   "panel_users.password_vault_manage", "roles.create", "roles.edit"}
ALL_PERMISSION_KEYS = tuple(sorted(PERMISSION_KEYS))
role_permission_set = lambda role: set(json.loads(role.permissions))
updated_ast = ast.parse(updated)
grant_function = next(
    node for node in updated_ast.body
    if isinstance(node, ast.FunctionDef) and node.name == "role_assignable_permissions_for_admin"
)
grant_ns = {
    "AdminUser": AdminUser,
    "ALL_PERMISSION_KEYS": ALL_PERMISSION_KEYS,
    "role_permission_set": role_permission_set,
    "admin_is_super_admin": lambda actor: SUPERUSER_PERMISSION in role_permission_set(actor.role),
}
exec(compile(ast.Module(body=[grant_function], type_ignores=[]), "<rbac-grant>", "exec"), grant_ns)
grant = grant_ns["role_assignable_permissions_for_admin"]
super_actor = SimpleNamespace(role=SimpleNamespace(permissions=json.dumps(["*"])))
delegated_actor = SimpleNamespace(role=SimpleNamespace(
    permissions=json.dumps(["panel_users.view", "panel_users.password_reveal",
                            "panel_users.password_vault_manage", "roles.create", "roles.edit"]),
))
assert {"panel_users.password_reveal", "panel_users.password_vault_manage"}.issubset(grant(super_actor))
assert not ({"panel_users.password_reveal", "panel_users.password_vault_manage"} & grant(delegated_actor))
assert {"roles.create", "roles.edit"}.issubset(grant(delegated_actor))
print("PASS: v12.63 scoped Main patch, privilege grant ceiling, and preserved auth")
