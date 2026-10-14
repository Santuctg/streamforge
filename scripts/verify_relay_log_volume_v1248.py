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
updater_path = root / "scripts/force_update_v1248.sh"
updater = updater_path.read_text(encoding="utf-8") if updater_path.exists() else ""

errors = []


def version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return ()


# STREAMFORGE_V1248_REGRESSION_CARRY_FORWARD:
# This verifier protects behavior introduced in v12.48, so later releases must
# keep passing it instead of failing merely because VERSION advanced.
if not version_tuple(version) or version_tuple(version) < (12, 48):
    errors.append(f"Main version must be v12.48 or newer (Main={version})")
if not version_tuple(node_version) or version_tuple(node_version) < (12, 48):
    errors.append(f"Node version must be v12.48 or newer (Node={node_version})")

required_policy = (
    '$status:$uri',
    '~^409:',
    '~^410:',
    '~^404:/relay/',
    'segment_',
    '~^499:/relay/',
    '~^[45][0-9][0-9]: 1;',
)
for name, text in (("fresh Nginx config", deploy), ("runtime renderer", renderer)):
    for token in required_policy:
        if token not in text:
            errors.append(f"{name}: missing relay log policy token {token!r}")

if '"~^404:/relay/[0-9a-f]{32}/' not in deploy:
    errors.append("fresh Nginx config: relay segment regex key must be quoted")
if '\\"~^404:/relay/[0-9a-f]{32}/' not in renderer:
    errors.append("runtime renderer: generated relay segment regex key must be quoted")

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
    errors.append("missing scripts/force_update_v1248.sh")
else:
    for token in (
        "STREAMFORGE_V1248_RELAY_LOG_POLICY",
        "STREAMFORGE_V1248_DEAD_INPUT_SCHEMA_BEFORE_VERIFY",
        "STREAMFORGE_NGINX_LOGROTATE_HOURLY_V1248",
        "cron.hourly/streamforge-logrotate",
        "STREAMFORGE_RELAY_ACCESS_LOG_POLICY_V1248",
        "STREAMFORGE_V1248_COMPOSITE_RELAY_MAP_GUARD",
        'new_guard =',
        'text = text.replace(old_guard, new_guard, 1)',
        'map "$status:$uri" $streamforge_relay_access_loggable',
        '"~^404:/relay/[0-9a-f]{32}/',
        "logrotate -d /etc/logrotate.d/nginx",
        "force_update_v1236.sh",
    ):
        if token not in updater:
            errors.append(f"v12.48 updater missing {token}")
    if "curl -fsSL" in updater:
        errors.append("v12.48 wrapper should not add an extra direct curl step")

if "github.com/video-dev/hls.js/releases/download/" in fetcher:
    errors.append("HLS.js fetcher still tries the known-bad GitHub release URL before CDN fallbacks")

if errors:
    print("v12.48 relay log-volume regression FAILED:", file=sys.stderr)
    for error in errors:
        print(f" - {error}", file=sys.stderr)
    raise SystemExit(1)

print("v12.48 relay log-volume regression OK")
