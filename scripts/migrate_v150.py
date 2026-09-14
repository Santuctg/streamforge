#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v150.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        existing = columns(con, "stream_users")
        if "xtream_username" not in existing:
            con.execute("ALTER TABLE stream_users ADD COLUMN xtream_username VARCHAR(120)")
        if "xtream_password_hash" not in existing:
            con.execute("ALTER TABLE stream_users ADD COLUMN xtream_password_hash VARCHAR(255)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_stream_users_xtream_username ON stream_users(xtream_username)")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_stream_users_xtream_username_lower "
            "ON stream_users(lower(xtream_username)) "
            "WHERE xtream_username IS NOT NULL AND xtream_username <> ''"
        )
        con.commit()
        print("v1.5.0 Xtream credential migration complete")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
