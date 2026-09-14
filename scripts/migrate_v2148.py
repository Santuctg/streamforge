#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v2148.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    connection = sqlite3.connect(path)
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(channel_nodes)")}
        if "hls_segment_time" not in columns:
            connection.execute("ALTER TABLE channel_nodes ADD COLUMN hls_segment_time INTEGER")
        connection.commit()
        print("v2.1.48 migration complete")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
