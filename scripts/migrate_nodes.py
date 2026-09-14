#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    database = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    database.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(database)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                slug VARCHAR(120) NOT NULL,
                node_type VARCHAR(20) NOT NULL DEFAULT 'remote',
                api_url TEXT,
                api_token VARCHAR(255),
                verify_tls BOOLEAN NOT NULL DEFAULT 1,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                status VARCHAR(30) NOT NULL DEFAULT 'unknown',
                last_seen_at DATETIME,
                last_error TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_nodes_name ON nodes(name)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_nodes_slug ON nodes(slug)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_nodes_node_type ON nodes(node_type)")

        row = con.execute("SELECT id FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1").fetchone()
        if row:
            local_id = int(row[0])
            con.execute(
                "UPDATE nodes SET enabled=1,status='online',last_seen_at=?,updated_at=? WHERE id=?",
                (now, now, local_id),
            )
        else:
            cursor = con.execute(
                "INSERT INTO nodes(name,slug,node_type,api_url,api_token,verify_tls,enabled,status,last_seen_at,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Local Node", "local-node", "local", None, None, 1, 1, "online", now, None, now, now),
            )
            local_id = int(cursor.lastrowid)

        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "channels" in tables:
            columns = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
            if "node_id" not in columns:
                con.execute("ALTER TABLE channels ADD COLUMN node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL")
            con.execute("CREATE INDEX IF NOT EXISTS ix_channels_node_id ON channels(node_id)")
            con.execute("UPDATE channels SET node_id=? WHERE node_id IS NULL", (local_id,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(f"Node migration complete: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
