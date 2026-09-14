#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v200.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"database not found: {path}", file=sys.stderr)
        return 1

    changed = 0
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        if "channels" not in tables:
            print("v2.0.0 low-latency migration skipped: channels table missing")
            return 0
        columns = {row[1] for row in connection.execute("pragma table_info(channels)")}
        if "hls_segment_time" not in columns or "output_type" not in columns:
            print("v2.0.0 low-latency migration skipped: required channel columns missing")
            return 0
        cursor = connection.execute(
            """
            update channels
               set hls_segment_time = 2
             where lower(coalesce(output_type, 'hls')) = 'hls'
               and coalesce(hls_segment_time, 0) <> 2
            """
        )
        changed = max(0, int(cursor.rowcount or 0))
        connection.commit()
    print(f"v2.0.0 low-latency migration complete: {changed} HLS channel(s) set to 2-second segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
