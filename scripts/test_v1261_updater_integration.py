#!/usr/bin/env python3
"""Bounded v12.61 install and rollback without touching Node runtime or existing channels."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.61":
    print("SKIP: v12.61 updater test runs only while VERSION is 12.61")
    raise SystemExit(0)
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1261.sh"
FILES=("VERSION","node_agent/VERSION","app/templates/admin_users.html","app/templates/admin_user_form.html")

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def setup(base):
    app=base/"opt/streamforge"
    backups=base/"backups"
    (app/"app/templates").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent",dirs_exist_ok=True)
    for name in ("auth.py", "models.py"):
        shutil.copy2(ROOT/"app"/name, app/"app"/name)
    (app/"VERSION").write_text("12.60\n")
    (app/"node_agent/VERSION").write_text("12.60\n")
    for file in FILES[2:]:
        (app/file).write_text("PREVIOUS_V1260_TEMPLATE\n")
    shutil.copy2(ROOT/"app/templates/node_updates.html",app/"app/templates/node_updates.html")
    return app,backups

def update(app,backups,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backups),STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1261_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False)

def main():
    if not UPDATER.is_file():
        raise SystemExit("scripts/force_update_v1261.sh is missing")
    with tempfile.TemporaryDirectory(prefix="streamforge-v1261-install-") as tmp:
        app,backups=setup(Path(tmp))
        original_node={p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        original_channels=digest(app/"app/templates/node_updates.html")
        result=update(app,backups)
        if result.returncode:raise SystemExit("Install failed:\n"+result.stdout)
        for name in FILES[:2]:assert (app/name).read_text().strip()=="12.61"
        for name in FILES[2:]:assert digest(app/name)==digest(ROOT/name),name
        assert digest(app/"app/templates/node_updates.html")==original_channels
        assert {p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}==original_node
        again=update(app,backups)
        if again.returncode:raise SystemExit("Idempotent reinstall failed:\n"+again.stdout)
    with tempfile.TemporaryDirectory(prefix="streamforge-v1261-rollback-") as tmp:
        app,backups=setup(Path(tmp))
        originals={name:(app/name).read_bytes() for name in FILES}
        result=update(app,backups,fail=True)
        assert result.returncode!=0, "expected controlled rollback failure"
        for name,content in originals.items():assert (app/name).read_bytes()==content,name
    print("PASS: v12.61 bounded install + reinstall + rollback, unchanged Node runtime and update center")

if __name__=="__main__":
    main()
