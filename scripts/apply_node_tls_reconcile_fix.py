#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TLS = ROOT / "node_agent" / "apply_node_tls.py"
INSTALLER = ROOT / "scripts" / "install_node_agent.sh"


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} occurrence(s), found {count}")
    return text.replace(old, new)


tls = TLS.read_text(encoding="utf-8")

enable = 'subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)'
conditional = (
    'if subprocess.run(["systemctl", "is-enabled", "--quiet", "nginx"], '
    'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0:\n'
    '        subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)'
)
# The two call sites have different indentation. Patch line-by-line to preserve it.
lines = []
found_enable = 0
for line in tls.splitlines():
    if line.strip() == enable:
        found_enable += 1
        indent = line[: len(line) - len(line.lstrip())]
        lines.append(
            indent
            + 'if subprocess.run(["systemctl", "is-enabled", "--quiet", "nginx"], '
            + 'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0:'
        )
        lines.append(indent + "    " + enable)
    else:
        lines.append(line)
if found_enable != 2:
    raise SystemExit(f"conditional nginx enable: expected 2 call sites, found {found_enable}")
tls = "\n".join(lines) + "\n"

unlink = "REQUEST_FILE.unlink(missing_ok=True)"
marker = "# STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT_V1239: keep request file for PathChanged watcher"
lines = []
found_unlink = 0
for line in tls.splitlines():
    if line.strip() == unlink:
        found_unlink += 1
        indent = line[: len(line) - len(line.lstrip())]
        lines.append(indent + marker)
    else:
        lines.append(line)
if found_unlink != 2:
    raise SystemExit(f"persistent request file: expected 2 unlink call sites, found {found_unlink}")
tls = "\n".join(lines) + "\n"
TLS.write_text(tls, encoding="utf-8")

installer = INSTALLER.read_text(encoding="utf-8")
anchor = '''  # STREAMFORGE_NODE_NATIVE_TLS_TRIGGER_V39: TLS is best-effort and never
  # takes the authenticated native HTTP Agent offline when DNS/ACME is pending.
  systemctl enable --now streamforge-node-tls.path streamforge-node-tls.timer >/dev/null 2>&1 || true
'''
replacement = '''  # STREAMFORGE_NODE_NATIVE_TLS_TRIGGER_V39: TLS is best-effort and never
  # takes the authenticated native HTTP Agent offline when DNS/ACME is pending.
  # STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT_V1239: PathChanged must always watch
  # an existing request file. The reconciler keeps this file and later requests
  # overwrite it, avoiding systemd's missing-path inotify re-arm loop.
  if [[ ! -f /var/lib/streamforge-node/tls-reconcile.request ]]; then
    printf '%s\\n' '{"requested_at":0,"force_retry":false}' > /var/lib/streamforge-node/tls-reconcile.request
    chown streamforge-node:streamforge-node /var/lib/streamforge-node/tls-reconcile.request
    chmod 0644 /var/lib/streamforge-node/tls-reconcile.request
  fi
  systemctl enable --now streamforge-node-tls.path streamforge-node-tls.timer >/dev/null 2>&1 || true
'''
installer = replace_exact(installer, anchor, replacement, 1, "installer persistent request seed")
INSTALLER.write_text(installer, encoding="utf-8")

print("Applied StreamForge Node TLS persistent-request fix")
