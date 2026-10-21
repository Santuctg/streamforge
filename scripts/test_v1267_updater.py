#!/usr/bin/env python3
"""v12.67 Node Agent-only scoped install, reinstall and rollback tests."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/force_update_v1267.sh"
MARKER="    # STREAMFORGE_V1267_SELF_PASSWORD_PERMISSION_FIX: empty permission means\n"
OLD='    if not _has_panel_permission(user, permission):\n'
NEW='    if permission and not _has_panel_permission(user, permission):\n'

def blob_sha(content: bytes):
    return hashlib.sha1(b"blob "+str(len(content)).encode()+b"\0"+content).hexdigest()

def prepare(tmp: Path) -> Path:
    installed=tmp/"installed"
    shutil.copytree(ROOT/"node_agent", installed/"node_agent")
    source=(installed/"node_agent/app.py").read_text(encoding="utf-8")
    assert MARKER in source and NEW in source
    reverted=source.replace(MARKER, "", 1).replace(
        "    # valid personal session only; do not require Super Admin wildcard.\n"
        "    # All actual resource checks still enforce the named permission.\n", "", 1
    ).replace(NEW, OLD, 1)
    assert blob_sha(reverted.encode("utf-8")) == "a071e851db49804c413a6d4db9de792a20adb69a"
    (installed/"node_agent/app.py").write_text(reverted,encoding="utf-8")
    (installed/"VERSION").write_text("12.66\n")
    (installed/"node_agent/VERSION").write_text("12.66\n")
    return installed

def run(installed, backup, fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(installed),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail: env["STREAMFORGE_V1267_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(SCRIPT)],cwd=ROOT,env=env,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)

with tempfile.TemporaryDirectory(prefix="sf-v1267-ok-") as tmp:
    base=Path(tmp)
    installed=prepare(base)
    r=run(installed,base/"backups")
    assert r.returncode==0,r.stdout
    assert (installed/"VERSION").read_text().strip()=="12.67"
    assert (installed/"node_agent/VERSION").read_text().strip()=="12.67"
    assert (installed/"node_agent/app.py").read_bytes()==(ROOT/"node_agent/app.py").read_bytes()
    second=run(installed,base/"backups")
    assert second.returncode==0,second.stdout

with tempfile.TemporaryDirectory(prefix="sf-v1267-rollback-") as tmp:
    base=Path(tmp)
    installed=prepare(base)
    original={p:(installed/p).read_bytes() for p in ["VERSION","node_agent/VERSION","node_agent/app.py"]}
    r=run(installed,base/"backups",fail=True)
    assert r.returncode!=0,r.stdout
    for p,content in original.items(): assert (installed/p).read_bytes()==content,p

print("PASS: v12.67 Node session permission fix; bounded upgrade/reinstall/rollback")
