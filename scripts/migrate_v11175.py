#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

def add_column(con: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

def main() -> None:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    con = sqlite3.connect(db)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone():
            print("v1.11.75 migration skipped: nodes table not found")
            return
        add_column(con, "nodes", "playlist_access_slug VARCHAR(120)")
        con.execute("UPDATE nodes SET playlist_access_slug=access_slug WHERE node_type='remote' AND COALESCE(TRIM(playlist_access_slug),'')='' AND COALESCE(TRIM(access_slug),'')<>''")
        con.commit()
    finally:
        con.close()
    print("v1.11.75 independent node slugs migration complete")

if __name__ == "__main__":
    main()
