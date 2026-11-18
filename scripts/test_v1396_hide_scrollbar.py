"""v13.96 collapsed sidebar scrollbar hiding contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
assert "STREAMFORGE_COLLAPSED_HIDE_SCROLLBAR_V1396" in s
assert ".app-shell.sidebar-collapsed .sidebar{scrollbar-width:none!important;scrollbar-gutter:auto!important}" in s
assert ".app-shell.sidebar-collapsed .sidebar::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}" in s
print("PASS: v13.96 hide scrollbar contract")
