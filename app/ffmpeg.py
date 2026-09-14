from __future__ import annotations

import json
import os
import re
import socket
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from sqlalchemy import select, text, update

from .config import settings
from .db import SessionLocal
from .input_options import build_input_args
from .models import Channel
from .stream_info import probe_stream
from .source_resolver import is_youtube_url, resolve_stream_source
from .audit_log import log_event


_BITRATE_RE = re.compile(r"([0-9.]+)kbits/s")
_SPEED_RE = re.compile(r"([0-9.]+)x")
_VIDEO_DIMENSION_RE = re.compile(r"(?<!\d)([1-9]\d{1,4})x([1-9]\d{1,4})(?!\d)")
_VIDEO_FPS_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*fps", re.IGNORECASE)
_AUTO_RESTART_DELAYS = (3, 5, 10, 20, 30, 60)


def _remote_output_urls(raw: str | None) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in str(raw or "").splitlines() if line.strip()))


def _tee_escape_filename(value: str) -> str:
    # FFmpeg tee uses | as the slave separator; escape it at filename level.
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _remote_tee_slave(url: str, *, http_put: bool = False, onfail_ignore: bool = True) -> str:
    target = _tee_escape_filename(url)
    options = ["f=mpegts", "mpegts_flags=+resend_headers"]
    if onfail_ignore:
        options.append("onfail=ignore")
    lower = url.lower()
    if lower.startswith(("http://", "https://")):
        options.extend(["content_type=video/mp2t", f"method={'PUT' if http_put else 'POST'}"])
    return "[" + ":".join(options) + "]" + target
_AUDIO_ENCODERS = {
    "aac": "aac",
    "ac3": "ac3",
    "eac3": "eac3",
    "mp2": "mp2",
    "libmp3lame": "libmp3lame",
    "libopus": "libopus",
    "flac": "flac",
    "libvorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
}


