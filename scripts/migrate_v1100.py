#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v1100.py /path/to/streamforge.db", file=sys.stderr); return 2
    path=Path(sys.argv[1]); con=sqlite3.connect(path)
    try:
        tables={row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        if "channel_categories" in tables:
            cols={row[1] for row in con.execute("pragma table_info(channel_categories)")}
            if "sort_order" not in cols:
                con.execute("alter table channel_categories add column sort_order integer not null default 100")
            rows=con.execute("select id from channel_categories order by coalesce(sort_order,100), lower(name), id").fetchall()
            for index,(category_id,) in enumerate(rows,1):
                con.execute("update channel_categories set sort_order=? where id=?",(index*10,category_id))
        if "stream_users" in tables:
            cols={row[1] for row in con.execute("pragma table_info(stream_users)")}
            if "xtream_password_enc" not in cols:
                con.execute("alter table stream_users add column xtream_password_enc text")
        con.commit(); return 0
    finally: con.close()
if __name__ == '__main__': raise SystemExit(main())
