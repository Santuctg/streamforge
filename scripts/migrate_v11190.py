#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import sqlite3
import sys
import urllib.parse
from pathlib import Path


def configured_hosts(row: sqlite3.Row, columns: set[str]) -> set[str]:
    values: list[str] = []
    for key in ("api_urls", "api_url", "playlist_urls", "playlist_url"):
        if key in columns:
            values.extend(str(row[key] or "").splitlines())
    hosts: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        try:
            parsed = urllib.parse.urlsplit(item)
        except ValueError:
            continue
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    return hosts


def has_domain(hosts: set[str]) -> bool:
    for host in hosts:
        if host == "localhost":
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return True
    return False


def main() -> None:
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    changed = 0
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone():
            print("v1.11.90 Main configured-host migration skipped: nodes table not found")
            return
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        if not {"id", "node_type", "dns_only"}.issubset(columns):
            print("v1.11.90 Main configured-host migration skipped: required columns not found")
            return
        row = con.execute("SELECT * FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1").fetchone()
        if row and not bool(row["dns_only"]):
            hosts = configured_hosts(row, columns)
            # Existing installations are switched to configured-host-only mode
            # only when at least one real domain alias is present. Pure-IP
            # deployments stay reachable until the operator enables the option.
            if hosts and has_domain(hosts):
                updates = ["dns_only=1"]
                if "playlist_dns_only" in columns:
                    updates.append("playlist_dns_only=1")
                con.execute(f"UPDATE nodes SET {','.join(updates)} WHERE id=?", (int(row["id"]),))
                changed = 1
        con.commit()
    finally:
        con.close()
    print(f"v1.11.90 Main configured-host migration complete: changed={changed}")


if __name__ == "__main__":
    main()
