from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    secret_key: str = os.getenv("STREAMFORGE_SECRET_KEY", "dev-change-me-now")
    admin_user: str = os.getenv("STREAMFORGE_ADMIN_USER", "admin")
    admin_password: str = os.getenv("STREAMFORGE_ADMIN_PASSWORD", "ChangeMe123!")
    database_url: str = os.getenv(
        "STREAMFORGE_DATABASE_URL", "sqlite:////var/lib/streamforge/streamforge.db"
    )
    main_access_runtime_dir: Path = Path(
        os.getenv("STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR", "/var/lib/streamforge/main-access-runtime")
    )
    hls_root: Path = Path(os.getenv("STREAMFORGE_HLS_ROOT", "/var/lib/streamforge/hls"))
    logo_root: Path = Path(os.getenv("STREAMFORGE_LOGO_ROOT", "/opt/streamforge/logo"))
    node_logo_root: Path = Path(os.getenv("STREAMFORGE_NODE_LOGO_ROOT", "/opt/streamforge/logo"))
    webplayer_download_root: Path = Path(
        os.getenv("STREAMFORGE_WEBPLAYER_DOWNLOAD_ROOT", "/var/lib/streamforge/webplayer-downloads")
    )
    ffmpeg_bin: str = os.getenv("STREAMFORGE_FFMPEG_BIN", "/usr/bin/ffmpeg")
    # STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117:
    # Legacy NVIDIA cards can require an FFmpeg build linked against an older
    # nv-codec-headers API than Ubuntu's system FFmpeg. Only explicit NVENC
    # profiles use this binary; copy/CPU/other hardware profiles stay untouched.
    nvenc_ffmpeg_bin: str = os.getenv("STREAMFORGE_NVENC_FFMPEG_BIN", "/opt/ffmpeg-nvenc470/bin/ffmpeg")
    ffprobe_bin: str = os.getenv("STREAMFORGE_FFPROBE_BIN", "/usr/bin/ffprobe")
    ffprobe_timeout: int = max(3, int(os.getenv("STREAMFORGE_FFPROBE_TIMEOUT", "10")))
    ffprobe_analyzeduration: int = max(1000000, int(os.getenv("STREAMFORGE_FFPROBE_ANALYZEDURATION", "8000000")))
    ffprobe_probesize: int = max(1000000, int(os.getenv("STREAMFORGE_FFPROBE_PROBESIZE", "20000000")))
    public_base_url: str = os.getenv("STREAMFORGE_PUBLIC_BASE_URL", "http://127.0.0.1")
    relay_base_url: str = os.getenv(
        "STREAMFORGE_RELAY_BASE_URL", os.getenv("STREAMFORGE_PUBLIC_BASE_URL", "http://127.0.0.1")
    )
    relay_start_wait_seconds: int = max(5, int(os.getenv("STREAMFORGE_RELAY_START_WAIT_SECONDS", "20")))
    cpu_encode_threads: int = max(1, int(os.getenv("STREAMFORGE_CPU_ENCODE_THREADS", "2")))
    progress_interval: int = max(1, int(os.getenv("STREAMFORGE_PROGRESS_INTERVAL", "3")))
    vaapi_device: str = os.getenv("STREAMFORGE_VAAPI_DEVICE", "/dev/dri/renderD128")
    http_user_agent: str = os.getenv(
        "STREAMFORGE_HTTP_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 StreamForge/3.4",
    )
    http_rw_timeout_us: int = max(1000000, int(os.getenv("STREAMFORGE_HTTP_RW_TIMEOUT_US", "15000000")))
    http_reconnect_delay_max: int = max(1, int(os.getenv("STREAMFORGE_HTTP_RECONNECT_DELAY_MAX", "10")))
    auto_restart_stall_seconds: int = max(
        15, int(os.getenv("STREAMFORGE_AUTO_RESTART_STALL_SECONDS", "30"))
    )
    auto_restart_check_interval: int = max(
        2, int(os.getenv("STREAMFORGE_AUTO_RESTART_CHECK_INTERVAL", "5"))
    )
    failback_probe_timeout: int = max(2, int(os.getenv("STREAMFORGE_FAILBACK_PROBE_TIMEOUT", "5")))
    log_page_limit: int = max(50, min(2000, int(os.getenv("STREAMFORGE_LOG_PAGE_LIMIT", "500"))))
    timezone_name: str = os.getenv("STREAMFORGE_TIMEZONE", "Asia/Dhaka")
    geoip_auto_update: bool = os.getenv("STREAMFORGE_GEOIP_AUTO_UPDATE", "0").strip().lower() in {"1", "true", "yes", "on"}
    maxmind_account_id: str = os.getenv("STREAMFORGE_MAXMIND_ACCOUNT_ID", "")
    maxmind_license_key: str = os.getenv("STREAMFORGE_MAXMIND_LICENSE_KEY", "")
    asn_db_path: Path = Path(os.getenv("STREAMFORGE_ASN_DB_PATH", "/opt/streamforge/GeoLite2-ASN.mmdb"))
    country_db_path: Path = Path(os.getenv("STREAMFORGE_COUNTRY_DB_PATH", "/opt/streamforge/GeoLite2-Country.mmdb"))
    viewer_key_ttl_seconds: int = max(300, int(os.getenv("STREAMFORGE_VIEWER_KEY_TTL_SECONDS", "43200")))
    restream_key_ttl_seconds: int = max(3600, int(os.getenv("STREAMFORGE_RESTREAM_KEY_TTL_SECONDS", "86400")))
    # STREAMFORGE_REDIS_SHARED_STATE_V61: Redis is used only for short-lived
    # playback/session state. Persistent configuration remains in SQL.
    redis_enabled: bool = os.getenv("STREAMFORGE_REDIS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
    redis_url: str = os.getenv("STREAMFORGE_REDIS_URL", "redis://127.0.0.1:6379/0")
    redis_prefix: str = os.getenv("STREAMFORGE_REDIS_PREFIX", "streamforge")
    redis_timeout_ms: int = max(50, min(2000, int(os.getenv("STREAMFORGE_REDIS_TIMEOUT_MS", "150"))))
    redis_touch_interval_ms: int = max(250, min(5000, int(os.getenv("STREAMFORGE_REDIS_TOUCH_INTERVAL_MS", "1000"))))
    redis_failure_backoff_seconds: int = max(1, min(60, int(os.getenv("STREAMFORGE_REDIS_FAILURE_BACKOFF_SECONDS", "5"))))


settings = Settings()
