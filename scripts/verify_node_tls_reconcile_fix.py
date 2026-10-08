#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source_path = root / "node_agent" / "apply_node_tls.py"
service_path = root / "node_agent" / "deploy" / "streamforge-node-tls.service"
source = source_path.read_text(encoding="utf-8")
service = service_path.read_text(encoding="utf-8")

unlink_line = "REQUEST_FILE.unlink(missing_ok=True)"
enable_line = 'subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)'

errors = []

unlink_count = sum(line.strip() == unlink_line for line in source.splitlines())
enable_count = sum(line.strip() == enable_line for line in source.splitlines())
if unlink_count != 2:
    errors.append(f"expected 2 legacy request-file unlink sites, found {unlink_count}")
if enable_count != 2:
    errors.append(f"expected 2 legacy unconditional nginx-enable sites, found {enable_count}")

required_service_fragments = [
    "STREAMFORGE_NODE_TLS_REQUEST_PERSISTENT_V1239",
    "RuntimeDirectory=streamforge-node-tls",
    "ExecStartPre=/usr/bin/cp /usr/local/libexec/streamforge-node/apply_node_tls.py /run/streamforge-node-tls/apply_node_tls.py",
    "REQUEST_FILE\\.unlink(missing_ok=True)",
    'subprocess\\.run(\\["systemctl", "enable", "nginx"\\]',
    "/usr/bin/systemctl is-enabled --quiet nginx || /usr/bin/systemctl enable nginx",
    "ExecStart=/usr/bin/python3 /run/streamforge-node-tls/apply_node_tls.py",
]
for fragment in required_service_fragments:
    if fragment not in service:
        errors.append(f"service hardening fragment missing: {fragment}")

# Mirror the two service sed deletions and compile the exact runtime source.
runtime_lines = [
    line for line in source.splitlines()
    if line.strip() not in {unlink_line, enable_line}
]
runtime_source = "\n".join(runtime_lines) + "\n"
if unlink_line in runtime_source:
    errors.append("runtime source still deletes tls-reconcile.request")
if enable_line in runtime_source:
    errors.append("runtime source still unconditionally enables nginx")
try:
    compile(runtime_source, str(source_path), "exec")
except SyntaxError as exc:
    errors.append(f"runtime-filtered TLS helper does not compile: {exc}")

if errors:
    raise SystemExit("\n".join(f"FAIL: {item}" for item in errors))

print("Node TLS reconciliation runtime hardening: PASS")
