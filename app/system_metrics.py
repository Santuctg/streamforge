from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_NET_DEV = Path("/proc/net/dev")
_PROC_UPTIME = Path("/proc/uptime")


def _read_cpu_times() -> tuple[int, int]:
    try:
        first_line = _PROC_STAT.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        fields = [int(value) for value in first_line.split()[1:]]
        if len(fields) < 4:
            return 0, 0
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return sum(fields), idle
    except (OSError, ValueError, IndexError):
        return 0, 0


def _read_memory() -> dict[str, float | int]:
    values: dict[str, int] = {}
    try:
        for line in _PROC_MEMINFO.read_text(encoding="utf-8", errors="replace").splitlines():
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts:
                values[key] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass

    total = max(0, values.get("MemTotal", 0))
    available = values.get("MemAvailable")
    if available is None:
        available = (
            values.get("MemFree", 0)
            + values.get("Buffers", 0)
            + values.get("Cached", 0)
            + values.get("SReclaimable", 0)
            - values.get("Shmem", 0)
        )
    available = max(0, min(total, available or 0))
    used = max(0, total - available)
    percent = (used / total * 100.0) if total else 0.0
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "percent": round(percent, 1),
    }


def _read_network_totals(selected: set[str] | None = None) -> tuple[int, int, list[str]]:
    rx_total = 0
    tx_total = 0
    interfaces: list[str] = []
    try:
        lines = _PROC_NET_DEV.read_text(encoding="utf-8", errors="replace").splitlines()[2:]
        for line in lines:
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            interface = name.strip()
            if not interface or interface == "lo":
                continue
            if selected is not None and interface not in selected:
                continue
            fields = raw.split()
            if len(fields) < 9:
                continue
            rx_total += int(fields[0])
            tx_total += int(fields[8])
            interfaces.append(interface)
    except (OSError, ValueError):
        pass
    return rx_total, tx_total, sorted(interfaces)


def available_network_interfaces() -> list[str]:
    """Return non-loopback interfaces currently reported by Linux."""
    return _read_network_totals()[2]


# STREAMFORGE_DASHBOARD_DISK_USAGE_V96:
def _read_disk() -> dict[str, float | int | str]:
    target = Path(os.getenv("STREAMFORGE_HLS_ROOT", "/var/lib/streamforge/hls"))
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
        total = max(0, int(usage.total))
        used = max(0, int(usage.used))
        free = max(0, int(usage.free))
    except OSError:
        total = used = free = 0
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "percent": round((used / total * 100.0) if total else 0.0, 1),
        "path": str(probe),
    }


def _read_uptime() -> int:
    try:
        return max(0, int(float(_PROC_UPTIME.read_text(encoding="utf-8").split()[0])))
    except (OSError, ValueError, IndexError):
        return 0


def _read_load_average() -> tuple[float, float, float]:
    try:
        one, five, fifteen = os.getloadavg()
        return round(one, 2), round(five, 2), round(fifteen, 2)
    except (AttributeError, OSError):
        return 0.0, 0.0, 0.0



