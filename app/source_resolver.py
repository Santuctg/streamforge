from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit


# STREAMFORGE_YOUTUBE_SOURCE_RESOLVER_V1012:
# Keep the saved channel source stable (the YouTube page URL) and resolve a
# fresh signed media URL only when FFmpeg/ffprobe needs to open the source.
# STREAMFORGE_YOUTUBE_ANTIBOT_COOKIE_FALLBACK_V1013:
# STREAMFORGE_YOUTUBE_RUNTIME_DIAGNOSTICS_V1014:
# YouTube can require a trusted/browser session for datacenter IPs.  Prefer the
# normal extractor first, retry with live-friendly clients, and automatically
# use a server-local Netscape cookies.txt when the operator has uploaded one.
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_CACHE_TTL_SECONDS = 90.0
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, str]] = {}
_DETAIL_CACHE: dict[str, tuple[float, dict]] = {}
_DEFAULT_COOKIE_PATH = Path(os.getenv("STREAMFORGE_YOUTUBE_COOKIES_FILE", "/var/lib/streamforge/youtube-cookies.txt"))


class SourceResolveError(RuntimeError):
    pass


def youtube_cookie_path() -> Path:
    return _DEFAULT_COOKIE_PATH


def youtube_cookie_configured() -> bool:
    try:
        return _DEFAULT_COOKIE_PATH.is_file() and _DEFAULT_COOKIE_PATH.stat().st_size > 0 and os.access(_DEFAULT_COOKIE_PATH, os.R_OK)
    except OSError:
        return False


def _base_target(target: str) -> str:
    return str(target or "").strip().split("|", 1)[0].strip()


def is_youtube_url(target: str) -> bool:
    raw = _base_target(target)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return parts.scheme.lower() in {"http", "https"} and host in _YOUTUBE_HOSTS


def _header_suffix(headers: dict[str, str]) -> str:
    selected: list[tuple[str, str]] = []
    for source_name, output_name in (
        ("User-Agent", "User-Agent"),
        ("Referer", "Referer"),
        ("Origin", "Origin"),
        ("Cookie", "Cookie"),
    ):
        value = str(headers.get(source_name) or headers.get(source_name.lower()) or "").strip()
        if value:
            selected.append((output_name, value))
    return urlencode(selected) if selected else ""


def _codec_label(value: object, kind: str) -> str:
    text = str(value or "unknown").strip().lower()
    if kind == "video":
        if text.startswith(("avc1", "avc3")):
            return "h264"
        if text.startswith(("hev1", "hvc1")):
            return "hevc"
        if text.startswith("vp09"):
            return "vp9"
        if text.startswith("av01"):
            return "av1"
    if kind == "audio":
        if text.startswith("mp4a"):
            return "aac"
    return text or "unknown"


def _youtube_probe_payload(info: dict) -> dict:
    protocol = str(info.get("protocol") or "").strip().lower()
    format_name = "hls" if "m3u8" in protocol else (protocol or str(info.get("ext") or "youtube"))
    streams: list[dict] = []
    vcodec = str(info.get("vcodec") or "none").strip().lower()
    acodec = str(info.get("acodec") or "none").strip().lower()
    if vcodec not in {"", "none"}:
        streams.append({
            "index": 0, "type": "video", "codec": _codec_label(vcodec, "video"),
            "codec_long": None, "program_id": None,
            "width": info.get("width"), "height": info.get("height"),
            "fps": info.get("fps"), "sample_rate": None, "channels": None,
            "language": info.get("language"), "title": info.get("format_note"),
        })
    if acodec not in {"", "none"}:
        streams.append({
            "index": 1 if streams else 0, "type": "audio", "codec": _codec_label(acodec, "audio"),
            "codec_long": None, "program_id": None,
            "width": None, "height": None, "fps": None,
            "sample_rate": info.get("asr"), "channels": info.get("audio_channels"),
            "language": info.get("language"), "title": None,
        })
    counts: dict[str, int] = {}
    for item in streams:
        kind = str(item.get("type") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    tbr = info.get("tbr")
    try:
        bit_rate = int(float(tbr) * 1000) if tbr not in (None, "") else None
    except (TypeError, ValueError):
        bit_rate = None
    return {
        "ok": True,
        "format": {
            "name": format_name,
            "long_name": str(info.get("format") or info.get("format_note") or "YouTube Live"),
            "duration": info.get("duration"),
            "bit_rate": bit_rate,
            "service_name": info.get("channel") or info.get("uploader"),
            "service_provider": "YouTube",
        },
        "programs": [],
        "streams": streams,
        "counts": counts,
        "stderr": None,
        "probe_method": "yt-dlp",
    }


def youtube_probe_payload(target: str) -> dict | None:
    key = _base_target(target)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _DETAIL_CACHE.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])
    return None


