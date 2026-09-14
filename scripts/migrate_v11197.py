#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v11197.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"database not found: {path}", file=sys.stderr)
        return 1
    # v1.11.98 restores per-user load-balancing and custom-channel controls.
    # This retained migration name is intentionally non-destructive so future
    # force updates do not clear user_nodes or overwrite load_balance_enabled.
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        if "stream_users" not in tables:
            print("v1.11.97 compatibility migration skipped: stream_users table missing")
            return 0
    print("v1.11.97 compatibility migration complete: user node restrictions preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
