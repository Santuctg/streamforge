#!/usr/bin/env python3
"""Patch a staged v12.83 tree for the pending Role Manager fix.

This is a next-release building block, NOT a production updater. It does not
back up files, set VERSION or restart workers. Keep published v12.83 hunks
immutable; a release updater must stage, back up and apply this patch itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

CHANGES = {
    "app/main.py": [
        (
            '        ("logs.view", "/logs"),\n',
            '        ("logs.view", "/logs"),\n'
            '        # Settings-only and backup-only operators still need a landing page.\n'
            '        ("settings.view", "/system/branding"),\n'
            '        ("backups.view", "/system/backups"),\n',
        ),
        (
            '    db.add(role)\n    db.commit()\n    sensitive = ',
            '    db.add(role)\n    db.commit()\n'
            '    log_event("Panel role created", scope="auth", actor=actor.username,\n'
            '              details={"role_id": role.id, "role_name": role.name,\n'
            '                       "permissions": sorted(decode_permissions(role.permissions))})\n'
            '    sensitive = ',
        ),
        (
            '    role.name = cleaned_name\n',
            '    old_name = role.name\n    role.name = cleaned_name\n',
        ),
        (
            '    role.permissions = encode_permissions(submitted_permissions & allowed_permissions)\n'
            '    db.commit()\n',
            '    role.permissions = encode_permissions(submitted_permissions & allowed_permissions)\n'
            '    db.commit()\n'
            '    updated_permissions = set(decode_permissions(role.permissions))\n'
            '    log_event("Panel role updated", scope="auth", actor=actor.username,\n'
            '              details={"role_id": role.id, "role_name": role.name,\n'
            '                       "previous_name": old_name,\n'
            '                       "granted": sorted(updated_permissions - existing_permissions),\n'
            '                       "revoked": sorted(existing_permissions - updated_permissions)})\n',
        ),
        (
            '    db.delete(role)\n    db.commit()\n',
            '    deleted_role = {"role_id": role.id, "role_name": role.name,\n'
            '                    "permissions": sorted(decode_permissions(role.permissions))}\n'
            '    db.delete(role)\n    db.commit()\n'
            '    log_event("Panel role deleted", scope="auth", actor=actor.username, details=deleted_role)\n',
        ),
    ],
    "app/permissions.py": [
        (
            '                ("panel_users.password_reveal", "Reveal saved passwords", '
            '"View opt-in encrypted credentials for panel users in your access scope; requires own-password verification and audit."),',
            '                ("panel_users.password_reveal", "Reveal saved passwords", '
            '"View saved encrypted credentials for panel users in your access scope when the vault is enabled; '
            'requires own-password verification and audit."),',
        ),
        (
            '                ("panel_users.password_vault_manage", "Save passwords in vault", '
            '"Opt in to encrypted storage on Panel User create or password reset (also requires create/edit permission)."),',
            '                ("panel_users.password_vault_manage", "Legacy vault-management flag", '
            '"Retained for compatibility; does not control password saving. When the vault is enabled, '
            'passwords are saved automatically on create/reset. Reveal is controlled separately."),',
        ),
    ],
}


def transform(relative: str, source: str) -> str:
    for number, (old, new) in enumerate(CHANGES[relative], 1):
        if source.count(new) == 1:
            continue
        if source.count(new) or source.count(old) != 1:
            raise ValueError(f"{relative}: hunk {number} is missing or ambiguous")
        source = source.replace(old, new, 1)
    return source


def apply(root: Path) -> None:
    # Preflight every file before writing any file; retain existing modes.
    planned = {}
    for relative in CHANGES:
        path = root / relative
        result = transform(relative, path.read_text(encoding="utf-8"))
        compile(result, str(path), "exec")
        planned[path] = result
    for path, result in planned.items():
        if path.read_text(encoding="utf-8") != result:
            path.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_role_manager_fix.py STAGED_ROOT (not a production updater)")
    apply(Path(sys.argv[1]))
