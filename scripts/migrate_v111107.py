#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v111107.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"database not found: {path}", file=sys.stderr)
        return 1
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        if "playlist_profiles" not in tables:
            print("v1.11.107 playlist content-mode migration skipped: playlist_profiles table missing")
            return 0
        columns = {row[1] for row in connection.execute("pragma table_info(playlist_profiles)")}
        if "all_enabled_channels" not in columns:
            connection.execute(
                "alter table playlist_profiles add column all_enabled_channels boolean not null default 0"
            )
        connection.execute(
            "update playlist_profiles set all_enabled_channels=0 where all_enabled_channels is null"
        )
        connection.commit()
    print("v1.11.107 Main playlist content-mode migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
