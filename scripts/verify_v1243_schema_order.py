from pathlib import Path

bridge = Path('scripts/force_update_v1243.sh')
assert bridge.exists(), 'force_update_v1243.sh missing'
text = bridge.read_text(encoding='utf-8')
required = [
    'STREAMFORGE_V1243_DEAD_INPUT_SCHEMA_BEFORE_VERIFY',
    'dead_input_recovery_interval',
    'ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30',
    'verify_main_install.py',
]
for marker in required:
    assert marker in text, f'missing v12.43 schema-order marker: {marker}'

inject = text.index('STREAMFORGE_V1243_DEAD_INPUT_SCHEMA_BEFORE_VERIFY')
verify = text.index('verify_main_install.py')
assert inject < verify, 'schema migration injection must be defined before install verification anchor handling'
assert "if 'dead_input_recovery_interval' not in columns:" in text, 'migration must be idempotent'
print('OK: v12.43 injects dead-input schema migration before Main install verification')
