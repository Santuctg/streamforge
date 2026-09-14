from __future__ import annotations

import json
import hashlib
import os
import re
import posixpath
import shutil
import sqlite3
import subprocess
import tarfile
import io
import urllib.request
import urllib.error
import urllib.parse
import time
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import paramiko
from cryptography.fernet import Fernet

CONFIG_FILE = Path("/var/lib/streamforge/backup-targets.json")
ARCHIVE_ROOT = Path("/var/lib/streamforge/backups")
BACKUP_SECRET_KEY = Path("/var/lib/streamforge/backups/secret.key")
BACKUP_SMB_AUTH_ROOT = Path("/var/lib/streamforge/backups/smb-auth")
LOCAL_BACKUP_CONFIG_FILE = Path("/var/lib/streamforge/local-backup-settings.json")


def _normalize_schedule_time(value: Any) -> str:
    """Return a strict server-local HH:MM backup time."""
    text = str(value or "00:00").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError("Backup time must use HH:MM format")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Backup time must be between 00:00 and 23:59")
    return f"{hour:02d}:{minute:02d}"


def _secret_box() -> Fernet:
    BACKUP_SECRET_KEY.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP_SECRET_KEY.exists():
        BACKUP_SECRET_KEY.write_bytes(Fernet.generate_key())
        os.chmod(BACKUP_SECRET_KEY, 0o600)
    return Fernet(BACKUP_SECRET_KEY.read_bytes().strip())


def _encrypt_secret(value: str) -> str:
    value = str(value or "")
    return _secret_box().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def _decrypt_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    try:
        return _secret_box().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("Stored backup credential could not be decrypted") from exc


