#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v11119.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db_path), timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "channels" not in tables:
            print("channels table not found; nothing to migrate")
            return 0
        columns = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
        added = False
        if "sort_order" not in columns:
            con.execute("ALTER TABLE channels ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100")
            added = True

        # A newly added column gives every row the same default. Backfill a
        # deterministic order inside each category without changing membership.
        rows = con.execute(
            """
            SELECT id, category_id
            FROM channels
            ORDER BY CASE WHEN category_id IS NULL THEN 1 ELSE 0 END,
                     category_id, lower(name), id
            """
        ).fetchall()
        grouped: dict[int | None, list[int]] = {}
        for channel_id, category_id in rows:
            grouped.setdefault(category_id, []).append(int(channel_id))
        if added or con.execute("SELECT count(*) FROM channels WHERE sort_order IS NULL OR sort_order <= 0").fetchone()[0]:
            for channel_ids in grouped.values():
                for index, channel_id in enumerate(channel_ids, 1):
                    con.execute("UPDATE channels SET sort_order=? WHERE id=?", (index * 10, channel_id))

        con.execute("CREATE INDEX IF NOT EXISTS ix_channels_sort_order ON channels(sort_order)")
        con.commit()
        print(f"v1.11.19 channel ordering migration complete ({len(rows)} channels)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
