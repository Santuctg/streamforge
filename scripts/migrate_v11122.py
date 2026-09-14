#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v11122.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Database not found: {path}", file=sys.stderr)
        return 1
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        if "nodes" in tables:
            columns = {row[1] for row in con.execute("pragma table_info(nodes)")}
            if "total_max_connections" not in columns:
                con.execute("alter table nodes add column total_max_connections integer not null default 0")
        con.execute(
            "create table if not exists app_settings ("
            "key varchar(120) primary key, value text not null default '', updated_at datetime)"
        )
        if "stream_users" in tables:
            columns = {row[1] for row in con.execute("pragma table_info(stream_users)")}
            assignments: list[str] = []
            if "delivery_mode" in columns:
                assignments.append("delivery_mode='central'")
            if "direct_node_id" in columns:
                assignments.append("direct_node_id=null")
            if "load_balance_enabled" in columns:
                assignments.append("load_balance_enabled=1")
            if assignments:
                con.execute(f"update stream_users set {', '.join(assignments)}")
        con.commit()
    print("v1.11.22 ownership and connection-capacity migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