def _write_smb_auth_file(username: str, password: str, domain: str = "") -> Path:
    BACKUP_SMB_AUTH_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_SMB_AUTH_ROOT, 0o700)
    path = BACKUP_SMB_AUTH_ROOT / f"auth-{uuid.uuid4().hex}.conf"
    lines = [f"username = {username}", f"password = {password}"]
    if str(domain or "").strip():
        lines.append(f"domain = {str(domain).strip()}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


GOOGLE_OAUTH_FILE = Path("/var/lib/streamforge/google_drive/oauth.json")
GOOGLE_TOKEN_FILE = Path("/var/lib/streamforge/google_drive/token.json")


def google_drive_status() -> dict[str, Any]:
    oauth_configured = False
    client_id_hint = ""
    connected = False
    try:
        data = json.loads(GOOGLE_OAUTH_FILE.read_text(encoding="utf-8")) if GOOGLE_OAUTH_FILE.exists() else {}
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        oauth_configured = bool(client_id and client_secret)
        if client_id:
            client_id_hint = client_id[:8] + "…" + client_id[-12:] if len(client_id) > 24 else client_id
    except Exception:
        pass
    try:
        data = json.loads(GOOGLE_TOKEN_FILE.read_text(encoding="utf-8")) if GOOGLE_TOKEN_FILE.exists() else {}
        connected = bool(str(data.get("refresh_token") or "").strip())
    except Exception:
        pass
    return {
        "oauth_configured": oauth_configured,
        "client_id_hint": client_id_hint,
        "connected": connected,
        "oauth_path": str(GOOGLE_OAUTH_FILE),
        "token_path": str(GOOGLE_TOKEN_FILE),
    }


def save_google_oauth_credentials(client_id: str, client_secret: str) -> None:
    client_id = str(client_id or "").strip()
    client_secret = str(client_secret or "").strip()
    if not client_id or not client_secret:
        raise ValueError("Google OAuth Client ID and Client Secret are required")
    GOOGLE_OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_OAUTH_FILE.write_text(
        json.dumps({"client_id": client_id, "client_secret": client_secret}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(GOOGLE_OAUTH_FILE, 0o600)


def load_google_oauth_credentials() -> dict[str, str]:
    if not GOOGLE_OAUTH_FILE.exists():
        raise RuntimeError("Google OAuth credentials are not configured")
    data = json.loads(GOOGLE_OAUTH_FILE.read_text(encoding="utf-8"))
    client_id = str(data.get("client_id") or "").strip()
    client_secret = str(data.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuth Client ID or Client Secret is missing")
    return {"client_id": client_id, "client_secret": client_secret}


def save_google_drive_token(token: dict[str, Any]) -> None:
    if not str(token.get("refresh_token") or "").strip():
        raise RuntimeError("Google did not return a refresh token")
    GOOGLE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN_FILE.write_text(json.dumps(token, separators=(",", ":")), encoding="utf-8")
    os.chmod(GOOGLE_TOKEN_FILE, 0o600)


def load_google_drive_token() -> dict[str, Any]:
    if not GOOGLE_TOKEN_FILE.exists():
        raise RuntimeError("Google Drive is not connected")
    data = json.loads(GOOGLE_TOKEN_FILE.read_text(encoding="utf-8"))
    if not str(data.get("refresh_token") or "").strip():
        raise RuntimeError("Google Drive refresh token is missing")
    return data


def _google_access_token() -> str:
    creds = load_google_oauth_credentials()
    saved = load_google_drive_token()
    payload = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": saved["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[-500:]
        raise RuntimeError("Google token refresh failed: " + detail) from exc
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Google did not return an access token")
    return access_token


def _google_request(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 120) -> bytes:
    request_headers = {"Authorization": f"Bearer {_google_access_token()}"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"Google Drive API error ({exc.code}): {detail}") from exc


def test_google_drive_connection() -> dict[str, Any]:
    raw = _google_request("https://www.googleapis.com/drive/v3/about?fields=user")
    user = (json.loads(raw.decode("utf-8")).get("user") or {})
    return {
        "message": "Google Drive connected successfully",
        "email": str(user.get("emailAddress") or "").strip(),
    }


def _drive_find_folder(name: str, parent_id: str | None = None) -> str | None:
    safe = name.replace("'", "\\'")
    query = f"name='{safe}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    params = urllib.parse.urlencode({"q": query, "fields": "files(id,name)", "pageSize": "10"})
    data = json.loads(_google_request("https://www.googleapis.com/drive/v3/files?" + params).decode("utf-8"))
    files = data.get("files") or []
    return str(files[0]["id"]) if files else None


def _drive_create_folder(name: str, parent_id: str | None = None) -> str:
    metadata: dict[str, Any] = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    raw = _google_request(
        "https://www.googleapis.com/drive/v3/files?fields=id",
        method="POST",
        data=json.dumps(metadata).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )
    return str(json.loads(raw.decode("utf-8"))["id"])


def _drive_ensure_folder(folder_path: str) -> str | None:
    parts = [p for p in str(folder_path or "").strip("/").split("/") if p]
    parent_id: str | None = None
    for part in parts:
        parent_id = _drive_find_folder(part, parent_id) or _drive_create_folder(part, parent_id)
    return parent_id


def upload_file_to_google_drive(local_path: Path, folder_path: str = "StreamForge-Backups") -> dict[str, Any]:
    if not local_path.exists():
        raise FileNotFoundError(str(local_path))
    parent_id = _drive_ensure_folder(folder_path)
    metadata: dict[str, Any] = {"name": local_path.name}
    if parent_id:
        metadata["parents"] = [parent_id]

    boundary = "streamforge-direct-drive-boundary"
    prefix = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(metadata, separators=(",", ":"))
        + f"\r\n--{boundary}\r\n"
        "Content-Type: application/gzip\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = prefix + local_path.read_bytes() + suffix

    raw = _google_request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink",
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        timeout=1800,
    )
    return json.loads(raw.decode("utf-8"))


def list_local_backup_archives(limit: int = 100) -> list[dict[str, Any]]:
    """List retained Main Server backup archives."""
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in ARCHIVE_ROOT.iterdir():
        if not path.is_file() or not path.name.endswith((".tar.gz", ".tgz")):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "name": path.name,
            "size_bytes": int(stat.st_size),
            "modified_ts": float(stat.st_mtime),
        })
    items.sort(key=lambda item: item["modified_ts"], reverse=True)
    return items[:max(1, min(500, int(limit or 100)))]


def resolve_local_backup_archive(name: str) -> Path:
    """Resolve a retained backup strictly inside ARCHIVE_ROOT."""
    raw = str(name or "")
    safe = Path(raw).name
    if not safe or safe != raw or not safe.endswith((".tar.gz", ".tgz")):
        raise ValueError("Invalid backup archive name")
    root = ARCHIVE_ROOT.resolve()
    path = (ARCHIVE_ROOT / safe).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("Backup archive was not found")
    return path


def _prune_local_backup_archives(keep: int = 20) -> None:
    archives = list_local_backup_archives(limit=500)
    for item in archives[max(1, int(keep or 20)):]:
        try:
            resolve_local_backup_archive(str(item["name"])).unlink(missing_ok=True)
        except Exception:
            pass


def _backup_name_allowed(name: str) -> bool:
    value = Path(str(name or "")).name
    return (
        value == str(name or "")
        and value.startswith("streamforge-main-")
        and value.endswith((".tar.gz", ".tgz"))
    )


def _target_by_id(target_id: str) -> dict[str, Any]:
    target = next((item for item in load_targets() if str(item.get("id")) == str(target_id)), None)
    if not target:
        raise ValueError("Backup destination was not found")
    return target


def _remote_file_row(name: str, size: int = 0, modified_ts: float = 0, remote_id: str = "") -> dict[str, Any]:
    return {
        "name": str(name),
        "size_bytes": int(size or 0),
        "modified_ts": float(modified_ts or 0),
        "remote_id": str(remote_id or ""),
    }


# STREAMFORGE_SMB_BACKUP_MODIFIED_TIME_V94:
def _backup_timestamp_from_name(name: str) -> float:
    """Return the UTC creation stamp embedded in StreamForge backup names."""
    match = re.search(r"streamforge-main-(\d{8})-(\d{6})", str(name or ""))
    if not match:
        return 0.0
    try:
        stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
        return stamp.replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _parse_smbclient_backup_line(line: str) -> tuple[str, int, float] | None:
    """Parse smbclient's long `ls` row for a StreamForge backup archive.

    Typical Samba output is:
      name.tar.gz     A  12345  Sun Aug 16 00:00:46 2026
    The timestamp displayed by smbclient is local to the client process, so a
    naive datetime.timestamp() preserves the same local wall-clock meaning.
    If Samba/localized output cannot be parsed, fall back to the UTC timestamp
    embedded in the backup filename rather than showing an empty Modified cell.
    """
    match = re.match(
        r"^\s*(streamforge-main-\S+\.(?:tar\.gz|tgz))\s+\S+\s+(\d+)\s+"
        r"([A-Za-z]{3})\s+([A-Za-z]{3})\s+(\d{1,2})\s+"
        r"(\d{1,2}:\d{2}:\d{2})\s+(\d{4})(?:\s|$)",
        str(line or ""),
    )
    if match:
        name = match.group(1)
        size = int(match.group(2) or 0)
        date_text = " ".join(match.group(i) for i in range(3, 8))
        try:
            modified = datetime.strptime(date_text, "%a %b %d %H:%M:%S %Y").timestamp()
        except (TypeError, ValueError, OverflowError):
            modified = _backup_timestamp_from_name(name)
        return name, size, modified

    # Some Samba builds/locales vary the date tokens. Keep the filename/size
    # parser tolerant and use the archive's embedded UTC timestamp as fallback.
    fallback = re.match(r"^\s*(streamforge-main-\S+\.(?:tar\.gz|tgz))\s+\S+\s+(\d+)(?:\s|$)", str(line or ""))
    if fallback:
        name = fallback.group(1)
        return name, int(fallback.group(2) or 0), _backup_timestamp_from_name(name)
    return None


def list_target_backup_archives(target_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """List backup archives at a configured local/SMB/SSH/Google destination."""
    target = _target_by_id(target_id)
    kind = str(target.get("kind") or "local")
    destination = str(target.get("destination") or "").strip()
    rows: list[dict[str, Any]] = []

    if kind == "local":
        folder = _require_absolute_directory(destination, create=False)
        for path in folder.iterdir():
            if path.is_file() and _backup_name_allowed(path.name):
                stat = path.stat()
                rows.append(_remote_file_row(path.name, stat.st_size, stat.st_mtime))

    elif kind == "smb":
        if destination.startswith(("//", "smb://")):
            _, _, remote_dir = _parse_smb_destination(destination)
            commands = []
            if remote_dir:
                commands.append(f"cd {_smb_quote(remote_dir)}")
            commands.append("ls")
            output = _run_smbclient(
                destination,
                str(target.get("username") or ""),
                _decrypt_secret(str(target.get("password_enc") or "")),
                str(target.get("domain") or ""),
                commands,
                90,
            )
            # STREAMFORGE_SMB_BACKUP_MODIFIED_TIME_V94:
            # Parse both size and Samba's modified time. Previous releases
            # hardcoded modified_ts=0, which forced the UI to display an em dash.
            for line in output.splitlines():
                parsed = _parse_smbclient_backup_line(line)
                if not parsed:
                    continue
                name, size, modified = parsed
                rows.append(_remote_file_row(name, size, modified))
        else:
            folder = _require_absolute_directory(destination, create=False)
            for path in folder.iterdir():
                if path.is_file() and _backup_name_allowed(path.name):
                    stat = path.stat()
                    rows.append(_remote_file_row(path.name, stat.st_size, stat.st_mtime))

    elif kind == "ssh":
        client, sftp, remote_dir = _open_sftp(
            destination,
            str(target.get("key_path") or ""),
            str(target.get("username") or ""),
            _decrypt_secret(str(target.get("password_enc") or "")),
        )
        try:
            for attr in sftp.listdir_attr(remote_dir):
                if _backup_name_allowed(attr.filename):
                    rows.append(_remote_file_row(attr.filename, int(attr.st_size or 0), float(attr.st_mtime or 0)))
        finally:
            try:
                sftp.close()
            finally:
                client.close()

    elif kind == "google_drive":
        folder_id = _drive_ensure_folder(destination or "StreamForge-Backups")
        query = "trashed=false"
        if folder_id:
            query += f" and '{folder_id}' in parents"
        params = urllib.parse.urlencode({
            "q": query,
            "fields": "files(id,name,size,modifiedTime)",
            "pageSize": "200",
            "orderBy": "modifiedTime desc",
        })
        data = json.loads(_google_request("https://www.googleapis.com/drive/v3/files?" + params).decode("utf-8"))
        for item in data.get("files") or []:
            name = str(item.get("name") or "")
            if not _backup_name_allowed(name):
                continue
            modified = 0.0
            raw_modified = str(item.get("modifiedTime") or "")
            if raw_modified:
                try:
                    modified = datetime.fromisoformat(raw_modified.replace("Z", "+00:00")).timestamp()
                except Exception:
                    modified = 0.0
            rows.append(_remote_file_row(name, int(item.get("size") or 0), modified, str(item.get("id") or "")))
    else:
        raise ValueError("Unsupported backup destination type")

    rows.sort(key=lambda item: (float(item.get("modified_ts") or 0), str(item.get("name") or "")), reverse=True)
    return rows[:max(1, min(500, int(limit or 100)))]


def _google_drive_file_id(folder_path: str, archive_name: str) -> str:
    folder_id = _drive_ensure_folder(folder_path or "StreamForge-Backups")
    safe = archive_name.replace("'", "\\'")
    query = f"name='{safe}' and trashed=false"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    params = urllib.parse.urlencode({"q": query, "fields": "files(id,name)", "pageSize": "10"})
    data = json.loads(_google_request("https://www.googleapis.com/drive/v3/files?" + params).decode("utf-8"))
    files = data.get("files") or []
    if not files:
        raise ValueError("Backup file was not found on Google Drive")
    return str(files[0]["id"])


def fetch_target_backup_archive(target_id: str, archive_name: str, local_path: Path) -> Path:
    """Download/copy a configured destination backup into local_path."""
    if not _backup_name_allowed(archive_name):
        raise ValueError("Invalid backup archive name")
    target = _target_by_id(target_id)
    kind = str(target.get("kind") or "local")
    destination = str(target.get("destination") or "").strip()
    local_path.parent.mkdir(parents=True, exist_ok=True)

    if kind == "local":
        folder = _require_absolute_directory(destination, create=False)
        source = (folder / archive_name).resolve()
        if source.parent != folder.resolve() or not source.is_file():
            raise ValueError("Backup file was not found")
        shutil.copy2(source, local_path)

    elif kind == "smb":
        if destination.startswith(("//", "smb://")):
            _, _, remote_dir = _parse_smb_destination(destination)
            commands = []
            if remote_dir:
                commands.append(f"cd {_smb_quote(remote_dir)}")
            commands.append(f"get {_smb_quote(archive_name)} {_smb_quote(str(local_path))}")
            _run_smbclient(
                destination,
                str(target.get("username") or ""),
                _decrypt_secret(str(target.get("password_enc") or "")),
                str(target.get("domain") or ""),
                commands,
                1800,
            )
        else:
            folder = _require_absolute_directory(destination, create=False)
            source = (folder / archive_name).resolve()
            if source.parent != folder.resolve() or not source.is_file():
                raise ValueError("Backup file was not found")
            shutil.copy2(source, local_path)

    elif kind == "ssh":
        client, sftp, remote_dir = _open_sftp(
            destination,
            str(target.get("key_path") or ""),
            str(target.get("username") or ""),
            _decrypt_secret(str(target.get("password_enc") or "")),
        )
        try:
            remote = posixpath.join(remote_dir.rstrip("/") or "/", archive_name)
            sftp.get(remote, str(local_path))
        finally:
            try:
                sftp.close()
            finally:
                client.close()

    elif kind == "google_drive":
        file_id = _google_drive_file_id(destination, archive_name)
        raw = _google_request(f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}?alt=media")
        local_path.write_bytes(raw)

    else:
        raise ValueError("Unsupported backup destination type")

    if not local_path.is_file() or local_path.stat().st_size < 128:
        local_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded backup archive is empty or invalid")
    return local_path


def delete_target_backup_archive(target_id: str, archive_name: str) -> None:
    """Delete one backup archive from a configured destination."""
    if not _backup_name_allowed(archive_name):
        raise ValueError("Invalid backup archive name")
    target = _target_by_id(target_id)
    kind = str(target.get("kind") or "local")
    destination = str(target.get("destination") or "").strip()

    if kind == "local":
        folder = _require_absolute_directory(destination, create=False)
        path = (folder / archive_name).resolve()
        if path.parent != folder.resolve() or not path.is_file():
            raise ValueError("Backup file was not found")
        path.unlink()

    elif kind == "smb":
        if destination.startswith(("//", "smb://")):
            _, _, remote_dir = _parse_smb_destination(destination)
            commands = []
            if remote_dir:
                commands.append(f"cd {_smb_quote(remote_dir)}")
            commands.append(f"del {_smb_quote(archive_name)}")
            _run_smbclient(
                destination,
                str(target.get("username") or ""),
                _decrypt_secret(str(target.get("password_enc") or "")),
                str(target.get("domain") or ""),
                commands,
                120,
            )
        else:
            folder = _require_absolute_directory(destination, create=False)
            path = (folder / archive_name).resolve()
            if path.parent != folder.resolve() or not path.is_file():
                raise ValueError("Backup file was not found")
            path.unlink()

    elif kind == "ssh":
        client, sftp, remote_dir = _open_sftp(
            destination,
            str(target.get("key_path") or ""),
            str(target.get("username") or ""),
            _decrypt_secret(str(target.get("password_enc") or "")),
        )
        try:
            sftp.remove(posixpath.join(remote_dir.rstrip("/") or "/", archive_name))
        finally:
            try:
                sftp.close()
            finally:
                client.close()

    elif kind == "google_drive":
        file_id = _google_drive_file_id(destination, archive_name)
        _google_request(
            f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}",
            method="DELETE",
        )
    else:
        raise ValueError("Unsupported backup destination type")


def _prune_target_backup_archives(target_id: str, keep: int = 20) -> dict[str, Any]:
    """Apply independent rotation to one configured backup destination.

    The listing API already normalizes local folders, mounted/secure SMB,
    SSH/SFTP and Google Drive. Reuse that same path so rotation behaves
    consistently for every target instead of duplicating provider logic.
    """
    keep_count = max(1, min(500, int(keep or 20)))
    archives = list_target_backup_archives(target_id, limit=500)
    stale = archives[keep_count:]
    deleted: list[str] = []
    errors: list[str] = []

    for item in stale:
        name = str(item.get("name") or "")
        if not _backup_name_allowed(name):
            continue
        try:
            delete_target_backup_archive(target_id, name)
            deleted.append(name)
        except Exception as exc:
            # A completed backup must not be reported as failed merely because
            # one old remote archive could not be deleted. Preserve the new
            # archive and expose cleanup details to callers/logging instead.
            errors.append(f"{name}: {str(exc)[-240:]}")

    return {
        "keep": keep_count,
        "found": len(archives),
        "deleted": deleted,
        "errors": errors,
    }



def local_backup_settings() -> dict[str, Any]:
    settings = {
        "name": "Main Server local backups",
        "destination": str(ARCHIVE_ROOT),
        "schedule_hours": 24,
        "schedule_time": "00:00",
        "rotation_keep": 20,
        "last_run": 0,
        "last_status": "Never run",
        "last_archive": "",
    }
    try:
        raw = json.loads(LOCAL_BACKUP_CONFIG_FILE.read_text(encoding="utf-8"))
        name = str(raw.get("name") or "").strip()
        if name:
            settings["name"] = name[:80]
        settings["schedule_hours"] = max(0, min(8760, int(raw.get("schedule_hours") if raw.get("schedule_hours") is not None else 24)))
        settings["schedule_time"] = _normalize_schedule_time(raw.get("schedule_time") or "00:00")
        settings["rotation_keep"] = max(1, min(500, int(raw.get("rotation_keep") or 20)))
        settings["last_run"] = max(0, int(raw.get("last_run") or 0))
        settings["last_status"] = str(raw.get("last_status") or "Never run")[:320]
        settings["last_archive"] = str(raw.get("last_archive") or "")[:500]
    except Exception:
        pass
    return settings


def save_local_backup_settings(name: str, rotation_keep: int, schedule_hours: int = 24, schedule_time: str = "00:00") -> dict[str, Any]:
    current = local_backup_settings()
    clean_name = str(name or "").strip()[:80] or current["name"]
    keep = max(1, min(500, int(rotation_keep or 20)))
    schedule = max(0, min(8760, int(schedule_hours if schedule_hours is not None else 24)))
    payload = {
        "name": clean_name,
        "rotation_keep": keep,
        "schedule_hours": schedule,
        "schedule_time": _normalize_schedule_time(schedule_time),
        "last_run": int(current.get("last_run") or 0),
        "last_status": str(current.get("last_status") or "Never run"),
        "last_archive": str(current.get("last_archive") or ""),
    }
    LOCAL_BACKUP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = LOCAL_BACKUP_CONFIG_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(LOCAL_BACKUP_CONFIG_FILE)
    return local_backup_settings()


def _write_local_backup_settings(settings: dict[str, Any]) -> None:
    payload = {
        "name": str(settings.get("name") or "Main Server local backups")[:80],
        "rotation_keep": max(1, min(500, int(settings.get("rotation_keep") or 20))),
        "schedule_hours": max(0, min(8760, int(settings.get("schedule_hours") if settings.get("schedule_hours") is not None else 24))),
        "schedule_time": _normalize_schedule_time(settings.get("schedule_time") or "00:00"),
        "last_run": max(0, int(settings.get("last_run") or 0)),
        "last_status": str(settings.get("last_status") or "Never run")[:320],
        "last_archive": str(settings.get("last_archive") or "")[:500],
    }
    LOCAL_BACKUP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = LOCAL_BACKUP_CONFIG_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(LOCAL_BACKUP_CONFIG_FILE)


def run_local_backup() -> dict[str, Any]:
    settings = local_backup_settings()
    archive: Path | None = None
    try:
        archive = _archive()
        settings.update({
            "last_run": int(time.time()),
            "last_status": "Success",
            "last_archive": archive.name,
        })
        _write_local_backup_settings(settings)
        _prune_local_backup_archives(
            keep=max(1, min(500, int(settings.get("rotation_keep") or 20)))
        )
        Path(str(archive) + ".partial").unlink(missing_ok=True)
        return local_backup_settings()
    except Exception as exc:
        settings.update({
            "last_run": int(time.time()),
            "last_status": f"Failed: {str(exc)[-300:]}",
            "last_archive": "",
        })
        _write_local_backup_settings(settings)
        if archive is not None:
            archive.unlink(missing_ok=True)
            Path(str(archive) + ".partial").unlink(missing_ok=True)
        raise

def load_targets() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        targets = [item for item in raw.get("targets", []) if isinstance(item, dict)]
        for item in targets:
            try:
                item["rotation_keep"] = max(1, min(500, int(item.get("rotation_keep") or 20)))
            except (TypeError, ValueError):
                item["rotation_keep"] = 20
            try:
                item["schedule_time"] = _normalize_schedule_time(item.get("schedule_time") or "00:00")
            except ValueError:
                item["schedule_time"] = "00:00"
        return targets
    except (OSError, ValueError, TypeError):
        return []


def save_targets(targets: list[dict[str, Any]]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"targets": targets}, separators=(",", ":")), encoding="utf-8")
    temporary.replace(CONFIG_FILE)



