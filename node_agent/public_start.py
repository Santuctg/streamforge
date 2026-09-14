#!/usr/bin/env python3
"""Start the Remote Node public plane as its own systemd-owned Gunicorn pool.

STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_V79

The control service must never own high-volume public Gunicorn children.  This
starter preserves the v6.5 hardware-aware worker sizing and Redis safety gate,
but execs Gunicorn as the main process of streamforge-node-public.service.
"""
from __future__ import annotations

import json
import math
import os
import socket
import time
from pathlib import Path


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _cpu_limit() -> int:
    try:
        detected = max(1, len(os.sched_getaffinity(0)))
    except Exception:
        detected = max(1, int(os.cpu_count() or 1))
    try:
        raw = Path('/sys/fs/cgroup/cpu.max').read_text().strip().split()
        if len(raw) == 2 and raw[0] != 'max':
            quota, period = int(raw[0]), int(raw[1])
            if quota > 0 and period > 0:
                detected = min(detected, max(1, math.ceil(quota / period)))
    except Exception:
        pass
    return detected


def _available_memory_mb() -> int:
    available = 1024
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                available = max(256, int(line.split()[1]) // 1024)
                break
    except Exception:
        pass
    try:
        limit_raw = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        current_raw = Path('/sys/fs/cgroup/memory.current').read_text().strip()
        if limit_raw != 'max':
            remaining = max(0, int(limit_raw) - int(current_raw)) // (1024 * 1024)
            if remaining:
                available = min(available, remaining)
    except Exception:
        pass
    return max(256, available)


def _ffmpeg_process_count() -> int:
    count = 0
    try:
        entries = list(Path('/proc').iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        if raw:
            first = raw.split(b'\0', 1)[0].decode('utf-8', errors='ignore')
            if Path(first).name == 'ffmpeg':
                count += 1
    return count



def _desired_channel_count() -> int:
    """Count enabled desired-running channels from the persisted Node catalogue.

    STREAMFORGE_NODE_PUBLIC_DESIRED_FFMPEG_RESERVE_V123
    The public service can start while the dedicated channel supervisor is still
    restoring FFmpeg. state.json already records desired_running, so use it as
    the startup workload floor instead of assuming a transient /proc count of 0
    means there is no encoding load.
    """
    path = Path(os.getenv('STREAMFORGE_NODE_STATE_FILE', '/var/lib/streamforge-node/state.json'))
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return 0
    count = 0
    for item in (raw.get('channels') or [] if isinstance(raw, dict) else []):
        if not isinstance(item, dict) or not bool(item.get('desired_running')):
            continue
        config = item.get('config') or {}
        if isinstance(config, dict) and bool(config.get('enabled', True)):
            count += 1
    return count

def _redis_ready() -> bool:
    enabled = str(os.getenv('STREAMFORGE_NODE_REDIS_ENABLED', '1')).strip().lower() in {'1', 'true', 'yes', 'on'}
    if not enabled:
        return False
    url = str(os.getenv('STREAMFORGE_NODE_REDIS_URL', 'redis://127.0.0.1:6379/1')).strip()
    try:
        import redis
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=0.15,
            socket_timeout=0.15,
            decode_responses=True,
        )
        return bool(client.ping())
    except Exception:
        return False


def _tier_auto_target(cpu: int) -> int:
    cpu = max(1, int(cpu))
    if cpu <= 16:
        return cpu
    if cpu <= 32:
        return max(16, math.ceil(cpu * 0.75))
    if cpu <= 64:
        return max(24, math.ceil(cpu * 0.625))
    return max(32, math.ceil(cpu * 4.0 / 7.0))


def _worker_ceiling(cpu: int, requested: str) -> tuple[int, str]:
    raw = str(os.getenv('STREAMFORGE_NODE_PUBLIC_WORKERS_MAX', 'auto') or 'auto').strip().lower()
    if raw in {'', 'auto'} or (raw == '32' and requested in {'', 'auto'}):
        return max(1, min(cpu, 256)), 'auto-cpu'
    try:
        return max(1, min(int(raw), cpu, 256)), 'configured'
    except ValueError:
        return max(1, min(cpu, 256)), 'invalid-auto-fallback'


def _wait_for_control() -> None:
    """Give the control worker a short head start so FFmpeg reserve is measured."""
    port = _int_env('STREAMFORGE_NODE_CONTROL_BACKEND_PORT', 8810, 1024, 65535)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                time.sleep(0.5)
                return
        except OSError:
            time.sleep(0.25)


def _worker_count() -> tuple[int, dict[str, object]]:
    cpu = _cpu_limit()
    mem_mb = _available_memory_mb()
    requested = str(os.getenv('STREAMFORGE_NODE_PUBLIC_WORKERS', 'auto') or 'auto').strip().lower()
    max_workers, max_mode = _worker_ceiling(cpu, requested)
    reserve_mb = min(4096, max(512, mem_mb // 8))
    usable_mb = max(256, mem_mb - reserve_mb)
    ram_workers = max(1, usable_mb // 256)
    ffmpeg_processes = _ffmpeg_process_count()
    desired_ffmpeg = _desired_channel_count()
    ffmpeg_workload = max(ffmpeg_processes, desired_ffmpeg)
    ffmpeg_cpu_reserve = min(max(0, cpu // 4), max(0, ffmpeg_workload // 2))
    effective_cpu = max(1, cpu - ffmpeg_cpu_reserve)
    auto_target = _tier_auto_target(effective_cpu)
    if requested not in {'', 'auto'}:
        try:
            workers = max(1, min(int(requested), max_workers, ram_workers))
            mode = 'manual'
        except ValueError:
            workers = max(1, min(auto_target, max_workers, ram_workers))
            mode = 'auto-invalid-worker-fallback'
    else:
        workers = max(1, min(auto_target, max_workers, ram_workers))
        mode = 'auto'
    redis_ok = _redis_ready()
    if not redis_ok:
        workers = 1
        mode += '-redis-safe-single'
    try:
        port = _int_env('STREAMFORGE_NODE_PUBLIC_BACKEND_PORT', 8821, 1024, 65535)
    except Exception:
        port = 8821
    state = {
        'workers': workers,
        'mode': mode,
        'redis': redis_ok,
        'reason': f'systemd-{mode}',
        'cpu': cpu,
        'effective_cpu': effective_cpu,
        'mem_available_mb': mem_mb,
        'ram_worker_ceiling': ram_workers,
        'ffmpeg_processes': ffmpeg_processes,
        'desired_ffmpeg': desired_ffmpeg,
        'ffmpeg_workload': ffmpeg_workload,
        'ffmpeg_cpu_reserve': ffmpeg_cpu_reserve,
        'auto_target': auto_target,
        'max_workers': max_workers,
        'max_mode': max_mode,
        'port': port,
        'owner': 'streamforge-node-public.service',
    }
    return workers, state


def main() -> None:
    _wait_for_control()
    workers, state = _worker_count()
    port = int(state['port'])
    os.environ['STREAMFORGE_NODE_MODE'] = 'public'
    os.environ['STREAMFORGE_NODE_PUBLIC_WORKER_COUNT'] = str(workers)
    os.environ['STREAMFORGE_NODE_PUBLIC_MANAGED_BY_SYSTEMD'] = '1'
    print(
        '[StreamForge Node Public] '
        f"mode={state['mode']} cpu={state['cpu']} effective_cpu={state['effective_cpu']} "
        f"ffmpeg={state['ffmpeg_processes']} desired_ffmpeg={state['desired_ffmpeg']} "
        f"workload={state['ffmpeg_workload']} mem_available_mb={state['mem_available_mb']} "
        f"redis={'online' if state['redis'] else 'offline'} auto_target={state['auto_target']} "
        f"max={state['max_workers']} workers={workers} port={port}",
        flush=True,
    )
    try:
        path = Path('/var/lib/streamforge-node/public-workers.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True) + '\n', encoding='utf-8')
    except Exception as exc:
        print(f'[StreamForge Node Public] worker-state write warning: {exc}', flush=True)
    argv = [
        '/usr/bin/gunicorn', 'app:app',
        '--bind', f'127.0.0.1:{port}',
        '--workers', str(workers),
        '--worker-class', 'uvicorn_worker.UvicornWorker',
        '--timeout', '45',
        '--graceful-timeout', '15',
        '--keep-alive', '10',
        '--backlog', '65535',
        '--access-logfile', '-',
        '--error-logfile', '-',
    ]
    os.execvpe(argv[0], argv, os.environ)


if __name__ == '__main__':
    main()
