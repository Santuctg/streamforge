#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v11116.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    con = sqlite3.connect(path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "playlist_profiles" not in tables:
            print("playlist_profiles table does not exist; nothing to migrate")
            return 0
        columns = {row[1] for row in con.execute("PRAGMA table_info(playlist_profiles)")}
        if "category_order" not in columns:
            con.execute("ALTER TABLE playlist_profiles ADD COLUMN category_order TEXT NOT NULL DEFAULT '[]'")

        required = {"playlist_profile_channels", "channels", "channel_categories"}
        if required.issubset(tables):
            playlist_ids = [row[0] for row in con.execute("SELECT id FROM playlist_profiles ORDER BY id")]
            for playlist_id in playlist_ids:
                current = con.execute(
                    "SELECT coalesce(category_order, '[]') FROM playlist_profiles WHERE id=?",
                    (playlist_id,),
                ).fetchone()[0]
                try:
                    parsed = json.loads(current or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = []
                if isinstance(parsed, list) and parsed:
                    continue
                rows = con.execute(
                    """
                    SELECT c.category_id, cc.name, coalesce(cc.sort_order, 100000)
                    FROM playlist_profile_channels ppc
                    JOIN channels c ON c.id=ppc.channel_id
                    LEFT JOIN channel_categories cc ON cc.id=c.category_id
                    WHERE ppc.playlist_id=?
                    GROUP BY c.category_id, cc.name, cc.sort_order
                    ORDER BY CASE WHEN c.category_id IS NULL THEN 1 ELSE 0 END,
                             coalesce(cc.sort_order, 100000), lower(coalesce(cc.name, 'Uncategorized'))
                    """,
                    (playlist_id,),
                ).fetchall()
                order = [f"category:{int(category_id)}" if category_id is not None else "uncategorized" for category_id, _name, _sort in rows]
                con.execute(
                    "UPDATE playlist_profiles SET category_order=? WHERE id=?",
                    (json.dumps(order, separators=(",", ":")), playlist_id),
                )
        con.commit()
    finally:
        con.close()
    print("v1.11.16 playlist hierarchy migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