def _pick_resolved_url(info: dict) -> tuple[str, dict[str, str]]:
    direct = str(info.get("url") or "").strip()
    headers = dict(info.get("http_headers") or {})
    if direct:
        return direct, headers

    formats = [item for item in list(info.get("formats") or []) if isinstance(item, dict)]
    candidates: list[dict] = []
    for item in formats:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        vcodec = str(item.get("vcodec") or "none").lower()
        acodec = str(item.get("acodec") or "none").lower()
        if vcodec == "none" or acodec == "none":
            continue
        candidates.append(item)
    if not candidates:
        raise SourceResolveError("YouTube did not expose a single audio+video media stream for this source")

    def score(item: dict) -> tuple[int, int, float, float]:
        protocol = str(item.get("protocol") or "").lower()
        hls = 1 if "m3u8" in protocol else 0
        try:
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        try:
            fps = float(item.get("fps") or 0.0)
        except (TypeError, ValueError):
            fps = 0.0
        try:
            tbr = float(item.get("tbr") or 0.0)
        except (TypeError, ValueError):
            tbr = 0.0
        return hls, height, fps, tbr

    chosen = max(candidates, key=score)
    chosen_headers = dict(headers)
    chosen_headers.update(dict(chosen.get("http_headers") or {}))
    return str(chosen.get("url") or "").strip(), chosen_headers


def _extract_youtube(target: str) -> str:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise SourceResolveError("YouTube source resolver is unavailable because yt-dlp is not installed") from exc

    cookie_path = youtube_cookie_path()
    has_cookies = youtube_cookie_configured()
    base_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 2,
        # StreamForge supplies one FFmpeg input, so prefer a pre-muxed HLS/live
        # format and then fall back to any single audio+video media format.
        "format": (
            "best[protocol^=m3u8][vcodec!=none][acodec!=none]/"
            "best[vcodec!=none][acodec!=none]/best"
        ),
    }
    # Public live streams are attempted without browser cookies first.  A
    # configured cookies.txt is a fallback only, so stale/account cookies do
    # not make otherwise-public streams slower or less reliable.
    client_fallback = {"youtube": {"player_client": ["default", "web_safari", "android_vr", "web_embedded"]}}
    attempts: list[tuple[dict | None, bool]] = [
        (None, False),
        (client_fallback, False),
    ]
    if has_cookies:
        attempts.extend([
            (None, True),
            (client_fallback, True),
        ])
    errors: list[str] = []
    info: dict | None = None
    for extractor_args, use_cookies in attempts:
        options = dict(base_options)
        if use_cookies:
            options["cookiefile"] = str(cookie_path)
        if extractor_args:
            options["extractor_args"] = extractor_args
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                candidate = ydl.extract_info(_base_target(target), download=False)
            if isinstance(candidate, dict):
                info = candidate
                break
            errors.append("extractor returned no media information")
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            prefix = "cookies: " if use_cookies else "public: "
            errors.append(prefix + detail[-850:])

    if not isinstance(info, dict):
        detail = errors[-1] if errors else "unknown extractor failure"
        lowered = " ".join(errors).lower()
        antibot = any(token in lowered for token in (
            "confirm you're not a bot",
            "confirm you’re not a bot",
            "sign in to confirm",
            "login_required",
            "cookies-from-browser",
        ))
        if antibot and not has_cookies:
            raise SourceResolveError(
                "YouTube requires a trusted browser session for this server IP. "
                "Upload a fresh YouTube cookies.txt in this server's Settings, then Scan/Start again."
            )
        if antibot and has_cookies:
            raise SourceResolveError(
                "YouTube rejected the saved cookies.txt for this server IP. "
                "Upload a fresh exported YouTube cookies.txt in Settings and retry."
            )
        raise SourceResolveError(f"Unable to resolve YouTube source: {detail[-700:]}")

    if info.get("entries"):
        entry = next((item for item in info.get("entries") or [] if isinstance(item, dict)), None)
        if entry:
            info = entry

    media_url, headers = _pick_resolved_url(info)
    if not media_url:
        raise SourceResolveError("Unable to resolve YouTube source: no playable media URL was returned")
    suffix = _header_suffix(headers)
    payload = _youtube_probe_payload(info)
    now = time.monotonic()
    with _CACHE_LOCK:
        _DETAIL_CACHE[_base_target(target)] = (now + _CACHE_TTL_SECONDS, payload)
    return media_url + ("|" + suffix if suffix else "")


def resolve_stream_source(target: str, *, force: bool = False) -> str:
    raw = str(target or "").strip()
    if not raw or not is_youtube_url(raw):
        return raw

    key = _base_target(raw)
    now = time.monotonic()
    if not force:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and cached[0] > now:
                return cached[1]

    resolved = _extract_youtube(raw)
    with _CACHE_LOCK:
        _CACHE[key] = (now + _CACHE_TTL_SECONDS, resolved)
        if len(_CACHE) > 256:
            expired = [name for name, item in _CACHE.items() if item[0] <= now]
            for name in expired:
                _CACHE.pop(name, None)
                _DETAIL_CACHE.pop(name, None)
            detail_expired = [name for name, item in _DETAIL_CACHE.items() if item[0] <= now]
            for name in detail_expired:
                _DETAIL_CACHE.pop(name, None)
            while len(_CACHE) > 256:
                oldest = next(iter(_CACHE))
                _CACHE.pop(oldest, None)
                _DETAIL_CACHE.pop(oldest, None)
    return resolved
