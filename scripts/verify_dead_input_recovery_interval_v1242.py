#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
model = (root / "app/models.py").read_text(encoding="utf-8")
ffmpeg = (root / "app/ffmpeg.py").read_text(encoding="utf-8")
main = (root / "app/main.py").read_text(encoding="utf-8")
template = (root / "app/templates/channel_form.html").read_text(encoding="utf-8")

checks = {
    "model field": "dead_input_recovery_interval" in model,
    "30 minute default": "1800" in model or "30 * 60" in model,
    "recovery scheduler uses channel interval": "dead_input_recovery_interval" in ffmpeg,
    "form parses recovery interval": "dead_input_recovery_interval" in main,
    "form exposes recovery interval": "dead_input_recovery_interval" in template,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'OK' if ok else 'FAIL'}: {name}")

if failed:
    raise SystemExit("Missing v12.42 dead-input recovery interval support: " + ", ".join(failed))
