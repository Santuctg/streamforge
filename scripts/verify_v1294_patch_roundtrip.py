#!/usr/bin/env python3
"""v12.94 Node Web Player expiry screen + Main dashboard count contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.94':
    print('SKIP: v12.94 release round-trip')
    raise SystemExit(0)

main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
node = (ROOT / 'node_agent/app.py').read_text(encoding='utf-8')
compile(main, str(ROOT / 'app/main.py'), 'exec')
compile(node, str(ROOT / 'node_agent/app.py'), 'exec')

# The Main dashboard counts expiries against the established BD wall clock.
assert 'STREAMFORGE_MAIN_DASHBOARD_EXPIRED_NODE_COUNT_V1294' in main
assert 'node_expiry_now = datetime.now(BANGLADESH_TZ).replace(tzinfo=None)' in main
assert 'Node.expires_at <= node_expiry_now' in main
assert 'expired_node_count=expired_node_count' in main
assert 'expired_node_count }} expired node' in dashboard
assert "can('nodes.view')" in dashboard

# Expired Node Web Player root serves a clear Subscription Expired page.
assert 'STREAMFORGE_NODE_WEBPLAYER_SUBSCRIPTION_EXPIRED_V1294' in node
assert 'def _node_web_page(user: NodeUserConfig | None, request: Request, error: str = "") -> str:' in node
web_page = node.split('def _node_web_page(', 1)[1].split('\ndef ', 1)[0]
assert 'manager.node_expiry_lock_reason()' in web_page
assert '<h1>Subscription Expired</h1>' in web_page
assert 'The Web Player is unavailable until payment is made.' in web_page
assert 'access will resume automatically.' in web_page
assert '_node_web_page(user, request, flash_error)' in node

print('PASS: v12.94 Web Player expiry screen and Main dashboard expired Node count')
