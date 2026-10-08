#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source_path = root / "node_agent" / "apply_node_tls.py"
installer_path = root / "scripts" / "install_node_agent.sh"
source = source_path.read_text(encoding="utf-8")
installer = installer_path.read_text(encoding="utf-8")

errors = []

if "REQUEST_FILE.unlink(missing_ok=True)" in source:
    errors.append("TLS reconciler still deletes tls-reconcile.request")

marker = "STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT_V1239"
if source.count(marker) < 2:
    errors.append("persistent request marker is missing from both reconcile exit paths")
if marker not in installer:
    errors.append("installer does not seed the persistent TLS request file")

conditional = 'subprocess.run(["systemctl", "is-enabled", "--quiet", "nginx"]'
if source.count(conditional) < 2:
    errors.append("both nginx enable call sites are not guarded by is-enabled")

legacy_enable = 'subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)'
for index, line in enumerate(source.splitlines(), start=1):
    if line.strip() != legacy_enable:
        continue
    previous = source.splitlines()[index - 2].strip() if index >= 2 else ""
    if 'systemctl", "is-enabled", "--quiet", "nginx"' not in previous:
        errors.append(f"unguarded nginx enable remains at line {index}")

if "/var/lib/streamforge-node/tls-reconcile.request" not in installer:
    errors.append("installer does not manage tls-reconcile.request")

try:
    compile(source, str(source_path), "exec")
except SyntaxError as exc:
    errors.append(f"TLS helper syntax error: {exc}")

if errors:
    raise SystemExit("\n".join(f"FAIL: {item}" for item in errors))

print("Node TLS persistent-request regression guard: PASS")
