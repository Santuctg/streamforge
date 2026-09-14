#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_ssh_dns_relay.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Database not found: {path}", file=sys.stderr)
        return 2
    con = sqlite3.connect(path)
    try:
        node_cols = columns(con, "nodes")
        if "dns_name" not in node_cols:
            con.execute("ALTER TABLE nodes ADD COLUMN dns_name VARCHAR(255)")
        if "dns_scheme" not in node_cols:
            con.execute("ALTER TABLE nodes ADD COLUMN dns_scheme VARCHAR(10) NOT NULL DEFAULT 'http'")
        if "dns_only" not in node_cols:
            con.execute("ALTER TABLE nodes ADD COLUMN dns_only BOOLEAN NOT NULL DEFAULT 0")

        channel_cols = columns(con, "channels")
        if "remote_input_mode" not in channel_cols:
            con.execute("ALTER TABLE channels ADD COLUMN remote_input_mode VARCHAR(30) NOT NULL DEFAULT 'source'")
        con.execute("UPDATE nodes SET dns_scheme='http' WHERE dns_scheme IS NULL OR dns_scheme='' ")
        con.execute("UPDATE nodes SET dns_only=0 WHERE dns_only IS NULL")
        con.execute("UPDATE channels SET remote_input_mode='source' WHERE remote_input_mode IS NULL OR remote_input_mode='' ")
        con.commit()
    finally:
        con.close()
    print("SSH/DNS/relay migration ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
