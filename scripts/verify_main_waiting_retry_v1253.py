#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_recovery_policy():
    path = ROOT / "app/recovery_policy.py"
    spec = importlib.util.spec_from_file_location("streamforge_recovery_policy_v1253", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load recovery_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


errors: list[str] = []
policy = load_recovery_policy()
if getattr(policy, "MAIN_WAITING_RETRY_SECONDS", None) != 60:
    errors.append("Main waiting retry interval is not fixed at 60 seconds")

models = read("app/models.py")
for snippet in (
    "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
    "waiting_since:",
    "next_retry_at:",
):
    if snippet not in models:
        errors.append(f"models.py missing {snippet}")

db = read("app/db.py")
for column in ("waiting_since", "next_retry_at"):
    if f'if "{column}" not in columns:' not in db:
        errors.append(f"db.py missing additive {column} migration")

ffmpeg = read("app/ffmpeg.py")
for snippet in (
    "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
    "MAIN_WAITING_RETRY_SECONDS",
    "if channel.waiting_since is None:",
    "channel.next_retry_at",
    "channel.waiting_since = None",
    "channel.next_retry_at = None",
):
    if snippet not in ffmpeg:
        errors.append(f"ffmpeg.py missing {snippet}")

# Full-cycle recovery must use the fixed one-minute interval instead of the
# historical per-channel 30-minute dead-input interval.
if "dead_input_recovery_seconds(configured_recovery)" in ffmpeg:
    errors.append("ffmpeg.py still uses long dead-input recovery seconds")

main = read("app/main.py")
for snippet in (
    '"waiting_seconds":',
    '"restart_in_seconds":',
    "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
):
    if snippet not in main:
        errors.append(f"main.py missing {snippet}")

js = read("app/static/app.js")
for snippet in (
    "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
    "waiting_seconds",
    "restart_in_seconds",
):
    if snippet not in js:
        errors.append(f"app.js missing {snippet}")

html = read("app/templates/channels.html")
if "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253" not in html:
    errors.append("channels.html missing V1253 waiting marker")

if errors:
    for error in errors:
        print(f"FAIL: {error}")
    raise SystemExit(1)

print("PASS: V1253 Main waiting recovery contract verified")
