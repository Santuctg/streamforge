#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

REQUIRED_ENV_KEYS = {
    "STREAMFORGE_SECRET_KEY",
    "STREAMFORGE_ADMIN_USER",
    "STREAMFORGE_ADMIN_PASSWORD",
    "STREAMFORGE_DATABASE_URL",
    "STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR",
    "STREAMFORGE_HLS_ROOT",
    "STREAMFORGE_LOGO_ROOT",
    "STREAMFORGE_NODE_LOGO_ROOT",
    "STREAMFORGE_FFMPEG_BIN",
    "STREAMFORGE_NVENC_FFMPEG_BIN",
    "STREAMFORGE_FFPROBE_BIN",
    "STREAMFORGE_FFPROBE_TIMEOUT",
    "STREAMFORGE_FFPROBE_ANALYZEDURATION",
    "STREAMFORGE_FFPROBE_PROBESIZE",
    "STREAMFORGE_PUBLIC_BASE_URL",
    "STREAMFORGE_RELAY_BASE_URL",
    "STREAMFORGE_RELAY_START_WAIT_SECONDS",
    "STREAMFORGE_CPU_ENCODE_THREADS",
    "STREAMFORGE_PROGRESS_INTERVAL",
    "STREAMFORGE_VAAPI_DEVICE",
    "STREAMFORGE_HTTP_USER_AGENT",
    "STREAMFORGE_HTTP_RW_TIMEOUT_US",
    "STREAMFORGE_HTTP_RECONNECT_DELAY_MAX",
    "STREAMFORGE_AUTO_RESTART_STALL_SECONDS",
    "STREAMFORGE_AUTO_RESTART_CHECK_INTERVAL",
    "STREAMFORGE_FAILBACK_PROBE_TIMEOUT",
    "STREAMFORGE_LOG_PAGE_LIMIT",
    "STREAMFORGE_ASN_DB_PATH",
    "STREAMFORGE_COUNTRY_DB_PATH",
    "STREAMFORGE_GEOIP_SETTINGS_FILE",
    "STREAMFORGE_VIEWER_KEY_TTL_SECONDS",
    "STREAMFORGE_RESTREAM_KEY_TTL_SECONDS",
    "STREAMFORGE_TIMEZONE",
    "STREAMFORGE_GEOIP_AUTO_UPDATE",
    "STREAMFORGE_MAXMIND_ACCOUNT_ID",
    "STREAMFORGE_MAXMIND_LICENSE_KEY",
}

def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", default="/opt/streamforge")
    parser.add_argument("--data-dir", default="/var/lib/streamforge")
    parser.add_argument("--env-file", default="/etc/streamforge.env")
    parser.add_argument("--nginx-site", default="/etc/nginx/sites-available/streamforge")
    args = parser.parse_args()

    app_dir = Path(args.app_dir)
    data_dir = Path(args.data_dir)
    env_file = Path(args.env_file)
    nginx_site = Path(args.nginx_site)
    errors: list[str] = []

    env = parse_env(env_file)
    missing_env = sorted(REQUIRED_ENV_KEYS - set(env))
    if missing_env:
        errors.append("missing env keys: " + ", ".join(missing_env))

    db_url = env.get("STREAMFORGE_DATABASE_URL", f"sqlite:///{data_dir / 'streamforge.db'}")
    if db_url.startswith("sqlite:///"):
        raw_path = db_url[len("sqlite:///"):]
        db_path = Path(raw_path if raw_path.startswith("/") else str(app_dir / raw_path))
    else:
        db_path = data_dir / "streamforge.db"
        errors.append("schema verifier currently expects SQLite STREAMFORGE_DATABASE_URL")

    sys.path.insert(0, str(app_dir))
    os.environ["STREAMFORGE_DATABASE_URL"] = f"sqlite:///{db_path}"

    try:
        from app.db import Base
        import app.models  # noqa: F401
    except Exception as exc:
        Base = None
        errors.append(f"cannot import current SQLAlchemy schema: {exc}")

    if Base is not None:
        if not db_path.exists():
            errors.append(f"database missing: {db_path}")
        else:
            connection = sqlite3.connect(str(db_path))
            try:
                actual_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                expected_tables = {table.name for table in Base.metadata.sorted_tables}
                missing_tables = sorted(expected_tables - actual_tables)
                if missing_tables:
                    errors.append("missing DB tables: " + ", ".join(missing_tables))

                for table in Base.metadata.sorted_tables:
                    if table.name not in actual_tables:
                        continue
                    actual_columns = {
                        row[1]
                        for row in connection.execute(
                            f'PRAGMA table_info("{table.name}")'
                        ).fetchall()
                    }
                    expected_columns = {column.name for column in table.columns}
                    missing_columns = sorted(expected_columns - actual_columns)
                    if missing_columns:
                        errors.append(
                            f"missing DB columns in {table.name}: " + ", ".join(missing_columns)
                        )
            finally:
                connection.close()

    for path in (
        app_dir / "app/main.py",
        app_dir / "app/main_channel_supervisor.py",
        app_dir / "deploy/streamforge.service",
        app_dir / "deploy/streamforge-channel-supervisor.service",
        app_dir / "deploy/nginx.conf",
        app_dir / "scripts/apply_main_access.py",
        app_dir / "scripts/main_system_control.py",
        data_dir / "gunicorn-tmp",
        data_dir / "main-access-runtime",
        data_dir / "main-system-runtime",
        data_dir / "restore-inbox",
        data_dir / ".ssh/known_hosts",
    ):
        if not path.exists():
            errors.append(f"required path missing: {path}")

    if not nginx_site.exists():
        errors.append(f"Nginx site missing: {nginx_site}")
    else:
        nginx_text = nginx_site.read_text(encoding="utf-8", errors="ignore")
        for marker in (
            "client_max_body_size 1g;",
            "proxy_request_buffering off;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "STREAMFORGE_NGINX_RELAY_FASTPATH_V60",
            'X-StreamForge-Relay "nginx-direct"',
        ):
            if marker not in nginx_text:
                errors.append(f"Nginx marker missing: {marker}")

        has_direct_main_proxy = "proxy_pass http://127.0.0.1:8800;" in nginx_text
        has_keepalive_main_proxy = (
            "upstream streamforge_main_backend" in nginx_text
            and "server 127.0.0.1:8800;" in nginx_text
            and "keepalive 512;" in nginx_text
            and "proxy_pass http://streamforge_main_backend;" in nginx_text
        )
        if not (has_direct_main_proxy or has_keepalive_main_proxy):
            errors.append("Nginx Main backend proxy configuration missing")

    for unit in (
        Path("/etc/systemd/system/streamforge.service"),
        Path("/etc/systemd/system/streamforge-channel-supervisor.service"),
        Path("/etc/systemd/system/streamforge-main-access.service"),
        Path("/etc/systemd/system/streamforge-main-access.path"),
        Path("/etc/systemd/system/streamforge-main-system.service"),
        Path("/etc/systemd/system/streamforge-main-system.path"),
    ):
        if not unit.exists():
            errors.append(f"systemd unit missing: {unit}")

    if errors:
        print("StreamForge Main install verification FAILED", file=sys.stderr)
        for item in errors:
            print(f" - {item}", file=sys.stderr)
        return 1

    print("StreamForge Main install verification OK")
    print(f" - env keys: {len(REQUIRED_ENV_KEYS)} required keys present")
    if Base is not None:
        print(f" - database: {len(Base.metadata.tables)} tables match current model columns")
    print(" - runtime directories/helpers: present")
    print(" - Nginx proxy/upload configuration: present")
    print(" - systemd Main/supervisor/access/system units: present")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
