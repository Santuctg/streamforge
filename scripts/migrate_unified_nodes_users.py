#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def tables(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def add_column(connection: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if table in tables(connection) and name not in columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_unified_nodes_users.py /path/to/streamforge.db", file=sys.stderr)
        return 2

    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        current_tables = tables(connection)
        if "nodes" in current_tables:
            add_column(connection, "nodes", "logo_url", "TEXT")
        if "admin_users" in current_tables:
            add_column(connection, "admin_users", "main_panel_access", "BOOLEAN NOT NULL DEFAULT 1")
        if "stream_users" in current_tables:
            add_column(connection, "stream_users", "delivery_mode", "VARCHAR(30) NOT NULL DEFAULT 'central'")
            add_column(connection, "stream_users", "direct_node_id", "INTEGER REFERENCES nodes(id) ON DELETE SET NULL")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_user_nodes (
                admin_user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
                node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                PRIMARY KEY (admin_user_id, node_id)
            );
            CREATE INDEX IF NOT EXISTS ix_stream_users_direct_node_id ON stream_users(direct_node_id);
            """
        )

        migrated = 0
        linked = 0
        current_tables = tables(connection)
        if {"node_stream_users", "stream_users"}.issubset(current_tables):
            legacy_columns = columns(connection, "node_stream_users")
            for legacy in connection.execute("SELECT * FROM node_stream_users ORDER BY id").fetchall():
                token = str(legacy["token"])
                existing = connection.execute(
                    "SELECT id FROM stream_users WHERE token = ?", (token,)
                ).fetchone()
                if existing:
                    stream_user_id = int(existing["id"])
                    connection.execute(
                        "UPDATE stream_users SET delivery_mode='direct_node', direct_node_id=? WHERE id=?",
                        (legacy["node_id"], stream_user_id),
                    )
                else:
                    created_at = legacy["created_at"] if "created_at" in legacy_columns else None
                    connection.execute(
                        """
                        INSERT INTO stream_users(
                            name, token, enabled, load_balance_enabled, delivery_mode,
                            direct_node_id, expires_at, max_connections, notes, created_at
                        ) VALUES (?, ?, ?, 0, 'direct_node', ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                        """,
                        (
                            legacy["name"],
                            token,
                            legacy["enabled"],
                            legacy["node_id"],
                            legacy["expires_at"],
                            legacy["max_connections"],
                            legacy["notes"],
                            created_at,
                        ),
                    )
                    stream_user_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                    migrated += 1

                if "node_user_channels" in current_tables:
                    channel_rows = connection.execute(
                        "SELECT channel_id FROM node_user_channels WHERE node_user_id=?",
                        (legacy["id"],),
                    ).fetchall()
                    for channel_row in channel_rows:
                        before = connection.total_changes
                        connection.execute(
                            "INSERT OR IGNORE INTO user_channels(user_id, channel_id) VALUES (?, ?)",
                            (stream_user_id, channel_row["channel_id"]),
                        )
                        if connection.total_changes > before:
                            linked += 1

        connection.commit()
        print(f"Unified node/user schema ready; migrated users={migrated}, channel links={linked}")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
