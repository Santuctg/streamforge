#!/usr/bin/env python3
"""Verify v12.65 Main patch on top of v12.64 installed-source contract."""
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = (root / "app/main.py").read_text(encoding="utf-8")
to64 = runpy.run_path(str(root / "scripts/apply_v1264_ip_lock.py"))["transform"]
to65 = runpy.run_path(str(root / "scripts/apply_v1265_ip_origin.py"))["transform"]
source = to64(base)
updated = to65(source)
# Later releases integrate this patch and the v12.64-v12.68 patches into app/main.py;
# the pre-integration transform is covered by this test in its own release ZIP.
if "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" in base:
    assert source == base and updated == base, "integrated release source must stay unchanged"
    print("PASS: v12.65 origin patch is a no-op on integrated Main source")
    raise SystemExit(0)
assert updated != source
assert to65(updated) == updated
compile(updated, "app/main.py", "exec")
assert "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" in updated
assert updated.count('record_ip_failure(source_ip, origin="main")') == 1
assert updated.count('record_ip_failure(source_ip, origin="node", node_id=node.id, node_name=node.name)') == 2
assert "record_ip_failure(source_ip)" not in updated
assert "vault_discard(db, admin.id)" in updated
print("PASS: bounded Main/Node source-server attribution, syntax and idempotence")
