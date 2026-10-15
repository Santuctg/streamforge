#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
template = (ROOT / "app/templates/node_updates.html").read_text(encoding="utf-8")
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
node_version = (ROOT / "node_agent/VERSION").read_text(encoding="utf-8").strip()

errors: list[str] = []

def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)

require(version == "12.60", "Main VERSION must be 12.60")
require(node_version == "12.60", "bundled Node VERSION must be 12.60")
require("STREAMFORGE_NODE_INLINE_UPDATE_LOG_V1260" in template, "v12.60 inline-log marker missing")
require("Live update log" in template, "Advanced panel must label the per-Node live update log")
require("data-update-log" in template, "Advanced panel must expose a per-Node update log target")
require("job.logs" in template, "Advanced panel must render existing per-Node job logs at first paint")
require("data.logs" in template, "live polling must render per-Node logs returned by status JSON")
require("logs.join('\\n')" in template or 'logs.join("\\n")' in template, "live polling must join log lines safely")
require("lastLog" in template, "per-Node log rendering must avoid rewriting unchanged logs")
require("scrollTop" in template and "scrollHeight" in template, "live log must follow new lines when already at the bottom")
require("STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259" in template, "v12.59 spacing guard must remain")

if errors:
    raise SystemExit("FAIL: " + "; ".join(errors))
print("PASS: v12.60 per-Node inline update log contract verified")
