#!/usr/bin/env python3
from __future__ import annotations

import getpass
import sys

from sqlalchemy import func, select

from app.auth import hash_password
from app.db import Base, SessionLocal, engine, ensure_runtime_schema
from app.models import AdminUser, Role
from app.permissions import SUPERUSER_PERMISSION, encode_permissions

username = input("Admin username [admin]: ").strip() or "admin"
password = getpass.getpass("New password: ")
if len(password) < 10:
    print("Password must be at least 10 characters", file=sys.stderr)
    raise SystemExit(1)

Base.metadata.create_all(bind=engine)
ensure_runtime_schema()
with SessionLocal() as db:
    super_role = db.scalar(select(Role).where(func.lower(Role.name) == "super admin"))
    if not super_role:
        super_role = Role(
            name="Super Admin",
            description="Built-in unrestricted role. This role cannot be edited or deleted.",
            permissions=encode_permissions([SUPERUSER_PERMISSION], allow_wildcard=True),
            is_system=True,
        )
        db.add(super_role)
        db.flush()
    else:
        super_role.permissions = encode_permissions([SUPERUSER_PERMISSION], allow_wildcard=True)
        super_role.is_system = True

    admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if not admin:
        admin = AdminUser(
            username=username,
            password_hash=hash_password(password),
            is_active=True,
            role=super_role,
        )
        db.add(admin)
    else:
        admin.password_hash = hash_password(password)
        admin.is_active = True
        admin.role = super_role
    db.commit()
print("Admin password updated and Super Admin access restored.")
