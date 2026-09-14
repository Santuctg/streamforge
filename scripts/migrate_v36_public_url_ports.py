#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import urllib.parse
from pathlib import Path

FIELDS = ("api_url", "api_urls", "playlist_url", "playlist_urls")


def normalize_line(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname or ""
        port = parsed.port
    except (TypeError, ValueError):
        return raw
    if parsed.scheme not in {"http", "https"} or not host:
        return raw
    # v3.5 could append the internal plaintext listener (usually :80) to an
    # HTTPS public URL, or the inverse :443 to an HTTP URL.  Those two values
    # are the opposite scheme's defaults and are safe to repair automatically.
    if not ((parsed.scheme == "https" and port == 80) or (parsed.scheme == "http" and port == 443)):
        return raw
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", "")).rstrip("/")


def normalize_value(value: object) -> str:
    text = str(value or "")
    if "\n" not in text and "\r" not in text:
        return normalize_line(text)
    rows = []
    for raw in text.replace("\r", "").split("\n"):
        stripped = raw.strip()
        if stripped:
            rows.append(normalize_line(stripped))
    return "\n".join(rows)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v36_public_url_ports.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("v3.6 public URL port migration skipped: database not found")
        return 0
    con = sqlite3.connect(str(path))
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" not in tables:
            print("v3.6 public URL port migration skipped: nodes table missing")
            return 0
        columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        fields = [field for field in FIELDS if field in columns]
        if not fields:
            print("v3.6 public URL port migration skipped: URL columns missing")
            return 0
        rows = con.execute(f"SELECT id, {', '.join(fields)} FROM nodes").fetchall()
        changed_rows = 0
        changed_values = 0
        for row in rows:
            node_id = int(row[0])
            updates: dict[str, str] = {}
            for field, old in zip(fields, row[1:]):
                if old is None:
                    continue
                new = normalize_value(old)
                if new != str(old):
                    updates[field] = new
            if not updates:
                continue
            assignments = ", ".join(f"{field}=?" for field in updates)
            con.execute(
                f"UPDATE nodes SET {assignments} WHERE id=?",
                [*updates.values(), node_id],
            )
            changed_rows += 1
            changed_values += len(updates)
        con.commit()
        print(
            f"v3.6 public URL port migration complete; repaired {changed_values} URL field(s) on {changed_rows} node(s)"
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
