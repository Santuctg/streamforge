"""v13.98 sidebar theme button specificity contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_SIDEBAR_THEME_BUTTON_MATCH_V1398" in s
assert ".app-shell .sidebar .sidebar-bottom>button.theme-toggle," in s
assert ".app-shell .sidebar .sidebar-bottom>form>.nav-button{" in s
print("PASS: v13.98 theme button contract")
