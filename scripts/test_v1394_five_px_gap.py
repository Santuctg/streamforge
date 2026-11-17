"""v13.94 collapsed sidebar 5px gap contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_COLLAPSED_5PX_GAP_V1394" in s
assert ".app-shell.sidebar-collapsed .sidebar #main-navigation{gap:5px!important}" in s
print("PASS: v13.94 five px gap contract")
