#!/usr/bin/env python3
"""v12.80: Overview charts follow the live metrics poll and re-read history per sample interval."""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
dash_js = (ROOT / "app/static/dashboard.js").read_text(encoding="utf-8")

# The 5s system-metrics poll publishes its payload; no new request is added.
assert "document.dispatchEvent(new CustomEvent('streamforge:system-metrics', {detail: data}));" in app_js
assert app_js.count("streamforge:system-metrics") == 1
assert "setInterval(refreshSystemMetrics, 5000);\n    }, 1000);" in app_js

# The charts extend their last point from that event and re-read history on a timer.
for snippet in (
    "STREAMFORGE_MAIN_DASHBOARD_LIVE_CHARTS_V1280",
    "document.addEventListener('streamforge:system-metrics'",
    "liveTail={...last,time:Math.floor(Date.now()/1000)",
    "download_bps:Number(network.rx_bits_per_second)||0",
    "load(historyHours,true)",
    # silent refreshes pause in background tabs, while zoomed and under the pointer
    "if(silent&&(document.hidden||zoomed()||pointerInside)){scheduleRefresh();return Promise.resolve()}",
    # one refresh per sample interval, bounded to 15 s .. 30 min
    "Math.max(15,Math.min(1800,sampleSeconds(history)))*1000",
    # stale responses from an earlier span never overwrite a newer one
    "if(seq!==loadSeq)return;",
    # a silent refresh keeps the user's zoom and never shows the loading note
    "if(!silent){sharedZoomStart=0;sharedZoomEnd=Infinity}",
    "if(!silent)chartMessage('Loading…')",
):
    assert snippet in dash_js, snippet
assert dash_js.count("fetch(root.dataset.endpoint") == 1, "history is fetched from one place"

if shutil.which("node"):
    for script in ("app/static/app.js", "app/static/dashboard.js"):
        subprocess.run(["node", "--check", str(ROOT / script)], check=True)
print("PASS: v12.80 live chart tail from the existing poll, interval history refresh, pause rules and syntax")
