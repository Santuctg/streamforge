#!/usr/bin/env python3
"""Bounded v12.66 upgrade and rollback from a simulated v12.65 install."""
import hashlib
import os
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT/"scripts/force_update_v1266.sh"
FILES = (
    "VERSION", "node_agent/VERSION", "node_agent/app.py",
    "app/main.py", "app/models.py", "app/permissions.py",
    "app/panel_password_vault.py", "app/panel_login_guard.py",
    "app/templates/admin_users.html", "app/templates/admin_user_form.html",
    "app/templates/account_password.html",
)

def make_main():
    original=(ROOT/"app/main.py").read_text(encoding="utf-8")
    a=runpy.run_path(str(ROOT/"scripts/apply_v1264_ip_lock.py"))["transform"]
    b=runpy.run_path(str(ROOT/"scripts/apply_v1265_ip_origin.py"))["transform"]
    return b(a(original))

def prepare(base):
    path=base/"installed"
    (path/"app/templates").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent",path/"node_agent",dirs_exist_ok=True)
    for file in FILES:
        if file in ("VERSION","node_agent/VERSION","app/main.py"):
            continue
        dest=path/file
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/file,dest)
    (path/"VERSION").write_text("12.65\n")
    (path/"node_agent/VERSION").write_text("12.65\n")
    (path/"app/main.py").write_text(make_main())
    return path

def run(app, backup, fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail: env["STREAMFORGE_V1266_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],env=env,cwd=ROOT,
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)

with tempfile.TemporaryDirectory(prefix="sf-auto-vault-install-") as tmp:
    base=Path(tmp)
    app=prepare(base)
    old={p:(app/p).read_bytes() for p in FILES}
    success=run(app,base/"backups")
    assert success.returncode==0, success.stdout
    assert (app/"VERSION").read_text().strip()=="12.66"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.66"
    for file in ("node_agent/app.py","app/templates/admin_user_form.html",
                 "app/templates/account_password.html"):
        assert (app/file).read_bytes()==(ROOT/file).read_bytes()
    assert "STREAMFORGE_V1266_AUTO_VAULT" in (app/"app/main.py").read_text()
    second=run(app,base/"backups")
    assert second.returncode==0, second.stdout

with tempfile.TemporaryDirectory(prefix="sf-auto-vault-rollback-") as tmp:
    base=Path(tmp)
    app=prepare(base)
    old={p:(app/p).read_bytes() for p in FILES}
    failed=run(app,base/"backups",fail=True)
    assert failed.returncode!=0
    for file, value in old.items():
        assert (app/file).read_bytes()==value,file

print("PASS: v12.66 install/idempotence/rollback Main + disclosed Node source")
