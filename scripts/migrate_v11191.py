#!/usr/bin/env python3
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

DUPLICATE_SUFFIX = re.compile(
    r"^(https?://(?:\[[^\]]+\]|[^/:?#]+)):(\d{1,5})(/[A-Za-z0-9_-]+):\2\3$",
    re.IGNORECASE,
)


def repair_line(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    match = DUPLICATE_SUFFIX.fullmatch(value)
    if match:
        value = f"{match.group(1)}:{match.group(2)}{match.group(3)}"
    return value


def repair_rows(value: object) -> str:
    rows: list[str] = []
    for raw in str(value or "").replace("\r", "").splitlines():
        item = repair_line(raw)
        if item and item not in rows:
            rows.append(item)
    return "\n".join(rows)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: migrate_v11191.py /path/to/streamforge.db")
    db_path = Path(sys.argv[1])
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    changed = 0
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(nodes)")}
        required = {"id", "api_url", "api_urls", "playlist_url", "playlist_urls", "access_slug", "playlist_access_slug", "agent_port", "playlist_port"}
        if not required.issubset(columns):
            print("v1.11.91 URL repair skipped: required node columns are not ready")
            return 0
        rows = connection.execute(
            "SELECT id, api_url, api_urls, playlist_url, playlist_urls FROM nodes"
        ).fetchall()
        for row in rows:
            api_rows = repair_rows(row["api_urls"] or row["api_url"])
            playlist_rows = repair_rows(row["playlist_urls"] or row["playlist_url"])
            api_primary = api_rows.splitlines()[0] if api_rows else repair_line(row["api_url"] or "")
            playlist_primary = playlist_rows.splitlines()[0] if playlist_rows else repair_line(row["playlist_url"] or "")
            access_slug = urlsplit(api_primary).path.strip("/") if api_primary else ""
            playlist_slug = urlsplit(playlist_primary).path.strip("/") if playlist_primary else ""
            api_port = int(urlsplit(api_primary).port or (443 if api_primary.startswith("https://") else 80)) if api_primary else 80
            playlist_port = int(urlsplit(playlist_primary).port or (443 if playlist_primary.startswith("https://") else 80)) if playlist_primary else api_port
            before = (row["api_url"] or "", row["api_urls"] or "", row["playlist_url"] or "", row["playlist_urls"] or "")
            after = (api_primary, api_rows or api_primary, playlist_primary, playlist_rows or playlist_primary)
            if before != after:
                changed += 1
            connection.execute(
                "UPDATE nodes SET api_url=?, api_urls=?, playlist_url=?, playlist_urls=?, access_slug=?, playlist_access_slug=?, agent_port=?, playlist_port=? WHERE id=?",
                (api_primary, api_rows or api_primary, playlist_primary, playlist_rows or playlist_primary, access_slug or None, playlist_slug or None, api_port, playlist_port, row["id"]),
            )
        connection.commit()
    finally:
        connection.close()
    print(f"v1.11.91 Main URL/listener repair complete: {changed} node record(s) normalized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
