#!/usr/bin/env python3
"""Execute tightly-scoped privileged StreamForge Main actions as root."""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

RUNTIME_DIR = Path("/var/lib/streamforge/main-system-runtime")
REQUEST_FILE = RUNTIME_DIR / "request.json"
RESULT_FILE = RUNTIME_DIR / "result.json"

DATA_DIR = Path("/var/lib/streamforge")
RESTORE_INBOX = DATA_DIR / "restore-inbox"
APP_DIR = Path("/opt/streamforge")
ENV_FILE = Path("/etc/streamforge.env")
BACKUP_DIR = Path("/var/backups/streamforge")
RESTORE_LOGO_SYNC_MARKER = DATA_DIR / "restore-logo-sync.pending"

ALLOWED_ACTIONS = {"restart_service", "reboot_server", "restore_backup"}
ALLOWED_RESTORE_FILES = {
    "BACKUP-MANIFEST.txt",
    "streamforge-assets/ASSET-MANIFEST.json",
    "var/lib/streamforge/streamforge.db",
    "var/lib/streamforge/streamforge.db-wal",
    "var/lib/streamforge/streamforge.db-shm",
    "etc/streamforge.env",
}
REQUIRED_RESTORE_FILES = {
    "BACKUP-MANIFEST.txt",
    "var/lib/streamforge/streamforge.db",
}

# STREAMFORGE_RESTORE_PRESERVE_CURRENT_ACCESS_V30:
PRESERVED_ENV_KEYS = (
    "STREAMFORGE_PUBLIC_BASE_URL",
    "STREAMFORGE_RELAY_BASE_URL",
    "STREAMFORGE_LOGO_ROOT",
    "STREAMFORGE_NODE_LOGO_ROOT",
)

PRESERVED_LOCAL_NODE_FIELDS = (
    "api_url",
    "api_urls",
    "playlist_url",
    "playlist_urls",
    "access_slug",
    "playlist_access_slug",
    "dns_name",
    "dns_scheme",
    "dns_only",
    "playlist_dns_only",
    "agent_port",
    "playlist_port",
)


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(0o640)
    temporary.replace(path)


def _safe_member_name(member: tarfile.TarInfo) -> str:
    name = str(member.name or "").lstrip("./")
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise RuntimeError("Restore archive contains an unsafe path")
    allowed_logo = name == "opt/streamforge/logo" or name.startswith("opt/streamforge/logo/")
    # STREAMFORGE_RESTORE_ASSET_MEMBER_POLICY_V3019: keep this policy explicit
    # because the root-installed helper, not the web process, extracts restore
    # archives. Every regular file below either managed logo tree is accepted;
    # inventory/checksum validation runs after safe staging.
    asset_directories = {
        "streamforge-assets/main-logo",
        "streamforge-assets/node-logo",
    }
    asset_prefixes = tuple(directory + "/" for directory in asset_directories)
    allowed_assets = name in asset_directories or name.startswith(asset_prefixes)
    if name not in ALLOWED_RESTORE_FILES and not allowed_logo and not allowed_assets:
        raise RuntimeError(f"Unexpected file in restore archive: {name}")
    if member.issym() or member.islnk() or member.isdev():
        raise RuntimeError("Restore archive contains unsupported links or device files")
    return name


def _validate_restore_path(value: object) -> Path:
    raw = Path(str(value or "")).resolve()
    restore_root = RESTORE_INBOX.resolve()
    if raw.parent != restore_root or not raw.is_file():
        raise RuntimeError("Rejected restore archive path")
    if raw.suffixes[-2:] != [".tar", ".gz"]:
        raise RuntimeError("Restore archive must be a .tar.gz file")
    return raw


