#!/usr/bin/env python3
"""Bounded v12.66 Main patch on top of integrated v12.65 Main.

Automatic recoverable credential retention is shown on the Main form; the
updated Remote Node explicitly reports that its form showed the disclosure.
Older Nodes retain hash-only behavior rather than silently escrow passwords.
"""
from pathlib import Path
import sys

MARKER = "STREAMFORGE_V1266_AUTO_VAULT"

def transform(source: str) -> str:
    if MARKER in source:
        return source
    if "STREAMFORGE_V1264_IP_GUARD" not in source or "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" not in source:
        raise ValueError("Expected integrated v12.65 Main login guard")
    replacements = [
        ("    vault_remember: Optional[str] = Form(None),\n", "", 2, "remove obsolete admin consent input"),
        ("    elif as_bool(vault_remember) and not (can_vault_manage(actor) and vault_enabled()):\n        error = \"Vault is not configured or you are not permitted to save credentials.\"\n", "", 2, "remove old checkbox privilege validation"),
        ("    if as_bool(vault_remember):\n        if not vault_store(db, panel_user, password):\n            raise HTTPException(503, \"Credential vault is not configured\")\n    db.commit()", "    # STREAMFORGE_V1266_AUTO_VAULT: the managed-password policy is automatic.\n    if not vault_store(db, panel_user, password):\n        raise HTTPException(503, \"Encrypted password vault unavailable; account not created\")\n    db.commit()", 1, "create auto-save"),
        ("    if password:\n        panel_user.password_hash = hash_password(password)\n        if as_bool(vault_remember):\n            if not vault_store(db, panel_user, password):\n                raise HTTPException(503, \"Credential vault is not configured\")\n        else:\n            vault_discard(db, panel_user.id)  # never show stale saved passwords\n    db.commit()", "    if password:\n        panel_user.password_hash = hash_password(password)\n        if not vault_store(db, panel_user, password):\n            raise HTTPException(503, \"Encrypted password vault unavailable; password unchanged\")\n    db.commit()", 1, "admin reset auto-save"),
        ("    admin.password_hash = hash_password(new_password)\n    vault_discard(db, admin.id)  # self-service changes invalidate old vault value\n", "    if not vault_enabled():\n        return render(request, \"account_password.html\", db, message=\"\", error=\"Managed password storage is unavailable; password not changed.\")\n    admin.password_hash = hash_password(new_password)\n    if not vault_store(db, admin, new_password):\n        raise HTTPException(503, \"Encrypted password vault unavailable; password unchanged\")\n", 1, "Main self-service auto-save"),
        ("        admin.password_hash = hash_password(new_password)\n        vault_discard(db, admin.id)  # Node password changes revoke the saved secret\n", "        admin.password_hash = hash_password(new_password)\n        if payload.get(\"managed_password_notice\") is True:\n            # The updated Node displayed the managed-credential notice.\n            if not vault_store(db, admin, new_password):\n                raise HTTPException(503, \"Encrypted password vault unavailable; password unchanged\")\n        else:\n            # Legacy Node versions did not display the notice. Do not silently escrow.\n            vault_discard(db, admin.id)\n", 1, "Remote Node self-service gated on form notice"),
    ]
    for old, new, expected, label in replacements:
        found = source.count(old)
        if found != expected:
            raise ValueError(f"{label}: unexpected source anchor count {found} != {expected}")
        source = source.replace(old, new)
    compile(source, "app/main.py", "exec")
    return source

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_v1266_auto_vault.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    old = path.read_text(encoding="utf-8")
    new = transform(old)
    if new != old:
        path.write_text(new, encoding="utf-8")
    print("PASS: v12.66 automatic managed password retention patch")

if __name__ == "__main__":
    main()
