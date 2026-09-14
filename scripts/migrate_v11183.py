#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import urllib.parse
from pathlib import Path

DEFAULT_WEB_PORT = 80
LEGACY_PLAYLIST_PORTS = {0, 79, 8080, 8811}


def effective_port(value: str, fallback: int = 0) -> int:
    try:
        parsed = urllib.parse.urlsplit(str(value or '').strip())
        if parsed.port is not None:
            return int(parsed.port)
        if parsed.scheme == 'https':
            return 443
        if parsed.scheme == 'http':
            return 80
    except (ValueError, TypeError):
        pass
    return int(fallback or 0)


def rewrite_http_port(value: str, desired_port: int) -> str:
    raw = str(value or '').strip()
    if not raw:
        return raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        current_port = parsed.port
    except (ValueError, TypeError):
        return raw
    if parsed.scheme.lower() != 'http' or not parsed.hostname:
        return raw
    current_effective = int(current_port or 80)
    if current_effective not in LEGACY_PLAYLIST_PORTS and current_effective != desired_port:
        return raw
    host = parsed.hostname
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    netloc = host if desired_port == 80 else f'{host}:{desired_port}'
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)).rstrip('/')


def rewrite_lines(value: str, desired_port: int) -> str:
    rows: list[str] = []
    for line in str(value or '').replace('\r', '').split('\n'):
        cleaned = line.strip()
        if not cleaned:
            continue
        rewritten = rewrite_http_port(cleaned, desired_port)
        if rewritten not in rows:
            rows.append(rewritten)
    return '\n'.join(rows)


def main() -> None:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else '/var/lib/streamforge/streamforge.db')
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    changed = 0
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone():
            print('v1.11.83 migration skipped: nodes table not found')
            return
        columns = {row[1] for row in con.execute('PRAGMA table_info(nodes)')}
        required = {'id', 'node_type', 'api_url', 'playlist_url', 'playlist_port'}
        if not required.issubset(columns):
            print('v1.11.83 migration skipped: required node columns are missing')
            return
        select_cols = ['id', 'api_url', 'playlist_url', 'playlist_port']
        for optional in ('api_urls', 'playlist_urls', 'agent_port'):
            if optional in columns:
                select_cols.append(optional)
        rows = con.execute(
            f"SELECT {','.join(select_cols)} FROM nodes WHERE node_type='remote'"
        ).fetchall()
        for row in rows:
            panel_port = effective_port(
                str(row['api_url'] or ''),
                int(row['agent_port'] or 80) if 'agent_port' in columns else 80,
            ) or 80
            current_playlist_port = int(row['playlist_port'] or 0)
            # v1.11.83 only auto-unifies known legacy/default Playlist ports.
            # A genuinely custom port remains untouched and can still be used.
            if panel_port != DEFAULT_WEB_PORT or current_playlist_port not in LEGACY_PLAYLIST_PORTS:
                continue
            updates: list[str] = []
            values: list[object] = []
            if current_playlist_port != panel_port:
                updates.append('playlist_port=?')
                values.append(panel_port)
            current_primary = str(row['playlist_url'] or '')
            new_primary = rewrite_http_port(current_primary, panel_port)
            if new_primary != current_primary:
                updates.append('playlist_url=?')
                values.append(new_primary)
            if 'playlist_urls' in columns:
                current_aliases = str(row['playlist_urls'] or '')
                new_aliases = rewrite_lines(current_aliases, panel_port)
                if new_aliases != current_aliases:
                    updates.append('playlist_urls=?')
                    values.append(new_aliases)
            if updates:
                values.append(int(row['id']))
                con.execute(f"UPDATE nodes SET {','.join(updates)} WHERE id=?", values)
                changed += 1
        con.commit()
    finally:
        con.close()
    print(f'v1.11.83 unified port migration complete: {changed} remote node(s) updated')


if __name__ == '__main__':
    main()
