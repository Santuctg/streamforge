#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


# STREAMFORGE_RESETDOMAIN_COMMAND_V3016:
# Recovery helper used by `sudo streamforge resetdomain`.  It intentionally
# uses only the Python standard library so it works before application imports.


def normalize_url(raw: str) -> tuple[str, str, str, int]:
    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must contain a hostname/IP and no credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not contain a query string or fragment")
    try:
        port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("URL port must be between 1 and 65535")

    host = parsed.hostname.strip().lower().rstrip(".")
    try:
        normalized_host = str(ipaddress.ip_address(host))
        display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    except ValueError:
        try:
            normalized_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("URL contains an invalid hostname") from exc
        labels = normalized_host.split(".")
        if len(normalized_host) > 253 or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("URL contains an invalid hostname")
        display_host = normalized_host

    slug = parsed.path.strip("/")
    if slug and ("/" in slug or not re.fullmatch(r"[A-Za-z0-9._~-]{1,120}", slug)):
        raise ValueError("URL path must be one safe segment of at most 120 characters")
    explicit_port = parsed.port is not None
    netloc = display_host + (f":{port}" if explicit_port else "")
    canonical = urlunsplit((parsed.scheme.lower(), netloc, f"/{slug}" if slug else "", "", ""))
    return canonical, normalized_host, slug, port


def database_path(database_url: str) -> Path:
    value = str(database_url or "").strip()
    if not value.startswith("sqlite:///"):
        raise ValueError("resetdomain currently requires StreamForge's SQLite database")
    raw_path = value[len("sqlite:///"):]
    if not raw_path:
        raise ValueError("SQLite database path is empty")
    path = Path(raw_path if raw_path.startswith("/") else f"/opt/streamforge/{raw_path}")
    return path.resolve()


