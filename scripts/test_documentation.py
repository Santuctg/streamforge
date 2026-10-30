#!/usr/bin/env python3
"""Local doc links and current-release/operator-safety regression checks."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
version = (ROOT / "VERSION").read_text().strip()
for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
    for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
        target = link.split("#")[0]
        if target and not re.match(r"\w+://", target):
            assert (path.parent / target).exists(), (path.relative_to(ROOT), link)

install = (ROOT / "docs/INSTALLATION.md").read_text()
api = (ROOT / "docs/API_AND_TESTING.md").read_text()
admin = (ROOT / "docs/ADMINISTRATION.md").read_text()
operations = (ROOT / "docs/OPERATIONS.md").read_text()
readme = (ROOT / "README.md").read_text()
assert f"VERSION={version}\n" in install
assert f"releases/tag/v{version}" in readme
compact = version.replace(".", "")
for name in (f"test_v{compact}_updater.py", f"verify_v{compact}_patch_roundtrip.py", "test_role_manager.py"):
    assert name in api and (ROOT / "scripts" / name).is_file(), name
assert 'sudo cp -a "$B/app/main.py"' not in install, "unsafe partial rollback recipe returned"
for name in ("streamforge.env", "nginx-site", "app/client_ip.py"):
    assert name in install, name
assert "`stream_users.*`" in admin and "`users.*`" not in admin
assert "`panel_users.password_vault_manage`" in admin and "automatic" in admin
assert "set_real_ip_from" in install and "self-signed" in install
assert "sudoedit /etc/streamforge.env" in operations
print("PASS: local doc links, release examples, test commands, rollback and role/vault guidance")
