#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version_text = (root / "VERSION").read_text(encoding="utf-8").strip()
try:
    version = tuple(int(part) for part in version_text.split(".")[:2])
except ValueError:
    version = (0, 0)

model = (root / "app/models.py").read_text(encoding="utf-8")
ffmpeg = (root / "app/ffmpeg.py").read_text(encoding="utf-8")
main = (root / "app/main.py").read_text(encoding="utf-8")
template = (root / "app/templates/channel_form.html").read_text(encoding="utf-8")
db = (root / "app/db.py").read_text(encoding="utf-8")
policy = (root / "app/recovery_policy.py").read_text(encoding="utf-8")

# STREAMFORGE_V1253_V1242_RECOVERY_COMPAT:
# v12.42 introduced the configurable long dead-input interval and the
# consecutive-cycle policy. v12.53 deliberately supersedes only the Main
# scheduler delay with a fixed one-minute full-cycle retry while preserving the
# model/schema/form compatibility and the same cycle-exhaustion decision.
if version >= (12, 53):
    scheduler_ok = (
        "dead_input_cycle_exhausted" in ffmpeg
        and "source_count = max(1, len(self.input_urls(channel)))" in ffmpeg
        and "MAIN_WAITING_RETRY_SECONDS" in ffmpeg
        and "MAIN_WAITING_RETRY_SECONDS = 60" in policy
        and "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253" in ffmpeg
        and "dead_input_recovery_seconds(configured_recovery)" not in ffmpeg
    )
    scheduler_label = "recovery scheduler uses v12.53 one-minute cycle policy"
else:
    scheduler_ok = (
        "dead_input_cycle_exhausted" in ffmpeg
        and "dead_input_recovery_seconds" in ffmpeg
        and "source_count = max(1, len(self.input_urls(channel)))" in ffmpeg
    )
    scheduler_label = "recovery scheduler uses consecutive-cycle policy"

checks = {
    "model field": "dead_input_recovery_interval" in model,
    "30 minute compatibility default": bool(re.search(r"dead_input_recovery_interval[^\n]*default=30", model)),
    "runtime schema compatibility default": "dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30" in db,
    scheduler_label: scheduler_ok,
    "form parses recovery interval": main.count("dead_input_recovery_interval: int = Form(30)") == 2,
    "create/edit persist recovery interval": "dead_input_recovery_interval=max(1, min(1440" in main and "channel.dead_input_recovery_interval = max(1, min(1440" in main,
    "form exposes recovery interval": "name=\"dead_input_recovery_interval\"" in template,
    "form offers common minute values": all(f'value=\"{value}\"' in template for value in (5, 10, 15, 30, 60)),
    "full failed cycle resets to primary": "channel.active_input_index = 0" in ffmpeg,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'OK' if ok else 'FAIL'}: {name}")

if failed:
    raise SystemExit("Missing dead-input recovery compatibility: " + ", ".join(failed))