def _require_absolute_directory(destination: str, *, create: bool) -> Path:
    folder = Path(str(destination or "").strip()).expanduser()
    if not folder.is_absolute():
        raise ValueError("Destination must be an absolute path")
    if create:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise RuntimeError(f"StreamForge cannot create destination folder: {folder}") from exc
    if not folder.exists():
        raise RuntimeError(f"Destination folder does not exist: {folder}")
    if not folder.is_dir():
        raise RuntimeError(f"Destination is not a directory: {folder}")
    return folder


def _write_probe(folder: Path) -> None:
    probe = folder / f".streamforge-backup-test-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"streamforge-backup-test\n")
        probe.unlink(missing_ok=True)
    except PermissionError as exc:
        raise RuntimeError(f"StreamForge does not have write permission to {folder}") from exc
    except OSError as exc:
        probe.unlink(missing_ok=True)
        if getattr(exc, "errno", None) == 28:
            raise RuntimeError(f"Destination is full: {folder}") from exc
        raise RuntimeError(f"Could not write to destination {folder}: {exc}") from exc


def _mounted_filesystem(path: Path) -> dict[str, str]:
    """Return findmnt information for the filesystem containing path."""
    findmnt = shutil.which("findmnt")
    if not findmnt:
        raise RuntimeError("findmnt is not installed; cannot verify SMB mount")
    result = subprocess.run(
        [findmnt, "-T", str(path), "-n", "-o", "FSTYPE,SOURCE,TARGET"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"No mounted filesystem was found for {path}")
    fields = result.stdout.strip().split(None, 2)
    return {
        "fstype": fields[0] if fields else "",
        "source": fields[1] if len(fields) > 1 else "",
        "target": fields[2] if len(fields) > 2 else "",
    }


def _parse_ssh_destination(destination: str) -> tuple[str, str, int, str]:
    """Parse user@host:/path or ssh://user@host[:port]/path."""
    value = str(destination or "").strip()
    if value.startswith("ssh://"):
        parsed = urllib.parse.urlparse(value)
        if not parsed.hostname or not parsed.username or not parsed.path:
            raise ValueError("SSH destination must look like ssh://user@host[:port]/absolute/path")
        return (
            urllib.parse.unquote(parsed.username),
            parsed.hostname,
            int(parsed.port or 22),
            urllib.parse.unquote(parsed.path),
        )

    match = re.fullmatch(r"([^@\s:]+)@(\[[^\]]+\]|[^:\s]+):(/.+)", value)
    if not match:
        raise ValueError("SSH destination must look like user@host:/absolute/path or ssh://user@host:port/path")
    user = match.group(1)
    host = match.group(2).strip("[]")
    remote_path = match.group(3)
    return user, host, 22, remote_path


def _ssh_key_for_target(key_path: str) -> str | None:
    value = str(key_path or "").strip()
    if not value:
        default = Path("/var/lib/streamforge/.ssh/id_ed25519")
        return str(default) if default.exists() else None
    path = Path(value).expanduser()
    if not path.is_file():
        raise RuntimeError(f"SSH private key does not exist: {path}")
    if not os.access(path, os.R_OK):
        raise RuntimeError(f"StreamForge cannot read SSH private key: {path}")
    return str(path)


def _open_sftp(destination: str, key_path: str, username_override: str = "", password: str = ""):
    user, host, port, remote_path = _parse_ssh_destination(destination)
    if str(username_override or "").strip():
        user = str(username_override).strip()
    key_file = _ssh_key_for_target(key_path)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=str(password or "") or None,
            key_filename=key_file,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            allow_agent=not bool(password) and not bool(key_file),
            look_for_keys=not bool(password) and not bool(key_file),
        )
        return client, client.open_sftp(), remote_path
    except Exception as exc:
        client.close()
        raise RuntimeError(f"SSH/SFTP connection failed: {exc}") from exc


