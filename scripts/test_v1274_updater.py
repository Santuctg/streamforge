#!/usr/bin/env python3
"""v12.74 metadata-only updater: installed Main verification, reinstall and rollback."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.74":
    print("SKIP: v12.74 updater test runs only while VERSION is 12.74")
    raise SystemExit(0)
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/force_update_v1274.sh"
FILES=("VERSION","node_agent/VERSION","app/main.py")

def prepare(base:Path,main_source:str|None=None):
    app=base/"installed"
    (app/"app").mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent")
    (app/"VERSION").write_text("12.73\n",encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.73\n",encoding="utf-8")
    # An installed v12.73 Main equals the integrated release source.
    source=main_source if main_source is not None else (ROOT/"app/main.py").read_text(encoding="utf-8")
    (app/"app/main.py").write_text(source,encoding="utf-8")
    return app

def run(app:Path,backup:Path,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:env["STREAMFORGE_V1274_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(SCRIPT)],cwd=ROOT,env=env,
                          capture_output=True,text=True)

def snapshot(app:Path):
    return {x:(app/x).read_bytes() for x in FILES}

with tempfile.TemporaryDirectory(prefix="sf-v1274-install-") as d:
    base=Path(d);app=prepare(base)
    main_before=(app/"app/main.py").read_bytes()
    result=run(app,base/"backups")
    assert result.returncode==0,result.stdout+result.stderr
    assert (app/"VERSION").read_text().strip()=="12.74"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.74"
    assert (app/"app/main.py").read_bytes()==main_before
    again=run(app,base/"backups")
    assert again.returncode==0,again.stdout+again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1274-rollback-") as d:
    base=Path(d);app=prepare(base)
    before=snapshot(app)
    result=run(app,base/"backups",fail=True)
    assert result.returncode!=0
    assert snapshot(app)==before

with tempfile.TemporaryDirectory(prefix="sf-v1274-unpatched-") as d:
    base=Path(d)
    source=(ROOT/"app/main.py").read_text(encoding="utf-8").replace("STREAMFORGE_V1268_PW_MODAL_CSRF","")
    app=prepare(base,source)
    before=snapshot(app)
    result=run(app,base/"backups")
    assert result.returncode!=0 and "missing earlier patches" in result.stderr,result.stdout+result.stderr
    assert snapshot(app)==before

with tempfile.TemporaryDirectory(prefix="sf-v1274-old-") as d:
    base=Path(d);app=prepare(base)
    (app/"VERSION").write_text("12.72\n",encoding="utf-8")
    before=snapshot(app)
    result=run(app,base/"backups")
    assert result.returncode!=0 and "requires installed Main v12.73" in result.stderr
    assert snapshot(app)==before
print("PASS: v12.74 metadata-only upgrade, reinstall, rollback, installed-Main and version gates, Node parity")
