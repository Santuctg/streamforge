#!/usr/bin/env python3
"""Exercise v12.72 Node-login scoped upgrade, reinstall and rollback."""
from pathlib import Path
import os,shutil,subprocess,tempfile,hashlib

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1272.sh"

def previous_agent() -> bytes:
    raw=subprocess.check_output(["git","show","v12.71:node_agent/app.py"],cwd=ROOT)
    blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
    assert blob=="b023c2fd96c46bbeec1c16aa42c786bcf7a5544e",blob
    return raw

def prepare(base:Path):
    installed=base/"installed"
    shutil.copytree(ROOT/"node_agent",installed/"node_agent")
    (installed/"node_agent/app.py").write_bytes(previous_agent())
    (installed/"VERSION").write_text("12.71\n")
    (installed/"node_agent/VERSION").write_text("12.71\n")
    return installed

def run(root:Path,base:Path,failure:bool=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(root),STREAMFORGE_BACKUP_ROOT=str(base/"backup"),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if failure: env["STREAMFORGE_V1272_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

with tempfile.TemporaryDirectory(prefix="sf-node-login-success-") as tmp:
    base=Path(tmp);root=prepare(base)
    initial={
        p.relative_to(root/"node_agent").as_posix():p.read_bytes()
        for p in (root/"node_agent").rglob("*") if p.is_file() and p.name not in ("VERSION","app.py")
    }
    ok=run(root,base)
    assert ok.returncode==0,ok.stdout
    assert (root/"VERSION").read_text().strip()=="12.72"
    assert (root/"node_agent/VERSION").read_text().strip()=="12.72"
    assert (root/"node_agent/app.py").read_bytes()==(ROOT/"node_agent/app.py").read_bytes()
    after={p.relative_to(root/"node_agent").as_posix():p.read_bytes()
           for p in (root/"node_agent").rglob("*") if p.is_file() and p.name not in ("VERSION","app.py")}
    assert initial==after
    second=run(root,base)
    assert second.returncode==0,second.stdout
with tempfile.TemporaryDirectory(prefix="sf-node-login-rollback-") as tmp:
    base=Path(tmp);root=prepare(base)
    before={p:(root/p).read_bytes() for p in ("VERSION","node_agent/VERSION","node_agent/app.py")}
    bad=run(root,base,True)
    assert bad.returncode!=0,bad.stdout
    for p,raw in before.items():assert (root/p).read_bytes()==raw,p
print("PASS: v12.72 Node-only login update, reinstall, unchanged runtime and controlled rollback")
