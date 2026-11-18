"""v13.97 expanded sidebar theme button styling contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_SIDEBAR_THEME_BUTTON_MATCH_V1397" in s
assert ".sidebar-bottom>button.theme-toggle,\n  .sidebar-bottom>form>.nav-button{" in s
assert "border:1px solid var(--sf-bd-304156)!important" in s
print("PASS: v13.97 theme button contract")
