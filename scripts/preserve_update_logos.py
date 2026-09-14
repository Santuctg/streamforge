#!/usr/bin/env python3
"""Preserve and verify canonical logo bytes across application updates."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


# STREAMFORGE_UPDATE_LOGO_SAFETY_SNAPSHOT_V3029:
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
MANIFEST_NAME = "UPDATE-LOGO-MANIFEST.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def images(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def snapshot(current: Path, backup_root: Path, destination: Path) -> tuple[Path | None, int]:
    candidates = [current]
    historical = sorted(
        (path for path in backup_root.glob("pre-v*/app/logo") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    candidates.extend(path for path in historical if path.resolve() != current.resolve())
    selected = next((path for path in candidates if images(path)), None)

    destination.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, object]] = []
    if selected is not None:
        for source in images(selected):
            relative = source.relative_to(selected)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append({
                "path": relative.as_posix(),
                "size": source.stat().st_size,
                "sha256": digest(source),
            })
    payload = {
        "schema": 1,
        "source": str(selected) if selected is not None else "",
        "files": entries,
    }
    (destination / MANIFEST_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return selected, len(entries)


def load_manifest(snapshot_root: Path) -> list[dict[str, object]]:
    manifest = snapshot_root / MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("files")
    if int(payload.get("schema", 0)) != 1 or not isinstance(entries, list):
        raise ValueError("Invalid update logo safety manifest")
    return entries


def restore(snapshot_root: Path, destination: Path) -> int:
    entries = load_manifest(snapshot_root)
    destination.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        relative = Path(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsafe update logo safety entry: {relative}")
        source = snapshot_root / relative
        expected_size = int(entry.get("size"))
        expected_hash = str(entry.get("sha256") or "")
        if not source.is_file() or source.is_symlink() or source.stat().st_size != expected_size or digest(source) != expected_hash:
            raise ValueError(f"Update logo safety snapshot verification failed: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.update-logo.tmp")
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != expected_size or digest(temporary) != expected_hash:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"Update logo copy verification failed: {relative}")
        temporary.replace(target)
    return verify(snapshot_root, destination)


def verify(snapshot_root: Path, destination: Path) -> int:
    entries = load_manifest(snapshot_root)
    for entry in entries:
        relative = Path(str(entry.get("path") or ""))
        target = destination / relative
        if (
            not target.is_file()
            or target.is_symlink()
            or target.stat().st_size != int(entry.get("size"))
            or digest(target) != str(entry.get("sha256") or "")
        ):
            raise ValueError(f"Live update logo verification failed: {relative}")
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("snapshot")
    save.add_argument("current")
    save.add_argument("backup_root")
    save.add_argument("destination")
    apply = commands.add_parser("restore")
    apply.add_argument("snapshot")
    apply.add_argument("destination")
    check = commands.add_parser("verify")
    check.add_argument("snapshot")
    check.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            source, count = snapshot(Path(args.current), Path(args.backup_root), Path(args.destination))
            print(f"Logo safety source: {source or 'none'}")
            print(f"Logo safety files: {count}")
        elif args.command == "restore":
            print(f"Logo safety files restored: {restore(Path(args.snapshot), Path(args.destination))}")
        else:
            print(f"Logo safety files verified: {verify(Path(args.snapshot), Path(args.destination))}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
