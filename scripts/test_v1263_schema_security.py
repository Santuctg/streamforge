#!/usr/bin/env python3
"""v12.63 actual SQLAlchemy migration + bound login query regression."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory(prefix="sf-v1263-schema-") as root:
    db_path=Path(root)/"legacy.sqlite3"
    os.environ["STREAMFORGE_DATABASE_URL"]="sqlite:///"+str(db_path)
    from sqlalchemy import select, text, inspect
    from sqlalchemy.orm import Session
    from app.db import engine, ensure_runtime_schema
    from app.models import AdminUser
    # Start from the preceding release's admin_users layout (no recovery data).
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE admin_users (id INTEGER PRIMARY KEY,"
                                "username VARCHAR(80),password_hash VARCHAR(255),"
                                "is_active BOOLEAN,main_panel_access BOOLEAN,role_id INTEGER,"
                                "created_at DATETIME)"))
        connection.execute(text("INSERT INTO admin_users(id,username,password_hash,is_active,main_panel_access)"
                                " VALUES(1,'operator','scrypt-hash-test',1,1)"))
    ensure_runtime_schema()
    ensure_runtime_schema()  # idempotence on a restored or already migrated DB
    columns={col["name"] for col in inspect(engine).get_columns("admin_users")}
    assert "password_vault_enc" in columns
    with Session(engine) as session:
        user=session.scalar(select(AdminUser).where(AdminUser.username=="operator"))
        assert user and user.password_vault_enc is None and user.password_hash=="scrypt-hash-test"
        payload="' OR 1=1 --"
        statement=select(AdminUser.id).where(AdminUser.username == payload)
        compiled=statement.compile(engine)
        assert payload not in str(compiled)
        assert payload in compiled.params.values()
        assert session.scalar(statement) is None
        user.password_vault_enc="fake-encrypted-placeholder"
        session.commit()
    with Session(engine) as session:
        user=session.get(AdminUser,1)
        assert user.password_hash=="scrypt-hash-test"
        assert user.password_vault_enc=="fake-encrypted-placeholder"
    print("PASS: legacy schema migration idempotent, old hashes preserved, parameterized auth lookups")