def reset_database(raw_url: str, database_url: str) -> tuple[str, Path]:
    canonical, host, slug, port = normalize_url(raw_url)
    path = database_path(database_url)
    if not path.is_file():
        raise ValueError(f"StreamForge database not found: {path}")

    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(nodes)")}
        if not columns:
            raise ValueError("StreamForge nodes table is missing")
        row = connection.execute(
            "SELECT id FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            raise ValueError("Local Main Server record is missing")

        backup_dir = path.parent / "domain-reset-backups"
        backup_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"streamforge-before-domain-reset-{stamp}.db"
        backup_connection = sqlite3.connect(backup_path)
        try:
            connection.backup(backup_connection)
        finally:
            backup_connection.close()
        os.chmod(backup_path, 0o640)

        values: dict[str, object] = {
            "api_url": canonical,
            "api_urls": canonical,
            "playlist_url": canonical,
            "playlist_urls": canonical,
            "access_slug": slug or None,
            "playlist_access_slug": slug or None,
            "dns_name": host,
            "dns_scheme": urlsplit(canonical).scheme,
            "dns_only": 1,
            "playlist_dns_only": 1,
            "agent_port": port,
            "playlist_port": port,
        }
        fields = [name for name in values if name in columns]
        connection.execute(
            "UPDATE nodes SET " + ", ".join(f"{name}=?" for name in fields) + " WHERE id=?",
            [values[name] for name in fields] + [row[0]],
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        return canonical, backup_path
    finally:
        connection.close()


def _role_urls_with_current_authority(
    value: object,
    primary_value: object,
    raw_authority: str,
) -> list[str]:
    """Preserve every role alias while guaranteeing a recovery authority."""
    # STREAMFORGE_UPDATE_ROLE_URL_DECONTAMINATION_V3036 compatibility marker:
    # role fields remain independent, but valid secondary paths are no longer
    # treated as contamination merely because they differ from the primary.
    # STREAMFORGE_UPDATE_PRESERVE_ALL_ACCESS_URLS_V3038:
    # Older updater logic retained only aliases whose path matched the primary
    # URL, then rebased every retained alias onto one authority.  That deleted
    # legitimate alternate paths and collapsed alternate ports into duplicate
    # rows.  Keep every valid distinct URL.  If any alias already uses the
    # current environment authority, no database URL needs rewriting.  Only
    # when the current authority is completely absent is the primary URL
    # rebased for recovery; all secondary aliases remain intact.
    authority, _host, _slug, _port = normalize_url(raw_authority)
    authority_parts = urlsplit(authority)
    primary_source = str(primary_value or "").strip().splitlines()
    primary_candidate = next((line.strip() for line in primary_source if line.strip()), "")
    if not primary_candidate:
        primary_candidate = next(
            (line.strip() for line in str(value or "").splitlines() if line.strip()),
            authority,
        )
    candidates = [primary_candidate] + [
        line.strip() for line in str(value or "").splitlines() if line.strip()
    ]
    normalized_candidates: list[str] = []
    for candidate in candidates:
        normalized, _old_host, _old_slug, _old_port = normalize_url(candidate)
        if normalized not in normalized_candidates:
            normalized_candidates.append(normalized)
    if not normalized_candidates:
        return [authority]

    def authority_key(url: str) -> tuple[str, str, int]:
        normalized, host, _slug, port = normalize_url(url)
        return urlsplit(normalized).scheme, host, port

    current_key = authority_key(authority)
    if any(authority_key(candidate) == current_key for candidate in normalized_candidates):
        return normalized_candidates

    primary_parts = urlsplit(normalized_candidates[0])
    rebased_primary = urlunsplit((
        authority_parts.scheme,
        authority_parts.netloc,
        primary_parts.path,
        "",
        "",
    )).rstrip("/")
    output = [rebased_primary]
    output.extend(candidate for candidate in normalized_candidates[1:] if candidate != rebased_primary)
    return output


# STREAMFORGE_UPDATE_PRESERVE_ACCESS_PATHS_V3026:
# Updates may need to move a stale Main IP/hostname to the destination server's
# current authority. Panel and Playlist paths are independent credentials and
# must never be replaced by the environment URL's (often empty) path.
def sync_database_authority(raw_url: str, database_url: str) -> tuple[str, Path, str, str]:
    canonical, host, _slug, port = normalize_url(raw_url)
    path = database_path(database_url)
    if not path.is_file():
        raise ValueError(f"StreamForge database not found: {path}")

    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(nodes)")}
        wanted = [name for name in (
            "id", "api_url", "api_urls", "playlist_url", "playlist_urls",
            "access_slug", "playlist_access_slug", "dns_name", "dns_scheme",
            "dns_only", "playlist_dns_only", "agent_port", "playlist_port",
        ) if name in columns]
        row = connection.execute(
            "SELECT " + ", ".join(wanted) + " FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("Local Main Server record is missing")

        backup_dir = path.parent / "domain-reset-backups"
        backup_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"streamforge-before-domain-authority-sync-{stamp}.db"
        backup_connection = sqlite3.connect(backup_path)
        try:
            connection.backup(backup_connection)
        finally:
            backup_connection.close()
        os.chmod(backup_path, 0o640)

        api_urls = _role_urls_with_current_authority(
            row["api_urls"] if "api_urls" in columns else None,
            row["api_url"] if "api_url" in columns else None,
            canonical,
        )
        playlist_urls = _role_urls_with_current_authority(
            row["playlist_urls"] if "playlist_urls" in columns else None,
            row["playlist_url"] if "playlist_url" in columns else None,
            canonical,
        )
        api_first = api_urls[0]
        playlist_first = playlist_urls[0]
        api_parts = urlsplit(api_first)
        playlist_parts = urlsplit(playlist_first)
        values: dict[str, object] = {
            "api_url": api_first,
            "api_urls": "\n".join(api_urls),
            "playlist_url": playlist_first,
            "playlist_urls": "\n".join(playlist_urls),
            "access_slug": urlsplit(api_first).path.strip("/") or None,
            "playlist_access_slug": urlsplit(playlist_first).path.strip("/") or None,
            "dns_name": api_parts.hostname or host,
            "dns_scheme": api_parts.scheme or urlsplit(canonical).scheme,
            "dns_only": 1,
            "playlist_dns_only": 1,
            "agent_port": int(api_parts.port or (443 if api_parts.scheme == "https" else 80)),
            "playlist_port": int(playlist_parts.port or (443 if playlist_parts.scheme == "https" else 80)),
        }
        fields = [name for name in values if name in columns]
        connection.execute(
            "UPDATE nodes SET " + ", ".join(f"{name}=?" for name in fields) + " WHERE id=?",
            [values[name] for name in fields] + [row["id"]],
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        return canonical, backup_path, api_first, playlist_first
    finally:
        connection.close()


_MAIN_ACCESS_FIELDS = (
    "api_url", "api_urls", "playlist_url", "playlist_urls",
    "access_slug", "playlist_access_slug", "dns_name", "dns_scheme",
    "dns_only", "playlist_dns_only", "agent_port", "playlist_port",
)


def _first_saved_access_url(row: dict[str, object], plural: str, primary: str) -> str:
    candidates = [str(row.get(primary) or "").strip()]
    candidates.extend(str(row.get(plural) or "").replace("\r", "").splitlines())
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        try:
            return normalize_url(value)[0]
        except ValueError:
            continue
    return ""


def _saved_url_uses_ip(value: str) -> bool:
    if not value:
        return False
    try:
        host = normalize_url(value)[1]
        ipaddress.ip_address(host)
        return True
    except (ValueError, TypeError):
        return False


def _read_local_access(path: Path) -> tuple[dict[str, object], set[str]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA busy_timeout=10000")
    connection.row_factory = sqlite3.Row
    try:
        columns = {str(item[1]) for item in connection.execute("PRAGMA table_info(nodes)")}
        wanted = ["id", *[name for name in _MAIN_ACCESS_FIELDS if name in columns]]
        if "id" not in columns or "node_type" not in columns:
            return {}, columns
        row = connection.execute(
            "SELECT " + ", ".join(wanted) + " FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
        ).fetchone()
        return (dict(row) if row is not None else {}), columns
    finally:
        connection.close()


# STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054:
# A normal package update must never treat a fallback environment IP as more
# authoritative than the exact Panel/Playlist URLs already stored in the Main
# database. v3.0.53 did that and could replace a configured domain with the
# server IP. If that precise regression is detected, restore every access field
# from the newest pre-authority-sync snapshot that still contains a DNS name.
def select_update_authority(database_url: str, installed_version: str = "") -> tuple[str, Path | None]:
    path = database_path(database_url)
    if not path.is_file():
        raise ValueError(f"StreamForge database not found: {path}")

    current, current_columns = _read_local_access(path)
    if not current:
        raise ValueError("Local Main Server record is missing")
    current_panel = _first_saved_access_url(current, "api_urls", "api_url")
    repaired_backup: Path | None = None

    if str(installed_version or "").strip() == "3.0.53" and _saved_url_uses_ip(current_panel):
        backup_dir = path.parent / "domain-reset-backups"
        snapshots = sorted(
            backup_dir.glob("streamforge-before-domain-authority-sync-*.db"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ) if backup_dir.is_dir() else []
        for snapshot in snapshots:
            try:
                saved, saved_columns = _read_local_access(snapshot)
                saved_panel = _first_saved_access_url(saved, "api_urls", "api_url")
            except (OSError, sqlite3.Error, ValueError):
                continue
            if not saved_panel or _saved_url_uses_ip(saved_panel):
                continue

            fields = [
                name for name in _MAIN_ACCESS_FIELDS
                if name in current_columns and name in saved_columns
            ]
            if not fields:
                continue
            backup_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            repaired_backup = backup_dir / f"streamforge-before-update-access-repair-{stamp}.db"

            connection = sqlite3.connect(path, timeout=30)
            connection.execute("PRAGMA busy_timeout=30000")
            safety = sqlite3.connect(repaired_backup)
            try:
                connection.backup(safety)
            finally:
                safety.close()
            os.chmod(repaired_backup, 0o640)
            try:
                connection.execute(
                    "UPDATE nodes SET " + ", ".join(f"{name}=?" for name in fields) + " WHERE id=?",
                    [saved.get(name) for name in fields] + [current["id"]],
                )
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            finally:
                connection.close()
            current_panel = saved_panel
            break

    if not current_panel:
        raise ValueError("Main Panel/API URL is missing from the database")
    return normalize_url(current_panel)[0], repaired_backup


def update_environment(raw_url: str, env_file: str) -> str:
    canonical, _host, _slug, _port = normalize_url(raw_url)
    path = Path(env_file)
    existing = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    keys = {"STREAMFORGE_PUBLIC_BASE_URL", "STREAMFORGE_RELAY_BASE_URL"}
    retained = [
        line for line in existing
        if not any(line.strip().startswith(key + "=") for key in keys)
    ]
    retained.extend([
        f"STREAMFORGE_PUBLIC_BASE_URL={canonical}",
        f"STREAMFORGE_RELAY_BASE_URL={canonical}",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_stat = path.stat() if path.exists() else None
    fd, temporary_name = tempfile.mkstemp(prefix=".streamforge-env-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if previous_stat:
            os.chown(temporary, previous_stat.st_uid, previous_stat.st_gid)
            os.chmod(temporary, previous_stat.st_mode & 0o777)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return canonical


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("url")
    path_parser = subparsers.add_parser("db-path")
    path_parser.add_argument("database_url")
    database_parser = subparsers.add_parser("database")
    database_parser.add_argument("url")
    database_parser.add_argument("database_url")
    authority_parser = subparsers.add_parser("database-authority")
    authority_parser.add_argument("url")
    authority_parser.add_argument("database_url")
    update_authority_parser = subparsers.add_parser("update-authority")
    update_authority_parser.add_argument("database_url")
    update_authority_parser.add_argument("installed_version", nargs="?", default="")
    environment_parser = subparsers.add_parser("environment")
    environment_parser.add_argument("url")
    environment_parser.add_argument("env_file")
    args = parser.parse_args()
    try:
        if args.command == "normalize":
            print(normalize_url(args.url)[0])
        elif args.command == "db-path":
            print(database_path(args.database_url))
        elif args.command == "database":
            canonical, backup_path = reset_database(args.url, args.database_url)
            print(f"Main access database reset: {canonical}")
            print(f"Safety backup: {backup_path}")
        elif args.command == "database-authority":
            canonical, backup_path, panel_url, playlist_url = sync_database_authority(args.url, args.database_url)
            print(f"Main access authority synchronized: {canonical}")
            print(f"Panel path preserved: {panel_url}")
            print(f"Playlist path preserved: {playlist_url}")
            print(f"Safety backup: {backup_path}")
        elif args.command == "update-authority":
            canonical, _repair_backup = select_update_authority(args.database_url, args.installed_version)
            print(canonical)
        elif args.command == "environment":
            print(f"Main environment URL reset: {update_environment(args.url, args.env_file)}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
