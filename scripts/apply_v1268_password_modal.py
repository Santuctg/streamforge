#!/usr/bin/env python3
"""Bounded v12.68 Main Panel Users CSRF-token context patch.

The password reset popup must work for edit-capable roles, not only
Super Admin/reveal users. No edit privilege is granted by this change.
"""
from pathlib import Path
import sys

MARKER = "STREAMFORGE_V1268_PW_MODAL_CSRF"
OLD = 'vault_csrf_token=(vault_csrf_token(request) if admin_is_super_admin(actor) or (can_vault_reveal(actor) and vault_enabled()) else ""),'
NEW = ('vault_csrf_token=(vault_csrf_token(request) if '
       'admin_is_super_admin(actor) or has_permission(actor, "panel_users.edit") '
       'or (can_vault_reveal(actor) and vault_enabled()) else ""),  # ' + MARKER)

def transform(source: str) -> str:
    if MARKER in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("Unexpected Panel Users CSRF context; refusing patch")
    result = source.replace(OLD, NEW)
    compile(result, "app/main.py", "exec")
    return result

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_v1268_password_modal.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if before != after:
        path.write_text(after, encoding="utf-8")
    print("PASS: bounded Panel Users password-popup CSRF context")

if __name__ == "__main__":
    main()
