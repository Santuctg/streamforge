#!/usr/bin/env python3
"""Migration: Add expires_at column to nodes table for node subscription expiry.

Usage:
    python scripts/migrate_node_expiry.py
"""
from pathlib import Path
from sqlalchemy import text
from app.db import SessionLocal

SCHEMA_SQL = """
ALTER TABLE nodes ADD COLUMN expires_at DATETIME NULL;
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_nodes_expires_at ON nodes(expires_at);
"""


def run() -> None:
    db = SessionLocal()
    try:
        # Check if column already exists (SQLite doesn't support ADD COLUMN IF NOT EXISTS)
        result = db.execute(text("PRAGMA table_info(nodes)"))
        columns = {row[1] for row in result.fetchall()}
        if "expires_at" not in columns:
            db.execute(text(SCHEMA_SQL))
            db.execute(text(INDEX_SQL))
            db.commit()
            print("OK: nodes.expires_at column added.")
        else:
            print("SKIP: nodes.expires_at column already exists.")
    except Exception as exc:
        db.rollback()
        print(f"FAIL: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
