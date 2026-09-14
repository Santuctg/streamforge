from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

from .config import settings
from .input_options import build_input_args
from .source_resolver import SourceResolveError, is_youtube_url, resolve_stream_source, youtube_probe_payload


def _number(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _rate(value: Any) -> float | None:
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        return round(float(Fraction(str(value))), 3)
    except (ValueError, ZeroDivisionError):
        return _number(value)


def _hex_pid(value: Any) -> str | None:
    if value in (None, "", "N/A"):
        return None
    text = str(value)
    try:
        parsed = int(text, 0)
    except ValueError:
        try:
            parsed = int(text)
        except ValueError:
            return text
    return f"0x{parsed:04X} ({parsed})"


def _stream_payload(stream: dict[str, Any], program_id: int | None = None) -> dict[str, Any]:
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    active_dispositions = [name for name, enabled in disposition.items() if enabled]
    codec_type = stream.get("codec_type") or "unknown"
    payload: dict[str, Any] = {
        "index": _integer(stream.get("index")),
        "type": codec_type,
        "codec": stream.get("codec_name") or "unknown",
        "codec_long": stream.get("codec_long_name"),
        "profile": stream.get("profile"),
        "level": stream.get("level"),
        "pid": _hex_pid(stream.get("id")),
        "program_id": program_id,
        "bit_rate": _integer(stream.get("bit_rate")),
        "language": tags.get("language"),
        "title": tags.get("title"),
        "service_name": tags.get("service_name"),
        "disposition": active_dispositions,
    }
    if codec_type == "video":
        payload.update(
            {
                "width": _integer(stream.get("width")),
                "height": _integer(stream.get("height")),
                "coded_width": _integer(stream.get("coded_width")),
                "coded_height": _integer(stream.get("coded_height")),
                "fps": _rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                "pixel_format": stream.get("pix_fmt"),
                "color_space": stream.get("color_space"),
                "field_order": stream.get("field_order"),
                "aspect_ratio": stream.get("display_aspect_ratio"),
            }
        )
    elif codec_type == "audio":
        payload.update(
            {
                "sample_rate": _integer(stream.get("sample_rate")),
                "channels": _integer(stream.get("channels")),
                "channel_layout": stream.get("channel_layout"),
                "sample_format": stream.get("sample_fmt"),
                "bits_per_sample": _integer(stream.get("bits_per_sample")),
            }
        )
    elif codec_type == "subtitle":
        payload.update({"subtitle_type": stream.get("codec_name")})
    return payload


def _program_payload(program: dict[str, Any]) -> dict[str, Any]:
    tags = program.get("tags") or {}
    program_id = _integer(program.get("program_id"))
    streams = [
        _stream_payload(stream, program_id=program_id)
        for stream in (program.get("streams") or [])
    ]
    return {
        "program_id": program_id,
        "program_num": _integer(program.get("program_num")),
        "service_name": tags.get("service_name") or tags.get("name"),
        "service_provider": tags.get("service_provider") or tags.get("provider_name"),
        "pmt_pid": _hex_pid(program.get("pmt_pid")),
        "pcr_pid": _hex_pid(program.get("pcr_pid")),
        "start_time": _number(program.get("start_time")),
        "duration": _number(program.get("duration")),
        "bit_rate": _integer(program.get("bit_rate")),
        "streams": streams,
    }


# STREAMFORGE_MAIN_YOUTUBE_PROBE_TIMEOUT_V1014:
def probe_stream(target: str | Path, timeout: int | None = None) -> dict[str, Any]:
    """Probe a local file or FFmpeg-compatible live input with a hard timeout."""
    ffprobe = settings.ffprobe_bin
    if not shutil.which(ffprobe) and not Path(ffprobe).exists():
        return {
            "ok": False,
            "error": f"ffprobe not found: {ffprobe}",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    youtube_source = is_youtube_url(str(target))
    timeout_seconds = max(30, int(timeout or settings.ffprobe_timeout)) if youtube_source else max(3, int(timeout or settings.ffprobe_timeout))
    resolve_started = time.monotonic()
    try:
        resolved_target = resolve_stream_source(str(target), force=youtube_source)
    except SourceResolveError as exc:
        return {
            "ok": False,
            "error": ("YouTube resolve failed: " if youtube_source else "") + str(exc),
            "stage": "resolve",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    resolve_seconds = round(time.monotonic() - resolve_started, 3)
    # STREAMFORGE_YOUTUBE_SCAN_METADATA_FASTPATH_V1015:
    # yt-dlp has already fetched and parsed the live manifest while resolving the
    # signed Googlevideo URL. Re-running a full ffprobe over the same live HLS can
    # block for tens of seconds on some YouTube edges. Use the resolver metadata
    # for Source Scan; actual FFmpeg playback still opens the resolved HLS normally.
    if youtube_source:
        metadata = youtube_probe_payload(str(target))
        if metadata:
            payload = dict(metadata)
            payload.update({
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "stage": "complete",
                "resolve_seconds": resolve_seconds,
            })
            return payload
    cmd = [
        ffprobe,
        "-v", "error",
        "-hide_banner",
        "-analyzeduration", str(settings.ffprobe_analyzeduration),
        "-probesize", str(settings.ffprobe_probesize),
        "-show_error",
        "-show_format",
        "-show_programs",
        "-show_streams",
        "-of", "json",
    ]
    cmd += build_input_args(resolved_target)
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        if youtube_source:
            return {
                "ok": False,
                "error": f"YouTube resolved successfully in {resolve_seconds:.1f}s, but HLS probe timed out after {timeout_seconds} seconds",
                "stage": "ffprobe",
                "resolve_seconds": resolve_seconds,
                "captured_at": captured_at,
            }
        return {
            "ok": False,
            "error": f"Probe timed out after {timeout_seconds} seconds. Check input reachability, localaddr and multicast routing.",
            "stage": "ffprobe",
            "captured_at": captured_at,
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "captured_at": captured_at}

    raw = result.stdout.strip()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        detail = (result.stderr or raw or "ffprobe returned invalid JSON").strip()
        return {"ok": False, "error": detail[-1200:], "captured_at": captured_at}

    ffprobe_error = data.get("error") or {}
    if result.returncode != 0 or ffprobe_error:
        detail = (
            ffprobe_error.get("string")
            or result.stderr
            or "Unable to read the stream"
        ).strip()
        return {
            "ok": False,
            "error": detail[-1200:],
            "captured_at": captured_at,
            "return_code": result.returncode,
        }

    format_info = data.get("format") or {}
    programs = [_program_payload(program) for program in (data.get("programs") or [])]

    stream_to_program: dict[int, int] = {}
    for program in programs:
        if program["program_id"] is None:
            continue
        for stream in program["streams"]:
            if stream["index"] is not None:
                stream_to_program[stream["index"]] = program["program_id"]

    streams = []
    for stream in data.get("streams") or []:
        index = _integer(stream.get("index"))
        streams.append(_stream_payload(stream, stream_to_program.get(index) if index is not None else None))

    type_counts: dict[str, int] = {}
    for stream in streams:
        type_counts[stream["type"]] = type_counts.get(stream["type"], 0) + 1

    format_tags = format_info.get("tags") or {}
    return {
        "ok": True,
        "captured_at": captured_at,
        "format": {
            "name": format_info.get("format_name"),
            "long_name": format_info.get("format_long_name"),
            "start_time": _number(format_info.get("start_time")),
            "duration": _number(format_info.get("duration")),
            "size": _integer(format_info.get("size")),
            "bit_rate": _integer(format_info.get("bit_rate")),
            "probe_score": _integer(format_info.get("probe_score")),
            "service_name": format_tags.get("service_name"),
            "service_provider": format_tags.get("service_provider"),
        },
        "programs": programs,
        "streams": streams,
        "counts": type_counts,
        "stderr": result.stderr.strip()[-1200:] if result.stderr.strip() else None,
    }
