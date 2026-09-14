#!/usr/bin/env python3
"""Set every existing HLS channel to the v2.0.1 one-second segment profile."""

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: migrate_v201.py /path/to/streamforge.db")
    db_path = Path(sys.argv[1])
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        if "channels" not in tables:
            print("v2.0.1 HLS migration skipped: channels table missing")
            return 0
        columns = {row[1] for row in connection.execute("pragma table_info(channels)")}
        if "hls_segment_time" not in columns or "output_type" not in columns:
            print("v2.0.1 HLS migration skipped: required channel columns missing")
            return 0
        cursor = connection.execute(
            """update channels
               set hls_segment_time = 1
               where lower(coalesce(output_type, '')) = 'hls'
               and coalesce(hls_segment_time, 0) <> 1"""
        )
        connection.commit()
    print(f"v2.0.1 HLS migration complete: {cursor.rowcount} channel(s) set to 1-second segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
