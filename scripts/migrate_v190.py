#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v190.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
        if "desired_running" not in cols:
            con.execute("ALTER TABLE channels ADD COLUMN desired_running INTEGER NOT NULL DEFAULT 0")
            # Preserve the operator's last intent from the pre-v1.9 runtime state.
            con.execute(
                "UPDATE channels SET desired_running=1 "
                "WHERE enabled=1 AND lower(coalesce(status,'')) NOT IN ('stopped','disabled')"
            )
        con.execute("CREATE INDEX IF NOT EXISTS ix_channels_desired_running ON channels(desired_running)")
        con.commit()
    finally:
        con.close()
    print("v1.9.0 desired-state migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
