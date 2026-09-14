#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    database = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    database.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    permissions = json.dumps(["*"], separators=(",", ":"))

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                description TEXT,
                permissions TEXT NOT NULL DEFAULT '[]',
                is_system BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_roles_name ON roles(name)")

        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "admin_users" in tables:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(admin_users)")
            }
            if "role_id" not in columns:
                connection.execute(
                    "ALTER TABLE admin_users ADD COLUMN role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_admin_users_role_id ON admin_users(role_id)"
            )

        row = connection.execute(
            "SELECT id FROM roles WHERE lower(name)=lower(?) LIMIT 1", ("Super Admin",)
        ).fetchone()
        if row:
            super_role_id = int(row[0])
            connection.execute(
                "UPDATE roles SET permissions=?, is_system=1, description=?, updated_at=? WHERE id=?",
                (
                    permissions,
                    "Built-in unrestricted role. This role cannot be edited or deleted.",
                    now,
                    super_role_id,
                ),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO roles(name, description, permissions, is_system, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (
                    "Super Admin",
                    "Built-in unrestricted role. This role cannot be edited or deleted.",
                    permissions,
                    1,
                    now,
                    now,
                ),
            )
            super_role_id = int(cursor.lastrowid)

        if "admin_users" in tables:
            connection.execute(
                "UPDATE admin_users SET role_id=? WHERE role_id IS NULL", (super_role_id,)
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"RBAC migration complete: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
