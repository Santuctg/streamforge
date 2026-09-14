#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def add(con: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    if name not in columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v110.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    db = Path(sys.argv[1])
    con = sqlite3.connect(db)
    try:
        for name, ddl in {
            "ssh_host": "VARCHAR(255)",
            "ssh_port": "INTEGER NOT NULL DEFAULT 22",
            "ssh_user": "VARCHAR(120)",
            "ssh_password_enc": "TEXT",
            "agent_port": "INTEGER NOT NULL DEFAULT 8810",
        }.items():
            add(con, "nodes", name, ddl)
        for name, ddl in {
            "backup_inputs": "TEXT",
            "active_input_index": "INTEGER NOT NULL DEFAULT 0",
            "failback_enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "failback_interval": "INTEGER NOT NULL DEFAULT 30",
        }.items():
            add(con, "channels", name, ddl)
        add(con, "stream_users", "playlist_order", "TEXT DEFAULT '[]'")

        # Repair legacy DNS values saved as full URLs. New forms store only
        # the hostname and keep the scheme in dns_scheme.
        node_cols = columns(con, "nodes")
        if {"id", "dns_name", "dns_scheme"}.issubset(node_cols):
            rows = con.execute(
                "SELECT id,dns_name FROM nodes WHERE dns_name IS NOT NULL AND dns_name <> ''"
            ).fetchall()
            for node_id, dns_name in rows:
                raw = str(dns_name).strip().rstrip("/")
                if "://" not in raw:
                    continue
                parsed = urlsplit(raw)
                if parsed.hostname:
                    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "http"
                    con.execute(
                        "UPDATE nodes SET dns_name=?, dns_scheme=? WHERE id=?",
                        (parsed.hostname.lower().rstrip("."), scheme, node_id),
                    )
        con.execute('''CREATE TABLE IF NOT EXISTS log_entries (
            id INTEGER PRIMARY KEY,
            scope VARCHAR(40) NOT NULL DEFAULT 'system',
            level VARCHAR(20) NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            channel_id INTEGER,
            node_id INTEGER,
            actor VARCHAR(120),
            details TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        for sql in (
            "CREATE INDEX IF NOT EXISTS ix_log_entries_scope ON log_entries(scope)",
            "CREATE INDEX IF NOT EXISTS ix_log_entries_level ON log_entries(level)",
            "CREATE INDEX IF NOT EXISTS ix_log_entries_channel_id ON log_entries(channel_id)",
            "CREATE INDEX IF NOT EXISTS ix_log_entries_node_id ON log_entries(node_id)",
            "CREATE INDEX IF NOT EXISTS ix_log_entries_created_at ON log_entries(created_at)",
        ):
            con.execute(sql)
        con.commit()
    finally:
        con.close()
    print("v1.1.0 migration applied")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
