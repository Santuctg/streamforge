"""v13.99 sidebar Theme full-width contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_SIDEBAR_THEME_BUTTON_FULLWIDTH_V1399" in s
assert ".app-shell .sidebar .sidebar-bottom>button.theme-toggle{grid-column:1/-1!important}" in s
assert "border:1px solid var(--sf-bd-40536a)!important" in s
print("PASS: v13.99 theme fullwidth contract")
