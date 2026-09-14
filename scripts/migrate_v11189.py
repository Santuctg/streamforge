#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> None:
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    con = sqlite3.connect(db_path)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone():
            print("v1.11.89 migration skipped: nodes table not found")
            return
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        changed = 0
        if "agent_version" not in columns:
            con.execute("ALTER TABLE nodes ADD COLUMN agent_version VARCHAR(64)")
            changed = 1
        con.commit()
    finally:
        con.close()
    print(f"v1.11.89 persisted Node version migration complete: changed={changed}")


if __name__ == "__main__":
    main()