def _sftp_mkdirs(sftp, remote_dir: str) -> None:
    clean = posixpath.normpath(remote_dir)
    if clean in {"", ".", "/"}:
        return
    parts = [part for part in clean.split("/") if part]
    current = "/" if clean.startswith("/") else ""
    for part in parts:
        current = posixpath.join(current, part) if current else part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _upload_sftp(
    archive: Path,
    destination: str,
    key_path: str,
    username_override: str = "",
    password: str = "",
) -> str:
    client, sftp, remote_dir = _open_sftp(destination, key_path, username_override, password)
    try:
        _sftp_mkdirs(sftp, remote_dir)
        final = posixpath.join(remote_dir.rstrip("/") or "/", archive.name)
        partial = final + ".partial"
        try:
            sftp.put(str(archive), partial)
            try:
                sftp.posix_rename(partial, final)
            except Exception:
                try:
                    sftp.remove(final)
                except Exception:
                    pass
                sftp.rename(partial, final)
        finally:
            try:
                sftp.remove(partial)
            except Exception:
                pass
        return final
    finally:
        try:
            sftp.close()
        finally:
            client.close()


def _parse_smb_destination(destination: str) -> tuple[str, str, str]:
    value = str(destination or "").strip().replace("\\", "/")
    if value.startswith("smb://"):
        parsed = urllib.parse.urlparse(value)
        host = parsed.hostname or ""
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if not host or not parts:
            raise ValueError("SMB destination must look like //server/share/folder or smb://server/share/folder")
        return host, parts[0], "/".join(parts[1:])
    if value.startswith("//"):
        parts = [p for p in value[2:].split("/") if p]
        if len(parts) < 2:
            raise ValueError("SMB destination must look like //server/share/folder")
        return parts[0], parts[1], "/".join(parts[2:])
    raise ValueError("Secure SMB destination must look like //server/share/folder or smb://server/share/folder")