# STREAMFORGE_GPU_BACKGROUND_METRICS_V1069:
# GPU discovery/sampling is intentionally isolated from dashboard request paths.
# The sampler runs in one low-rate background thread and snapshot() only copies
# the most recent cached result. NVIDIA uses nvidia-smi when available; Intel/
# AMD fall back to Linux DRM/sysfs metrics without adding Python dependencies.
class GPUMetricsSampler:
    def __init__(self, interval_seconds: float = 5.0) -> None:
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device_names: dict[str, str] = {}
        self._snapshot: dict[str, Any] = self._empty_snapshot()

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return {
            "available": False,
            "count": 0,
            "vendor": "",
            "name": "",
            "encoder": "",
            "usage_percent": None,
            "encoder_percent": None,
            "memory_used_bytes": 0,
            "memory_total_bytes": 0,
            "memory_percent": None,
            "temperature_c": None,
            "power_w": None,
            "active_streams": 0,
            "devices": [],
            "captured_at": 0.0,
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            text = str(value).strip()
            if not text or text.lower() in {"n/a", "na", "not supported", "[not supported]"}:
                return None
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_number(path: Path, divisor: float = 1.0) -> float | None:
        try:
            return float(path.read_text(encoding="utf-8", errors="replace").strip()) / divisor
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _active_gpu_streams() -> int:
        count = 0
        markers = (
            "_nvenc", "_qsv", "_vaapi", "h264_amf", "hevc_amf",
            "-hwaccel cuda", "-hwaccel qsv", "-vaapi_device",
            "hwupload_cuda", "hwupload=", "scale_cuda", "scale_qsv", "scale_vaapi",
        )
        try:
            proc_root = Path("/proc")
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore").lower()
                except OSError:
                    continue
                if "ffmpeg" not in raw:
                    continue
                if any(marker in raw for marker in markers):
                    count += 1
        except OSError:
            pass
        return count

    @staticmethod
    def _aggregate(devices: list[dict[str, Any]], active_streams: int) -> dict[str, Any]:
        if not devices:
            result = GPUMetricsSampler._empty_snapshot()
            result["captured_at"] = time.time()
            return result

        def avg(field: str) -> float | None:
            values = [float(item[field]) for item in devices if item.get(field) is not None]
            return round(sum(values) / len(values), 1) if values else None

        def maximum(field: str) -> float | None:
            values = [float(item[field]) for item in devices if item.get(field) is not None]
            return round(max(values), 1) if values else None

        total_memory = sum(max(0, int(item.get("memory_total_bytes") or 0)) for item in devices)
        used_memory = sum(max(0, int(item.get("memory_used_bytes") or 0)) for item in devices)
        power_values = [float(item["power_w"]) for item in devices if item.get("power_w") is not None]
        vendors = sorted({str(item.get("vendor") or "").strip() for item in devices if str(item.get("vendor") or "").strip()})
        encoders = sorted({str(item.get("encoder") or "").strip() for item in devices if str(item.get("encoder") or "").strip()})
        first_name = str(devices[0].get("name") or "GPU")
        name = first_name if len(devices) == 1 else f"{len(devices)} GPUs · {first_name}"
        return {
            "available": True,
            "count": len(devices),
            "vendor": vendors[0] if len(vendors) == 1 else "Mixed",
            "name": name,
            "encoder": "/".join(encoders),
            "usage_percent": avg("usage_percent"),
            "encoder_percent": avg("encoder_percent"),
            "memory_used_bytes": used_memory,
            "memory_total_bytes": total_memory,
            "memory_percent": round(used_memory / total_memory * 100.0, 1) if total_memory else None,
            "temperature_c": maximum("temperature_c"),
            "power_w": round(sum(power_values), 1) if power_values else None,
            "active_streams": max(0, int(active_streams)),
            "devices": devices,
            "captured_at": time.time(),
        }

    def _sample_nvidia(self) -> list[dict[str, Any]]:
        binary = shutil.which("nvidia-smi")
        if not binary:
            return []
        queries = [
            (
                "index,name,utilization.gpu,utilization.encoder,memory.used,memory.total,temperature.gpu,power.draw",
                True,
            ),
            (
                "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                False,
            ),
        ]
        for query, extended in queries:
            try:
                result = subprocess.run(
                    [binary, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                    text=True,
                    capture_output=True,
                    timeout=2.5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return []
            if result.returncode != 0:
                continue
            devices: list[dict[str, Any]] = []
            for line in result.stdout.splitlines():
                parts = [item.strip() for item in line.split(",")]
                expected = 8 if extended else 6
                if len(parts) < expected:
                    continue
                if extended:
                    index, name, usage, encoder, mem_used, mem_total, temperature, power = parts[:8]
                else:
                    index, name, usage, mem_used, mem_total, temperature = parts[:6]
                    encoder, power = "", ""
                used_mib = self._number(mem_used) or 0.0
                total_mib = self._number(mem_total) or 0.0
                devices.append({
                    "id": str(index),
                    "vendor": "NVIDIA",
                    "name": name or f"NVIDIA GPU {index}",
                    "encoder": "NVENC",
                    "usage_percent": self._number(usage),
                    "encoder_percent": self._number(encoder),
                    "memory_used_bytes": int(max(0.0, used_mib) * 1024 * 1024),
                    "memory_total_bytes": int(max(0.0, total_mib) * 1024 * 1024),
                    "temperature_c": self._number(temperature),
                    "power_w": self._number(power),
                })
            if devices:
                return devices
        return []

    def _sysfs_name(self, card: Path, vendor_name: str) -> str:
        cached = self._device_names.get(card.name)
        if cached:
            return cached
        device = card / "device"
        slot = ""
        driver = ""
        try:
            for line in (device / "uevent").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PCI_SLOT_NAME="):
                    slot = line.split("=", 1)[1].strip()
                elif line.startswith("DRIVER="):
                    driver = line.split("=", 1)[1].strip()
        except OSError:
            pass
        name = ""
        lspci = shutil.which("lspci")
        if lspci and slot:
            try:
                result = subprocess.run([lspci, "-s", slot], text=True, capture_output=True, timeout=1.5, check=False)
                text = result.stdout.strip()
                if ": " in text:
                    name = text.split(": ", 1)[1].strip()
            except (OSError, subprocess.SubprocessError):
                pass
        if not name:
            suffix = f" ({driver})" if driver else ""
            name = f"{vendor_name} GPU{suffix}"
        self._device_names[card.name] = name
        return name

    def _sample_sysfs(self) -> list[dict[str, Any]]:
        drm = Path("/sys/class/drm")
        if not drm.is_dir():
            return []
        vendors = {
            "0x10de": ("NVIDIA", "NVENC"),
            "0x8086": ("Intel", "QSV/VAAPI"),
            "0x1002": ("AMD", "VAAPI"),
        }
        devices: list[dict[str, Any]] = []
        for card in sorted(drm.glob("card[0-9]*")):
            if not re.fullmatch(r"card\d+", card.name):
                continue
            device = card / "device"
            try:
                vendor_id = (device / "vendor").read_text(encoding="utf-8", errors="replace").strip().lower()
            except OSError:
                continue
            if vendor_id not in vendors:
                continue
            vendor_name, encoder_name = vendors[vendor_id]
            usage = self._read_number(device / "gpu_busy_percent")
            memory_used = self._read_number(device / "mem_info_vram_used")
            memory_total = self._read_number(device / "mem_info_vram_total")
            temperature = None
            power = None
            for hwmon in sorted((device / "hwmon").glob("hwmon*")) if (device / "hwmon").is_dir() else []:
                if temperature is None:
                    temperature = self._read_number(hwmon / "temp1_input", 1000.0)
                if power is None:
                    power = self._read_number(hwmon / "power1_average", 1_000_000.0)
            devices.append({
                "id": card.name,
                "vendor": vendor_name,
                "name": self._sysfs_name(card, vendor_name),
                "encoder": encoder_name,
                "usage_percent": usage,
                "encoder_percent": None,
                "memory_used_bytes": int(max(0.0, memory_used or 0.0)),
                "memory_total_bytes": int(max(0.0, memory_total or 0.0)),
                "temperature_c": temperature,
                "power_w": power,
            })
        return devices

    def _sample(self) -> dict[str, Any]:
        devices = self._sample_nvidia()
        if not devices:
            devices = self._sample_sysfs()
        return self._aggregate(devices, self._active_gpu_streams())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                fresh = self._sample()
                with self._lock:
                    self._snapshot = fresh
            except Exception:
                pass
            if self._stop.wait(self.interval_seconds):
                break

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="streamforge-gpu-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._snapshot)
            result["devices"] = [dict(item) for item in self._snapshot.get("devices", []) if isinstance(item, dict)]
            return result


class SystemMetricsSampler:
    """Low-overhead Linux host metrics sourced from /proc.

    CPU and network rates are calculated from deltas between calls. Calls made
    too close together reuse the previous rate so concurrent requests do not
    produce spikes.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._selected_interfaces: set[str] | None = None
        self._last_cpu_total, self._last_cpu_idle = _read_cpu_times()
        self._last_rx, self._last_tx, self._interfaces = _read_network_totals()
        self._last_time = time.monotonic()
        self._cpu_percent = 0.0
        self._rx_bits_per_second = 0.0
        self._tx_bits_per_second = 0.0
        self._gpu = GPUMetricsSampler()

    def start(self) -> None:
        self._gpu.start()

    def stop(self) -> None:
        self._gpu.stop()

    def set_network_interfaces(self, interfaces: list[str] | tuple[str, ...] | set[str] | None) -> None:
        """Select NICs to aggregate; None or an empty collection means all."""
        cleaned = {str(item).strip() for item in (interfaces or []) if str(item).strip() and str(item).strip() != "lo"}
        selected = cleaned or None
        with self._lock:
            self._selected_interfaces = selected
            self._last_rx, self._last_tx, self._interfaces = _read_network_totals(selected)
            self._last_time = time.monotonic()
            self._rx_bits_per_second = 0.0
            self._tx_bits_per_second = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            cpu_total, cpu_idle = _read_cpu_times()
            rx_total, tx_total, interfaces = _read_network_totals(self._selected_interfaces)

            if elapsed >= 0.25:
                total_delta = cpu_total - self._last_cpu_total
                idle_delta = cpu_idle - self._last_cpu_idle
                if total_delta > 0:
                    busy_delta = max(0, total_delta - max(0, idle_delta))
                    self._cpu_percent = max(0.0, min(100.0, busy_delta / total_delta * 100.0))

                # STREAMFORGE_MAIN_NETWORK_COUNTER_CHURN_GUARD_V1123:
                # With "all interfaces" Linux may add/remove veth/bridge devices.
                # Summed /proc/net/dev counters can then move backwards even though
                # the physical NIC never stopped.  A counter reset/interface-set
                # change is a baseline event, not a real zero-throughput sample.
                interfaces_changed = tuple(interfaces) != tuple(self._interfaces)
                rx_delta = rx_total - self._last_rx
                tx_delta = tx_total - self._last_tx
                if not interfaces_changed and rx_delta >= 0:
                    self._rx_bits_per_second = rx_delta * 8.0 / elapsed
                if not interfaces_changed and tx_delta >= 0:
                    self._tx_bits_per_second = tx_delta * 8.0 / elapsed
                self._last_cpu_total = cpu_total
                self._last_cpu_idle = cpu_idle
                self._last_rx = rx_total
                self._last_tx = tx_total
                self._last_time = now
                self._interfaces = interfaces

            load_1, load_5, load_15 = _read_load_average()
            return {
                "cpu": {
                    "percent": round(self._cpu_percent, 1),
                    "cores": os.cpu_count() or 1,
                    "load_1": load_1,
                    "load_5": load_5,
                    "load_15": load_15,
                },
                "memory": _read_memory(),
                "disk": _read_disk(),
                "network": {
                    "rx_bits_per_second": round(self._rx_bits_per_second),
                    "tx_bits_per_second": round(self._tx_bits_per_second),
                    "rx_total_bytes": rx_total,
                    "tx_total_bytes": tx_total,
                    "interfaces": self._interfaces,
                },
                "gpu": self._gpu.snapshot(),
                "uptime_seconds": _read_uptime(),
                "captured_at": time.time(),
            }


system_metrics = SystemMetricsSampler()
