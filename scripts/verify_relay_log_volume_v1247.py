#!/usr/bin/env python3
"""Regression guard for relay log volume and updater carry-forward."""
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
deploy = (root / "deploy/nginx.conf").read_text(encoding="utf-8")
renderer = (root / "scripts/apply_main_access.py").read_text(encoding="utf-8")
fetcher = (root / "scripts/fetch_hlsjs.sh").read_text(encoding="utf-8")
version = (root / "VERSION").read_text(encoding="utf-8").strip()
node_version = (root / "node_agent/VERSION").read_text(encoding="utf-8").strip()
updater_path = root / "scripts/force_update_v1247.sh"
updater = updater_path.read_text(encoding="utf-8") if updater_path.exists() else ""

errors = []

if version != "12.47" or node_version != "12.47":
    errors.append(f"versions must both be 12.47 (Main={version}, Node={node_version})")

required_policy = (
    'map "$status:$uri"',
    '~^409:',
    '~^410:',
    '~^404:/relay/',
    'segment_',
    r'\.ts$ 0;',
    '~^499:/relay/',
    '~^[45][0-9][0-9]: 1;',
)
for name, text in (("fresh Nginx config", deploy), ("runtime renderer", renderer)):
    for token in required_policy:
        if token not in text:
            errors.append(f"{name}: missing relay log policy token {token!r}")
if r'\\.ts$ 0;' not in deploy:
    errors.append("fresh Nginx config: missing escaped .ts suffix matcher")
if r'\\\\.ts$ 0;' not in renderer:
    errors.append("runtime renderer: missing Python-escaped .ts suffix matcher")

access = "access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;"
def fallback_bodies(text: str):
    pattern = re.compile(
        r"location @streamforge_relay_fallback \{\{?(.*?)(?=\n\s{4}\}\}?)",
        re.S,
    )
    return pattern.findall(text)

for name, text in (("fresh Nginx config", deploy), ("runtime renderer", renderer)):
    bodies = fallback_bodies(text)
    if not bodies:
        errors.append(f"{name}: named relay fallback block not found")
    elif any(access not in body for body in bodies):
        errors.append(f"{name}: every @streamforge_relay_fallback block must have conditional access_log")

if not updater:
    errors.append("missing scripts/force_update_v1247.sh")
else:
    for token in (
        "STREAMFORGE_V1247_RELAY_LOG_POLICY",
        "STREAMFORGE_V1247_DEAD_INPUT_SCHEMA_BEFORE_VERIFY",
        "STREAMFORGE_NGINX_LOGROTATE_HOURLY_V1247",
        "cron.hourly/streamforge-logrotate",
        "STREAMFORGE_RELAY_ACCESS_LOG_POLICY_V1247",
        "logrotate -d /etc/logrotate.d/nginx",
        "force_update_v1236.sh",
    ):
        if token not in updater:
            errors.append(f"v12.47 updater missing {token}")
    if "curl -fsSL" in updater:
        errors.append("v12.47 wrapper should not add an extra direct curl step")

if "github.com/video-dev/hls.js/releases/download/" in fetcher:
    errors.append("HLS.js fetcher still tries the known-bad GitHub release URL before CDN fallbacks")

if errors:
    print("v12.47 relay log-volume regression FAILED:", file=sys.stderr)
    for error in errors:
        print(f" - {error}", file=sys.stderr)
    raise SystemExit(1)

print("v12.47 relay log-volume regression OK")
