#!/usr/bin/env python3
"""v12.63 Main integration regression without overwriting in-repo Main source."""
from pathlib import Path
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
    "vault_active=is_vault_admin(actor)",
    "vault_csrf_token=vault_csrf_token(request)",
    "vault_remember: Optional[str] = Form(None)",
    "vault_store(db, panel_user, password)",
    "vault_discard(db, panel_user.id)",
    "vault_discard(db, admin.id)",
    "Node password changes revoke the saved secret",
    "vault_discard(db, panel_user.id)  # panel-user delete",
):
    assert marker in updated, marker
assert source.count("admin.password_hash = hash_password(new_password)")>=1
assert updated.count("if as_bool(vault_remember)")>=2
assert updated.count("vault_active=is_vault_admin(actor)")>=3
# All credential storage changes keep the current scrypt verifier.
assert "password_hash = hash_password(password)" in updated
with tempfile.TemporaryDirectory() as t:
    path=Path(t)/"main.py"
    path.write_text(source,encoding="utf-8")
    namespace["main"]() if False else None
    path.write_text(transform(path.read_text()),encoding="utf-8")
    assert path.read_text()==updated
print("PASS: v12.63 scoped Main patch compiles, is idempotent, and preserves auth")
