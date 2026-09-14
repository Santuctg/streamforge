#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v120.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS playlist_profiles (
                id INTEGER PRIMARY KEY,
                name VARCHAR(150) NOT NULL UNIQUE,
                description TEXT,
                logo_url TEXT,
                channel_order TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE INDEX IF NOT EXISTS ix_playlist_profiles_name ON playlist_profiles(name);
            CREATE TABLE IF NOT EXISTS playlist_profile_channels (
                playlist_id INTEGER NOT NULL REFERENCES playlist_profiles(id) ON DELETE CASCADE,
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                PRIMARY KEY (playlist_id, channel_id)
            );
            """
        )
        if "playlist_id" not in columns(con, "stream_users"):
            con.execute("ALTER TABLE stream_users ADD COLUMN playlist_id INTEGER REFERENCES playlist_profiles(id) ON DELETE SET NULL")
        con.execute("CREATE INDEX IF NOT EXISTS ix_stream_users_playlist_id ON stream_users(playlist_id)")
        if "input_mode" not in columns(con, "channel_nodes"):
            con.execute("ALTER TABLE channel_nodes ADD COLUMN input_mode VARCHAR(30) NOT NULL DEFAULT 'source'")
        if "remote_input_mode" in columns(con, "channels"):
            con.execute(
                """
                UPDATE channel_nodes
                SET input_mode='local_relay'
                WHERE channel_id IN (
                    SELECT id FROM channels WHERE remote_input_mode='local_relay'
                )
                AND node_id IN (SELECT id FROM nodes WHERE node_type='remote')
                """
            )
        con.commit()
        print("v1.2.0 migration complete")
        return 0
    finally:
        con.close()

if __name__ == '__main__':
    raise SystemExit(main())
