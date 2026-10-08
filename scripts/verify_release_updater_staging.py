#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version = (root / "VERSION").read_text(encoding="utf-8").strip()
compact = version.replace(".", "")
updater = root / "scripts" / f"force_update_v{compact}.sh"

errors = []
if not updater.is_file():
    errors.append(f"missing current updater: {updater.name}")
else:
    text = updater.read_text(encoding="utf-8")
    required = [
        "find \"$STAGE/scripts\" -maxdepth 1 -type f -name 'force_update_v*.sh'",
        "! -name 'force_update_v1236.sh' -delete",
    ]
    for marker in required:
        if marker not in text:
            errors.append(f"{updater.name} missing staging cleanup marker: {marker}")

    if re.search(r'rm -f \"\$STAGE/scripts/force_update_v\d+\.sh\"', text):
        errors.append(f"{updater.name} uses single-file cleanup and can leave stale force updaters")

if errors:
    raise SystemExit("\n".join(f"FAIL: {e}" for e in errors))
print("Force-updater staging regression guard: PASS")