def _smb_quote(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _smb_mkdir_commands(remote_dir: str) -> list[str]:
    commands = []
    current = ""
    for part in [p for p in str(remote_dir or "").replace("\\", "/").split("/") if p]:
        current = part if not current else current + "/" + part
        commands.append(f"mkdir {_smb_quote(current)}")
    return commands


def _run_smbclient(destination: str, username: str, password: str, domain: str, commands: list[str], timeout: int) -> str:
    binary = shutil.which("smbclient")
    if not binary:
        raise RuntimeError("smbclient is not installed on the Main Server")
    host, share, _ = _parse_smb_destination(destination)
    auth_file = _write_smb_auth_file(username, password, domain)
    try:
        result = subprocess.run(
            [binary, f"//{host}/{share}", "-A", str(auth_file), "-c", "; ".join(commands)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        auth_file.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError("SMB operation failed: " + (detail[-800:] or f"exit status {result.returncode}"))
    return (result.stdout or "").strip()


def _test_secure_smb(destination: str, username: str, password: str, domain: str = "") -> dict[str, Any]:
    if not str(username or "").strip():
        raise ValueError("SMB username is required")
    if not str(password or ""):
        raise ValueError("SMB password is required")
    _, _, remote_dir = _parse_smb_destination(destination)
    probe = f".streamforge-backup-test-{uuid.uuid4().hex[:8]}"
    commands = _smb_mkdir_commands(remote_dir)
    if remote_dir:
        commands.append(f"cd {_smb_quote(remote_dir)}")
    commands += [f"put /dev/null {_smb_quote(probe)}", f"del {_smb_quote(probe)}"]
    _run_smbclient(destination, username, password, domain, commands, 60)
    return {"message": "SMB share connected and writable"}


def _upload_secure_smb(archive: Path, destination: str, username: str, password: str, domain: str = "") -> str:
    host, share, remote_dir = _parse_smb_destination(destination)
    partial = archive.name + ".partial"
    commands = _smb_mkdir_commands(remote_dir)
    if remote_dir:
        commands.append(f"cd {_smb_quote(remote_dir)}")
    commands += [
        f"put {_smb_quote(str(archive))} {_smb_quote(partial)}",
        f"del {_smb_quote(archive.name)}",
        f"rename {_smb_quote(partial)} {_smb_quote(archive.name)}",
    ]
    _run_smbclient(destination, username, password, domain, commands, 1800)
    remote = "/".join(part for part in [remote_dir.strip("/"), archive.name] if part)
    return f"//{host}/{share}/" + remote


def _copy_to_folder(archive: Path, folder: Path) -> str:
    final = folder / archive.name
    partial = folder / (archive.name + ".partial")
    try:
        shutil.copy2(archive, partial)
        partial.replace(final)
        return str(final)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        if getattr(exc, "errno", None) == 28:
            raise RuntimeError(f"Backup destination is full: {folder}") from exc
        raise


def test_destination(kind: str, destination: str, key_path: str = "", username: str = "", password: str = "", domain: str = "") -> dict[str, Any]:
    kind = str(kind or "").strip()
    destination = str(destination or "").strip()

    if kind == "local":
        folder = _require_absolute_directory(destination, create=True)
        _write_probe(folder)
        return {"message": f"Local folder is writable: {folder}"}

    if kind == "smb":
        if str(destination).strip().startswith(("//", "smb://")):
            return _test_secure_smb(destination, username, password, domain)
        folder = _require_absolute_directory(destination, create=False)
        mount = _mounted_filesystem(folder)
        fstype = mount.get("fstype", "").lower()
        if fstype not in {"cifs", "smb3"}:
            raise RuntimeError(
                f"{folder} is mounted as '{mount.get('fstype') or 'unknown'}', not an SMB/CIFS filesystem"
            )
        _write_probe(folder)
        source = mount.get("source") or "SMB share"
        return {"message": f"SMB mount is writable: {source} → {folder}"}

    if kind == "ssh":
        client, sftp, remote_dir = _open_sftp(destination, key_path, username, password)
        try:
            _sftp_mkdirs(sftp, remote_dir)
            probe = posixpath.join(remote_dir.rstrip("/") or "/", f".streamforge-backup-test-{uuid.uuid4().hex[:8]}")
            try:
                with sftp.open(probe, "wb") as handle:
                    handle.write(b"streamforge-backup-test\n")
            finally:
                try:
                    sftp.remove(probe)
                except Exception:
                    pass
        finally:
            try:
                sftp.close()
            finally:
                client.close()
        return {"message": "SSH/SFTP destination connected and writable"}

    if kind == "google_drive":
        return test_google_drive_connection()

    raise ValueError("Unsupported backup target type")

def add_target(name: str, kind: str, destination: str, key_path: str = "", schedule_hours: int = 24, username: str = "", password: str = "", domain: str = "", rotation_keep: int = 20, schedule_time: str = "00:00") -> dict[str, Any]:
    kind = kind if kind in {"local", "smb", "ssh", "google_drive"} else "local"
    target = {
        "id": uuid.uuid4().hex[:12], "name": name.strip()[:80] or kind.title(), "kind": kind,
        "destination": destination.strip()[:2000], "key_path": key_path.strip()[:1000],
        "username": str(username or "").strip()[:300], "password_enc": _encrypt_secret(password), "domain": str(domain or "").strip()[:300],
        "schedule_hours": max(0, min(8760, int(schedule_hours or 0))),
        "schedule_time": _normalize_schedule_time(schedule_time),
        "rotation_keep": max(1, min(500, int(rotation_keep or 20))),
        "last_run": 0,
        "last_status": "Never run", "last_archive": "",
    }
    if not target["destination"]:
        raise ValueError("Backup destination is required")
    if kind == "local":
        _require_absolute_directory(target["destination"], create=False)
    elif kind == "smb":
        if target["destination"].startswith(("//", "smb://")):
            _parse_smb_destination(target["destination"])
            if not target["username"]:
                raise ValueError("SMB username is required for secure share")
            if not target["password_enc"]:
                raise ValueError("SMB password is required for secure share")
        else:
            _require_absolute_directory(target["destination"], create=False)
    elif kind == "ssh":
        _parse_ssh_destination(target["destination"])
        if target["key_path"]:
            _ssh_key_for_target(target["key_path"])
    elif kind == "google_drive":
        status = google_drive_status()
        if not status["oauth_configured"]:
            raise ValueError("Google OAuth credentials are not configured")
        if not status["connected"]:
            raise ValueError("Google Drive is not connected")
        target["destination"] = target["destination"].strip("/") or "StreamForge-Backups"

    targets = load_targets()
    targets.append(target)
    save_targets(targets)
    return target



def update_target(
    target_id: str,
    name: str,
    kind: str,
    destination: str,
    key_path: str = "",
    schedule_hours: int = 24,
    username: str = "",
    password: str = "",
    domain: str = "",
    rotation_keep: int = 20,
    schedule_time: str = "00:00",
) -> dict[str, Any]:
    targets = load_targets()
    target = next((item for item in targets if str(item.get("id")) == str(target_id)), None)
    if not target:
        raise ValueError("Backup destination was not found")

    # A configured destination keeps its original type when edited.
    # This prevents accidental type conversion (for example Google Drive -> Local).
    kind = str(target.get("kind") or "local")
    destination = str(destination or "").strip()[:2000]
    if not destination:
        raise ValueError("Backup destination is required")

    existing_password_enc = str(target.get("password_enc") or "")
    new_password_enc = _encrypt_secret(password) if str(password or "") else existing_password_enc
    new_username = str(username or "").strip()[:300]
    new_domain = str(domain or "").strip()[:300]
    new_key_path = str(key_path or "").strip()[:1000]

    if kind == "local":
        _require_absolute_directory(destination, create=False)
    elif kind == "smb":
        if destination.startswith(("//", "smb://")):
            _parse_smb_destination(destination)
            if not new_username:
                raise ValueError("SMB username is required for secure share")
            if not new_password_enc:
                raise ValueError("SMB password is required for secure share")
        else:
            _require_absolute_directory(destination, create=False)
            # Mounted SMB targets don't need stored network credentials.
            new_username = ""
            new_password_enc = ""
            new_domain = ""
    elif kind == "ssh":
        _parse_ssh_destination(destination)
        if new_key_path:
            _ssh_key_for_target(new_key_path)
    elif kind == "google_drive":
        status = google_drive_status()
        if not status["oauth_configured"]:
            raise ValueError("Google OAuth credentials are not configured")
        if not status["connected"]:
            raise ValueError("Google Drive is not connected")
        destination = destination.strip("/")
        if not destination:
            raise ValueError("Google Drive folder is required")
        new_username = ""
        new_password_enc = ""
        new_domain = ""
        new_key_path = ""

    target["name"] = str(name or "").strip()[:80] or kind.title()
    target["kind"] = kind
    target["destination"] = destination
    target["key_path"] = new_key_path
    target["username"] = new_username
    target["password_enc"] = new_password_enc
    target["domain"] = new_domain
    target["schedule_hours"] = max(0, min(8760, int(schedule_hours or 0)))
    target["schedule_time"] = _normalize_schedule_time(schedule_time)
    target["rotation_keep"] = max(1, min(500, int(rotation_keep or 20)))

    save_targets(targets)
    return target

def delete_target(target_id: str) -> bool:
    targets = load_targets()
    remaining = [item for item in targets if str(item.get("id")) != target_id]
    save_targets(remaining)
    return len(remaining) != len(targets)


# STREAMFORGE_BACKUP_COMPLETE_LOGO_ASSETS_V3017:
# Back up the effective configured logo roots under stable archive paths.  The
# old implementation archived only /opt/streamforge/logo, which silently lost
# assets whenever the environment pointed at a different destination path.
def _local_logo_references(database: Path, logo_root: Path, node_logo_root: Path) -> list[Path]:
    references: list[Path] = []
    if not database.is_file():
        return references
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "channels" in tables:
            for (value,) in connection.execute("SELECT logo_url FROM channels WHERE logo_url LIKE '/channel-logos/%'"):
                filename = Path(str(value or "")[len("/channel-logos/"):]).name
                if filename and filename == str(value or "")[len("/channel-logos/"):]:
                    references.append(logo_root / filename)
        if "nodes" in tables:
            for (value,) in connection.execute("SELECT logo_url FROM nodes WHERE logo_url LIKE '/node-logos/%'"):
                filename = Path(str(value or "")[len("/node-logos/"):]).name
                if filename and filename == str(value or "")[len("/node-logos/"):]:
                    references.append(node_logo_root / filename)
        if "app_settings" in tables:
            for key, value in connection.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('branding_logo','branding_favicon') OR key LIKE 'node_favicon_%'"
            ):
                raw = str(value or "")
                if raw.startswith("/branding-assets/"):
                    filename = Path(raw[len("/branding-assets/"):]).name
                    if filename and filename == raw[len("/branding-assets/"):]:
                        references.append(logo_root / "branding" / filename)
                elif str(key).startswith("node_favicon_") and raw.startswith("/node-logos/"):
                    filename = Path(raw[len("/node-logos/"):]).name
                    if filename and filename == raw[len("/node-logos/"):]:
                        references.append(node_logo_root / filename)
    finally:
        connection.close()
    return sorted(set(path.resolve() for path in references))


def _logo_asset_files(root: Path, archive_prefix: str) -> list[tuple[Path, str]]:
    resolved = root.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        return []
    assets: list[tuple[Path, str]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Logo backup source contains an unsupported symbolic link: {path}")
        if not path.is_file():
            continue
        # STREAMFORGE_LOGO_ASSET_FILETYPE_FILTER_V3023: the logo directory can
        # accidentally contain downloaded update ZIPs. Never treat those as
        # restorable presentation assets.
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}:
            continue
        relative = path.relative_to(resolved)
        assets.append((path, f"{archive_prefix}/{relative.as_posix()}"))
    return assets


def _configured_backup_database_path() -> Path:
    explicit = str(os.environ.get("STREAMFORGE_DATABASE_PATH") or "").strip()
    if explicit:
        return Path(explicit).resolve()
    database_url = str(os.environ.get("STREAMFORGE_DATABASE_URL") or "").strip()
    if database_url.startswith("sqlite:///"):
        raw = database_url[len("sqlite:///"):]
        return Path(raw if raw.startswith("/") else f"/opt/streamforge/{raw}").resolve()
    return Path("/var/lib/streamforge/streamforge.db")


def _archive() -> Path:
    """Create a service-safe Main Server backup archive."""
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    archive = ARCHIVE_ROOT / f"streamforge-main-{stamp}.tar.gz"
    partial = Path(str(archive) + ".partial")

    database_path = _configured_backup_database_path()
    logo_root = Path(os.environ.get("STREAMFORGE_LOGO_ROOT", "/opt/streamforge/logo")).resolve()
    node_logo_root = Path(os.environ.get("STREAMFORGE_NODE_LOGO_ROOT", str(logo_root))).resolve()

    readable_sources: list[tuple[Path, str]] = []
    for path, arcname in (
        (database_path, "var/lib/streamforge/streamforge.db"),
        (database_path.with_name(database_path.name + "-wal"), "var/lib/streamforge/streamforge.db-wal"),
        (database_path.with_name(database_path.name + "-shm"), "var/lib/streamforge/streamforge.db-shm"),
    ):
        if path.exists() and os.access(path, os.R_OK):
            readable_sources.append((path, arcname))

    if not readable_sources:
        raise RuntimeError("No readable Main Server backup sources were found")

    # /etc/streamforge.env is root-only by design. The running service already
    # has its effective STREAMFORGE_* values, so preserve those directly.
    env_lines = []
    for key in sorted(os.environ):
        if key.startswith("STREAMFORGE_"):
            value = str(os.environ.get(key, "")).replace("\n", "\\n")
            env_lines.append(f"{key}={value}")
    env_payload = ("\n".join(env_lines) + "\n").encode("utf-8")

    main_assets = _logo_asset_files(logo_root, "streamforge-assets/main-logo")
    if node_logo_root == logo_root:
        node_assets: list[tuple[Path, str]] = []
    else:
        node_assets = _logo_asset_files(node_logo_root, "streamforge-assets/node-logo")
    all_assets = main_assets + node_assets
    archived_source_paths = {path.resolve() for path, _arcname in all_assets}
    missing_references = [
        path for path in _local_logo_references(database_path, logo_root, node_logo_root)
        if path not in archived_source_paths or not path.is_file()
    ]

    # STREAMFORGE_BACKUP_STALE_LOGO_TOLERANCE_V3018: an old database row may
    # still point at a logo that was deleted long ago.  That stale reference
    # must not stop every backup.  The asset inventory below proves that every
    # file which actually existed at backup time was archived byte-for-byte.
    asset_inventory = {
        "schema": 1,
        "files": [
            {
                "archive_path": arcname,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path, arcname in all_assets
        ],
        "stale_database_references": [str(path) for path in missing_references],
    }
    asset_inventory_payload = (json.dumps(asset_inventory, separators=(",", ":")) + "\n").encode("utf-8")

    try:
        with tarfile.open(partial, mode="w:gz") as tar:
            for path, arcname in readable_sources:
                tar.add(path, arcname=arcname, recursive=True)
            for path, arcname in all_assets:
                tar.add(path, arcname=arcname, recursive=False)

            env_info = tarfile.TarInfo(name="etc/streamforge.env")
            env_info.size = len(env_payload)
            env_info.mode = 0o600
            env_info.mtime = int(time.time())
            tar.addfile(env_info, io.BytesIO(env_payload))

            inventory_info = tarfile.TarInfo(name="streamforge-assets/ASSET-MANIFEST.json")
            inventory_info.size = len(asset_inventory_payload)
            inventory_info.mode = 0o600
            inventory_info.mtime = int(time.time())
            tar.addfile(inventory_info, io.BytesIO(asset_inventory_payload))

            manifest = (
                "StreamForge Main backup\n"
                f"created_utc={stamp}\n"
                "database=var/lib/streamforge/streamforge.db\n"
                "environment=etc/streamforge.env (runtime STREAMFORGE_* snapshot)\n"
                "assets_complete=1\n"
                f"main_logo_files={len(main_assets)}\n"
                f"node_logo_files={len(node_assets) if node_logo_root != logo_root else len(main_assets)}\n"
                f"shared_logo_root={int(node_logo_root == logo_root)}\n"
                f"stale_logo_references={len(missing_references)}\n"
                "asset_inventory=streamforge-assets/ASSET-MANIFEST.json\n"
            ).encode("utf-8")
            manifest_info = tarfile.TarInfo(name="BACKUP-MANIFEST.txt")
            manifest_info.size = len(manifest)
            manifest_info.mode = 0o600
            manifest_info.mtime = int(time.time())
            tar.addfile(manifest_info, io.BytesIO(manifest))

        # Reopen the completed payload before publishing it. Every enumerated
        # asset must be present under its stable archive name.
        with tarfile.open(partial, mode="r:gz") as verification_tar:
            archived_names = {str(member.name).lstrip("./") for member in verification_tar.getmembers() if member.isfile()}
            missing_archive_entries = [arcname for _path, arcname in all_assets if arcname not in archived_names]
            if missing_archive_entries:
                raise RuntimeError("Backup archive logo verification failed: " + ", ".join(missing_archive_entries[:5]))
            for item in asset_inventory["files"]:
                member = verification_tar.getmember(str(item["archive_path"]))
                source = verification_tar.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Backup archive logo verification failed: {item['archive_path']}")
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
                if size != int(item["size"]) or digest.hexdigest() != str(item["sha256"]):
                    raise RuntimeError(f"Backup archive logo checksum verification failed: {item['archive_path']}")

        partial.replace(archive)
        return archive
    except OSError as exc:
        partial.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if getattr(exc, "errno", None) == 28:
            raise RuntimeError("Backup archive could not be created because the server disk is full") from exc
        raise RuntimeError(f"Backup archive creation failed: {exc}") from exc
    except Exception:
        partial.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        raise


def run_target(target_id: str) -> dict[str, Any]:
    targets = load_targets()
    target = next((item for item in targets if str(item.get("id")) == target_id), None)
    if not target:
        raise ValueError("Backup target not found")
    archive = _archive()
    destination = str(target.get("destination") or "").strip()
    kind = str(target.get("kind") or "local")
    try:
        if kind == "local":
            folder = _require_absolute_directory(destination, create=True)
            _write_probe(folder)
            delivered = _copy_to_folder(archive, folder)
        elif kind == "smb":
            if destination.startswith(("//", "smb://")):
                delivered = _upload_secure_smb(
                    archive,
                    destination,
                    str(target.get("username") or ""),
                    _decrypt_secret(str(target.get("password_enc") or "")),
                    str(target.get("domain") or ""),
                )
            else:
                folder = _require_absolute_directory(destination, create=False)
                mount = _mounted_filesystem(folder)
                if mount.get("fstype", "").lower() not in {"cifs", "smb3"}:
                    raise RuntimeError(
                        f"SMB destination is not currently mounted as CIFS/SMB: {folder}"
                    )
                _write_probe(folder)
                delivered = _copy_to_folder(archive, folder)
        elif kind == "ssh":
            delivered = _upload_sftp(
                archive,
                destination,
                str(target.get("key_path") or ""),
                str(target.get("username") or ""),
                _decrypt_secret(str(target.get("password_enc") or "")),
            )
        elif kind == "google_drive":
            result = upload_file_to_google_drive(archive, destination or "StreamForge-Backups")
            delivered = str(result.get("webViewLink") or result.get("name") or archive.name)
        else:
            raise ValueError(f"Unsupported backup target type: {kind}")
        target.update({"last_run": int(time.time()), "last_status": "Success", "last_archive": delivered})
    except Exception as exc:
        target.update({"last_run": int(time.time()), "last_status": f"Failed: {str(exc)[-300:]}", "last_archive": ""})
        save_targets(targets)
        archive.unlink(missing_ok=True)
        Path(str(archive) + ".partial").unlink(missing_ok=True)
        raise
    save_targets(targets)
    keep = max(1, min(500, int(target.get("rotation_keep") or 20)))
    rotation = _prune_target_backup_archives(target_id, keep=keep)
    if rotation.get("errors"):
        target["last_status"] = "Success · rotation warning: " + "; ".join(rotation["errors"][:3])
        save_targets(targets)

    # Main Server local retained backups have their own independent rotation.
    local_keep = int(local_backup_settings().get("rotation_keep") or 20)
    _prune_local_backup_archives(keep=local_keep)
    Path(str(archive) + ".partial").unlink(missing_ok=True)
    return target


def _backup_schedule_due(settings: dict[str, Any], now: int) -> bool:
    """Check an interval schedule anchored to each destination's local time."""
    hours = max(0, int(settings.get("schedule_hours") or 0))
    if hours <= 0:
        return False
    schedule_time = _normalize_schedule_time(settings.get("schedule_time") or "00:00")
    hour, minute = (int(part) for part in schedule_time.split(":"))
    current = datetime.fromtimestamp(now)
    last_run = max(0, int(settings.get("last_run") or 0))

    # A destination that has never run waits for its configured clock minute;
    # creating a midnight schedule in the afternoon must not run immediately.
    if last_run <= 0:
        return current.hour == hour and current.minute == minute

    anchor = datetime(2000, 1, 1, hour, minute)
    interval_seconds = hours * 3600
    elapsed_seconds = int((current - anchor).total_seconds())
    latest_local_slot = anchor + timedelta(seconds=(elapsed_seconds // interval_seconds) * interval_seconds)
    latest_slot = int(time.mktime(latest_local_slot.timetuple()))
    return latest_slot <= now and last_run < latest_slot


def run_due_targets() -> None:
    now = int(time.time())

    local = local_backup_settings()
    if _backup_schedule_due(local, now):
        try:
            run_local_backup()
        except Exception:
            pass

    for target in load_targets():
        if _backup_schedule_due(target, now):
            try:
                run_target(str(target.get("id") or ""))
            except Exception:
                pass
