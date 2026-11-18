"""v13.95 collapsed sidebar centering contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_COLLAPSED_CENTER_GUTTER_V1395" in s
assert ".app-shell.sidebar-collapsed .sidebar{scrollbar-gutter:stable both-edges!important}" in s
print("PASS: v13.95 centering gutter contract")
