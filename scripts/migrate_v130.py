#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v130.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        existing = columns(con, "channel_nodes")
        additions = {
            "video_codec": "VARCHAR(30)",
            "video_bitrate": "VARCHAR(20)",
            "resolution": "VARCHAR(30)",
            "audio_codec": "VARCHAR(30)",
            "audio_bitrate": "VARCHAR(20)",
        }
        for name, sql_type in additions.items():
            if name not in existing:
                con.execute(f"ALTER TABLE channel_nodes ADD COLUMN {name} {sql_type}")
        con.commit()
        print("v1.3.0 migration complete")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
