#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v11120.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Database not found: {path}", file=sys.stderr)
        return 1
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        if "playlist_profiles" in tables:
            columns = {row[1] for row in con.execute("pragma table_info(playlist_profiles)")}
            assignments = []
            if "channel_order" in columns:
                assignments.append("channel_order='[]'")
            if "category_order" in columns:
                assignments.append("category_order='[]'")
            if assignments:
                con.execute(f"update playlist_profiles set {', '.join(assignments)}")
        if "stream_users" in tables:
            columns = {row[1] for row in con.execute("pragma table_info(stream_users)")}
            if "playlist_order" in columns and "playlist_id" in columns:
                con.execute("update stream_users set playlist_order='[]' where playlist_id is not null")
        con.commit()
    print("v1.11.20 playlist ordering migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
