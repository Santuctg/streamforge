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
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "nodes" not in tables:
            con.execute(
                """
                CREATE TABLE nodes (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    slug VARCHAR(120) NOT NULL,
                    node_type VARCHAR(20) NOT NULL DEFAULT 'remote',
                    api_url TEXT,
                    api_token VARCHAR(255),
                    verify_tls BOOLEAN NOT NULL DEFAULT 1,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    status VARCHAR(30) NOT NULL DEFAULT 'unknown',
                    client_prefixes TEXT,
                    last_seen_at DATETIME,
                    last_error TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            tables.add("nodes")
        else:
            columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
            if "client_prefixes" not in columns:
                con.execute("ALTER TABLE nodes ADD COLUMN client_prefixes TEXT")

        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_nodes_name ON nodes(name)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_nodes_slug ON nodes(slug)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_nodes_node_type ON nodes(node_type)")

        local = con.execute("SELECT id FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1").fetchone()
        if local:
            local_id = int(local[0])
            con.execute(
                "UPDATE nodes SET enabled=1,status='online',last_seen_at=?,updated_at=? WHERE id=?",
                (now, now, local_id),
            )
        else:
            cursor = con.execute(
                "INSERT INTO nodes(name,slug,node_type,api_url,api_token,verify_tls,enabled,status,client_prefixes,last_seen_at,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Local Node", "local-node", "local", None, None, 1, 1, "online", None, now, None, now, now),
            )
            local_id = int(cursor.lastrowid)

        if "channels" in tables:
            columns = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
            if "node_id" not in columns:
                con.execute("ALTER TABLE channels ADD COLUMN node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL")
            con.execute("CREATE INDEX IF NOT EXISTS ix_channels_node_id ON channels(node_id)")
            con.execute("UPDATE channels SET node_id=? WHERE node_id IS NULL", (local_id,))

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_nodes (
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                priority INTEGER NOT NULL DEFAULT 100,
                PRIMARY KEY(channel_id,node_id)
            )
            """
        )
        if "channels" in tables:
            con.execute(
                "INSERT OR IGNORE INTO channel_nodes(channel_id,node_id,priority) SELECT id,node_id,100 FROM channels WHERE node_id IS NOT NULL"
            )
            con.execute(
                "INSERT OR IGNORE INTO channel_nodes(channel_id,node_id,priority) SELECT id,?,100 FROM channels WHERE NOT EXISTS (SELECT 1 FROM channel_nodes cn WHERE cn.channel_id=channels.id)",
                (local_id,),
            )

        if "stream_users" in tables:
            user_columns = {row[1] for row in con.execute("PRAGMA table_info(stream_users)")}
            if "load_balance_enabled" not in user_columns:
                con.execute("ALTER TABLE stream_users ADD COLUMN load_balance_enabled BOOLEAN NOT NULL DEFAULT 1")
            con.execute("UPDATE stream_users SET load_balance_enabled=1 WHERE load_balance_enabled IS NULL")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS user_nodes (
                user_id INTEGER NOT NULL REFERENCES stream_users(id) ON DELETE CASCADE,
                node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                PRIMARY KEY(user_id,node_id)
            )
            """
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(f"Multi-node/load-balancing migration complete: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
