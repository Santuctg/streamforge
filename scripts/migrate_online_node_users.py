#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_online_node_users.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS node_stream_users (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                token VARCHAR(128) NOT NULL UNIQUE,
                node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                expires_at DATETIME,
                max_connections INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_node_stream_users_name ON node_stream_users(name);
            CREATE INDEX IF NOT EXISTS ix_node_stream_users_token ON node_stream_users(token);
            CREATE INDEX IF NOT EXISTS ix_node_stream_users_node_id ON node_stream_users(node_id);
            CREATE TABLE IF NOT EXISTS node_user_channels (
                node_user_id INTEGER NOT NULL REFERENCES node_stream_users(id) ON DELETE CASCADE,
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                PRIMARY KEY (node_user_id, channel_id)
            );
            """
        )
        node_user_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(node_stream_users)")
        }
        if "playlist_order" not in node_user_columns:
            connection.execute(
                "ALTER TABLE node_stream_users ADD COLUMN playlist_order TEXT NOT NULL DEFAULT '[]'"
            )
        connection.commit()
    finally:
        connection.close()
    print("Node user and online-viewer schema ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
