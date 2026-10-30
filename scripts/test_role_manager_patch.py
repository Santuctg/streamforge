#!/usr/bin/env python3
"""Published v12.83 + pending role hunks must reproduce the working tree."""
from pathlib import Path
import subprocess
import tempfile

from apply_role_manager_fix import CHANGES, apply, transform

ROOT = Path(__file__).resolve().parents[1]
originals = {}
for relative in CHANGES:
    source = subprocess.check_output(["git", "show", f"v12.83:{relative}"], cwd=ROOT).decode()
    originals[relative] = source
    updated = transform(relative, source)
    assert updated == (ROOT / relative).read_text(), relative
    assert transform(relative, updated) == updated, relative
    old = CHANGES[relative][0][0]
    for bad in (source.replace(old, "# drifted anchor\n", 1), source + old):
        try:
            transform(relative, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{relative}: missing/ambiguous anchor accepted")

with tempfile.TemporaryDirectory(prefix="sf-role-patch-") as tmp:
    root = Path(tmp)
    for relative, source in originals.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    (root / "app/permissions.py").write_text("# locally modified\n")
    before = {f: (root / f).read_bytes() for f in CHANGES}
    try:
        apply(root)
    except ValueError:
        pass
    else:
        raise AssertionError("drifted staged tree must be refused")
    assert before == {f: (root / f).read_bytes() for f in CHANGES}, "preflight wrote partial changes"
    (root / "app/permissions.py").write_text(originals["app/permissions.py"])
    apply(root)
    apply(root)
    for relative in CHANGES:
        assert (root / relative).read_bytes() == (ROOT / relative).read_bytes()
print("PASS: v12.83 role patch round-trip, idempotence, anchor refusal and whole-tree preflight")
