#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import resource
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def sh(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def read_int(path: str, default: int = -1) -> int:
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return default


def unit_property(unit: str, prop: str) -> str:
    rc, out = sh("systemctl", "show", unit, f"--property={prop}", "--value")
    return out.strip() if rc == 0 else ""


def nginx_value(pattern: str) -> int:
    rc, out = sh("nginx", "-T")
    if rc != 0:
        return -1
    import re
    m = re.search(pattern, out)
    return int(m.group(1)) if m else -1


def fetch(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= int(r.status) < 300
    except Exception:
        return False


def mini_probe(url: str, concurrency: int, requests: int) -> dict:
    started = time.perf_counter()
    ok = 0
    fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(fetch, url) for _ in range(requests)]
        for fut in concurrent.futures.as_completed(futs):
            if fut.result():
                ok += 1
            else:
                fail += 1
    elapsed = max(0.0001, time.perf_counter() - started)
    return {
        "url": url,
        "concurrency": concurrency,
        "requests": requests,
        "success": ok,
        "failed": fail,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(requests / elapsed, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="StreamForge high-concurrency readiness audit")
    ap.add_argument("--probe", action="store_true", help="Run a bounded local HTTP probe against /health")
    ap.add_argument("--probe-concurrency", type=int, default=200)
    ap.add_argument("--probe-requests", type=int, default=2000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    checks = []

    def add(name: str, value, expected: str, ok: bool, note: str = ""):
        checks.append({
            "name": name,
            "value": value,
            "expected": expected,
            "ok": bool(ok),
            "note": note,
        })

    # Kernel / process ceilings.
    file_max = read_int("/proc/sys/fs/file-max")
    somaxconn = read_int("/proc/sys/net/core/somaxconn")
    netdev = read_int("/proc/sys/net/core/netdev_max_backlog")
    syn = read_int("/proc/sys/net/ipv4/tcp_max_syn_backlog")

    add("fs.file-max", file_max, ">= 2,097,152", file_max >= 2097152)
    add("net.core.somaxconn", somaxconn, ">= 65,535", somaxconn >= 65535)
    add("net.core.netdev_max_backlog", netdev, ">= 65,535", netdev >= 65535)
    add("net.ipv4.tcp_max_syn_backlog", syn, ">= 65,535", syn >= 65535)

    # Nginx limits.
    wc = nginx_value(r"worker_connections\s+(\d+)\s*;")
    wr = nginx_value(r"worker_rlimit_nofile\s+(\d+)\s*;")
    add("nginx worker_connections", wc, ">= 65,535", wc >= 65535)
    add("nginx worker_rlimit_nofile", wr, ">= 1,048,576", wr >= 1048576)

    # systemd service limits.
    main_nofile = unit_property("streamforge.service", "LimitNOFILE")
    nginx_nofile = unit_property("nginx.service", "LimitNOFILE")
    try:
        main_nofile_i = int(main_nofile)
    except Exception:
        main_nofile_i = -1
    try:
        nginx_nofile_i = int(nginx_nofile)
    except Exception:
        nginx_nofile_i = -1
    add("streamforge LimitNOFILE", main_nofile_i, ">= 1,048,576", main_nofile_i >= 1048576)
    add("nginx LimitNOFILE", nginx_nofile_i, ">= 1,048,576", nginx_nofile_i >= 1048576)

    main_tasks = unit_property("streamforge.service", "TasksMax")
    nginx_tasks = unit_property("nginx.service", "TasksMax")
    add("streamforge TasksMax", main_tasks or "unknown", "infinity", main_tasks == "infinity")
    add("nginx TasksMax", nginx_tasks or "unknown", "infinity", nginx_tasks == "infinity")

    # Active service state.
    for unit in ("streamforge.service", "nginx.service"):
        rc, _ = sh("systemctl", "is-active", "--quiet", unit)
        add(f"{unit} active", "yes" if rc == 0 else "no", "yes", rc == 0)

    # Gunicorn command-line markers.
    rc, main_cmd = sh("systemctl", "show", "streamforge.service", "--property=ExecStart", "--value")
    add("Gunicorn backlog", "65535" if "--backlog 65535" in main_cmd else "missing",
        "65535", "--backlog 65535" in main_cmd)
    add("Gunicorn access log disabled", "yes" if "--access-logfile -" not in main_cmd else "no",
        "yes", "--access-logfile -" not in main_cmd)
    add("Gunicorn graceful reload", "configured" if Path("/etc/systemd/system/streamforge.service").exists() and "ExecReload=/bin/kill -HUP $MAINPID" in Path("/etc/systemd/system/streamforge.service").read_text(errors="ignore") else "missing",
        "configured",
        Path("/etc/systemd/system/streamforge.service").exists() and "ExecReload=/bin/kill -HUP $MAINPID" in Path("/etc/systemd/system/streamforge.service").read_text(errors="ignore"))

    # Nginx generated site markers.
    site = Path("/etc/nginx/sites-available/streamforge")
    site_text = site.read_text(encoding="utf-8", errors="ignore") if site.exists() else ""
    add("Nginx Main upstream keepalive", "512" if "keepalive 512;" in site_text else "missing",
        "512", "keepalive 512;" in site_text)
    add("Nginx request buffering", "off" if "proxy_request_buffering off;" in site_text else "not found",
        "off", "proxy_request_buffering off;" in site_text)

    # Capacity math: theoretical FD ceiling, not a throughput promise.
    cpu = os.cpu_count() or 1
    mem_bytes = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_bytes = int(line.split()[1]) * 1024
                break
    except Exception:
        pass

    theoretical_nginx_connections = max(0, wc) * max(1, cpu)
    fd_ceiling = min(x for x in [file_max, nginx_nofile_i if nginx_nofile_i > 0 else file_max] if x > 0)
    theoretical_ceiling = min(theoretical_nginx_connections, fd_ceiling)
    target_50k = theoretical_ceiling >= 50000

    result = {
        "cpu_count": cpu,
        "memory_gib": round(mem_bytes / (1024**3), 2) if mem_bytes else None,
        "theoretical_connection_ceiling": theoretical_ceiling,
        "target_50k_fd_listener_headroom": target_50k,
        "checks": checks,
    }

    if args.probe:
        c = max(1, min(1000, int(args.probe_concurrency)))
        r = max(c, min(20000, int(args.probe_requests)))
        result["probe"] = mini_probe("http://127.0.0.1:8800/health", c, r)

    failed = [x for x in checks if not x["ok"]]
    result["failed_checks"] = len(failed)
    result["status"] = "PASS" if not failed else "WARN"

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("StreamForge High-Concurrency Readiness Audit")
        print("=" * 48)
        print(f"CPU cores: {result['cpu_count']}")
        print(f"Memory: {result['memory_gib']} GiB")
        print(f"Theoretical listener/FD ceiling: {result['theoretical_connection_ceiling']:,}")
        print(f"50k FD/listener headroom: {'YES' if result['target_50k_fd_listener_headroom'] else 'NO'}")
        print()
        for item in checks:
            mark = "OK" if item["ok"] else "WARN"
            print(f"[{mark}] {item['name']}: {item['value']} (target {item['expected']})")
        if "probe" in result:
            p = result["probe"]
            print()
            print("Bounded local /health probe:")
            print(f"  concurrency={p['concurrency']} requests={p['requests']} success={p['success']} failed={p['failed']}")
            print(f"  elapsed={p['elapsed_seconds']}s rps={p['requests_per_second']}")
        print()
        print(f"Result: {result['status']} ({result['failed_checks']} warning(s))")
        print("Note: this verifies ceilings/config and a bounded local control-plane probe.")
        print("It does not prove 50k real HLS viewers; that requires distributed load generation against real playback paths.")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
