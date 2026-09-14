from __future__ import annotations

from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from .config import settings


_HEADER_NAMES: Mapping[str, str] = {
    "referer": "Referer",
    "referrer": "Referer",
    "origin": "Origin",
    "cookie": "Cookie",
    "authorization": "Authorization",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "x-forwarded-for": "X-Forwarded-For",
}


def split_extended_url(raw_target: str | Path) -> tuple[str, dict[str, str]]:
    """Split common IPTV URL syntax: URL|User-Agent=...&Referer=...."""
    target = str(raw_target).strip()
    if "|" not in target:
        return target, {}
    base, option_text = target.split("|", 1)
    headers: dict[str, str] = {}
    for key, value in parse_qsl(option_text, keep_blank_values=True):
        normalized = key.strip().lower().replace("_", "-")
        if normalized and value:
            headers[normalized] = value.strip()
    return base.strip(), headers


def is_http_target(target: str | Path) -> bool:
    base, _ = split_extended_url(target)
    return urlsplit(base).scheme.lower() in {"http", "https"}


def is_hls_http_target(target: str | Path) -> bool:
    base, _ = split_extended_url(target)
    parts = urlsplit(base)
    if parts.scheme.lower() not in {"http", "https"}:
        return False
    lowered = (parts.path + "?" + parts.query).lower()
    return ".m3u8" in lowered or "format=hls" in lowered


def build_input_args(target: str | Path, *, realtime_hls: bool = False, youtube_live: bool = False) -> list[str]:
    """Return protocol-safe input options ending in ``-i TARGET``.

    ``realtime_hls`` is intentionally opt-in so long-running FFmpeg playback can
    pace completed HLS segments at wall-clock rate without slowing ffprobe.
    """
    base, supplied_headers = split_extended_url(target)
    scheme = urlsplit(base).scheme.lower()
    args: list[str] = []

    if scheme in {"http", "https"}:
        user_agent = supplied_headers.pop("user-agent", settings.http_user_agent)
        # STREAMFORGE_MAIN_YOUTUBE_403_RECONNECT_V1023:
        # Googlevideo can transiently reject a live segment/keepalive request with
        # HTTP 403 while the signed live manifest is still valid.  Remote Nodes
        # already recover 4xx responses, but Main historically retried only
        # 408/429/5xx and could therefore exit cleanly and trigger backup-source
        # flapping.  Retry 403 only for resolved YouTube Live; ordinary HTTP/HLS
        # keeps the narrower policy so a real 403/404 does not loop forever.
        reconnect_http_errors = "403,408,429,5xx" if youtube_live else "408,429,5xx"
        args += [
            "-rw_timeout", str(settings.http_rw_timeout_us),
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_on_http_error", reconnect_http_errors,
            "-reconnect_delay_max", str(settings.http_reconnect_delay_max),
            "-user_agent", user_agent,
        ]
        if is_hls_http_target(base):
            # STREAMFORGE_YOUTUBE_LIVE_EDGE_V1017:
            # Resolved YouTube Live manifests publish ~5s media segments. Starting
            # three segments behind adds roughly 15s before StreamForge's own HLS
            # output. Keep the conservative -3 edge for ordinary HLS, but start
            # YouTube one segment behind so it stays close to live while retaining
            # a small jitter cushion.
            args += [
                "-live_start_index", "-1" if youtube_live else "-3",
                "-allowed_extensions", "ALL",
                "-http_persistent", "1",
            ]
            # STREAMFORGE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK:
            # Providers often publish one completed 6-7s HLS segment at a time.
            # Without input pacing, stream-copy FFmpeg can consume that segment
            # immediately and publish several ~2s output segments in a burst.
            # -re provides wall-clock input pacing and works on older FFmpeg builds.
            if realtime_hls:
                args += ["-re"]
        else:
            # Raw HTTP MPEG-TS/FLV streams may end unexpectedly; reconnecting at EOF is useful there.
            # HLS playlist requests normally end at every reload, so enabling this for HLS causes a loop.
            args += ["-reconnect_at_eof", "1"]

        custom_lines: list[str] = []
        for key, value in supplied_headers.items():
            if key in {"user-agent", "useragent"}:
                continue
            display_name = _HEADER_NAMES.get(key, "-".join(part.capitalize() for part in key.split("-")))
            custom_lines.append(f"{display_name}: {value}")
        if custom_lines:
            args += ["-headers", "\r\n".join(custom_lines) + "\r\n"]

    return args + ["-i", base]
