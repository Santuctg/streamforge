#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

MARKER = "STREAMFORGE_V98_PANEL_IP_WHITELIST_MIGRATION"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v98_panel_ip_whitelist.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("v9.8 panel whitelist migration skipped: database not found")
        return 0

    con = sqlite3.connect(str(path), timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" not in tables:
            print("v9.8 panel whitelist migration skipped: nodes table missing")
            return 0
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        if "panel_ip_whitelist" in columns:
            print("v9.8 panel whitelist migration already applied")
            return 0
        con.execute("ALTER TABLE nodes ADD COLUMN panel_ip_whitelist TEXT")
        con.commit()
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        if "panel_ip_whitelist" not in columns:
            raise RuntimeError("panel_ip_whitelist column was not created")
        print("v9.8 panel whitelist migration complete; added nodes.panel_ip_whitelist")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
