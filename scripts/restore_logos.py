#!/usr/bin/env python3
"""Restore only verified StreamForge logo assets; never replace access/data."""
from __future__ import annotations

import argparse
import os
import pwd
import runpy
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path


# STREAMFORGE_LOGO_ONLY_RESTORE_V3023:
def sqlite_backup(source: Path, destination: Path) -> None:
    live = sqlite3.connect(str(source), timeout=30)
    saved = sqlite3.connect(str(destination))
    try:
        live.backup(saved)
    finally:
        saved.close()
        live.close()
    os.chmod(destination, 0o640)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore only verified logo assets from a Main backup")
    parser.add_argument("archive")
    parser.add_argument("--database", default="/var/lib/streamforge/streamforge.db")
    parser.add_argument("--env-file", default="/etc/streamforge.env")
    parser.add_argument("--app-dir", default="/opt/streamforge")
    parser.add_argument("--data-dir", default="/var/lib/streamforge")
    parser.add_argument("--safety-root", default="/var/backups/streamforge")
    args = parser.parse_args()

    archive = Path(args.archive).resolve()
    database = Path(args.database).resolve()
    env_file = Path(args.env_file).resolve()
    app_dir = Path(args.app_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    helper_path = app_dir / "scripts/main_system_control.py"
    if not archive.is_file() or archive.suffixes[-2:] not in ([".tar", ".gz"], [".tgz"]):
        raise SystemExit("ERROR: Logo-only restore requires an existing .tar.gz/.tgz Main backup")
    if not database.is_file():
        raise SystemExit(f"ERROR: Main database not found: {database}")
    if not helper_path.is_file():
        raise SystemExit(f"ERROR: Restore helper not found: {helper_path}")

    identity = pwd.getpwnam("streamforge")
    namespace = runpy.run_path(str(helper_path), run_name="streamforge_logo_only_restore")
    stage_archive = namespace["_stage_restore"]
    canonicalize = namespace["_canonicalize_logo_storage"]
    restore_verified = namespace["_restore_verified_logo_payload"]
    globals_dict = restore_verified.__globals__
    globals_dict["ENV_FILE"] = env_file
    globals_dict["APP_DIR"] = app_dir
    globals_dict["DATA_DIR"] = data_dir

    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="streamforge-logo-only-", dir=str(data_dir)))
    service_stopped = False
    try:
        stage_archive(archive, staging)
        main_source, node_source = namespace["_staged_logo_sources"](staging)
        if main_source is None and node_source is None:
            raise RuntimeError("Backup archive contains no restorable logo assets")

        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        safety = Path(args.safety_root).resolve() / f"pre-logo-only-restore-{stamp}"
        safety.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
        safety.mkdir(parents=True, mode=0o750, exist_ok=False)
        sqlite_backup(database, safety / "streamforge.db")
        if env_file.is_file():
            shutil.copy2(env_file, safety / "streamforge.env")
        canonical = app_dir / "logo"
        if canonical.is_dir():
            shutil.copytree(canonical, safety / "logo", symlinks=False)

        subprocess.run(["systemctl", "stop", "streamforge.service"], check=True, timeout=90)
        service_stopped = True
        migrated = canonicalize(identity.pw_uid, identity.pw_gid)
        restored, rebased = restore_verified(staging, database, identity.pw_uid, identity.pw_gid)
        subprocess.run(["systemctl", "start", "streamforge.service"], check=True, timeout=90)
        service_stopped = False

        ready = False
        for _attempt in range(45):
            probe = subprocess.run(
                ["curl", "-fsS", "--max-time", "2", "http://127.0.0.1:8800/health"],
                check=False, capture_output=True, text=True,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("Logo files were restored but Main health check failed")

        print(f"Logo-only restore complete: {restored} verified file(s)")
        print(f"Logo references rebased: {rebased}")
        print(f"Files migrated from an older logo root: {migrated}")
        print(f"Canonical logo path: {canonical}")
        print(f"Safety backup: {safety}")
        print("Main access/domain settings were not changed")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if service_stopped:
            subprocess.run(["systemctl", "start", "streamforge.service"], check=False, timeout=90)
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
