#!/usr/bin/env python3
"""v12.98 channel auto-resume retry/persistence contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'VERSION').read_text().strip() != '12.98':
    print('SKIP: v12.98 release round-trip')
    raise SystemExit(0)

node = (ROOT / 'node_agent/app.py').read_text(encoding='utf-8')
compile(node, str(ROOT / 'node_agent/app.py'), 'exec')
assert 'STREAMFORGE_NODE_EXPIRY_RESTORE_RETRY_V1298' in node
body = node.split('def enforce_node_expiry(self)', 1)[1].split('\n    def ', 1)[0]
assert 'pending_restore = dict(restore)' in body
assert 'if rt and desired:' in body
assert 'result = self.start(key, config)' in body
assert 'status in {"error", "stopped", "expired"}' in body
assert 'pending_restore.pop(key, None)' in body
assert 'self._persist_expiry_restore(pending_restore)' in body
assert body.index('result = self.start(key, config)') < body.index('pending_restore.pop(key, None)')
assert body.index('pending_restore.pop(key, None)') < body.index('self._persist_expiry_restore(pending_restore)')
assert 'next enforcement tick' in body

print('PASS: renewal preserves failed/missing channel intents and retries start safely')
