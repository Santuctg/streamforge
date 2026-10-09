from pathlib import Path

bridge = Path('scripts/force_update_v1244.sh')
assert bridge.exists(), 'force_update_v1244.sh missing'
text = bridge.read_text(encoding='utf-8')
required = [
    'STREAMFORGE_V1244_DEAD_INPUT_SCHEMA_BEFORE_VERIFY',
    'dead_input_recovery_interval',
    'ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30',
    'verify_main_install.py',
]
for marker in required:
    assert marker in text, f'missing v12.44 schema carry-forward marker: {marker}'
assert "if 'dead_input_recovery_interval' not in columns:" in text, 'migration must remain idempotent'
print('OK: v12.44 carries schema-order hotfix forward')
