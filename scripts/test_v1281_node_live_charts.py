#!/usr/bin/env python3
"""v12.81: Node Panel dashboard legend values, loading note and live charts."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1281-node-")
source = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
# Point every Node state path at a temp dir so importing never touches /var/lib.
for name in sorted(set(re.findall(r'getenv\("(STREAMFORGE_NODE_[A-Z_]*(?:ROOT|FILE|PATH|WEBROOT|DIR))"', source))):
    os.environ[name] = os.path.join(d, name.lower())
os.environ["STREAMFORGE_GEOIP_SETTINGS_FILE"] = os.path.join(d, "geo.json")
sys.path.insert(0, str(ROOT / "node_agent"))
import app as node  # noqa: E402

user = node.PanelAccessUser(username="admin", permissions=["channels.view"])
html = node._panel_page(user)

for key in ("cpu", "memory", "input", "output", "users", "total-channels", "online-channels",
            "gpu-usage", "gpu-encoder", "gpu-vram", "gpu-temp"):
    assert f'data-node-legend-value="{key}"' in html, key
assert "STREAMFORGE_NODE_DASHBOARD_LIVE_CHARTS_V1281" in html

scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
dashboard = next(s for s in scripts if "loadHistory" in s)
for snippet in (
    "fetch('/panel/metrics-history.json'",
    "chartMessage('Loading…')",
    "chartMessage('No history yet')",
    "chartMessage('Could not load history')",
    "if(typeof window.streamforgeNodeLiveCharts==='function')window.streamforgeNodeLiveCharts(d);",
    "window.streamforgeNodeLiveCharts=d=>{",
    "cpu_metric_version:2",
    "if(silent&&(document.hidden||zoomed()||pointerInside)){scheduleRefresh();return Promise.resolve()}",
    "Math.max(15,Math.min(1800,sampleSeconds(historyPoints)))*1000",
    "if(seq!==loadSeq)return;",
    "refreshNodeDashboard();setInterval(refreshNodeDashboard,3000);",
):
    assert snippet in dashboard, snippet
# A top-level `history` binding would shadow window.history in the Node page script.
assert not re.search(r"(?<![\w.])let history\b|(?<![\w.])const history\b", dashboard)
assert dashboard.count("fetch('/panel/metrics-history.json'") == 1

if shutil.which("node"):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(dashboard)
    subprocess.run(["node", "--check", handle.name], check=True)
print("PASS: v12.81 Node Panel legend values, loading note, live chart tail and interval history refresh")
