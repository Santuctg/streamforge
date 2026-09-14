#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
from urllib.parse import urlsplit

def public_url(row: sqlite3.Row) -> str:
    dns = str(row["dns_name"] or "").strip().strip("/")
    scheme = str(row["dns_scheme"] or "http").strip().lower()
    port = int(row["agent_port"] or 8810)
    if dns:
        default = 443 if scheme == "https" else 80
        suffix = "" if port == default else f":{port}"
        return f"{scheme}://{dns}{suffix}"
    return str(row["api_url"] or "").strip().rstrip("/")

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: migrate_v180.py /path/to/streamforge.db", file=sys.stderr); return 2
    path=Path(sys.argv[1]); con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    try:
        cols={row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        if "playlist_url" not in cols:
            con.execute("ALTER TABLE nodes ADD COLUMN playlist_url TEXT")
        if "playlist_dns_only" not in cols:
            con.execute("ALTER TABLE nodes ADD COLUMN playlist_dns_only BOOLEAN NOT NULL DEFAULT 0")
        for row in con.execute("SELECT id,node_type,api_url,dns_name,dns_scheme,dns_only,agent_port,playlist_url,playlist_dns_only FROM nodes"):
            if row["node_type"] != "remote": continue
            url = str(row["playlist_url"] or "").strip() or public_url(row)
            con.execute("UPDATE nodes SET playlist_url=?, playlist_dns_only=? WHERE id=?", (url or None, int(bool(row["dns_only"])), row["id"]))
        con.commit()
    finally:
        con.close()
    print("v1.8.0 node Android/API access migration complete")
    return 0
if __name__ == "__main__": raise SystemExit(main())
