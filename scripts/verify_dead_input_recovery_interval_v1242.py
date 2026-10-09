#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
model = (root / "app/models.py").read_text(encoding="utf-8")
ffmpeg = (root / "app/ffmpeg.py").read_text(encoding="utf-8")
main = (root / "app/main.py").read_text(encoding="utf-8")
template = (root / "app/templates/channel_form.html").read_text(encoding="utf-8")
db = (root / "app/db.py").read_text(encoding="utf-8")

checks = {
    "model field": "dead_input_recovery_interval" in model,
    "30 minute default": bool(re.search(r"dead_input_recovery_interval[^\n]*default=30", model)),
    "runtime schema default": "dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30" in db,
    "recovery scheduler uses channel interval": "_dead_input_recovery_delay" in ffmpeg and "full_cycle_exhausted" in ffmpeg,
    "form parses recovery interval": main.count("dead_input_recovery_interval: int = Form(30)") == 2,
    "create/edit persist recovery interval": "dead_input_recovery_interval=max(1, min(1440" in main and "channel.dead_input_recovery_interval = max(1, min(1440" in main,
    "form exposes recovery interval": "name=\"dead_input_recovery_interval\"" in template,
    "form offers common minute values": all(f'value=\"{value}\"' in template for value in (5, 10, 15, 30, 60)),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'OK' if ok else 'FAIL'}: {name}")

if failed:
    raise SystemExit("Missing v12.42 dead-input recovery interval support: " + ", ".join(failed))
