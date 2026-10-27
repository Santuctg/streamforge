#!/usr/bin/env python3
"""v12.83 Batch 2B: rightmost X-Forwarded-For, always-verified Node TLS,
secret-key hard fail, and the Nginx real_ip trust block."""
from __future__ import annotations

import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

d = tempfile.mkdtemp(prefix="sf-v1283-batch2b-")
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db",
    STREAMFORGE_HLS_ROOT=f"{d}/h", STREAMFORGE_LOGO_ROOT=f"{d}/l",
    STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3",
    STREAMFORGE_XTREAM_GUARD_DB=f"{d}/xtream.sqlite3",
    STREAMFORGE_SECRET_KEY="x" * 64,
    STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
)

from app.client_ip import forwarded_client_ip, forwarded_hops, loopback_backend, strip_port  # noqa: E402


def fake_request(peer: str, xff: str | None = None, server=("127.0.0.1", 8800)):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers=headers,
        scope={"server": server} if server else {},
    )


# 1. The shared helper: rightmost hop, loopback-backend gate, normalization.
assert strip_port("198.51.100.7:4711") == "198.51.100.7"
assert strip_port("[2001:db8::1]:443") == "2001:db8::1"
assert strip_port("2001:db8::1") == "2001:db8::1"
assert forwarded_hops(" 203.0.113.9 , ,198.51.100.7:8443 ") == ["203.0.113.9", "198.51.100.7"]

# The client prepends a forged hop; Nginx appends the real socket peer.
spoofed = fake_request("203.0.113.9", "203.0.113.9, 198.51.100.7")
assert forwarded_client_ip(spoofed) == "198.51.100.7", forwarded_client_ip(spoofed)
# Uvicorn may have rewritten request.client to the forged leftmost hop; the
# server bind (not request.client) is the trust gate, so the answer stands.
assert spoofed.client.host == "203.0.113.9"
# A public backend bind trusts no forwarded header at all.
public = fake_request("203.0.113.9", "203.0.113.9, 198.51.100.7", server=("0.0.0.0", 8800))
assert forwarded_client_ip(public) is None
assert loopback_backend(public) is False
# No header, blank header, malformed rightmost hop: no trusted value.
assert forwarded_client_ip(fake_request("198.51.100.7")) is None
assert forwarded_client_ip(fake_request("198.51.100.7", "   ")) is None
assert forwarded_client_ip(fake_request("198.51.100.7", "203.0.113.9, garbage")) is None
assert forwarded_client_ip(fake_request("198.51.100.7", ",")) is None
# Port suffixes, brackets and IPv4-mapped IPv6 canonicalize.
assert forwarded_client_ip(fake_request("x", "203.0.113.9, 198.51.100.7:8443")) == "198.51.100.7"
assert forwarded_client_ip(fake_request("x", "203.0.113.9, [2001:db8::1]")) == "2001:db8::1"
assert forwarded_client_ip(fake_request("x", "203.0.113.9, ::ffff:198.51.100.7")) == "198.51.100.7"
# Missing scope falls back to the socket peer for the loopback gate.
peer_only = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"),
                            headers={"x-forwarded-for": "203.0.113.9, 198.51.100.7"}, scope={})
assert forwarded_client_ip(peer_only) == "198.51.100.7"

# 2. Every former leftmost consumer now resolves the same rightmost hop.
from app.access_control import client_ip as access_client_ip  # noqa: E402
from app.load_balancer import client_ip as lb_client_ip  # noqa: E402
from app.viewer_tracking import viewer_tracker  # noqa: E402
from app.panel_login_guard import LoginGuardUnavailable, main_panel_client_ip  # noqa: E402
import app.main as m  # noqa: E402

for label, resolved in [
    ("access_control.client_ip", access_client_ip(spoofed)),
    ("load_balancer.client_ip", lb_client_ip(spoofed)),
    ("viewer_tracker.client_ip", viewer_tracker.client_ip(spoofed)),
    ("ConnectionTracker._client_ip", m.ConnectionTracker._client_ip(spoofed)),
]:
    assert resolved == "198.51.100.7", f"{label} resolved {resolved}"