class StreamManager:
    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        self._encoder_cache: set[str] | None = None
        # STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117: encoder discovery is
        # cached per binary so an explicit NVIDIA profile can use a compatible
        # legacy-NVENC FFmpeg without changing the system FFmpeg used elsewhere.
        self._encoder_cache_by_binary: dict[str, set[str]] = {}
        self._encoder_probe_cache: dict[tuple[str, str], tuple[bool, str]] = {}
        self._auto_encoder_cache: dict[str, str] = {}
        self._auto_encoder_failures: dict[str, dict[str, str]] = {}
        self._started_monotonic: dict[int, float] = {}
        self._live_metrics: dict[int, dict[str, float]] = {}
        self._intentional_stops: set[int] = set()
        self._restart_timers: dict[int, threading.Timer] = {}
        self._restart_attempts: dict[int, int] = {}
        self._last_auto_restart_monotonic: dict[int, float] = {}
        self._auto_restart_counts: dict[int, int] = {}
        self._failback_watchers: set[int] = set()
        # STREAMFORGE_MAIN_RUNTIME_DB_WRITE_THROTTLE_V34:
        # Live bitrate is served from _live_metrics. Persisting every FFmpeg
        # progress sample creates unnecessary SQLite writer contention.
        self._last_db_metric_write: dict[int, float] = {}
        # STREAMFORGE_MAIN_FFMPEG_STDERR_EXIT_PRESERVE_V121:
        # Keep the final stderr tail in memory until the wait thread has
        # observed process exit. This prevents the old stderr/wait race from
        # replacing a useful HTTP/source error with only an exit code.
        self._stderr_tails: dict[int, tuple[subprocess.Popen[str], str]] = {}
        # STREAMFORGE_MAIN_CLOCK_SAFE_HLS_FRESHNESS_V121:
        # HLS freshness for the stall watchdog is tracked by file change using
        # monotonic time, so an NTP/wall-clock correction cannot keep a stale
        # future-mtime playlist falsely fresh.
        self._hls_freshness_state: dict[int, tuple[tuple[object, ...], float]] = {}

    def reset_stale_state(self) -> None:
        with self._lock:
            for timer in self._restart_timers.values():
                timer.cancel()
            self._restart_timers.clear()
            self._restart_attempts.clear()
            self._last_auto_restart_monotonic.clear()
            self._auto_restart_counts.clear()
            self._intentional_stops.clear()
            self._live_metrics.clear()
            self._last_db_metric_write.clear()
            self._stderr_tails.clear()
            self._hls_freshness_state.clear()
        # STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2
        # Use a set-based UPDATE instead of mutating ORM rows one-by-one.
        # During update/startup a channel can disappear between SELECT and flush
        # (for example from another control path).  ORM then raises StaleDataError
        # and aborts the whole application boot.  A bulk UPDATE is naturally
        # tolerant of rows that no longer exist and is also substantially cheaper.
        with SessionLocal() as db:
            db.execute(
                update(Channel).values(
                    status="stopped",
                    pid=None,
                    live_bitrate_kbps=0,
                )
            )
            db.commit()

    def _hls_dir(self, channel: Channel) -> Path:
        return settings.hls_root / channel.slug

    # STREAMFORGE_MAIN_STALL_GUARD_HLS_FRESHNESS_V1167:
    # STREAMFORGE_MAIN_CLOCK_SAFE_HLS_FRESHNESS_V121:
    # FFmpeg's -progress pipe can occasionally stop advancing while the HLS
    # muxer is healthy. Track actual playlist/segment mutation with monotonic
    # time instead of trusting wall-clock mtime age alone. A backward NTP step
    # therefore cannot make an old future-mtime file look fresh indefinitely.
    def _local_hls_output_fresh(self, channel: Channel) -> bool:
        if str(getattr(channel, "output_type", "") or "").strip().lower() != "hls":
            return False
        playlist = self._hls_dir(channel) / "index.m3u8"
        try:
            playlist_stat = playlist.stat()
            if playlist_stat.st_size < 16:
                return False
            lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
            segments = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
            if not segments:
                return False
            newest = playlist.parent / Path(segments[-1]).name
            newest_stat = newest.stat()
            segment_time = max(1, int(getattr(channel, "hls_segment_time", 1) or 1))
            freshness_window = max(20.0, min(float(settings.auto_restart_stall_seconds), float(segment_time * 8)))
            signature = (
                playlist_stat.st_mtime_ns, playlist_stat.st_size,
                newest.name, newest_stat.st_mtime_ns, newest_stat.st_size,
            )
            now_mono = time.monotonic()
            wall_age = time.time() - max(playlist_stat.st_mtime, newest_stat.st_mtime)
            with self._lock:
                previous = self._hls_freshness_state.get(int(channel.id))
                if previous is None or previous[0] != signature:
                    # A genuinely old file must not become fresh merely because
                    # this is the watchdog's first observation. Future mtimes are
                    # allowed one monotonic observation window so active output
                    # can prove itself by changing again after a clock correction.
                    if wall_age > freshness_window:
                        self._hls_freshness_state[int(channel.id)] = (signature, now_mono - freshness_window - 1.0)
                        return False
                    self._hls_freshness_state[int(channel.id)] = (signature, now_mono)
                    return True
                return (now_mono - previous[1]) <= freshness_window
        except (OSError, ValueError):
            return False

    def _clear_local_hls_output(self, channel: Channel) -> None:
        """Remove stale Local HLS artifacts after a channel is no longer up.

        STREAMFORGE_LOCAL_HLS_CLEAN_ON_DOWN_V63R3: an old index.m3u8 and media
        segments can remain fresh enough to pass the HLS readiness window after
        FFmpeg stops.  Playlist eligibility no longer relies on file cleanup,
        but removing stale output makes the disk state truthful as well.
        """
        if str(getattr(channel, "output_type", "") or "").strip().lower() != "hls":
            return
        out_dir = self._hls_dir(channel)
        try:
            for old in out_dir.glob("*"):
                try:
                    if old.is_file() or old.is_symlink():
                        old.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            return

    @staticmethod
    def _apply_local_node_encoding_profile(db, channel: Channel) -> dict[str, object]:
        """Apply the Local Node override without changing channel-wide defaults."""
        original = {
            "video_codec": channel.video_codec,
            "video_bitrate": channel.video_bitrate,
            "width": channel.width,
            "height": channel.height,
            "audio_codec": channel.audio_codec,
            "audio_bitrate": channel.audio_bitrate,
            "hls_segment_time": channel.hls_segment_time,
        }
        row = db.execute(
            text(
                "SELECT cn.video_codec,cn.video_bitrate,cn.resolution,cn.audio_codec,cn.audio_bitrate,cn.hls_segment_time "
                "FROM channel_nodes cn JOIN nodes n ON n.id=cn.node_id "
                "WHERE cn.channel_id=:channel_id AND n.node_type='local' LIMIT 1"
            ),
            {"channel_id": channel.id},
        ).first()
        if not row:
            return original
        video_codec, video_bitrate, resolution, audio_codec, audio_bitrate, hls_segment_time = row
        if video_codec:
            channel.video_codec = str(video_codec)
        if video_bitrate:
            channel.video_bitrate = str(video_bitrate)
        if resolution:
            value = str(resolution).strip().lower()
            if value == "source":
                channel.width = None
                channel.height = None
            elif "x" in value:
                try:
                    width_text, height_text = value.split("x", 1)
                    width, height = int(width_text), int(height_text)
                    if width >= 2 and height >= 2 and width % 2 == 0 and height % 2 == 0:
                        channel.width, channel.height = width, height
                except ValueError:
                    pass
        if audio_codec:
            channel.audio_codec = str(audio_codec)
        if audio_bitrate:
            channel.audio_bitrate = str(audio_bitrate)
        if hls_segment_time is not None:
            channel.hls_segment_time = max(1, min(20, int(hls_segment_time)))
        return original

    @staticmethod
    def _process_write_counter(pid: int) -> int | None:
        """Return cumulative bytes passed to write-like syscalls by FFmpeg.

        FFmpeg reports ``total_size=N/A`` for some muxers, including HLS.
        Linux ``/proc/<pid>/io`` still provides a cumulative ``wchar`` counter
        for files and sockets, which gives the panel a practical fallback for
        live output-throughput calculation.
        """
        try:
            for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "wchar":
                    return max(0, int(value.strip()))
        except (OSError, ValueError):
            return None
        return None

    def available_encoders_for(self, ffmpeg_bin: str, refresh: bool = False) -> set[str]:
        binary = str(ffmpeg_bin or settings.ffmpeg_bin)
        if refresh:
            self._encoder_cache_by_binary.pop(binary, None)
            self._encoder_probe_cache = {key: value for key, value in self._encoder_probe_cache.items() if key[0] != binary}
            if binary == settings.ffmpeg_bin:
                self._encoder_cache = None
                self._auto_encoder_cache = {}
                self._auto_encoder_failures = {}
        cached = self._encoder_cache_by_binary.get(binary)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                [binary, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            encoders: set[str] = set()
            self._encoder_cache_by_binary[binary] = encoders
            if binary == settings.ffmpeg_bin:
                self._encoder_cache = encoders
            return encoders

        encoders = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) >= 1 and parts[0][0] in {"V", "A", "S"}:
                encoders.add(parts[1])
        self._encoder_cache_by_binary[binary] = encoders
        if binary == settings.ffmpeg_bin:
            self._encoder_cache = encoders
        return encoders

    def available_encoders(self, refresh: bool = False) -> set[str]:
        return self.available_encoders_for(settings.ffmpeg_bin, refresh=refresh)

    @staticmethod
    def _binary_exists(binary: str) -> bool:
        return bool(shutil.which(binary) or Path(binary).exists())

    def _ffmpeg_for_codec(self, codec: str) -> str:
        # STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117:
        # Prefer the administrator-supplied NVENC-compatible FFmpeg only for
        # explicit NVIDIA codecs. There is deliberately no concurrency/channel
        # cap here; the operator controls how many GPU profiles are started.
        if str(codec or "").lower() == "h264_nvenc":
            candidate = str(settings.nvenc_ffmpeg_bin or "").strip()
            if candidate and self._binary_exists(candidate):
                return candidate
        return settings.ffmpeg_bin

    @staticmethod
    def _render_nodes() -> list[Path]:
        return sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []

    @staticmethod
    def _device_vendor(node: Path) -> str | None:
        vendor = Path("/sys/class/drm") / node.name / "device/vendor"
        try:
            return vendor.read_text().strip().lower()
        except OSError:
            return None

    @classmethod
    def _intel_render_nodes(cls) -> list[Path]:
        nodes = [node for node in cls._render_nodes() if cls._device_vendor(node) == "0x8086"]
        if nodes:
            return nodes
        if Path("/sys/module/i915").exists():
            return cls._render_nodes()
        return []

    @staticmethod
    def _has_nvidia_device() -> bool:
        return (
            Path("/dev/nvidia0").exists()
            or Path("/dev/nvidiactl").exists()
            or Path("/proc/driver/nvidia/gpus").exists()
        )

    def _probe_encoder(self, codec: str, timeout: int = 8, ffmpeg_bin: str | None = None) -> tuple[bool, str]:
        """Run a one-frame encode so an advertised but unusable GPU is not selected."""
        binary = ffmpeg_bin or settings.ffmpeg_bin
        if codec not in self.available_encoders_for(binary):
            return False, "encoder is not included in this FFmpeg build"

        cache_key = (binary, codec)
        cached = self._encoder_probe_cache.get(cache_key)
        if cached is not None:
            return cached

        cmd = [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if codec.endswith("_qsv"):
            intel_nodes = self._intel_render_nodes()
            if not intel_nodes:
                return False, "Intel render device was not found"
            cmd += ["-qsv_device", str(intel_nodes[0])]
        elif codec.endswith("_vaapi"):
            vaapi_device = Path(settings.vaapi_device)
            if not vaapi_device.exists():
                nodes = self._render_nodes()
                if not nodes:
                    return False, "VAAPI render device was not found"
                vaapi_device = nodes[0]
            cmd += ["-vaapi_device", str(vaapi_device)]

        cmd += [
            "-f", "lavfi",
            "-i", "color=c=black:s=128x72:r=25:d=0.08",
            "-frames:v", "1",
        ]
        if codec.endswith("_nvenc"):
            cmd += ["-c:v", codec, "-preset", "p1", "-f", "null", "-"]
        elif codec.endswith("_qsv"):
            cmd += ["-vf", "format=nv12", "-c:v", codec, "-f", "null", "-"]
        elif codec.endswith("_vaapi"):
            cmd += ["-vf", "format=nv12,hwupload", "-c:v", codec, "-f", "null", "-"]
        else:
            cmd += ["-c:v", codec, "-preset", "ultrafast", "-f", "null", "-"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return False, f"probe timed out after {timeout}s"
        except OSError as exc:
            return False, str(exc)
        if result.returncode == 0:
            result_value = (True, "ok")
            self._encoder_probe_cache[cache_key] = result_value
            return result_value
        error = (result.stderr or result.stdout or "encoder probe failed").strip()
        result_value = (False, error[-600:])
        self._encoder_probe_cache[cache_key] = result_value
        return result_value

    @staticmethod
    def _auto_candidates(family: str) -> tuple[list[str], set[str]]:
        if family == "h264":
            return (
                ["h264_nvenc", "h264_qsv", "h264_vaapi", "libx264", "h264"],
                {"h264_nvenc", "h264_qsv", "h264_vaapi"},
            )
        if family in {"h265", "hevc"}:
            return (
                ["hevc_nvenc", "hevc_qsv", "hevc_vaapi", "libx265", "hevc"],
                {"hevc_nvenc", "hevc_qsv", "hevc_vaapi"},
            )
        raise ValueError(f"Unsupported automatic encoder family: {family}")

    def auto_video_encoder_status(self, family: str = "h264", refresh: bool = False) -> dict[str, object]:
        normalized = "h265" if family in {"h265", "hevc"} else "h264"
        selected = self.detect_auto_video_encoder(normalized, refresh=refresh)
        _, hardware_codecs = self._auto_candidates(normalized)
        return {
            "family": normalized,
            "selected": selected,
            "hardware": selected in hardware_codecs,
            "failures": dict(self._auto_encoder_failures.get(normalized, {})),
        }

    def detect_auto_video_encoder(self, family: str = "h264", refresh: bool = False) -> str:
        """Select a working GPU encoder, falling back to a CPU encoder.

        H.264 order: NVIDIA NVENC, Intel Quick Sync, VAAPI, libx264.
        H.265 order: NVIDIA NVENC, Intel Quick Sync, VAAPI, libx265.
        Every candidate is verified with a real one-frame encode.
        """
        normalized = "h265" if family in {"h265", "hevc"} else "h264"
        if refresh:
            self.available_encoders(refresh=True)
        if normalized in self._auto_encoder_cache:
            return self._auto_encoder_cache[normalized]

        encoders = self.available_encoders()
        ordered, _ = self._auto_candidates(normalized)
        candidates: list[str] = []
        for codec in ordered:
            if codec not in encoders:
                continue
            if codec.endswith("_nvenc") and not self._has_nvidia_device():
                continue
            if codec.endswith("_qsv") and not self._intel_render_nodes():
                continue
            if codec.endswith("_vaapi") and not self._render_nodes():
                continue
            candidates.append(codec)

        failures: dict[str, str] = {}
        self._auto_encoder_failures[normalized] = failures
        for codec in candidates:
            ok, detail = self._probe_encoder(codec)
            if ok:
                self._auto_encoder_cache[normalized] = codec
                return codec
            failures[codec] = detail

        details = "; ".join(f"{name}: {reason}" for name, reason in failures.items())
        display = "H.265/HEVC" if normalized == "h265" else "H.264/AVC"
        raise RuntimeError(f"No usable {display} encoder was found{': ' + details if details else ''}")

    def resolve_video_codec(self, requested: str) -> str:
        if requested in {"auto", "auto_h264"}:
            return self.detect_auto_video_encoder("h264")
        if requested in {"auto_hevc", "auto_h265"}:
            return self.detect_auto_video_encoder("h265")
        return requested

    @staticmethod
    def _software_filters(channel: Channel) -> list[str]:
        filters: list[str] = []
        if channel.width and channel.height:
            filters.append(
                f"scale={channel.width}:{channel.height}:force_original_aspect_ratio=decrease,"
                f"pad={channel.width}:{channel.height}:(ow-iw)/2:(oh-ih)/2"
            )
        if channel.fps:
            filters.append(f"fps={channel.fps}")
        return filters

    @staticmethod
    def _gop_args(channel: Channel, segment_time_override: int | None = None) -> list[str]:
        if channel.output_type != "hls":
            return []
        fps = channel.fps or 25
        segment_time = max(1, int(segment_time_override or channel.hls_segment_time))
        gop = max(24, fps * segment_time)
        return [
            "-g", str(gop),
            "-force_key_frames", f"expr:gte(t,n_forced*{segment_time})",
        ]

    def _video_args(
        self,
        channel: Channel,
        *,
        force_h264_transcode: bool = False,
        hls_segment_time_override: int | None = None,
        resolved_codec: str | None = None,
        ffmpeg_bin: str | None = None,
    ) -> list[str]:
        requested = (channel.video_codec or "copy").strip().lower()
        # STREAMFORGE_YOUTUBE_TRUE_1S_GOP_TRANSCODE_V1022:
        # A copied YouTube HLS feed commonly exposes keyframes only every ~5s.
        # For HLS playback, copy mode therefore cannot produce independently
        # decodable 1s fragments.  Upgrade YouTube copy-mode channels to the
        # existing hardware-aware H.264 encoder path and force a 1s keyframe.
        codec = resolved_codec or (self.detect_auto_video_encoder("h264") if force_h264_transcode and requested == "copy" else self.resolve_video_codec(requested))
        if codec == "copy":
            return ["-c:v", "copy"]

        active_binary = ffmpeg_bin or self._ffmpeg_for_codec(codec)
        available = self.available_encoders_for(active_binary)
        if codec not in available:
            raise RuntimeError(f"Video encoder is not available: {codec}")

        args: list[str] = []
        filters = self._software_filters(channel)
        common_gop = self._gop_args(channel, hls_segment_time_override)
        hevc_tag = ["-tag:v", "hvc1"] if codec in {
            "libx265", "hevc", "hevc_nvenc", "hevc_qsv", "hevc_vaapi"
        } else []

        if codec in {"libx264", "libx265", "h264", "hevc"}:
            args += [
                "-c:v", codec,
                "-b:v", channel.video_bitrate,
                "-preset", channel.preset or "ultrafast",
                "-tune", "zerolatency",
                "-bf", "0",
                "-threads:v", str(settings.cpu_encode_threads),
                "-pix_fmt", "yuv420p",
            ]
            if codec in {"libx264", "h264"}:
                args += ["-profile:v", "main", "-sc_threshold", "0"]
            if filters:
                args += ["-filter_threads", "1", "-vf", ",".join(filters)]
            return args + hevc_tag + common_gop

        if codec in {"h264_nvenc", "hevc_nvenc"}:
            nvenc_preset = "p4" if requested == "h264_nvenc" else "p1"
            args += [
                "-c:v", codec,
                "-b:v", channel.video_bitrate,
                "-preset", nvenc_preset,
                "-tune", "ll",
                "-rc", "cbr",
                "-bf", "0",
                "-pix_fmt", "yuv420p",
            ]
            if codec == "h264_nvenc":
                args += ["-profile:v", "main"]
            if filters:
                args += ["-filter_threads", "1", "-vf", ",".join(filters)]
            return args + hevc_tag + common_gop

        if codec in {"h264_qsv", "hevc_qsv"}:
            qsv_filters = filters + ["format=nv12"]
            args += [
                "-c:v", codec,
                "-b:v", channel.video_bitrate,
                "-preset", "veryfast",
                "-look_ahead", "0",
                "-bf", "0",
                "-vf", ",".join(qsv_filters),
            ]
            return args + hevc_tag + common_gop

        if codec in {"h264_vaapi", "hevc_vaapi"}:
            vaapi_filters = filters + ["format=nv12", "hwupload"]
            args += [
                "-vaapi_device", settings.vaapi_device,
                "-vf", ",".join(vaapi_filters),
                "-c:v", codec,
                "-b:v", channel.video_bitrate,
                "-bf", "0",
            ]
            return args + hevc_tag + common_gop

        raise RuntimeError(f"Unsupported video codec: {codec}")

    def _audio_args(self, channel: Channel, ffmpeg_bin: str | None = None) -> list[str]:
        requested = channel.audio_codec
        codec = "aac" if requested == "auto" else requested
        if codec == "copy":
            return ["-c:a", "copy"]
        encoder = _AUDIO_ENCODERS.get(codec)
        if not encoder:
            raise RuntimeError(f"Unsupported audio codec: {codec}")
        active_binary = ffmpeg_bin or settings.ffmpeg_bin
        if encoder not in self.available_encoders_for(active_binary):
            raise RuntimeError(f"Audio encoder is not available in {active_binary}: {encoder}")

        args = ["-c:a", encoder, "-ac", "2", "-ar", "48000", "-threads:a", "1"]
        if encoder not in {"flac", "pcm_s16le"}:
            args += ["-b:a", channel.audio_bitrate]
        if encoder == "libopus":
            args += ["-application", "audio"]
        return args

    @staticmethod
    def input_urls(channel: Channel) -> list[str]:
        values = [(channel.input_url or "").strip()]
        for line in (channel.backup_inputs or "").replace("\r", "").split("\n"):
            item = line.strip()
            if item and item not in values:
                values.append(item)
        return [item for item in values if item]

    def active_input_url(self, channel: Channel) -> str:
        values = self.input_urls(channel)
        if not values:
            raise RuntimeError("No input URL is configured")
        index = max(0, min(int(channel.active_input_index or 0), len(values) - 1))
        return values[index]

    def source_program_ids(self, channel: Channel) -> list[int | None]:
        urls = self.input_urls(channel)
        try:
            raw = json.loads(channel.source_program_ids or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
        if not isinstance(raw, list):
            raw = []
        values: list[int | None] = []
        for item in raw[:len(urls)]:
            try:
                parsed = int(item) if item not in (None, "") else None
            except (TypeError, ValueError):
                parsed = None
            values.append(parsed if parsed and 1 <= parsed <= 65535 else None)
        while len(values) < len(urls):
            values.append(None)
        if values and values[0] is None and channel.program_id:
            values[0] = int(channel.program_id)
        return values

    def active_program_id(self, channel: Channel) -> int | None:
        values = self.source_program_ids(channel)
        if not values:
            return None
        index = max(0, min(int(channel.active_input_index or 0), len(values) - 1))
        return values[index]

    def _advance_input(self, channel_id: int) -> tuple[int, str] | None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                return None
            values = self.input_urls(channel)
            if len(values) < 2:
                return None
            channel.active_input_index = (int(channel.active_input_index or 0) + 1) % len(values)
            index = channel.active_input_index
            url = values[index]
            db.commit()
        with self._lock:
            metrics = self._live_metrics.get(channel_id)
            if metrics is not None:
                metrics["active_input_index"] = float(index)
        log_event(f"Input failover switched to source #{index + 1}", scope="channel", level="warning", channel_id=channel_id, details=url)
        return index, url

    def _probe_input(self, url: str) -> bool:
        youtube_source = is_youtube_url(url)
        try:
            target = resolve_stream_source(url, force=youtube_source)
        except Exception:
            return False
        cmd = [settings.ffprobe_bin, "-v", "error", "-analyzeduration", "2000000", "-probesize", "4000000"]
        cmd += build_input_args(target, youtube_live=youtube_source)
        cmd += ["-show_entries", "stream=index", "-of", "csv=p=0"]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=max(20, settings.failback_probe_timeout) if youtube_source else settings.failback_probe_timeout, check=False)
            return result.returncode == 0 and bool(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _probe_live_metadata(
        self,
        channel_id: int,
        process: subprocess.Popen[str],
        target: str,
        program_id: int | None,
    ) -> None:
        """Probe source dimensions/FPS once after start without blocking playback.

        FFmpeg progress is dependable for bitrate and speed, but passthrough
        streams often report ``fps=0`` and do not repeat input dimensions in
        stderr.  A single background ffprobe gives the panel stable source
        metadata without probing again on every status refresh.
        """
        result = probe_stream(target, timeout=max(5, min(15, int(settings.ffprobe_timeout))))
        if not result.get("ok"):
            return
        streams = list(result.get("streams") or [])
        if program_id is not None:
            preferred = [
                item for item in streams
                if item.get("type") == "video" and item.get("program_id") == program_id
            ]
        else:
            preferred = []
        video = next(iter(preferred), None) or next(
            (item for item in streams if item.get("type") == "video"), None
        )
        if not video:
            return
        try:
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        try:
            fps = float(video.get("fps") or 0.0)
        except (TypeError, ValueError):
            fps = 0.0
        with self._lock:
            if self._processes.get(channel_id) is not process or process.poll() is not None:
                return
            metrics = self._live_metrics.setdefault(channel_id, {})
            if width > 0 and height > 0:
                metrics["width"] = float(width)
                metrics["height"] = float(height)
            if fps > 0:
                metrics["fps"] = fps
            metrics["metadata_monotonic"] = time.monotonic()

    def _watch_failback(self, channel_id: int, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if channel_id in self._failback_watchers:
                return
            self._failback_watchers.add(channel_id)
        try:
            while True:
                with SessionLocal() as db:
                    channel = db.get(Channel, channel_id)
                    if not channel or not channel.failback_enabled or int(channel.active_input_index or 0) <= 0:
                        return
                    interval = max(10, int(channel.failback_interval or 30))
                    primary = (channel.input_url or "").strip()
                time.sleep(interval)
                with self._lock:
                    if self._processes.get(channel_id) is not process or process.poll() is not None or channel_id in self._intentional_stops:
                        return
                if primary and self._probe_input(primary):
                    with SessionLocal() as db:
                        channel = db.get(Channel, channel_id)
                        if not channel:
                            return
                        channel.active_input_index = 0
                        db.commit()
                    with self._lock:
                        metrics = self._live_metrics.get(channel_id)
                        if metrics is not None:
                            metrics["active_input_index"] = 0.0
                    log_event("Primary input recovered; failing back to source #1", scope="channel", channel_id=channel_id)
                    self.restart(channel_id)
                    return
        finally:
            with self._lock:
                self._failback_watchers.discard(channel_id)

    def build_command(self, channel: Channel) -> list[str]:
        requested_video = (channel.video_codec or "copy").strip().lower()
        source_target = self.active_input_url(channel)
        youtube_source = is_youtube_url(source_target)
        youtube_low_latency_transcode = youtube_source and channel.output_type == "hls"
        resolved_video_codec = (
            self.detect_auto_video_encoder("h264")
            if youtube_low_latency_transcode and requested_video == "copy"
            else self.resolve_video_codec(requested_video)
        )
        ffmpeg_bin = self._ffmpeg_for_codec(resolved_video_codec)
        if not self._binary_exists(ffmpeg_bin):
            raise RuntimeError(f"FFmpeg not found: {ffmpeg_bin}")

        # Explicit NVENC profiles must be present in the selected NVIDIA binary.
        # No channel/session limit is imposed by StreamForge.
        if resolved_video_codec.endswith("_nvenc"):
            if not self._has_nvidia_device():
                raise RuntimeError("NVIDIA NVENC profile selected but no NVIDIA device is available")
            ok, detail = self._probe_encoder(resolved_video_codec, ffmpeg_bin=ffmpeg_bin)
            if not ok:
                raise RuntimeError(f"NVIDIA encoder is not usable with {ffmpeg_bin}: {detail}")

        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "warning",
            "-nostdin",
            "-y",
            "-fflags", "+genpts+discardcorrupt",
            "-thread_queue_size", "1024",
        ]
        # STREAMFORGE_MAIN_HLS_REALTIME_READRATE_V76: pace HTTP HLS media in
        # realtime so long upstream segments cannot fan out as output bursts.
        resolved_target = resolve_stream_source(source_target, force=youtube_source)
        if youtube_source:
            log_event("YouTube source resolved", scope="channel", channel_id=channel.id, details=source_target)
        # STREAMFORGE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016:
        # Historical note: v10.16 disabled HLS pacing for YouTube so a stalled
        # source could catch up. Real production traces later showed YouTube's
        # completed ~5s upstream chunks then fan out into several 1s local HLS
        # files in a burst, leaving the WebPlayer with multi-second dry gaps.
        # STREAMFORGE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021:
        # Pace resolved YouTube HLS at media time just like other HLS inputs.
        # FFmpeg 6.1's -re/readrate behavior still catches up after a blocked
        # read, while steady-state pacing prevents 5-at-once local segment bursts.
        cmd += build_input_args(resolved_target, realtime_hls=True, youtube_live=youtube_source)

        active_program_id = self.active_program_id(channel)
        if active_program_id:
            cmd += [
                "-map", f"0:p:{active_program_id}:v:0?",
                "-map", f"0:p:{active_program_id}:a:0?",
            ]
        else:
            cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]

        if youtube_low_latency_transcode and requested_video == "copy":
            log_event(
                "YouTube low-latency H.264 transcode enabled",
                scope="channel",
                channel_id=channel.id,
                details="copy input upgraded to hardware-aware H.264 with 1s forced keyframes",
            )
        cmd += self._video_args(
            channel,
            force_h264_transcode=youtube_low_latency_transcode,
            hls_segment_time_override=1 if youtube_low_latency_transcode else None,
            resolved_codec=resolved_video_codec,
            ffmpeg_bin=ffmpeg_bin,
        )
        cmd += self._audio_args(channel, ffmpeg_bin=ffmpeg_bin)
        cmd += ["-max_muxing_queue_size", "2048"]

        cmd += [
            "-progress", "pipe:1",
            "-stats_period", str(settings.progress_interval),
            "-nostats",
        ]

        # STREAMFORGE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020:
        # STREAMFORGE_YOUTUBE_TRUE_1S_GOP_OUTPUT_V1022:
        # v10.22 preserves keyframe-safe HLS but no longer relies on YouTube's
        # ~5s source GOP when the channel was configured as video=copy. The video
        # is transcoded through the existing hardware-aware H.264 path with forced
        # 1s keyframes, so the HLS muxer can close genuinely independent 1s files.
        local_hls_time = 1 if youtube_source else max(1, channel.hls_segment_time)
        local_hls_flags = "delete_segments+independent_segments+program_date_time+temp_file+omit_endlist"
        # Keep enough YouTube history for recovery without making the player start
        # farther from the edge; liveSyncDurationCount remains edge-relative.
        local_hls_list_size = 8 if youtube_source else 6
        # STREAMFORGE_MAIN_HLS_BROWSER_SEGMENT_GRACE_V1167:
        # Browser HLS may legally sit 12-25 seconds behind the live edge while
        # its buffer drains/rebuilds. Keeping only ~8 seconds of unreferenced
        # segments let a still-online channel delete a fragment before HLS.js
        # fetched it, producing 404/network-fatal recovery and a visible reconnect.
        # Retain ~30 seconds of segments that have fallen out of the playlist.
        # This changes disk retention only; the playlist size/live-edge latency
        # remains unchanged.
        local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)

        remote_outputs = _remote_output_urls(channel.output_url)
        if channel.output_type == "hls" and not remote_outputs:
            out_dir = self._hls_dir(channel)
            out_dir.mkdir(parents=True, exist_ok=True)
            for old in out_dir.glob("*"):
                if old.is_file():
                    old.unlink(missing_ok=True)
            segment_pattern = str(out_dir / "segment_%06d.ts")
            playlist = str(out_dir / "index.m3u8")
            cmd += [
                "-f", "hls",
                "-hls_segment_type", "mpegts",
                "-hls_time", str(local_hls_time),
                "-hls_list_size", str(local_hls_list_size),
                "-hls_delete_threshold", str(local_hls_delete_threshold),
                "-hls_allow_cache", "0",
                "-hls_start_number_source", "epoch",
                "-hls_flags", local_hls_flags,
                "-hls_segment_filename", segment_pattern,
                playlist,
            ]
        elif channel.output_type in {"hls", "udp", "srt", "http", "http_post", "http_put"}:
            if channel.output_type != "hls" and not remote_outputs:
                raise RuntimeError("At least one remote output URL is required")
            slaves: list[str] = []
            if channel.output_type == "hls":
                out_dir = self._hls_dir(channel)
                out_dir.mkdir(parents=True, exist_ok=True)
                for old in out_dir.glob("*"):
                    if old.is_file():
                        old.unlink(missing_ok=True)
                segment_pattern = _tee_escape_filename(str(out_dir / "segment_%06d.ts"))
                playlist = _tee_escape_filename(str(out_dir / "index.m3u8"))
                hls_opts = (
                    f"[f=hls:hls_segment_type=mpegts:hls_time={local_hls_time}:"
                    f"hls_list_size={local_hls_list_size}:hls_delete_threshold={local_hls_delete_threshold}:hls_allow_cache=0:"
                    "hls_start_number_source=epoch:"
                    f"hls_flags={local_hls_flags}:"
                    f"hls_segment_filename={segment_pattern}]"
                )
                slaves.append(hls_opts + playlist)
            for index, url in enumerate(remote_outputs):
                slaves.append(_remote_tee_slave(
                    url,
                    http_put=channel.output_type == "http_put",
                    onfail_ignore=(channel.output_type == "hls" or index > 0),
                ))
            # STREAMFORGE_MULTI_REMOTE_OUTPUT_TEE_V54: encode once and fan out
            # through the tee muxer. Optional mirrors cannot take the HLS/primary
            # delivery down when one destination disconnects.
            cmd += ["-f", "tee", "-use_fifo", "1", "|".join(slaves)]
        else:
            raise RuntimeError(f"Unsupported output type: {channel.output_type}")
        return cmd

    def _cancel_restart_timer_locked(self, channel_id: int) -> None:
        timer = self._restart_timers.pop(channel_id, None)
        if timer:
            timer.cancel()

    @staticmethod
    def _restart_delay(attempt: int) -> int:
        index = min(max(1, attempt), len(_AUTO_RESTART_DELAYS)) - 1
        return _AUTO_RESTART_DELAYS[index]

    def _schedule_auto_restart(self, channel_id: int, reason: str | None = None) -> None:
        """Schedule one recovery attempt with bounded exponential backoff."""
        with self._lock:
            if channel_id in self._intentional_stops:
                return
            existing = self._restart_timers.get(channel_id)
            if existing and existing.is_alive():
                return
            attempt = self._restart_attempts.get(channel_id, 0) + 1
            self._restart_attempts[channel_id] = attempt
            delay = self._restart_delay(attempt)
            timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))
            timer.daemon = True
            self._restart_timers[channel_id] = timer

        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel or not channel.enabled or not channel.auto_restart:
                with self._lock:
                    self._cancel_restart_timer_locked(channel_id)
                return
            channel.status = "restarting"
            channel.pid = None
            channel.live_bitrate_kbps = 0
            if reason and not channel.last_error:
                channel.last_error = reason[-4000:]
            db.commit()
        timer.start()

    def _run_auto_restart(self, channel_id: int) -> None:
        with self._lock:
            self._restart_timers.pop(channel_id, None)
            if channel_id in self._intentional_stops:
                return

        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel or not channel.enabled or not channel.auto_restart:
                return

        try:
            self.start(channel_id, recovery=True)
        except RuntimeError as exc:
            with self._lock:
                if channel_id in self._intentional_stops:
                    return
            with SessionLocal() as db:
                channel = db.get(Channel, channel_id)
                if channel and channel.enabled and channel.auto_restart:
                    channel.status = "restarting"
                    channel.last_error = str(exc)[-4000:]
                    db.commit()
            self._schedule_auto_restart(channel_id, str(exc))

    def start(self, channel_id: int, *, recovery: bool = False) -> None:
        with self._lock:
            if recovery and channel_id in self._intentional_stops:
                raise RuntimeError("Automatic restart was cancelled by a manual stop")
            self._cancel_restart_timer_locked(channel_id)
            self._intentional_stops.discard(channel_id)
            if not recovery:
                self._restart_attempts.pop(channel_id, None)
            existing = self._processes.get(channel_id)
            if existing and existing.poll() is None:
                return

            with SessionLocal() as db:
                channel = db.get(Channel, channel_id)
                if not channel:
                    raise RuntimeError("Channel not found")
                channel.desired_running = True
                if not recovery:
                    channel.active_input_index = 0
                    db.flush()
                original_profile = self._apply_local_node_encoding_profile(db, channel)
                try:
                    cmd = self.build_command(channel)
                finally:
                    # Overrides only shape this FFmpeg process. They must never
                    # be flushed back into the channel-wide defaults.
                    for field, value in original_profile.items():
                        setattr(channel, field, value)
                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        start_new_session=True,
                    )
                except OSError as exc:
                    channel.status = "error"
                    channel.last_error = str(exc)
                    db.commit()
                    raise RuntimeError(str(exc)) from exc

                self._processes[channel_id] = process
                self._started_monotonic[channel_id] = time.monotonic()
                self._live_metrics[channel_id] = {
                    "bitrate_kbps": 0.0,
                    "speed_x": 0.0,
                    "fps": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "updated_monotonic": time.monotonic(),
                    "activity_monotonic": time.monotonic(),
                    "out_time_us": 0.0,
                    "active_input_index": float(channel.active_input_index or 0),
                }
                self._last_db_metric_write[channel_id] = 0.0
                self._stderr_tails.pop(channel_id, None)
                self._hls_freshness_state.pop(channel_id, None)
                if recovery:
                    # STREAMFORGE_MAIN_AUTO_RESTART_VISIBLE_V54:
                    # Keep a short-lived in-memory recovery marker so the 3s UI
                    # poll cannot miss a fast restarting -> running transition.
                    self._last_auto_restart_monotonic[channel_id] = time.monotonic()
                    self._auto_restart_counts[channel_id] = self._auto_restart_counts.get(channel_id, 0) + 1
                else:
                    self._last_auto_restart_monotonic.pop(channel_id, None)
                channel.status = "starting"
                channel.pid = process.pid
                channel.live_bitrate_kbps = 0
                channel.last_error = None
                db.commit()
                watch_for_stall = bool(channel.auto_restart)
                active_index = int(channel.active_input_index or 0)
                active_target = resolve_stream_source(self.active_input_url(channel))
                active_program_id = self.active_program_id(channel)

            log_event(f"Local FFmpeg started on input #{active_index + 1}", scope="channel", channel_id=channel_id, details={"pid": process.pid})
            threading.Thread(target=self._read_progress, args=(channel_id, process), daemon=True).start()
            error_thread = threading.Thread(
                target=self._read_errors, args=(channel_id, process), daemon=True,
                name=f"streamforge-main-ffmpeg-stderr-{channel_id}",
            )
            error_thread.start()
            threading.Thread(
                target=self._probe_live_metadata,
                args=(channel_id, process, active_target, active_program_id),
                daemon=True,
            ).start()
            threading.Thread(target=self._wait, args=(channel_id, process, error_thread), daemon=True).start()
            if watch_for_stall:
                threading.Thread(
                    target=self._watch_for_stall,
                    args=(channel_id, process),
                    daemon=True,
                ).start()
            with SessionLocal() as db:
                current = db.get(Channel, channel_id)
                if current and current.failback_enabled and int(current.active_input_index or 0) > 0:
                    threading.Thread(target=self._watch_failback, args=(channel_id, process), daemon=True).start()

    def _watch_for_stall(self, channel_id: int, process: subprocess.Popen[str]) -> None:
        """Recover a live FFmpeg process that stops producing progress.

        UDP and some HTTP inputs can leave FFmpeg alive even after the source
        disappears. The regular exit handler cannot help in that situation, so
        this low-frequency watchdog terminates the stalled process. The normal
        wait handler then applies the same bounded auto-restart backoff.
        """
        threshold = float(settings.auto_restart_stall_seconds)
        # STREAMFORGE_YOUTUBE_LIVE_STALL_TOLERANCE_V1016:
        # A live YouTube CDN/proxy can pause briefly while rotating/reloading HLS
        # segments. Give it one bounded recovery window before the normal restart
        # path kills FFmpeg and forces another signed-URL resolution.
        with SessionLocal() as db:
            current = db.get(Channel, channel_id)
            if current and is_youtube_url(self.active_input_url(current)):
                threshold = max(threshold, 60.0)
        interval = float(settings.auto_restart_check_interval)
        while True:
            time.sleep(interval)
            now = time.monotonic()
            with self._lock:
                if self._processes.get(channel_id) is not process:
                    return
                if process.poll() is not None or channel_id in self._intentional_stops:
                    return
                started = self._started_monotonic.get(channel_id, now)
                activity = self._live_metrics.get(channel_id, {}).get("activity_monotonic", started)

            if now - started < threshold or now - float(activity) <= threshold:
                continue

            with SessionLocal() as db:
                channel = db.get(Channel, channel_id)
                if not channel or not channel.enabled or not channel.auto_restart:
                    return
                # STREAMFORGE_MAIN_STALL_GUARD_HLS_FRESHNESS_V1167:
                # A fresh local playlist is stronger evidence of live output than
                # the progress pipe alone.  Keep the healthy FFmpeg PID running
                # and resynchronize the watchdog instead of forcing a restart.
                if self._local_hls_output_fresh(channel):
                    with self._lock:
                        current_metrics = self._live_metrics.get(channel_id)
                        if self._processes.get(channel_id) is process and current_metrics is not None:
                            current_metrics["activity_monotonic"] = now
                    if str(channel.last_error or "").startswith("No media-time progress for"):
                        channel.last_error = None
                        db.commit()
                    continue
                channel.last_error = (
                    f"No media-time progress for {int(now - float(activity))} seconds; "
                    "automatic recovery requested"
                )
                db.commit()

            with self._lock:
                if self._processes.get(channel_id) is not process:
                    return
                if process.poll() is not None or channel_id in self._intentional_stops:
                    return
                try:
                    process.terminate()
                except OSError:
                    pass
            return

    def _read_progress(self, channel_id: int, process: subprocess.Popen[str]) -> None:
        """Read FFmpeg key/value progress and publish live throughput/speed.

        FFmpeg's ``bitrate`` value is often an average and can be ``N/A`` for
        passthrough/HLS during startup.  To keep the channel list useful, the
        panel also derives current output throughput from ``total_size`` over a
        rolling time window.
        """
        if not process.stdout:
            return

        pending_bitrate: int | None = None
        pending_total_size: int | None = None
        pending_speed: float | None = None
        pending_fps: float | None = None
        pending_out_time_us: int | None = None
        last_output_counter: int | None = None
        size_samples: deque[tuple[float, int]] = deque(maxlen=20)

        for raw in process.stdout:
            line = raw.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)

            if key == "bitrate":
                match = _BITRATE_RE.search(value)
                if match:
                    pending_bitrate = max(0, int(round(float(match.group(1)))))
                continue

            if key == "total_size":
                try:
                    pending_total_size = max(0, int(value))
                except ValueError:
                    pending_total_size = None
                continue

            if key == "speed":
                match = _SPEED_RE.search(value)
                if match:
                    try:
                        pending_speed = max(0.0, float(match.group(1)))
                    except ValueError:
                        pending_speed = None
                continue

            if key == "fps":
                try:
                    pending_fps = max(0.0, float(value))
                except ValueError:
                    pending_fps = None
                continue

            if key in {"out_time_us", "out_time_ms"}:
                try:
                    pending_out_time_us = max(0, int(value))
                except ValueError:
                    pending_out_time_us = None
                continue

            if key != "progress":
                continue

            now = time.monotonic()
            calculated_bitrate: int | None = None
            output_activity = False
            output_counter = pending_total_size
            if output_counter is None:
                output_counter = self._process_write_counter(process.pid)
            if output_counter is not None:
                if last_output_counter is not None and output_counter - last_output_counter >= 4096:
                    output_activity = True
                last_output_counter = output_counter
                if size_samples and output_counter < size_samples[-1][1]:
                    size_samples.clear()
                size_samples.append((now, output_counter))
                # Roughly 12 seconds smooths HLS segment write bursts while
                # still reacting quickly to bitrate changes.
                while len(size_samples) > 2 and now - size_samples[0][0] > 12.0:
                    size_samples.popleft()
                if len(size_samples) >= 2:
                    first_time, first_size = size_samples[0]
                    elapsed = now - first_time
                    byte_delta = output_counter - first_size
                    if elapsed >= 0.5 and byte_delta >= 0:
                        calculated_bitrate = max(0, int(round((byte_delta * 8) / (elapsed * 1000))))

            sample_bitrate = calculated_bitrate
            if (sample_bitrate is None or sample_bitrate <= 0) and pending_bitrate is not None:
                sample_bitrate = pending_bitrate

            with self._lock:
                metrics = self._live_metrics.setdefault(
                    channel_id,
                    {
                        "bitrate_kbps": 0.0,
                        "speed_x": 0.0,
                        "fps": 0.0,
                        "width": 0.0,
                        "height": 0.0,
                        "updated_monotonic": now,
                        "activity_monotonic": now,
                        "out_time_us": 0.0,
                        "active_input_index": 0.0,
                    },
                )
                if sample_bitrate is not None:
                    metrics["bitrate_kbps"] = float(sample_bitrate)
                if pending_speed is not None:
                    metrics["speed_x"] = pending_speed
                if pending_fps is not None and pending_fps > 0:
                    metrics["fps"] = pending_fps
                if pending_out_time_us is not None:
                    previous_out_time = float(metrics.get("out_time_us", 0.0))
                    if pending_out_time_us > previous_out_time:
                        metrics["activity_monotonic"] = now
                        metrics["out_time_us"] = float(pending_out_time_us)
                if output_activity:
                    metrics["activity_monotonic"] = now
                metrics["updated_monotonic"] = now
                display_bitrate = int(round(metrics.get("bitrate_kbps", 0.0)))

            # STREAMFORGE_MAIN_RUNTIME_DB_WRITE_THROTTLE_V34:
            # The panel reads bitrate/speed/fps from memory. Persist bitrate at
            # most once every 15s (or immediately for starting->running) so
            # dozens of local FFmpeg progress threads cannot create a SQLite
            # write storm that delays Panel/API requests.
            with self._lock:
                last_persist = float(self._last_db_metric_write.get(channel_id, 0.0))
            persist_due = (now - last_persist) >= 15.0
            if persist_due or value != "continue":
                with SessionLocal() as db:
                    channel = db.get(Channel, channel_id)
                    if not channel:
                        return
                    changed = False
                    if persist_due and channel.live_bitrate_kbps != display_bitrate:
                        channel.live_bitrate_kbps = display_bitrate
                        changed = True
                    target_status = "running" if value == "continue" else channel.status
                    if channel.status != target_status:
                        channel.status = target_status
                        changed = True
                    if changed:
                        db.commit()
                    if persist_due:
                        with self._lock:
                            self._last_db_metric_write[channel_id] = now

            pending_bitrate = None
            pending_total_size = None
            pending_speed = None
            pending_fps = None
            pending_out_time_us = None

    def _read_errors(self, channel_id: int, process: subprocess.Popen[str]) -> None:
        if not process.stderr:
            return
        recent: deque[str] = deque(maxlen=20)
        for raw in process.stderr:
            line = raw.strip()
            if not line:
                continue
            recent.append(line)
            # Keep the tail current while FFmpeg is alive; the exit waiter can
            # consume it immediately even if pipe EOF scheduling lags slightly.
            with self._lock:
                if self._processes.get(channel_id) is process:
                    self._stderr_tails[channel_id] = (process, "\n".join(recent)[-4000:])
            if "Video:" in line:
                dimension = _VIDEO_DIMENSION_RE.search(line)
                fps_match = _VIDEO_FPS_RE.search(line)
                if dimension or fps_match:
                    with self._lock:
                        if self._processes.get(channel_id) is not process:
                            return
                        metrics = self._live_metrics.setdefault(channel_id, {})
                        if dimension:
                            metrics["width"] = float(dimension.group(1))
                            metrics["height"] = float(dimension.group(2))
                        if fps_match:
                            try:
                                metrics["fps"] = max(float(metrics.get("fps", 0.0)), float(fps_match.group(1)))
                            except ValueError:
                                pass
        detail = "\n".join(recent)[-4000:] if recent else ""
        with self._lock:
            if self._processes.get(channel_id) is process:
                self._stderr_tails[channel_id] = (process, detail)

    def _wait(
        self, channel_id: int, process: subprocess.Popen[str],
        error_thread: threading.Thread | None = None,
    ) -> None:
        return_code = process.wait()
        if error_thread is not None and error_thread is not threading.current_thread():
            error_thread.join(timeout=1.0)
        with self._lock:
            is_current_process = self._processes.get(channel_id) is process
            intentional_stop = channel_id in self._intentional_stops
            started = self._started_monotonic.get(channel_id)
            stderr_record = self._stderr_tails.get(channel_id)
            stderr_detail = stderr_record[1] if stderr_record and stderr_record[0] is process else ""
            if is_current_process:
                self._processes.pop(channel_id, None)
                self._started_monotonic.pop(channel_id, None)
                self._live_metrics.pop(channel_id, None)
                self._last_db_metric_write.pop(channel_id, None)
                self._stderr_tails.pop(channel_id, None)
                self._hls_freshness_state.pop(channel_id, None)
        # A channel may already have been restarted with a replacement FFmpeg
        # process. Never let the old wait thread overwrite the new process state.
        if not is_current_process:
            return
        uptime = time.monotonic() - started if started else 0.0
        if uptime >= 60:
            with self._lock:
                self._restart_attempts.pop(channel_id, None)

        should_restart = False
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if channel:
                channel.pid = None
                channel.live_bitrate_kbps = 0
                should_restart = bool(
                    not intentional_stop
                    and channel.enabled
                    and channel.auto_restart
                )
                failure_reason = str(channel.last_error or "").strip()
                if should_restart:
                    channel.status = "restarting"
                    # Preserve an explicit watchdog reason. Otherwise prefer the
                    # complete final FFmpeg stderr tail, even for exit code 0.
                    if not failure_reason.startswith("No media-time progress for"):
                        failure_reason = stderr_detail.strip() or f"FFmpeg exited with code {return_code}"
                    channel.last_error = failure_reason[-4000:]
                else:
                    channel.status = "stopped" if (
                        intentional_stop or return_code in (0, -signal.SIGTERM, -signal.SIGINT)
                    ) else "error"
                    if not intentional_stop and stderr_detail.strip():
                        channel.last_error = stderr_detail.strip()[-4000:]
                db.commit()
                self._clear_local_hls_output(channel)
        log_event(f"Local FFmpeg exited with code {return_code}", scope="channel", level="warning" if should_restart else "info", channel_id=channel_id)
        if should_restart:
            self._advance_input(channel_id)
            self._schedule_auto_restart(channel_id, failure_reason or f"FFmpeg exited with code {return_code}")

    def stop(self, channel_id: int) -> None:
        with self._lock:
            self._intentional_stops.add(channel_id)
            self._cancel_restart_timer_locked(channel_id)
            self._restart_attempts.pop(channel_id, None)
            self._last_auto_restart_monotonic.pop(channel_id, None)
            process = self._processes.get(channel_id)
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._processes.pop(channel_id, None)
            self._started_monotonic.pop(channel_id, None)
            self._live_metrics.pop(channel_id, None)
            self._last_db_metric_write.pop(channel_id, None)
            self._stderr_tails.pop(channel_id, None)
            self._hls_freshness_state.pop(channel_id, None)
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if channel:
                channel.desired_running = False
                channel.status = "stopped"
                channel.pid = None
                channel.live_bitrate_kbps = 0
                db.commit()
                self._clear_local_hls_output(channel)


    def restart(self, channel_id: int) -> None:
        """Stop and immediately start an enabled channel with fresh FFmpeg state."""
        self.stop(channel_id)
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                raise RuntimeError("Channel not found")
            if not channel.enabled:
                channel.last_error = "Channel is disabled"
                db.commit()
                raise RuntimeError("Channel is disabled")
        self.start(channel_id)

    def forget(self, channel_id: int) -> None:
        """Remove recovery bookkeeping after a channel configuration is deleted."""
        with self._lock:
            self._cancel_restart_timer_locked(channel_id)
            self._restart_attempts.pop(channel_id, None)
            self._intentional_stops.discard(channel_id)
            self._last_auto_restart_monotonic.pop(channel_id, None)
            self._auto_restart_counts.pop(channel_id, None)
            self._last_db_metric_write.pop(channel_id, None)


    def runtime_snapshot(self, channel_id: int) -> dict[str, object]:
        # STREAMFORGE_MAIN_RUNTIME_SNAPSHOT_NO_DB_V34:
        # This method is called for every visible channel every few seconds. It
        # must remain lock-local/in-memory and never open SQLite while holding
        # the process lock.
        with self._lock:
            process = self._processes.get(channel_id)
            started = self._started_monotonic.get(channel_id)
            alive = bool(process and process.poll() is None)
            return_code = process.poll() if process else None
            metrics = self._live_metrics.get(channel_id, {})
            updated = metrics.get("updated_monotonic", 0.0)
            stale_after = max(10.0, float(settings.progress_interval) * 4.0)
            metrics_fresh = bool(alive and updated and time.monotonic() - updated <= stale_after)
            bitrate = int(round(metrics.get("bitrate_kbps", 0.0))) if metrics_fresh else 0
            speed = round(float(metrics.get("speed_x", 0.0)), 2) if metrics_fresh else 0.0
            live_fps = round(float(metrics.get("fps", 0.0)), 2) if alive else 0.0
            live_width = int(metrics.get("width", 0) or 0) if alive else 0
            live_height = int(metrics.get("height", 0) or 0) if alive else 0
            restart_mark = self._last_auto_restart_monotonic.get(channel_id)
            restart_age = int(max(0.0, time.monotonic() - restart_mark)) if restart_mark else 0
            return {
                "managed": process is not None,
                "alive": alive,
                "pid": process.pid if process else None,
                "uptime_seconds": int(time.monotonic() - started) if alive and started else 0,
                "return_code": return_code,
                "bitrate_kbps": bitrate,
                "speed_x": speed,
                "fps": live_fps,
                "width": live_width or None,
                "height": live_height or None,
                "metrics_fresh": metrics_fresh,
                "active_input_index": int(metrics.get("active_input_index", 0.0) or 0),
                "auto_restarted_recently": bool(restart_mark is not None and restart_age <= 15),
                "auto_restart_age_seconds": restart_age,
                "restart_count": int(self._auto_restart_counts.get(channel_id, 0)),
            }

    def shutdown_all(self) -> None:
        """Terminate local FFmpeg processes without changing desired state."""
        with self._lock:
            ids = list(self._processes)
            self._intentional_stops.update(ids)
            for timer in self._restart_timers.values():
                timer.cancel()
            self._restart_timers.clear()
        for channel_id in ids:
            with self._lock:
                process = self._processes.get(channel_id)
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
            with self._lock:
                self._processes.pop(channel_id, None)
                self._started_monotonic.pop(channel_id, None)
                self._live_metrics.pop(channel_id, None)
                self._last_db_metric_write.pop(channel_id, None)
        with SessionLocal() as db:
            for channel_id in ids:
                channel = db.get(Channel, channel_id)
                if channel:
                    channel.status = "unknown" if channel.desired_running else "stopped"
                    channel.pid = None
                    channel.live_bitrate_kbps = 0
            db.commit()

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._processes)
        for channel_id in ids:
            self.stop(channel_id)


# STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_V1115:
# The Main HTTP control worker must never own FFmpeg children or their progress/
# restart reader threads.  A dedicated systemd service owns StreamManager and
# exposes the small lifecycle/runtime surface over a private Unix socket.
_MAIN_SUPERVISOR_SOCKET = Path(os.getenv(
    "STREAMFORGE_MAIN_CHANNEL_SUPERVISOR_SOCKET",
    "/var/lib/streamforge/main-channel-supervisor.sock",
))


def _empty_runtime_snapshot() -> dict[str, object]:
    return {
        "managed": False,
        "alive": False,
        "pid": None,
        "uptime_seconds": 0,
        "return_code": None,
        "bitrate_kbps": 0,
        "speed_x": 0.0,
        "fps": 0.0,
        "width": None,
        "height": None,
        "metrics_fresh": False,
        "active_input_index": 0,
        "auto_restarted_recently": False,
        "auto_restart_age_seconds": 0,
        "restart_count": 0,
    }


class MainChannelSupervisorProxy:
    """Thin Main/Public-worker client for the dedicated FFmpeg supervisor.

    Runtime status is fetched in one bulk RPC and micro-cached.  A Dashboard
    with 100+ channels therefore performs one Unix-socket round trip per poll,
    not one round trip per row.  Codec capability probes remain local because
    they do not own long-lived processes or progress threads.
    """

    def __init__(self) -> None:
        self._probe_manager = StreamManager()
        self._lock = threading.RLock()
        self._snapshot_cache_at = 0.0
        self._snapshot_cache: dict[int, dict[str, object]] = {}
        self._supervisor_healthy_at = 0.0

    @staticmethod
    def _rpc(payload: dict[str, object], *, timeout: float = 5.0) -> dict[str, object]:
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(max(0.1, float(timeout)))
                client.connect(str(_MAIN_SUPERVISOR_SOCKET))
                client.sendall(data)
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        raise RuntimeError("Main channel supervisor response is too large")
                    if b"\n" in chunk:
                        break
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(f"Main channel supervisor unavailable: {exc}") from exc
        raw = b"".join(chunks).split(b"\n", 1)[0]
        try:
            response = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Invalid Main channel supervisor response") from exc
        if not isinstance(response, dict):
            raise RuntimeError("Invalid Main channel supervisor response")
        if not bool(response.get("ok", False)):
            raise RuntimeError(str(response.get("error") or "Main channel supervisor command failed"))
        return response

    def supervisor_ping(self, *, timeout: float = 0.4) -> bool:
        try:
            return bool(self._rpc({"action": "ping"}, timeout=timeout).get("pong"))
        except RuntimeError:
            return False

    def _invalidate_runtime_cache(self) -> None:
        with self._lock:
            self._snapshot_cache_at = 0.0
            self._snapshot_cache = {}
            self._supervisor_healthy_at = 0.0

    @staticmethod
    def _fallback_runtime_snapshot(channel_id: int) -> dict[str, object]:
        """Best-effort read-only fallback while the supervisor socket recovers."""
        result = _empty_runtime_snapshot()
        try:
            with SessionLocal() as db:
                row = db.execute(
                    text("SELECT pid,live_bitrate_kbps,status FROM channels WHERE id=:id LIMIT 1"),
                    {"id": int(channel_id)},
                ).first()
            if not row:
                return result
            pid, bitrate, status = row
            pid_value = int(pid or 0)
            alive = False
            if pid_value > 0:
                try:
                    cmdline = Path(f"/proc/{pid_value}/cmdline").read_bytes().replace(b"\x00", b" ").lower()
                    alive = b"ffmpeg" in cmdline
                except OSError:
                    alive = False
            result.update({
                "managed": bool(pid_value),
                "alive": bool(alive),
                "pid": pid_value or None,
                "bitrate_kbps": int(bitrate or 0) if alive else 0,
                "metrics_fresh": False,
                "runtime_status": str(status or "unknown"),
            })
        except Exception:
            pass
        return result

    def _refresh_runtime_cache(self, *, max_age: float = 0.35) -> dict[int, dict[str, object]]:
        now = time.monotonic()
        with self._lock:
            if self._snapshot_cache_at and now - self._snapshot_cache_at <= max_age:
                return self._snapshot_cache
            try:
                response = self._rpc({"action": "snapshots"}, timeout=0.8)
                raw = response.get("snapshots") or {}
                parsed: dict[int, dict[str, object]] = {}
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        try:
                            channel_id = int(key)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(value, dict):
                            parsed[channel_id] = dict(value)
                self._snapshot_cache = parsed
                self._snapshot_cache_at = time.monotonic()
                self._supervisor_healthy_at = self._snapshot_cache_at
            except RuntimeError:
                # Do not keep a failed/empty result for long. The next request
                # can recover as soon as systemd recreates the Unix socket.
                self._snapshot_cache_at = now
            return self._snapshot_cache

    def runtime_snapshot(self, channel_id: int) -> dict[str, object]:
        channel_key = int(channel_id)
        snapshots = self._refresh_runtime_cache()
        item = snapshots.get(channel_key)
        if item is not None:
            return dict(item)
        # A stopped/unmanaged channel is normally absent from the bulk map. A
        # successful bulk refresh proves supervisor health for this cache epoch,
        # so do not open one extra Unix socket for every stopped table row.
        with self._lock:
            healthy = bool(self._supervisor_healthy_at and time.monotonic() - self._supervisor_healthy_at <= 1.0)
        if healthy:
            return _empty_runtime_snapshot()
        return self._fallback_runtime_snapshot(channel_key)

    def start(self, channel_id: int, *, recovery: bool = False) -> None:
        self._rpc({"action": "start", "channel_id": int(channel_id), "recovery": bool(recovery)}, timeout=45.0)
        self._invalidate_runtime_cache()

    def stop(self, channel_id: int) -> None:
        self._rpc({"action": "stop", "channel_id": int(channel_id)}, timeout=20.0)
        self._invalidate_runtime_cache()

    def restart(self, channel_id: int) -> None:
        self._rpc({"action": "restart", "channel_id": int(channel_id)}, timeout=50.0)
        self._invalidate_runtime_cache()

    def forget(self, channel_id: int) -> None:
        self._rpc({"action": "forget", "channel_id": int(channel_id)}, timeout=5.0)
        self._invalidate_runtime_cache()

    def reset_stale_state(self) -> None:
        self._rpc({"action": "reset_stale_state"}, timeout=10.0)
        self._invalidate_runtime_cache()

    def shutdown_all(self) -> None:
        self._rpc({"action": "shutdown_all"}, timeout=45.0)
        self._invalidate_runtime_cache()

    def stop_all(self) -> None:
        self._rpc({"action": "stop_all"}, timeout=45.0)
        self._invalidate_runtime_cache()

    # Capability probing is read-only and intentionally remains in the HTTP
    # worker so Settings pages do not depend on a lifecycle RPC for ffmpeg -encoders.
    def available_encoders(self, refresh: bool = False) -> set[str]:
        return self._probe_manager.available_encoders(refresh=refresh)

    def auto_video_encoder_status(self, family: str = "h264", refresh: bool = False) -> dict[str, object]:
        return self._probe_manager.auto_video_encoder_status(family, refresh=refresh)

    def detect_auto_video_encoder(self, family: str = "h264", refresh: bool = False) -> str:
        return self._probe_manager.detect_auto_video_encoder(family, refresh=refresh)


if str(os.getenv("STREAMFORGE_MAIN_CHANNEL_SUPERVISOR_MODE", "")).strip().lower() in {"1", "true", "yes", "supervisor"}:
    stream_manager: StreamManager | MainChannelSupervisorProxy = StreamManager()
else:
    stream_manager = MainChannelSupervisorProxy()
