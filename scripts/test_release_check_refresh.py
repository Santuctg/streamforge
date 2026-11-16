#!/usr/bin/env python3
"""Regression contract for forcing a fresh GitHub release check."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
template = (ROOT / "app/templates/main_updates.html").read_text(encoding="utf-8")

assert "refresh=1" in template, "Check again must bypass the release cache"
assert "force_refresh" in main, "Update page must accept a forced refresh"
assert "_github_release_cache.clear" in main or "_github_release_cache[\"release\"] = {}" in main

print("PASS: release checker forced refresh contract")
