#!/usr/bin/env python3
import sqlite3, sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/var/lib/streamforge/streamforge.db')
con = sqlite3.connect(path)
try:
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("""CREATE TABLE IF NOT EXISTS channel_category_links (
        channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        category_id INTEGER NOT NULL REFERENCES channel_categories(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 100,
        PRIMARY KEY(channel_id, category_id)
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_channel_category_links_category_id ON channel_category_links(category_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_channel_category_links_channel_id ON channel_category_links(channel_id)")
    con.execute("INSERT OR IGNORE INTO channel_category_links(channel_id, category_id, position) SELECT id, category_id, 10 FROM channels WHERE category_id IS NOT NULL")
    con.commit()
    count = con.execute("SELECT count(*) FROM channel_category_links").fetchone()[0]
    print(f"v1.11.63 multi-category migration complete ({count} links)")
finally:
    con.close()
