#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def add_column(con: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v160.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" in tables:
            add_column(con, "nodes", "ip_whitelist", "TEXT")
            add_column(con, "nodes", "ip_blacklist", "TEXT")
            add_column(con, "nodes", "asn_whitelist", "TEXT")
            add_column(con, "nodes", "asn_blacklist", "TEXT")
        if "stream_users" in tables:
            add_column(con, "stream_users", "user_type", "VARCHAR(20) NOT NULL DEFAULT 'viewer'")
            add_column(con, "stream_users", "restream_allowed_ips", "TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS ix_stream_users_user_type ON stream_users(user_type)")
            # Credential-based accounts created by older releases are viewers by default.
            con.execute("UPDATE stream_users SET user_type='viewer' WHERE user_type IS NULL OR trim(user_type)='' ")
        con.commit()
        print("v1.6.0 access-control and restream-account migration complete")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
