#!/usr/bin/env python3
"""v12.77: Node Panel security headers and the 12-character Node password form."""
from __future__ import annotations
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = tempfile.mkdtemp(prefix="sf-v1277-node-")
source = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
# Point every Node state path at a temp dir so importing never touches /var/lib.
for name in sorted(set(re.findall(r'getenv\("(STREAMFORGE_NODE_[A-Z_]*(?:ROOT|FILE|PATH|WEBROOT|DIR))"', source))):
    os.environ[name] = os.path.join(d, name.lower())
os.environ["STREAMFORGE_GEOIP_SETTINGS_FILE"] = os.path.join(d, "geo.json")
sys.path.insert(0, str(ROOT / "node_agent"))
import app as node  # noqa: E402


async def headers_for(path: str) -> dict[bytes, bytes]:
    captured: dict[bytes, bytes] = {}

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        if message["type"] == "http.response.start":
            captured.update(dict(message["headers"]))

    await node.NodeSecurityHeadersMiddleware(inner)({"type": "http", "path": path, "headers": []}, None, send)
    return captured


for path in ("/", "/panel", "/panel/login", "/panel/users"):
    h = asyncio.run(headers_for(path))
    assert h[b"x-frame-options"] == b"SAMEORIGIN", path
    assert b"frame-ancestors 'self'" in h[b"content-security-policy"], path
    assert h[b"x-content-type-options"] == b"nosniff" and h[b"referrer-policy"] == b"strict-origin-when-cross-origin"
for path in ("/web-player", "/web-player/watch/x", "/get.php", "/playlist/abc.m3u", "/live/u/p/1.m3u8"):
    h = asyncio.run(headers_for(path))
    assert b"x-frame-options" not in h and b"content-security-policy" not in h, path
    assert h[b"x-content-type-options"] == b"nosniff", path

# Registered inside enforce_node_access (outer middlewares come first).
inner_app = node.app
while not hasattr(inner_app, "user_middleware"):
    inner_app = inner_app.app
names = [mw.cls.__name__ if hasattr(mw.cls, "__name__") else str(mw.cls) for mw in inner_app.user_middleware]
assert names.index("NodeSecurityHeadersMiddleware") > names.index("BaseHTTPMiddleware"), names
assert "STREAMFORGE_PANEL_PASSWORD_POLICY_V1277" in source
assert 'name="new_password" minlength="12"' in source and "if len(new_password) < 12:" in source
print("PASS: v12.77 Node Panel frame/nosniff/referrer headers, Web Player embeddable, 12-character Node password form")