# The Panel login guard keeps its fail-closed contract on top of the helper.
assert main_panel_client_ip(spoofed) == "198.51.100.7"
try:
    main_panel_client_ip(public)
    raise AssertionError("publicly bound backend must reject forwarded spoofing")
except LoginGuardUnavailable:
    pass

# 3. verify_tls is gone: HTTPS Node traffic always verifies certificates.
for relative in ("app/main.py",):
    assert "verify_tls" not in (ROOT / relative).read_text(encoding="utf-8"), f"verify_tls still referenced in {relative}"
for relative in ("app/templates/node_form.html", "app/templates/node_install.html"):
    assert 'name="verify_tls"' not in (ROOT / relative).read_text(encoding="utf-8"), f"verify_tls field still in {relative}"
node_manager_source = (ROOT / "app/node_manager.py").read_text(encoding="utf-8")
assert "node.verify_tls" not in node_manager_source
assert "unverified" not in node_manager_source

from app.node_manager import NodeController  # noqa: E402

https_node = SimpleNamespace(api_urls="https://node.example.com", api_url="https://node.example.com")
http_node = SimpleNamespace(api_urls="http://node.example.com", api_url="http://node.example.com")
context = NodeController._context(https_node)
assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
assert NodeController._context(http_node) is None

# 4. Nginx real_ip: loopback trust only, recursive, never trust-the-world.
nginx = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
assert "set_real_ip_from 127.0.0.1;" in nginx
assert "set_real_ip_from ::1;" in nginx
assert "real_ip_recursive on;" in nginx
# Exactly the two loopback entries: no broad CIDR is trusted by default.
assert nginx.count("set_real_ip_from") == 2, nginx.count("set_real_ip_from")

# 5. Secret-key hard fail: the problem detector and the boot refusal.
from app.config import secret_key_problem  # noqa: E402

assert secret_key_problem("") != ""
assert secret_key_problem("dev-change-me-now") != ""
assert secret_key_problem("x" * 31) != ""
assert secret_key_problem("x" * 32) == ""
assert secret_key_problem("x" * 64) == ""
assert "raise RuntimeError(problem)" in (ROOT / "app/main.py").read_text(encoding="utf-8")

BOOT = (
    "import os\n"
    "from pathlib import Path\n"
    "from fastapi.testclient import TestClient\n"
    "import app.main as m\n"
    "m.metrics_history.path = Path(os.environ['SF_BOOT_TMP']) / 'metrics-history.jsonl'\n"
    "with TestClient(m.app):\n"
    "    pass\n"
)


def boot(secret_key: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="sf-v1283-boot-") as tmp:
        env = dict(os.environ)
        env.update(
            PYTHONPATH=".",
            SF_BOOT_TMP=tmp,
            STREAMFORGE_DATABASE_URL=f"sqlite:///{tmp}/t.db",
            STREAMFORGE_HLS_ROOT=f"{tmp}/h", STREAMFORGE_LOGO_ROOT=f"{tmp}/l",
            STREAMFORGE_NODE_LOGO_ROOT=f"{tmp}/n",
            STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{tmp}/guard.sqlite3",
            STREAMFORGE_XTREAM_GUARD_DB=f"{tmp}/xtream.sqlite3",
            STREAMFORGE_SECRET_KEY=secret_key,
            STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="ChangeMe123!",
        )
        return subprocess.run(
            [sys.executable, "-c", BOOT], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=300,
        )


weak = boot("dev-change-me-now")
assert weak.returncode != 0, "a development secret key must refuse to boot"
assert "STREAMFORGE_SECRET_KEY" in weak.stderr, weak.stderr[-400:]
strong = boot("x" * 64)
assert strong.returncode == 0, strong.stderr[-600:]

print("PASS: rightmost XFF, verified Node TLS, Nginx real_ip and secret-key hard fail")
