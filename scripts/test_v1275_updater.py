#!/usr/bin/env python3
"""v12.75 Secure session cookie updater: staged main.py patch, reinstall and rollback."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.75":
    print("SKIP: v12.75 updater test runs only while VERSION is 12.75")
    raise SystemExit(0)
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/force_update_v1275.sh"
FILES=("VERSION","node_agent/VERSION","app/main.py")
sys.path.insert(0,str(ROOT/"scripts"))
import apply_main_hotfixes as hotfixes  # noqa: E402

def v1274_main()->str:
    """The repo main.py with the v12.75 Secure-cookie fix reverted (installed v12.74 Main)."""
    source=(ROOT/"app/main.py").read_text(encoding="utf-8")
    pairs=(("\n\n\n"+hotfixes.SECURE_COOKIE_CLASS+hotfixes.SECURE_COOKIE_CLASS_ANCHOR,
            hotfixes.SECURE_COOKIE_CLASS_ANCHOR),
           (hotfixes.SECURE_COOKIE_MIDDLEWARE_UPDATED,hotfixes.SECURE_COOKIE_MIDDLEWARE_ORIGINAL))
    for new,old in pairs:
        assert source.count(new)==1,new[:40]
        source=source.replace(new,old)
    assert hotfixes.SECURE_COOKIE_MARKER not in source
    assert hotfixes.transform(source)==(ROOT/"app/main.py").read_text(encoding="utf-8")
    return source

def prepare(base:Path):
    app=base/"installed"
    (app/"app").mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent")
    (app/"VERSION").write_text("12.74\n",encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.74\n",encoding="utf-8")
    (app/"app/main.py").write_text(v1274_main(),encoding="utf-8")
    return app

def run(app:Path,backup:Path,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:env["STREAMFORGE_V1275_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(SCRIPT)],cwd=ROOT,env=env,
                          capture_output=True,text=True)

def snapshot(app:Path):
    return {x:(app/x).read_bytes() for x in FILES}

with tempfile.TemporaryDirectory(prefix="sf-v1275-install-") as d:
    base=Path(d);app=prepare(base)
    before_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                 for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    result=run(app,base/"backups")
    assert result.returncode==0,result.stdout+result.stderr
    assert (app/"VERSION").read_text().strip()=="12.75"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.75"
    assert (app/"app/main.py").read_bytes()==(ROOT/"app/main.py").read_bytes()
    after_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    assert before_node==after_node
    again=run(app,base/"backups")
    assert again.returncode==0,again.stdout+again.stderr
    assert (app/"app/main.py").read_bytes()==(ROOT/"app/main.py").read_bytes()

with tempfile.TemporaryDirectory(prefix="sf-v1275-rollback-") as d:
    base=Path(d);app=prepare(base)
    before=snapshot(app)
    result=run(app,base/"backups",fail=True)
    assert result.returncode!=0
    assert snapshot(app)==before

with tempfile.TemporaryDirectory(prefix="sf-v1275-old-") as d:
    base=Path(d);app=prepare(base)
    (app/"VERSION").write_text("12.73\n",encoding="utf-8")
    before=snapshot(app)
    result=run(app,base/"backups")
    assert result.returncode!=0 and "requires installed Main v12.74" in result.stderr
    assert snapshot(app)==before
print("PASS: v12.75 staged Secure-cookie Main upgrade, reinstall, rollback, version gate and exact Node parity")
