#!/usr/bin/env python3
"""v12.95 Main dashboard Node count summary contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.95':
    print('SKIP: v12.95 release round-trip')
    raise SystemExit(0)

main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
compile(main, str(ROOT / 'app/main.py'), 'exec')

assert 'STREAMFORGE_MAIN_DASHBOARD_NODE_SUMMARY_V1295' in main
assert 'offline_node_count = max(0, node_count - online_node_count)' in main
assert 'expired_node_count=expired_node_count' in main
assert 'offline_node_count=offline_node_count' in main
assert '<strong>{{ node_count }}</strong>' in dashboard
assert '{{ offline_node_count }} offline node' in dashboard
assert '{{ expired_node_count }} expired node' in dashboard
assert '{{ online_node_count }}/{{ node_count }}' not in dashboard

print('PASS: Main dashboard shows total count, offline count, and expired count')
