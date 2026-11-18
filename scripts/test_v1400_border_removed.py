"""v14.00 sidebar extra border removal contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_SIDEBAR_EXTRA_BORDER_REMOVED_V1400" in s
assert ".app-shell .sidebar .sidebar-bottom>button.theme-toggle,\n  .app-shell .sidebar .sidebar-bottom>form>.nav-button{\n    border-color:transparent!important;" in s
print("PASS: v14.00 border removed contract")
