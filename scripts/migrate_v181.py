#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
from urllib.parse import urlsplit

def main() -> int:
    if len(sys.argv) != 2:
        print('usage: migrate_v181.py /path/to/streamforge.db', file=sys.stderr)
        return 2
    path=Path(sys.argv[1])
    con=sqlite3.connect(path)
    con.row_factory=sqlite3.Row
    try:
        cols={row[1] for row in con.execute('PRAGMA table_info(nodes)')}
        if 'playlist_port' not in cols:
            con.execute('ALTER TABLE nodes ADD COLUMN playlist_port INTEGER NOT NULL DEFAULT 8810')
        rows=list(con.execute('SELECT id,node_type,api_url,agent_port,playlist_url,playlist_port FROM nodes'))
        for row in rows:
            if row['node_type'] != 'remote':
                continue
            raw=str(row['playlist_url'] or row['api_url'] or '').strip()
            parsed=urlsplit(raw)
            port=parsed.port or int(row['agent_port'] or 8810)
            current=int(row['playlist_port'] or 0)
            # The newly added default is 8810. Preserve an explicit legacy URL
            # port when it differs, otherwise keep the existing agent port.
            if current in (0, 8810) and port:
                con.execute('UPDATE nodes SET playlist_port=? WHERE id=?', (int(port), row['id']))
        con.commit()
    finally:
        con.close()
    print('v1.8.1 playlist/API port migration complete')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
