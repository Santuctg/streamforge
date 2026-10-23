#!/usr/bin/env python3
"""Stop Main from re-creating a deleted env-configured admin on restart.

Startup used to add STREAMFORGE_ADMIN_USER (with STREAMFORGE_ADMIN_PASSWORD,
default ChangeMe123!) whenever that username was missing, so a deliberately
deleted admin returned as a Super Admin after every update or restart. The
patched startup seeds it only when no Panel admin exists at all.

Installed Main sources are patched in place by the updaters, so this applies
the same change to an existing /opt/streamforge/app/main.py:

    sudo cp /opt/streamforge/app/main.py /opt/streamforge/app/main.py.bak
    sudo python3 scripts/apply_admin_seed_fix.py /opt/streamforge/app/main.py
    sudo systemctl restart streamforge.service
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY"

ORIGINAL = """        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_user))
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

UPDATED = """        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_user))
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


def transform(source: str) -> str:
    if MARKER in source:
        return source
    matches = source.count(ORIGINAL)
    if matches != 1:
        raise ValueError(f"Admin seed block: expected one anchor; found {matches}")
    source = source.replace(ORIGINAL, UPDATED)
    compile(source, "app/main.py", "exec")
    return source


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_admin_seed_fix.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if before != after:
        path.write_text(after, encoding="utf-8")
    print("PASS: admin seed fix installed or already present")


if __name__ == "__main__":
    main()
