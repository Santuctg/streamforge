#!/usr/bin/env python3
"""v12.96 expired-page copy/logo and dashboard status-row contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.96':
    print('SKIP: v12.96 release round-trip')
    raise SystemExit(0)

node = (ROOT / 'node_agent/app.py').read_text(encoding='utf-8')
dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
compile(node, str(ROOT / 'node_agent/app.py'), 'exec')
compile(main, str(ROOT / 'app/main.py'), 'exec')

assert 'STREAMFORGE_NODE_WEBPLAYER_EXPIRED_BRANDING_V1296' in node
assert '<h1>Subscription Expired</h1>' in node
assert 'This subscription has expired. The Web Player is unavailable until payment is made. ' in node
assert 'Once payment is received, access will resume automatically.' in node
assert "Expired: ' + html.escape(expiry_display)" in node
assert 'expiry_display, _expiry_class = manager.node_expiry_bd_display()' in node
assert 'node_logo.startswith("/node-logos/")' in node
assert 'prefix.rstrip("/") + "/web-player/logo"' in node
assert "'<div class=\"expired-brand\">' + logo_html" in node

assert 'STREAMFORGE_MAIN_DASHBOARD_NODE_STATUS_ROW_V1296' in dashboard
assert 'justify-content:space-between' in dashboard
assert '{% if offline_node_count %}' in dashboard
assert '{% if expired_node_count %}' in dashboard
assert '{{ offline_node_count }} offline node' in dashboard
assert '{{ expired_node_count }} expired node' in dashboard
assert 'offline_node_count=offline_node_count' in main
assert 'expired_node_count=expired_node_count' in main

print('PASS: v12.96 Web Player expired copy/logo and conditional left/right dashboard counts')
