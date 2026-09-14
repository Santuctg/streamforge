#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
MARKER = "STREAMFORGE_V99_PANEL_ACCESS_POLICY_MIGRATION"
COLUMNS = ("panel_ip_blacklist", "panel_asn_whitelist", "panel_asn_blacklist")
def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v99_panel_access_policy.py /path/to/streamforge.db", file=sys.stderr); return 2
    path=Path(sys.argv[1])
    if not path.is_file(): print("v9.9 panel access migration skipped: database not found"); return 0
    con=sqlite3.connect(str(path), timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" not in tables: print("v9.9 panel access migration skipped: nodes table missing"); return 0
        cols={row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        added=[]
        for name in COLUMNS:
            if name not in cols:
                con.execute(f"ALTER TABLE nodes ADD COLUMN {name} TEXT"); added.append(name)
        con.commit()
        cols={row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        missing=[name for name in COLUMNS if name not in cols]
        if missing: raise RuntimeError("missing columns after migration: "+", ".join(missing))
        print("v9.9 panel access migration complete; added " + (", ".join(added) if added else "no new columns"))
        return 0
    finally: con.close()
if __name__ == "__main__": raise SystemExit(main())
