#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path


def env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def valid_base(value: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    return cleaned


def path_slug(value: str) -> str:
    try:
        raw = urllib.parse.urlsplit(value).path.strip("/")
    except ValueError:
        return ""
    return raw if raw and "/" not in raw else ""


def effective_port(value: str) -> int:
    parsed = urllib.parse.urlsplit(value)
    return int(parsed.port or (443 if parsed.scheme == "https" else 80))


def main() -> None:
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/streamforge/streamforge.db")
    env_path = Path(sys.argv[2] if len(sys.argv) > 2 else "/etc/streamforge.env")
    base = valid_base(env_value(env_path, "STREAMFORGE_PUBLIC_BASE_URL")) or "http://127.0.0.1"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    local_changed = 0
    role_changed = 0
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" in tables:
            columns = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
            wanted = {
                "api_url", "api_urls", "playlist_url", "playlist_urls",
                "access_slug", "playlist_access_slug", "agent_port", "playlist_port",
            }
            if {"id", "node_type"}.issubset(columns):
                row = con.execute("SELECT * FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1").fetchone()
                if row:
                    current_api = valid_base(str(row["api_url"] or "")) if "api_url" in columns else ""
                    current_api_aliases = str(row["api_urls"] or "").strip() if "api_urls" in columns else ""
                    current_playlist = valid_base(str(row["playlist_url"] or "")) if "playlist_url" in columns else ""
                    current_playlist_aliases = str(row["playlist_urls"] or "").strip() if "playlist_urls" in columns else ""
                    api = current_api or valid_base(current_api_aliases.splitlines()[0] if current_api_aliases else "") or base
                    playlist = current_playlist or valid_base(current_playlist_aliases.splitlines()[0] if current_playlist_aliases else "") or api
                    updates: list[str] = []
                    values: list[object] = []
                    candidates = {
                        "api_url": api,
                        "api_urls": current_api_aliases or api,
                        "playlist_url": playlist,
                        "playlist_urls": current_playlist_aliases or playlist,
                        "access_slug": path_slug(api) or None,
                        "playlist_access_slug": path_slug(playlist) or None,
                        "agent_port": effective_port(api),
                        "playlist_port": effective_port(playlist),
                    }
                    for key, value in candidates.items():
                        if key not in columns:
                            continue
                        current = row[key]
                        if key in {"api_url", "api_urls", "playlist_url", "playlist_urls"} and str(current or "").strip():
                            continue
                        if key in {"access_slug", "playlist_access_slug"} and str(current or "").strip():
                            continue
                        if current != value:
                            updates.append(f"{key}=?")
                            values.append(value)
                    # Ports should reflect the public URL even if a legacy local
                    # record inherited the old Remote Node port defaults.
                    for key, value in (("agent_port", effective_port(api)), ("playlist_port", effective_port(playlist))):
                        if key in columns and int(row[key] or 0) != value and key not in [item.split("=",1)[0] for item in updates]:
                            updates.append(f"{key}=?")
                            values.append(value)
                    if updates:
                        values.append(int(row["id"]))
                        con.execute(f"UPDATE nodes SET {','.join(updates)} WHERE id=?", values)
                        local_changed = 1

        if "roles" in tables:
            columns = {row[1] for row in con.execute("PRAGMA table_info(roles)")}
            if {"id", "permissions"}.issubset(columns):
                for row in con.execute("SELECT id, permissions FROM roles").fetchall():
                    try:
                        permissions = json.loads(str(row["permissions"] or "[]"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(permissions, list) or "*" in permissions or "nodes.manage" not in permissions or "main_access.manage" in permissions:
                        continue
                    permissions.append("main_access.manage")
                    normalized = sorted({str(item) for item in permissions})
                    con.execute("UPDATE roles SET permissions=? WHERE id=?", (json.dumps(normalized, separators=(",", ":")), int(row["id"])))
                    role_changed += 1
        con.commit()
    finally:
        con.close()
    print(f"v1.11.85 Main/Local access migration complete: local={local_changed}, roles={role_changed}")


if __name__ == "__main__":
    main()
