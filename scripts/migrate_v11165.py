#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path

db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
conn = sqlite3.connect(db_path)
try:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(channels)")}
    if "source_program_ids" not in columns:
        conn.execute("ALTER TABLE channels ADD COLUMN source_program_ids TEXT")
    rows = conn.execute("SELECT id, program_id, source_program_ids FROM channels").fetchall()
    for channel_id, program_id, source_program_ids in rows:
        if source_program_ids:
            continue
        conn.execute("UPDATE channels SET source_program_ids=? WHERE id=?", (json.dumps([program_id if program_id else None]), channel_id))
    conn.commit()
    print(f"v1.11.67 source program migration complete ({len(rows)} channels)")
finally:
    conn.close()
