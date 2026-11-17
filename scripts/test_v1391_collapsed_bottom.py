"""v13.91 collapsed sidebar bottom-actions order contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_COLLAPSED_BOTTOM_AFTER_LINKS_V1391" in s
assert ".app-shell.sidebar-collapsed .sidebar .main-nav-links{flex:0 0 auto!important;min-height:0!important}" in s
assert ".app-shell.sidebar-collapsed .sidebar-bottom{position:static!important;margin-top:8px!important" in s
print("PASS: v13.91 collapsed bottom contract")
