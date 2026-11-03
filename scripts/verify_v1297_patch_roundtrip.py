#!/usr/bin/env python3
"""v12.97 all-clear and conditional Node dashboard statuses."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.97':
    print('SKIP: v12.97 release round-trip')
    raise SystemExit(0)

dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
assert 'STREAMFORGE_MAIN_DASHBOARD_ALL_CLEAR_STATUS_V1297' in dashboard
assert '{% if node_count %}' in dashboard
assert '{% if offline_node_count %}' in dashboard
assert 'All nodes online' in dashboard
assert '{% if expired_node_count %}' in dashboard
assert 'All nodes active' in dashboard
assert 'justify-content:space-between' in dashboard
assert 'offline_node_count }} offline node' in dashboard
assert 'expired_node_count }} expired node' in dashboard

print('PASS: zero counts show all-online/all-active status and empty Nodes show neither')