def _copy_member(tar: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    if member.isdir():
        destination.mkdir(parents=True, exist_ok=True)
        return
    if not member.isfile():
        return
    source = tar.extractfile(member)
    if source is None:
        raise RuntimeError(f"Could not read restore member: {member.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(source, handle, length=1024 * 1024)


def _stage_restore(archive: Path, staging: Path) -> set[str]:
    found: set[str] = set()
    with tarfile.open(archive, mode="r:gz") as tar:
        for member in tar.getmembers():
            name = _safe_member_name(member)
            destination = staging / name
            _copy_member(tar, member, destination)
            if member.isfile():
                found.add(name)
    missing = sorted(REQUIRED_RESTORE_FILES - found)
    if missing:
        raise RuntimeError("Restore archive is missing required Main Server files")
    return found


def _safety_backup(request_id: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = BACKUP_DIR / f"pre-restore-{stamp}-{request_id[-6:]}"
    target.mkdir(parents=True, exist_ok=False)

    for name in ("streamforge.db", "streamforge.db-wal", "streamforge.db-shm"):
        source = DATA_DIR / name
        if source.exists():
            shutil.copy2(source, target / name)

    if ENV_FILE.exists():
        shutil.copy2(ENV_FILE, target / "streamforge.env")

    main_logo = Path(_env_value(ENV_FILE, "STREAMFORGE_LOGO_ROOT") or str(APP_DIR / "logo")).resolve()
    node_logo = Path(_env_value(ENV_FILE, "STREAMFORGE_NODE_LOGO_ROOT") or str(main_logo)).resolve()
    for source, name in ((main_logo, "main-logo"), (node_logo, "node-logo")):
        if source == main_logo and name == "node-logo":
            continue
        if source.exists():
            if source.is_dir():
                shutil.copytree(source, target / name, symlinks=False)
            else:
                shutil.copy2(source, target / name)

    return target


def _install_file(source: Path, destination: Path, mode: int, uid: int, gid: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030: the privileged restore
    # service deliberately exposes only /etc/streamforge.env as writable.
    # Creating a sibling temporary file under /etc therefore fails with
    # EROFS even though the destination file itself is writable.  The restore
    # already has a verified pre-restore environment backup, so install this
    # one sandboxed file in place and fsync it before continuing. Database
    # files keep the atomic sibling-temp replacement below.
    if destination == ENV_FILE:
        with source.open("rb") as input_handle, destination.open("wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.chmod(destination, mode)
        os.chown(destination, uid, gid)
        return
    temporary = destination.with_name(f".{destination.name}.restore.tmp")
    shutil.copy2(source, temporary)
    os.chmod(temporary, mode)
    os.chown(temporary, uid, gid)
    temporary.replace(destination)



def _read_env_snapshot(path: Path) -> dict[str, str | None]:
    parsed: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            parsed[key.strip()] = value
    return {key: parsed.get(key) for key in PRESERVED_ENV_KEYS}


def _merge_preserved_env(path: Path, snapshot: dict[str, str | None]) -> None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    wanted = set(PRESERVED_ENV_KEYS)
    kept: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                continue
        kept.append(raw)
    for key in PRESERVED_ENV_KEYS:
        value = snapshot.get(key)
        if value is not None:
            kept.append(f"{key}={value}")
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    os.chown(path, 0, 0)


# STREAMFORGE_CANONICAL_LOGO_STORAGE_V3022: Main-owned branding, channel and
# Node presentation assets have one deterministic storage location. Copy any
# files left in a previously configured path, then make runtime and restore
# agree on /opt/streamforge/logo.
def _set_env_values(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    pending = dict(values)
    output: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                output.append(f"{key}={pending.pop(key)}")
                continue
        output.append(raw)
    output.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    os.chown(path, 0, 0)


def _canonicalize_logo_storage(uid: int, gid: int) -> int:
    canonical = (APP_DIR / "logo").resolve()
    configured = {
        Path(_env_value(ENV_FILE, "STREAMFORGE_LOGO_ROOT") or str(canonical)).resolve(),
        Path(_env_value(ENV_FILE, "STREAMFORGE_NODE_LOGO_ROOT") or str(canonical)).resolve(),
    }
    canonical.mkdir(parents=True, exist_ok=True)
    copied = 0
    forbidden_sources = {Path("/"), Path("/opt"), Path("/var"), APP_DIR.resolve(), DATA_DIR.resolve()}
    for source in sorted(configured, key=str):
        if source == canonical or source in forbidden_sources or not source.is_dir() or source.is_symlink():
            continue
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(source)
            destination = canonical / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
    _set_env_values(ENV_FILE, {
        "STREAMFORGE_LOGO_ROOT": str(canonical),
        "STREAMFORGE_NODE_LOGO_ROOT": str(canonical),
    })
    for path in [canonical, *canonical.rglob("*")]:
        if path.is_symlink():
            continue
        os.chown(path, uid, gid)
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    return copied


# STREAMFORGE_RESTORE_COMPLETE_LOGO_ASSETS_V3017:
def _env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    prefix = key + "="
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _staged_logo_sources(staging: Path) -> tuple[Path | None, Path | None]:
    main_source = staging / "streamforge-assets/main-logo"
    node_source = staging / "streamforge-assets/node-logo"
    legacy_source = staging / "opt/streamforge/logo"
    if not main_source.is_dir():
        main_source = legacy_source if legacy_source.is_dir() else None
    if not node_source.is_dir():
        node_source = main_source
    return main_source, node_source


def _asset_manifest_is_complete(staging: Path) -> bool:
    manifest = staging / "BACKUP-MANIFEST.txt"
    if not manifest.is_file():
        return False
    return any(
        raw.strip() == "assets_complete=1"
        for raw in manifest.read_text(encoding="utf-8", errors="ignore").splitlines()
    )


def _referenced_asset_paths(db_path: Path) -> list[tuple[str, Path]]:
    references: list[tuple[str, Path]] = []
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "channels" in tables:
            for (value,) in connection.execute("SELECT logo_url FROM channels WHERE logo_url LIKE '/channel-logos/%'"):
                raw = str(value or "")
                filename = Path(raw[len("/channel-logos/"):]).name
                if filename and filename == raw[len("/channel-logos/"):]:
                    references.append(("main", Path(filename)))
        if "nodes" in tables:
            for (value,) in connection.execute("SELECT logo_url FROM nodes WHERE logo_url LIKE '/node-logos/%'"):
                raw = str(value or "")
                filename = Path(raw[len("/node-logos/"):]).name
                if filename and filename == raw[len("/node-logos/"):]:
                    references.append(("node", Path(filename)))
        if "app_settings" in tables:
            for key, value in connection.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('branding_logo','branding_favicon') OR key LIKE 'node_favicon_%'"
            ):
                raw = str(value or "")
                if raw.startswith("/branding-assets/"):
                    filename = Path(raw[len("/branding-assets/"):]).name
                    if filename and filename == raw[len("/branding-assets/"):]:
                        references.append(("main", Path("branding") / filename))
                elif str(key).startswith("node_favicon_") and raw.startswith("/node-logos/"):
                    filename = Path(raw[len("/node-logos/"):]).name
                    if filename and filename == raw[len("/node-logos/"):]:
                        references.append(("node", Path(filename)))
    finally:
        connection.close()
    return sorted(set(references), key=lambda item: (item[0], item[1].as_posix()))


def _validate_complete_staged_assets(staging: Path) -> None:
    if not _asset_manifest_is_complete(staging):
        return
    # STREAMFORGE_RESTORE_ASSET_INVENTORY_V3018: v3.0.18+ backups validate
    # the exact files that existed when the archive was created. Database
    # references to files already absent at backup time are intentionally not
    # treated as archive corruption.
    inventory_path = staging / "streamforge-assets/ASSET-MANIFEST.json"
    if inventory_path.is_file():
        try:
            payload = json.loads(inventory_path.read_text(encoding="utf-8"))
            items = payload["files"]
            if int(payload.get("schema", 0)) != 1 or not isinstance(items, list):
                raise ValueError("unsupported schema")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Restore logo asset inventory is invalid: {exc}") from exc

        expected: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError("Restore logo asset inventory contains an invalid entry")
            name = str(item.get("archive_path") or "")
            relative = Path(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not (name.startswith("streamforge-assets/main-logo/") or name.startswith("streamforge-assets/node-logo/"))
                or name in expected
            ):
                raise RuntimeError(f"Restore logo asset inventory contains an unsafe path: {name}")
            expected.add(name)
            source = staging / relative
            try:
                wanted_size = int(item.get("size"))
                wanted_hash = str(item.get("sha256") or "")
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Restore logo asset inventory entry is invalid: {name}") from exc
            if not source.is_file() or source.is_symlink():
                raise RuntimeError(f"Restore archive is missing inventoried logo asset: {name}")
            if source.stat().st_size != wanted_size or not re.fullmatch(r"[0-9a-f]{64}", wanted_hash) or _sha256(source) != wanted_hash:
                raise RuntimeError(f"Restore archive logo checksum verification failed: {name}")

        actual = {
            path.relative_to(staging).as_posix()
            for root in (staging / "streamforge-assets/main-logo", staging / "streamforge-assets/node-logo")
            if root.is_dir()
            for path in root.rglob("*")
            if path.is_file()
        }
        unexpected = sorted(actual - expected)
        if unexpected:
            raise RuntimeError("Restore archive contains uninventoried logo assets: " + ", ".join(unexpected[:8]))
        return

    # Compatibility for v3.0.17 backups, which used database references as
    # their completeness proof and did not yet carry a hash inventory.
    main_source, node_source = _staged_logo_sources(staging)
    missing: list[str] = []
    for role, relative in _referenced_asset_paths(staging / "var/lib/streamforge/streamforge.db"):
        source = main_source if role == "main" else node_source
        if source is None or not (source / relative).is_file():
            missing.append(f"{role}:{relative.as_posix()}")
    if missing:
        raise RuntimeError("Restore archive is missing referenced logo assets: " + ", ".join(missing[:8]))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_logo_destination(value: str, fallback: Path) -> Path:
    destination = Path(value or str(fallback)).resolve()
    forbidden = {Path("/"), Path("/opt"), Path("/var"), APP_DIR.resolve(), DATA_DIR.resolve()}
    if destination in forbidden or len(destination.parts) < 3:
        raise RuntimeError(f"Unsafe restored logo destination: {destination}")
    return destination


def _replace_logo_tree(sources: list[Path], destination: Path, uid: int, gid: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=str(destination.parent)))
    expected: dict[Path, str] = {}
    previous = destination.with_name(f".{destination.name}.pre-logo-restore-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        for source in sources:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError(f"Restore logo assets contain a symbolic link: {path}")
                if not path.is_file():
                    continue
                if path.suffix.lower() not in _RESTORABLE_IMAGE_SUFFIXES:
                    continue
                relative = path.relative_to(source)
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                expected[relative] = _sha256(path)
        for relative, digest in expected.items():
            target = temporary / relative
            if not target.is_file() or _sha256(target) != digest:
                raise RuntimeError(f"Restored logo checksum verification failed: {relative}")
        for path in [temporary, *temporary.rglob("*")]:
            os.chown(path, uid, gid)
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        if destination.exists():
            os.replace(destination, previous)
        os.replace(temporary, destination)
        if previous.exists():
            shutil.rmtree(previous) if previous.is_dir() else previous.unlink()
        return len(expected)
    except Exception:
        if not destination.exists() and previous.exists():
            os.replace(previous, destination)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _restore_logo_assets(staging: Path, uid: int, gid: int) -> int:
    main_source, node_source = _staged_logo_sources(staging)
    if main_source is None and node_source is None:
        return 0
    main_destination = _safe_logo_destination(str(APP_DIR / "logo"), APP_DIR / "logo")
    node_destination = main_destination
    groups: dict[Path, list[Path]] = {}
    if main_source is not None:
        groups.setdefault(main_destination, []).append(main_source)
    if node_source is not None:
        grouped = groups.setdefault(node_destination, [])
        if node_source not in grouped:
            grouped.append(node_source)
    restored = 0
    for destination, sources in groups.items():
        restored += _replace_logo_tree(sources, destination, uid, gid)
    return restored


# STREAMFORGE_SHARED_VERIFIED_LOGO_RESTORE_V3026:
# Full backup restore and `streamforge restorelogos` must execute one exact
# verified pipeline. The post-copy digest check prevents a full restore from
# reporting success while the canonical logo directory is empty/incomplete.
def _restore_verified_logo_payload(staging: Path, db_path: Path, uid: int, gid: int) -> tuple[int, int]:
    _validate_complete_staged_assets(staging)
    main_source, node_source = _staged_logo_sources(staging)
    sources: list[Path] = []
    for source in (main_source, node_source):
        if source is not None and source not in sources:
            sources.append(source)

    expected: dict[Path, str] = {}
    for source in sources:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in _RESTORABLE_IMAGE_SUFFIXES:
                continue
            relative = path.relative_to(source)
            digest = _sha256(path)
            previous = expected.get(relative)
            if previous is not None and previous != digest:
                raise RuntimeError(f"Conflicting Main/Node logo payload: {relative}")
            expected[relative] = digest

    # STREAMFORGE_EMPTY_LOGO_PAYLOAD_PRESERVE_V3029: an archive carrying an
    # empty asset directory must not replace a populated destination with an
    # empty tree. Empty is treated as "no logo payload" rather than deletion.
    if not expected:
        return 0, _reconcile_restored_logo_references(db_path)

    restored = _restore_logo_assets(staging, uid, gid)
    canonical = _safe_logo_destination(str(APP_DIR / "logo"), APP_DIR / "logo")
    for relative, digest in expected.items():
        installed = canonical / relative
        if not installed.is_file() or installed.is_symlink() or _sha256(installed) != digest:
            raise RuntimeError(f"Full restore canonical logo verification failed: {relative}")
    if expected and restored != len(expected):
        raise RuntimeError(f"Full restore logo count mismatch: expected {len(expected)}, restored {restored}")
    rebased = _reconcile_restored_logo_references(db_path)
    return restored, rebased


# STREAMFORGE_RESTORE_LOGO_REFERENCE_REBASE_V3021: imported/edited channels
# can still hold a remote or obsolete URL even when a slug-named uploaded logo
# is present in the backup. After restoring bytes, prefer that exact archived
# channel asset so the restored server is self-contained.
_RESTORABLE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}


def _existing_local_asset(value: object, prefix: str, root: Path) -> bool:
    raw = str(value or "").strip()
    if not raw.startswith(prefix):
        return False
    filename = raw[len(prefix):]
    return bool(filename and filename == Path(filename).name and (root / filename).is_file())


def _image_files(root: Path, *, nested: bool = False) -> list[Path]:
    if not root.is_dir():
        return []
    paths = root.rglob("*") if nested else root.iterdir()
    return sorted(
        path for path in paths
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in _RESTORABLE_IMAGE_SUFFIXES
    )


def _reconcile_restored_logo_references(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    main_root = _safe_logo_destination(str(APP_DIR / "logo"), APP_DIR / "logo")
    node_root = main_root
    main_images = _image_files(main_root)
    by_slug: dict[str, list[Path]] = {}
    for path in main_images:
        by_slug.setdefault(path.stem, []).append(path)

    connection = sqlite3.connect(str(db_path), timeout=20)
    changed = 0
    try:
        connection.execute("PRAGMA busy_timeout=20000")
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "channels" in tables:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(channels)")}
            if {"id", "slug", "logo_url"} <= columns:
                for channel_id, slug, logo_url in connection.execute("SELECT id, slug, logo_url FROM channels"):
                    if _existing_local_asset(logo_url, "/channel-logos/", main_root):
                        continue
                    candidates = by_slug.get(str(slug or "").strip(), [])
                    if candidates:
                        new_url = "/channel-logos/" + candidates[0].name
                        connection.execute("UPDATE channels SET logo_url = ? WHERE id = ?", (new_url, int(channel_id)))
                        changed += 1

        if "app_settings" in tables:
            settings_rows = {
                str(key): str(value or "")
                for key, value in connection.execute("SELECT key, value FROM app_settings")
            }
            branding_root = main_root / "branding"
            branding_images = _image_files(branding_root)
            for key, want_favicon in (("branding_logo", False), ("branding_favicon", True)):
                if _existing_local_asset(settings_rows.get(key), "/branding-assets/", branding_root):
                    continue
                candidates = [path for path in branding_images if path.name.startswith("favicon-") == want_favicon]
                if candidates and key in settings_rows:
                    connection.execute(
                        "UPDATE app_settings SET value = ? WHERE key = ?",
                        ("/branding-assets/" + candidates[0].name, key),
                    )
                    changed += 1

            node_images = _image_files(node_root)
            for key, value in settings_rows.items():
                match = re.fullmatch(r"node_favicon_(\d+)", key)
                if not match or _existing_local_asset(value, "/node-logos/", node_root):
                    continue
                prefix = f"node-{int(match.group(1))}-favicon-"
                candidates = [path for path in node_images if path.name.startswith(prefix)]
                if candidates:
                    connection.execute(
                        "UPDATE app_settings SET value = ? WHERE key = ?",
                        ("/node-logos/" + candidates[0].name, key),
                    )
                    changed += 1

        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        return changed
    finally:
        connection.close()


def _snapshot_local_access(db_path: Path) -> dict[str, object] | None:
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        fields = [field for field in PRESERVED_LOCAL_NODE_FIELDS if field in columns]
        if not fields or "node_type" not in columns:
            return None
        row = conn.execute(
            "SELECT " + ", ".join(["id", *fields]) + " FROM nodes WHERE node_type = 'local' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {field: row[field] for field in fields}
    finally:
        conn.close()


def _validated_recovery_url(value: object) -> str:
    """Validate the authenticated browser URL carried by a restore request."""
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid Main restore recovery URL: {exc}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise RuntimeError("Invalid Main restore recovery URL")
    slug = parsed.path.strip("/")
    if "/" in slug or (slug and not re.fullmatch(r"[A-Za-z0-9_-]+", slug)):
        raise RuntimeError("Main restore recovery URL has an invalid access path")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise RuntimeError("Main restore recovery URL has an invalid hostname") from exc
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise RuntimeError("Main restore recovery URL has an invalid hostname")
    authority_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    authority = authority_host if port == default_port else f"{authority_host}:{port}"
    path = f"/{slug}" if slug else ""
    return urlunsplit((parsed.scheme, authority, path, "", "")).rstrip("/")


def _access_snapshot_with_recovery_url(
    snapshot: dict[str, object] | None,
    recovery_url: str,
) -> dict[str, object]:
    """Preserve the complete destination access snapshot across restore."""
    # STREAMFORGE_RESTORE_REMOVE_SOURCE_DOMAIN_V3012 compatibility marker:
    # archived/source aliases are removed by restoring the destination
    # snapshot itself; destination aliases must never be rebased or removed.
    # STREAMFORGE_RESTORE_PRESERVE_SEPARATE_ACCESS_PATHS_V3026 compatibility.
    # STREAMFORGE_RESTORE_PRESERVE_ALL_ACCESS_URLS_V3039:
    # The snapshot comes from the destination database before archived files
    # are installed.  It is therefore the authoritative complete Panel/API and
    # Playlist/App configuration.  Rebase must not collapse alternate ports or
    # overwrite alternate paths.  If a usable snapshot exists, return it byte
    # for byte.  The browser-proven recovery URL is only a fallback for a new or
    # incomplete destination with no saved access URLs.
    result = dict(snapshot or {})
    if any(
        str(result.get(field) or "").strip()
        for field in ("api_url", "api_urls", "playlist_url", "playlist_urls")
    ):
        return result

    parsed = urlsplit(recovery_url)
    default_port = 443 if parsed.scheme == "https" else 80
    authority_host = f"[{parsed.hostname}]" if parsed.hostname and ":" in parsed.hostname else str(parsed.hostname or "")
    authority = authority_host if int(parsed.port or default_port) == default_port else f"{authority_host}:{int(parsed.port)}"
    fallback_url = urlunsplit((parsed.scheme, authority, parsed.path, "", "")).rstrip("/")
    result.update({
        "api_url": fallback_url,
        "api_urls": fallback_url,
        "playlist_url": fallback_url,
        "playlist_urls": fallback_url,
        "access_slug": parsed.path.strip("/") or None,
        "playlist_access_slug": parsed.path.strip("/") or None,
        "dns_name": parsed.hostname,
        "dns_scheme": parsed.scheme,
        "agent_port": int(parsed.port or (443 if parsed.scheme == "https" else 80)),
        "playlist_port": int(parsed.port or (443 if parsed.scheme == "https" else 80)),
        # STREAMFORGE_RESTORE_EXACT_ROLE_POLICY_V3031: the destination paths
        # and browser-proven authority are already preserved above, so normal
        # restore can enforce their exact Panel/Playlist roles immediately.
        # _open_recovery_access remains the explicit verified emergency path.
        "dns_only": 1,
        "playlist_dns_only": 1,
    })
    return result


def _probe_recovery_access(recovery_url: str) -> tuple[bool, str]:
    """Probe the restored public route through local Nginx, not port 8800."""
    parsed = urlsplit(recovery_url)
    host = str(parsed.hostname or "")
    public_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    # Managed HTTPS URLs terminate TLS upstream (for example at Cloudflare)
    # and reach the generated origin listener on port 80.
    origin_port = 80 if parsed.scheme == "https" else public_port
    authority_host = f"[{host}]" if ":" in host else host
    authority = authority_host if origin_port == 80 else f"{authority_host}:{origin_port}"
    probe_path = (parsed.path.rstrip("/") if parsed.path else "") + "/login"
    probe_url = urlunsplit(("http", authority, probe_path, "", ""))
    command = [
        "/usr/bin/curl", "-k", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
        "--max-time", "12", "--resolve", f"{host}:{origin_port}:127.0.0.1", probe_url,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    status = str(result.stdout or "").strip()
    ok = result.returncode == 0 and status.isdigit() and int(status) not in {0, 421}
    detail = f"curl={result.returncode}, HTTP={status or 'none'}"
    return ok, detail


def _open_recovery_access(db_path: Path, recovery_url: str) -> None:
    """Fail open on the proven URL if strict post-restore routing is unusable."""
    # STREAMFORGE_RESTORE_VERIFIED_FAIL_OPEN_V3011:
    # STREAMFORGE_RESTORE_FAIL_OPEN_KEEP_ACCESS_URLS_V3039:
    # Fail-open changes only enforcement flags.  The destination's complete
    # Panel/API and Playlist/App lists must remain available for the operator
    # to diagnose or repair; never replace them with one recovery URL.
    conn = sqlite3.connect(str(db_path), timeout=20)
    try:
        conn.execute("PRAGMA busy_timeout=20000")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        target = conn.execute("SELECT id FROM nodes WHERE node_type = 'local' ORDER BY id LIMIT 1").fetchone()
        if target is None:
            raise RuntimeError("Restored database has no Main/Local Node for recovery")
        values: dict[str, object] = {
            "dns_only": 0,
            "playlist_dns_only": 0,
        }
        fields = [field for field in values if field in columns]
        if not fields:
            raise RuntimeError("Restored database has no compatible Main access fields")
        conn.execute(
            "UPDATE nodes SET " + ", ".join(f"{field} = ?" for field in fields) + " WHERE id = ?",
            [values[field] for field in fields] + [int(target[0])],
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()


def _restore_local_access(db_path: Path, snapshot: dict[str, object] | None, fallback_url: str = "") -> str:
    """Preserve destination access or fail open when no destination exists.

    A restore must never silently inherit a backup server's strict hostname,
    port or path policy. Persist the destination snapshot into the restored DB
    and force its WAL into the main database before services reopen it.
    """
    # STREAMFORGE_RESTORE_ACCESS_LOCKOUT_GUARD_V309:
    if not db_path.is_file():
        raise RuntimeError("Restored Main database is missing")
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=20000")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        target = conn.execute("SELECT id FROM nodes WHERE node_type = 'local' ORDER BY id LIMIT 1").fetchone()
        if target is None:
            raise RuntimeError("Restored database has no Main/Local Node to preserve current access settings")

        fields = [
            field for field in PRESERVED_LOCAL_NODE_FIELDS
            if snapshot and field in columns and field in snapshot
        ]
        if fields:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            values = [snapshot[field] for field in fields] + [int(target["id"])]
            conn.execute(f"UPDATE nodes SET {assignments} WHERE id = ?", values)
            mode = "preserved"
        else:
            # A brand-new/partial destination may not have a snapshot. Use
            # this destination's root-owned public URL (or loopback fallback)
            # and enforce that exact destination instead of inheriting the
            # backup server's hostname or port.
            safe_fallback = str(fallback_url or "").strip().rstrip("/")
            try:
                parsed_fallback = urlsplit(safe_fallback)
            except ValueError:
                parsed_fallback = urlsplit("")
            if parsed_fallback.scheme not in {"http", "https"} or not parsed_fallback.hostname:
                safe_fallback = "http://127.0.0.1"
                parsed_fallback = urlsplit(safe_fallback)
            fallback_values: dict[str, object] = {
                "api_url": safe_fallback,
                "api_urls": safe_fallback,
                "playlist_url": safe_fallback,
                "playlist_urls": safe_fallback,
                "access_slug": parsed_fallback.path.strip("/") or None,
                "playlist_access_slug": parsed_fallback.path.strip("/") or None,
                "dns_name": parsed_fallback.hostname,
                "dns_scheme": parsed_fallback.scheme,
                "dns_only": 1,
                "playlist_dns_only": 1,
                "agent_port": int(parsed_fallback.port or (443 if parsed_fallback.scheme == "https" else 80)),
                "playlist_port": int(parsed_fallback.port or (443 if parsed_fallback.scheme == "https" else 80)),
            }
            fallback_fields = [field for field in PRESERVED_LOCAL_NODE_FIELDS if field in columns and field in fallback_values]
            assignments = ", ".join(f"{field} = ?" for field in fallback_fields)
            values = [fallback_values[field] for field in fallback_fields] + [int(target["id"])]
            conn.execute(f"UPDATE nodes SET {assignments} WHERE id = ?", values)
            mode = "opened"
        conn.commit()

        # STREAMFORGE_RESTORE_ACCESS_WAL_CHECKPOINT_V309: make the access
        # repair durable in streamforge.db instead of leaving it dependent on
        # a restored/stale WAL-SHM pair.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

        if mode == "preserved":
            verified = conn.execute(
                "SELECT " + ", ".join(fields) + " FROM nodes WHERE id = ?",
                (int(target["id"]),),
            ).fetchone()
            mismatched = [field for field in fields if verified[field] != snapshot[field]]
            if mismatched:
                raise RuntimeError("Could not preserve current Main access fields: " + ", ".join(mismatched))
        else:
            strict_fields = [field for field in ("dns_only", "playlist_dns_only") if field in fallback_fields]
            verified = conn.execute(
                "SELECT " + ", ".join(strict_fields) + " FROM nodes WHERE id = ?",
                (int(target["id"]),),
            ).fetchone() if strict_fields else None
            if verified is not None and any(int(verified[field] or 0) != 0 for field in strict_fields):
                raise RuntimeError("Could not disable restored strict Main access policy")
        return mode
    finally:
        conn.close()


def restore_backup(archive: Path, request_id: str, recovery_url: str) -> str:
    streamforge = shutil.which("systemctl")
    if not streamforge:
        raise RuntimeError("systemctl is unavailable")

    user_info = subprocess.run(
        ["/usr/bin/id", "-u", "streamforge"],
        check=True,
        capture_output=True,
        text=True,
    )
    group_info = subprocess.run(
        ["/usr/bin/id", "-g", "streamforge"],
        check=True,
        capture_output=True,
        text=True,
    )
    uid = int(user_info.stdout.strip())
    gid = int(group_info.stdout.strip())

    staging_root = Path(tempfile.mkdtemp(prefix="streamforge-restore-", dir=str(DATA_DIR)))
    service_stopped = False
    safety = None
    restored_logo_files = 0
    rebased_logo_references = 0
    try:
        found = _stage_restore(archive, staging_root)
        _validate_complete_staged_assets(staging_root)
        safety = _safety_backup(request_id)

        # STREAMFORGE_RESTORE_DESTINATION_ACCESS_SNAPSHOT_V30:
        current_env_access = _read_env_snapshot(ENV_FILE)
        current_local_access = _snapshot_local_access(DATA_DIR / "streamforge.db")
        # STREAMFORGE_RESTORE_ACTIVE_URL_AUTHORITY_V3010: a DB snapshot can be
        # stale or already restrictive. The authenticated browser URL that
        # submitted this restore is the authoritative recovery alias.
        active_recovery_url = _validated_recovery_url(recovery_url)
        effective_local_access = _access_snapshot_with_recovery_url(
            current_local_access,
            active_recovery_url,
        )
        (safety / "main-access-snapshot.json").write_text(
            json.dumps({
                "env": current_env_access,
                "local_node": current_local_access,
                "active_recovery_url": active_recovery_url,
                "effective_local_node": effective_local_access,
            }, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        subprocess.run(["/usr/bin/systemctl", "stop", "streamforge.service"], check=True, timeout=90)
        service_stopped = True

        # Restore SQLite database and matching WAL/SHM files as one snapshot.
        for name in ("streamforge.db", "streamforge.db-wal", "streamforge.db-shm"):
            live = DATA_DIR / name
            staged = staging_root / "var/lib/streamforge" / name
            if staged.exists():
                _install_file(staged, live, 0o640, uid, gid)
            elif name != "streamforge.db":
                live.unlink(missing_ok=True)

        # Restore the runtime environment snapshot when it is present.
        staged_env = staging_root / "etc/streamforge.env"
        if staged_env.exists():
            _install_file(staged_env, ENV_FILE, 0o600, 0, 0)
        # STREAMFORGE_RESTORE_ENV_ACCESS_PRESERVE_V30:
        # STREAMFORGE_RESTORE_ENV_REMOVE_SOURCE_DOMAIN_V3012: restored or
        # previously contaminated env values must not reintroduce the backup
        # server domain at startup.
        preserved_environment = dict(current_env_access)
        preserved_environment.update({
            "STREAMFORGE_PUBLIC_BASE_URL": active_recovery_url,
            "STREAMFORGE_RELAY_BASE_URL": active_recovery_url,
        })
        _merge_preserved_env(ENV_FILE, preserved_environment)
        _canonicalize_logo_storage(uid, gid)

        # STREAMFORGE_RESTORE_DB_ACCESS_PRESERVE_V30:
        access_restore_mode = _restore_local_access(
            DATA_DIR / "streamforge.db",
            effective_local_access,
            active_recovery_url,
        )

        # STREAMFORGE_RESTORE_ACCESS_NGINX_REAPPLY_V309: the restored database
        # and the public listener must agree before the Main service returns.
        subprocess.run(
            ["/usr/bin/systemctl", "start", "streamforge-main-access.service"],
            check=True,
            timeout=90,
        )

        # Restore all Main branding/favicon, channel and Node logo assets to
        # the destination server's configured roots and verify every byte.
        restored_logo_files, rebased_logo_references = _restore_verified_logo_payload(
            staging_root, DATA_DIR / "streamforge.db", uid, gid
        )
        if restored_logo_files:
            RESTORE_LOGO_SYNC_MARKER.write_text(
                json.dumps({
                    "request_id": request_id,
                    "restored_logo_files": restored_logo_files,
                    "rebased_logo_references": rebased_logo_references,
                    "created_at": time.time(),
                }, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.chmod(RESTORE_LOGO_SYNC_MARKER, 0o640)
            os.chown(RESTORE_LOGO_SYNC_MARKER, uid, gid)

        subprocess.run(["/usr/bin/systemctl", "start", "streamforge.service"], check=True, timeout=90)
        service_stopped = False

        # A restored DB may need the current code's additive migrations.
        time.sleep(3)
        health = subprocess.run(
            ["/usr/bin/curl", "-fsS", "--max-time", "8", "http://127.0.0.1:8800/health"],
            check=False,
            capture_output=True,
            text=True,
        )
        if health.returncode != 0:
            raise RuntimeError("Restore completed but Main health check failed after restart")

        # STREAMFORGE_RESTORE_PUBLIC_ROUTE_VERIFY_V3011: port 8800 health can
        # pass while Nginx or Main host/path enforcement still rejects the URL
        # used by the administrator. Verify that exact route through Nginx and
        # automatically disable strict enforcement if it is not reachable.
        route_ok, route_detail = _probe_recovery_access(active_recovery_url)
        if not route_ok:
            subprocess.run(["/usr/bin/systemctl", "stop", "streamforge.service"], check=True, timeout=90)
            service_stopped = True
            _open_recovery_access(DATA_DIR / "streamforge.db", active_recovery_url)
            subprocess.run(
                ["/usr/bin/systemctl", "start", "streamforge-main-access.service"],
                check=True,
                timeout=90,
            )
            subprocess.run(["/usr/bin/systemctl", "start", "streamforge.service"], check=True, timeout=90)
            service_stopped = False
            time.sleep(3)
            route_ok, retry_detail = _probe_recovery_access(active_recovery_url)
            if not route_ok:
                raise RuntimeError(
                    "Restore recovery URL is still unreachable after automatic fail-open "
                    f"({route_detail}; retry {retry_detail})"
                )
            access_restore_mode = "opened"

        archive.unlink(missing_ok=True)
        access_message = (
            "Current Main domain/access settings preserved and verified"
            if access_restore_mode == "preserved"
            else "Strict URL enforcement was automatically disabled because the recovery route required fail-open protection"
        )
        return (
            f"Main backup restored successfully. {access_message}. "
            f"Logo assets restored and verified: {restored_logo_files}. "
            f"Logo references rebased to archived files: {rebased_logo_references}. "
            f"Recovery URL: {active_recovery_url}. Safety backup: {safety}"
        )
    except Exception:
        if service_stopped:
            subprocess.run(["/usr/bin/systemctl", "start", "streamforge.service"], check=False, timeout=90)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def main() -> int:
    try:
        payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except Exception as exc:
        atomic_json(RESULT_FILE, {"ok": False, "error": f"Invalid request: {exc}"})
        REQUEST_FILE.unlink(missing_ok=True)
        return 1

    request_id = str(payload.get("request_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not request_id or action not in ALLOWED_ACTIONS:
        atomic_json(
            RESULT_FILE,
            {"ok": False, "request_id": request_id, "error": "Rejected Main Server action"},
        )
        REQUEST_FILE.unlink(missing_ok=True)
        return 1

    restore_path = ""
    recovery_url = ""
    if action == "restore_backup":
        try:
            restore_path = str(_validate_restore_path(payload.get("restore_path")))
            recovery_url = _validated_recovery_url(payload.get("recovery_url"))
        except Exception as exc:
            atomic_json(
                RESULT_FILE,
                {"ok": False, "request_id": request_id, "action": action, "error": str(exc)},
            )
            REQUEST_FILE.unlink(missing_ok=True)
            return 1

    REQUEST_FILE.unlink(missing_ok=True)

    # Publish scheduled first so the web request can redirect before the helper
    # stops/restarts StreamForge.
    atomic_json(
        RESULT_FILE,
        {"ok": True, "request_id": request_id, "action": action, "status": "scheduled"},
    )

    if action == "restore_backup":
        time.sleep(3)
        try:
            message = restore_backup(Path(restore_path), request_id, recovery_url)
            atomic_json(
                RESULT_FILE,
                {"ok": True, "request_id": request_id, "action": action, "status": "completed", "message": message},
            )
            return 0
        except Exception as exc:
            atomic_json(
                RESULT_FILE,
                {"ok": False, "request_id": request_id, "action": action, "status": "failed", "error": str(exc)[-1200:]},
            )
            return 1

    time.sleep(4 if action == "restart_service" else 6)
    if action == "restart_service":
        subprocess.run(["/usr/bin/systemctl", "restart", "streamforge.service"], check=True)
    else:
        subprocess.run(["/usr/bin/systemctl", "reboot"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
