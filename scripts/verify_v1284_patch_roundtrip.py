#!/usr/bin/env python3
"""Current release entry point for the bounded v12.83 → v12.84 role patch."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text().strip() != "12.84":
    print("SKIP: v12.84 release round-trip")
else:
    runpy.run_path(str(ROOT / "scripts/test_role_manager_patch.py"), run_name="__main__")
