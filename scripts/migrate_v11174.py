#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

def add_column(con: sqlite3.Connection, name: str, ddl: str) -> None:
    cols={row[1] for row in con.execute('PRAGMA table_info(nodes)')}
    if name not in cols:
        con.execute(f'ALTER TABLE nodes ADD COLUMN {ddl}')

def main() -> None:
    db=Path(sys.argv[1] if len(sys.argv)>1 else '/var/lib/streamforge/streamforge.db')
    con=sqlite3.connect(db)
    try:
        add_column(con,'api_urls','api_urls TEXT')
        add_column(con,'playlist_urls','playlist_urls TEXT')
        add_column(con,'access_slug','access_slug VARCHAR(120)')
        con.execute("UPDATE nodes SET api_urls=api_url WHERE node_type='remote' AND COALESCE(TRIM(api_urls),'')='' AND COALESCE(TRIM(api_url),'')<>''")
        con.execute("UPDATE nodes SET playlist_urls=playlist_url WHERE node_type='remote' AND COALESCE(TRIM(playlist_urls),'')='' AND COALESCE(TRIM(playlist_url),'')<>''")
        con.commit()
        print('v1.11.74 node access URL migration complete')
    finally:
        con.close()
if __name__=='__main__': main()
