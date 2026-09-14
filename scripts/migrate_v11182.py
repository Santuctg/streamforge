#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import urllib.parse
from pathlib import Path

DEFAULT_NODE_PORT = 80
LEGACY_NODE_PORT = 8810


def rewrite_http_legacy_port(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        return value
    try:
        port = parsed.port
    except ValueError:
        return value
    if port != LEGACY_NODE_PORT:
        return value
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{DEFAULT_NODE_PORT}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def rewrite_lines(value: str) -> str:
    rows: list[str] = []
    for raw in str(value or "").replace("\r", "").split("\n"):
        cleaned = raw.strip()
        if not cleaned:
            continue
        rewritten = rewrite_http_legacy_port(cleaned)
        if rewritten not in rows:
            rows.append(rewritten)
    return "\n".join(rows)


def main() -> None:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    changed = 0
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone():
            print("v1.11.82 migration skipped: nodes table not found")
            return
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        required = {"id", "node_type", "api_url", "agent_port"}
        if not required.issubset(columns):
            print("v1.11.82 migration skipped: required node columns are missing")
            return
        select_columns = ["id", "node_type", "api_url", "agent_port"]
        if "api_urls" in columns:
            select_columns.append("api_urls")
        for row in con.execute(f"SELECT {','.join(select_columns)} FROM nodes WHERE node_type='remote'").fetchall():
            current_port = int(row["agent_port"] or 0)
            new_port = DEFAULT_NODE_PORT if current_port in (0, LEGACY_NODE_PORT) else current_port
            current_api = str(row["api_url"] or "")
            new_api = rewrite_http_legacy_port(current_api)
            current_aliases = str(row["api_urls"] or "") if "api_urls" in columns else ""
            new_aliases = rewrite_lines(current_aliases) if "api_urls" in columns else ""
            updates: list[str] = []
            values: list[object] = []
            if new_port != current_port:
                updates.append("agent_port=?")
                values.append(new_port)
            if new_api != current_api:
                updates.append("api_url=?")
                values.append(new_api)
            if "api_urls" in columns and new_aliases != current_aliases:
                updates.append("api_urls=?")
                values.append(new_aliases)
            if updates:
                values.append(int(row["id"]))
                con.execute(f"UPDATE nodes SET {','.join(updates)} WHERE id=?", values)
                changed += 1
        con.commit()
    finally:
        con.close()
    print(f"v1.11.82 port-80 default migration complete: {changed} remote node(s) updated")


if __name__ == "__main__":
    main()
