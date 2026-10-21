#!/usr/bin/env python3
"""Release v12.63 updater integration: bounded install, rollback and preserved key."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1263.sh"
FILES=("VERSION","node_agent/VERSION","app/main.py","app/db.py","app/models.py","app/templates/admin_users.html","app/templates/admin_user_form.html")
NEW="app/panel_password_security.py"

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def setup(base):
    app=base/"opt/streamforge"
    backup=base/"backups"
    key=base/"vault.key"
    (app/"app/templates").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent",dirs_exist_ok=True)
    for name in FILES:
        dest=app/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text("12.62\n" if name.endswith("VERSION") else ("OLD-V1262-"+name+"\n"))
    shutil.copy2(ROOT/"app/templates/node_updates.html",app/"app/templates/node_updates.html")
    return app,backup,key

def run(app,backup,key,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
        STREAMFORGE_HOTFIX_TEST_MODE="1",STREAMFORGE_PANEL_VAULT_KEY_FILE=str(key))
    if fail:env["STREAMFORGE_V1263_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False)

def verify():
    assert UPDATER.exists()
    with tempfile.TemporaryDirectory(prefix="streamforge-v1263-install-") as temp:
        app,backup,key=setup(Path(temp))
        node_before={p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        old_update_ui=digest(app/"app/templates/node_updates.html")
        proc=run(app,backup,key)
        if proc.returncode:raise AssertionError("INSTALL FAILED:\n"+proc.stdout)
        assert key.is_file(), "separate key missing"
        key_before=key.read_bytes()
        assert len(bytes.fromhex(key_before.decode().strip()))==32
        for name in FILES:
            if name.endswith("VERSION"):assert (app/name).read_text().strip()=="12.63"
            else:assert digest(app/name)==digest(ROOT/name),name
        assert digest(app/NEW)==digest(ROOT/NEW)
        assert digest(app/"app/templates/node_updates.html")==old_update_ui
        assert node_before=={p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        proc=run(app,backup,key)
        if proc.returncode:raise AssertionError("REINSTALL FAILED:\n"+proc.stdout)
        assert key.read_bytes()==key_before, "reinstall rotated the encryption key"
    with tempfile.TemporaryDirectory(prefix="streamforge-v1263-rollback-") as temp:
        app,backup,key=setup(Path(temp))
        old={name:(app/name).read_bytes() for name in FILES}
        proc=run(app,backup,key,fail=True)
        assert proc.returncode!=0, "failure injection unexpectedly succeeded"
        for name,original in old.items():assert (app/name).read_bytes()==original,name
        assert not (app/NEW).exists(), "rollback left the newly added vault source installed"
    print("PASS: v12.63 updater install, reinstall, rollback, key persistence, Node unchanged")

if __name__=="__main__":
    verify()
