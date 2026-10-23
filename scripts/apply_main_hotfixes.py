#!/usr/bin/env python3
"""Apply the Main hotfixes to an installed app/main.py.

Installed Main sources are patched in place by the updaters (the repo's
main.py is never copied over them), so this applies the same changes the
repo carries:

1. ``import shutil``: restoring a saved backup archive always failed with
   "name 'shutil' is not defined".
2. Branding error renders no longer call the undefined
   ``backup_rclone_status()``, which turned an empty brand name or a rejected
   logo/favicon/cookie upload into a 500.
3. Startup seeds STREAMFORGE_ADMIN_USER only when no Panel admin exists, so a
   deliberately deleted admin no longer returns (with the env/default
   password, as Super Admin) after every update or restart. Locked-out
   installs recover with scripts/reset_admin.py.

Each fix is skipped when already present, so re-running is safe:

    sudo cp /opt/streamforge/app/main.py /opt/streamforge/app/main.py.bak
    sudo python3 scripts/apply_main_hotfixes.py /opt/streamforge/app/main.py
    sudo systemctl restart streamforge.service
"""
from __future__ import annotations

import sys
from pathlib import Path

ADMIN_SEED_MARKER = "STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY"

BRANDING_ERROR_CONTEXT = " backup_targets=load_backup_targets(), backup_rclone=backup_rclone_status(),"

ADMIN_SEED_ORIGINAL = """        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_user))
        if not existing:
            db.add(
                AdminUser(
                    username=settings.admin_user,
                    password_hash=hash_password(settings.admin_password),
                    role=super_role,
                )
            )
        elif existing.role_id is None:
"""

ADMIN_SEED_UPDATED = """        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_user))
        # STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY:
        # Seed the env-configured admin only on a fresh install. Re-creating it
        # whenever that username is missing would resurrect a deliberately
        # deleted account (with the env/default password) on every restart.
        # Locked-out installs recover with scripts/reset_admin.py.
        if not existing:
            if not db.scalar(select(func.count()).select_from(AdminUser)):
                db.add(
                    AdminUser(
                        username=settings.admin_user,
                        password_hash=hash_password(settings.admin_password),
                        role=super_role,
                    )
                )
        elif existing.role_id is None:
"""


def _replace_once(source: str, original: str, updated: str, label: str) -> str:
    matches = source.count(original)
    if matches != 1:
        raise ValueError(f"{label}: expected one anchor; found {matches}")
    return source.replace(original, updated)


def transform(source: str) -> str:
    if "\nimport shutil\n" not in source:
        source = _replace_once(source, "\nimport secrets\n", "\nimport secrets\nimport shutil\n", "shutil import")
    if BRANDING_ERROR_CONTEXT in source:
        matches = source.count(BRANDING_ERROR_CONTEXT)
        if matches != 2:
            raise ValueError(f"Branding error renders: expected two anchors; found {matches}")
        source = source.replace(BRANDING_ERROR_CONTEXT, "")
    if ADMIN_SEED_MARKER not in source:
        source = _replace_once(source, ADMIN_SEED_ORIGINAL, ADMIN_SEED_UPDATED, "Admin seed block")
    compile(source, "app/main.py", "exec")
    return source


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_main_hotfixes.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if before != after:
        path.write_text(after, encoding="utf-8")
    print("PASS: Main hotfixes installed or already present")


if __name__ == "__main__":
    main()
