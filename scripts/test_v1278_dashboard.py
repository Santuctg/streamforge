#!/usr/bin/env python3
"""v12.78: complete Overview cards on first paint, chart legend values, SSH hls.js payload."""
from __future__ import annotations
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import jinja2
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1278-dash-")
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3", STREAMFORGE_XTREAM_GUARD_DB=f"{d}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64, STREAMFORGE_PANEL_PASSWORD_VAULT_KEY=Fernet.generate_key().decode(),
    STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402
from app.ssh_installer import _package_tarball  # noqa: E402
m.metrics_history.path = Path(d) / "metrics-history.jsonl"  # default path is under /var/lib/streamforge
m.Base.metadata.create_all(bind=m.engine)
with m.SessionLocal() as _db:
    _db.add(m.AdminUser(username="admin", password_hash=m.hash_password("BootTest!12345")))
    _db.commit()
m.main_panel_client_ip = lambda request: "198.51.100.7"

# 1. The Overview renders memory/disk sizes with the page instead of "—".
with TestClient(m.app, base_url="http://127.0.0.1") as c:
    r = c.post("/login", data={"username": "admin", "password": "BootTest!12345"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] != "/account/password", r.headers.get("location")
    page = c.get("/")
    assert page.status_code == 200, page.status_code
    html = page.text
    for key in ("memory-used", "memory-total", "disk-used", "disk-free", "disk-total"):
        value = re.search(rf"<b data-{key}>([^<]*)</b>", html)
        assert value and re.fullmatch(r"[0-9.]+ (GiB|MiB|KiB|B)", value.group(1)), (key, value and value.group(1))
    for key in ("cpu", "memory", "input", "output", "users", "total-channels", "online-channels",
                "gpu-usage", "gpu-encoder", "gpu-vram", "gpu-temp"):
        assert f'data-legend-value="{key}"' in html, key
    assert "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278" in html
    assert c.get("/system-metrics.json").status_code == 200

# 2. Jinja byte/bit formatting matches the browser formatter.
macros = jinja2.Environment().from_string(
    (ROOT / "app/templates/dashboard.html").read_text(encoding="utf-8").split("{% block content %}\n", 1)[1].split("\n<section", 1)[0]
    + "{{ sf_bytes(a) }}|{{ sf_bits(b) }}"
)
assert macros.render(a=8 * 1073741824, b=2_857_900_000).strip() == "8.00 GiB|2.86 Gb/s"
assert macros.render(a=0, b=0).strip() == "—|—"
assert macros.render(a=5 * 1048576, b=430_000_000).strip() == "5.00 MiB|430.00 Mb/s"

# 3. Static scripts: early first poll, live legends, chart loading note.
app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
dash_js = (ROOT / "app/static/dashboard.js").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_DASHBOARD_INSTANT_METRICS_V1278" in app_js
assert "setInterval(refreshSystemMetrics, 5000);\n    }, 1000);" in app_js
assert "setLegend('input', formatBits(rx));" in app_js
assert "STREAMFORGE_MAIN_DASHBOARD_LEGEND_VALUES_V1278" in dash_js
assert "chartMessage('Loading…')" in dash_js and "fillLegends(p)" in dash_js
if shutil.which("node"):
    for script in ("app/static/app.js", "app/static/dashboard.js"):
        subprocess.run(["node", "--check", str(ROOT / script)], check=True)

# 4. The SSH Node payload carries the hls.js helper used by install_node_agent.sh.
names = tarfile.open(fileobj=io.BytesIO(_package_tarball(ROOT)), mode="r:gz").getnames()
assert "streamforge_mvp/scripts/fetch_hlsjs.sh" in names, names
assert "streamforge_mvp/scripts/install_node_agent.sh" in names
installer = (ROOT / "scripts/install_node_agent.sh").read_text(encoding="utf-8")
for helper in re.findall(r'\$SOURCE_DIR/(scripts/[A-Za-z0-9_.-]+)', installer):
    assert f"streamforge_mvp/{helper}" in names, f"SSH payload misses {helper}"

# 5. Clearer SSH update confirmation.
for template in ("app/templates/node_updates.html", "app/templates/node_update.html"):
    text = (ROOT / template).read_text(encoding="utf-8")
    assert "Update this Node over SSH?" in text and "Reinstall and restart this" not in text, template
print("PASS: v12.78 Overview first-paint values, chart legend values, SSH hls.js payload and confirmation text")
