"""v13.92 collapsed sidebar runway removal contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_RAIL_RUNWAY_REMOVED_V1392" in s
assert ".app-shell.sidebar-collapsed .sidebar::after{content:none!important}" in s
print("PASS: v13.92 runway removed contract")
