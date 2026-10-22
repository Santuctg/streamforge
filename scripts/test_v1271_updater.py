#!/usr/bin/env python3
"""v12.71 scoped updater and rollback; no Node/runtime changes."""
from __future__ import annotations
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/force_update_v1271.sh"
FILES=("VERSION","node_agent/VERSION",
       "app/templates/login.html","app/templates/admin_users.html")

def prepare(base:Path):
    app=base/"installed"
    (app/"app/templates").mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent")
    (app/"VERSION").write_text("12.70\n",encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.70\n",encoding="utf-8")
    (app/"app/templates/login.html").write_text("OLD LOGIN\n",encoding="utf-8")
    (app/"app/templates/admin_users.html").write_text("OLD ADMIN USERS\n",encoding="utf-8")
    return app

def run(app:Path,backup:Path,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:env["STREAMFORGE_V1271_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(SCRIPT)],cwd=ROOT,env=env,
                          capture_output=True,text=True)

with tempfile.TemporaryDirectory(prefix="sf-v1271-install-") as d:
    base=Path(d);app=prepare(base)
    before_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                 for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    result=run(app,base/"backups")
    assert result.returncode==0,result.stdout+result.stderr
    assert (app/"VERSION").read_text().strip()=="12.71"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.71"
    for path in FILES[2:]:
        assert (app/path).read_bytes()==(ROOT/path).read_bytes(),path
    after_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    assert before_node==after_node
    again=run(app,base/"backups")
    assert again.returncode==0,again.stdout+again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1271-rollback-") as d:
    base=Path(d);app=prepare(base)
    before={x:(app/x).read_bytes() for x in FILES}
    result=run(app,base/"backups",fail=True)
    assert result.returncode!=0
    for path,content in before.items():
        assert (app/path).read_bytes()==content,path
print("PASS: v12.71 scoped Main-only upgrade, reinstall, rollback and exact Node parity")
