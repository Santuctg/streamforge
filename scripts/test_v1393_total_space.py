"""v13.93 collapsed sidebar total space removal contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_COLLAPSED_TOTAL_SPACE_REMOVED_V1393" in s
assert ".app-shell.sidebar-collapsed .sidebar .main-nav-links{padding-bottom:0!important}" in s
assert ".app-shell.sidebar-collapsed .sidebar #main-navigation{gap:0!important}" in s
print("PASS: v13.93 total space contract")
