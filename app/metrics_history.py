from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable


class MetricsHistory:
    def __init__(self, path: Path, snapshot: Callable[[], dict[str, Any]], online: Callable[[], int], retention_days: Callable[[], int], sample_interval: Callable[[], int] | None = None, channel_counts: Callable[[], dict[str, int]] | None = None) -> None:
        self.path = path
        self.snapshot = snapshot
        self.online = online
        self.retention_days = retention_days
        self.sample_interval = sample_interval or (lambda: 60)
        self.channel_counts = channel_counts or (lambda: {})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # STREAMFORGE_MAIN_NETWORK_HISTORY_INTERVAL_AVERAGE_V1123:
        # Historical traffic uses total-byte deltas between history samples, so
        # Dashboard request timing cannot turn a 60-second graph point into a
        # random instantaneous 250ms/5s slice.
        self._last_network_sample: tuple[float, int, int, tuple[str, ...]] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="streamforge-metrics-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _history_network_rates(self, network: dict[str, Any], now_mono: float) -> tuple[int, int]:
        try:
            rx_total = max(0, int(network.get("rx_total_bytes") or 0))
            tx_total = max(0, int(network.get("tx_total_bytes") or 0))
        except (TypeError, ValueError):
            rx_total = tx_total = 0
        interfaces = tuple(sorted(str(item) for item in (network.get("interfaces") or []) if str(item)))
        fallback_rx = max(0, int(network.get("rx_bits_per_second") or 0))
        fallback_tx = max(0, int(network.get("tx_bits_per_second") or 0))
        previous = self._last_network_sample
        self._last_network_sample = (now_mono, rx_total, tx_total, interfaces)
        if previous is None:
            # STREAMFORGE_MAIN_NETWORK_HISTORY_STARTUP_CARRY_V1123:
            # system_metrics resets its live-rate baseline during Main startup.
            # Keep the previous history rate for that one bootstrap point instead
            # of drawing a false zero needle while byte counters are already nonzero.
            if fallback_rx <= 0 and fallback_tx <= 0 and (rx_total > 0 or tx_total > 0):
                latest = self.latest()
                try:
                    return (
                        max(0, int(latest.get("download_bps") or 0)),
                        max(0, int(latest.get("upload_bps") or 0)),
                    )
                except (TypeError, ValueError):
                    pass
            return fallback_rx, fallback_tx
        previous_time, previous_rx, previous_tx, previous_interfaces = previous
        elapsed = now_mono - previous_time
        if elapsed <= 0 or interfaces != previous_interfaces:
            return fallback_rx, fallback_tx
        rx_delta = rx_total - previous_rx
        tx_delta = tx_total - previous_tx
        if rx_delta < 0 or tx_delta < 0:
            # Interface/counter reset: rebase without writing a false zero needle.
            return fallback_rx, fallback_tx
        return int(rx_delta * 8.0 / elapsed), int(tx_delta * 8.0 / elapsed)

    def _run(self) -> None:
        next_prune = 0.0
        # STREAMFORGE_METRICS_IMMEDIATE_FIRST_SAMPLE_V1024:
        # Record one fresh sample immediately after service startup. Dashboard
        # summaries must not keep showing the previous release's status-based
        # channel count for a full sampling interval after an update/restart.
        while not self._stop.is_set():
            try:
                metrics = self.snapshot()
                network = metrics.get("network") or {}
                history_rx_bps, history_tx_bps = self._history_network_rates(network, time.monotonic())
                gpu = metrics.get("gpu") or {}
                channels = self.channel_counts()
                row = {
                    "time": int(time.time()),
                    "cpu": float((metrics.get("cpu") or {}).get("percent") or 0),
                    "memory": float((metrics.get("memory") or {}).get("percent") or 0),
                    "download_bps": history_rx_bps,
                    "upload_bps": history_tx_bps,
                    "online_users": max(0, int(self.online() or 0)),
                    "total_channels": max(0, int(channels.get("total") or 0)),
                    "online_channels": max(0, int(channels.get("online") or 0)),
                    # STREAMFORGE_MAIN_GPU_HISTORY_V1069
                    "gpu_available": bool(gpu.get("available")),
                    "gpu_usage": float(gpu.get("usage_percent") or 0),
                    "gpu_encoder": float(gpu.get("encoder_percent") or 0),
                    "gpu_memory": float(gpu.get("memory_percent") or 0),
                    "gpu_temp_c": float(gpu.get("temperature_c") or 0),
                    "gpu_power_w": float(gpu.get("power_w") or 0),
                    "gpu_active_streams": max(0, int(gpu.get("active_streams") or 0)),
                }
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                if time.time() >= next_prune:
                    self.prune()
                    next_prune = time.time() + 3600
            except Exception:
                pass
            interval = float(max(5, min(3600, int(self.sample_interval() or 60))))
            if self._stop.wait(interval):
                break

    def prune(self) -> None:
        days = max(1, min(3650, int(self.retention_days() or 30)))
        cutoff = int(time.time()) - days * 86400
        rows = [item for item in self.read(raw=True) if int(item.get("time") or 0) >= cutoff]
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("".join(json.dumps(item, separators=(",", ":")) + "\n" for item in rows), encoding="utf-8")
        temporary.replace(self.path)

    # STREAMFORGE_MAIN_METRICS_TAIL_READ_V1113:
    # Dashboard needs only the newest sample and graph requests usually need a
    # recent time window. Never parse the entire retained JSONL history on a
    # normal Overview navigation.
    def latest(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size <= 0:
                    return {}
                block = min(size, 65536)
                handle.seek(size - block)
                data = handle.read(block)
            for raw in reversed(data.splitlines()):
                if not raw.strip():
                    continue
                try:
                    item = json.loads(raw.decode("utf-8", errors="replace"))
                except (ValueError, TypeError, UnicodeDecodeError):
                    continue
                if isinstance(item, dict):
                    return item
        except OSError:
            pass
        return {}

    def read_since(self, cutoff: int, *, max_points: int = 1440) -> list[dict[str, Any]]:
        """Read a chronological recent window by scanning the JSONL file backwards."""
        cutoff = max(0, int(cutoff or 0))
        rows_rev: list[dict[str, Any]] = []
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, 2)
                position = handle.tell()
                remainder = b""
                done = False
                while position > 0 and not done:
                    size = min(262144, position)
                    position -= size
                    handle.seek(position)
                    chunk = handle.read(size) + remainder
                    parts = chunk.split(b"\n")
                    remainder = parts[0]
                    for raw in reversed(parts[1:]):
                        if not raw.strip():
                            continue
                        try:
                            item = json.loads(raw.decode("utf-8", errors="replace"))
                        except (ValueError, TypeError, UnicodeDecodeError):
                            continue
                        if not isinstance(item, dict):
                            continue
                        timestamp = int(item.get("time") or 0)
                        if timestamp < cutoff:
                            done = True
                            break
                        rows_rev.append(item)
                if not done and remainder.strip():
                    try:
                        item = json.loads(remainder.decode("utf-8", errors="replace"))
                        if isinstance(item, dict) and int(item.get("time") or 0) >= cutoff:
                            rows_rev.append(item)
                    except (ValueError, TypeError, UnicodeDecodeError):
                        pass
        except OSError:
            return []
        rows = list(reversed(rows_rev))
        limit = max(1, int(max_points or 1440))
        if len(rows) <= limit:
            return rows
        step = max(1, (len(rows) + limit - 1) // limit)
        sampled = rows[::step]
        if sampled and sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])
        return sampled[-limit:]

    def read(self, *, raw: bool = False, max_points: int = 1440) -> list[dict[str, Any]]:
        try:
            rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError, TypeError):
            rows = []
        if raw or len(rows) <= max_points:
            return rows
        step = max(1, (len(rows) + max_points - 1) // max_points)
        return rows[::step][-max_points:]
