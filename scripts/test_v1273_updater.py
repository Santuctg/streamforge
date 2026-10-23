#!/usr/bin/env python3
"""v12.73 Main hotfix updater: staged main.py patch, reinstall and rollback."""
from __future__ import annotations
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/force_update_v1273.sh"
FILES=("VERSION","node_agent/VERSION","app/main.py")
sys.path.insert(0,str(ROOT/"scripts"))
import apply_main_hotfixes as hotfixes  # noqa: E402
INSTALLED_PATCHERS=("apply_v1264_ip_lock","apply_v1265_ip_origin",
                    "apply_v1266_auto_vault","apply_v1268_password_modal")

def installed_patches(path:Path):
    for name in INSTALLED_PATCHERS:
        subprocess.run([sys.executable,"-B",str(ROOT/"scripts"/f"{name}.py"),str(path)],
                       check=True,capture_output=True)

def pre_hotfix_main()->str:
    """The repo main.py with the three v12.73 hotfixes reverted."""
    source=(ROOT/"app/main.py").read_text(encoding="utf-8")
    pairs=(("\nimport secrets\nimport shutil\n","\nimport secrets\n",1),
           ('saved=False, message="", error=',
            'saved=False, message="",'+hotfixes.BRANDING_ERROR_CONTEXT+' error=',2),
           (hotfixes.ADMIN_SEED_UPDATED,hotfixes.ADMIN_SEED_ORIGINAL,1))
    for new,old,count in pairs:
        assert source.count(new)==count,new[:40]
        source=source.replace(new,old)
    assert hotfixes.transform(source)==(ROOT/"app/main.py").read_text(encoding="utf-8")
    return source

def prepare(base:Path):
    app=base/"installed"
    (app/"app").mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/"node_agent",app/"node_agent")
    (app/"VERSION").write_text("12.72\n",encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.72\n",encoding="utf-8")
    (app/"app/main.py").write_text(pre_hotfix_main(),encoding="utf-8")
    installed_patches(app/"app/main.py")
    return app

def run(app:Path,backup:Path,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:env["STREAMFORGE_V1273_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(SCRIPT)],cwd=ROOT,env=env,
                          capture_output=True,text=True)

with tempfile.TemporaryDirectory(prefix="sf-v1273-install-") as d:
    base=Path(d);app=prepare(base)
    expected=base/"expected-main.py"
    shutil.copy(ROOT/"app/main.py",expected)
    installed_patches(expected)
    assert (app/"app/main.py").read_bytes()!=expected.read_bytes()
    before_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                 for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    result=run(app,base/"backups")
    assert result.returncode==0,result.stdout+result.stderr
    assert (app/"VERSION").read_text().strip()=="12.73"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.73"
    assert (app/"app/main.py").read_bytes()==expected.read_bytes()
    after_node={(x.relative_to(app/"node_agent")).as_posix():x.read_bytes()
                for x in (app/"node_agent").rglob("*") if x.is_file() and x.name!="VERSION"}
    assert before_node==after_node
    again=run(app,base/"backups")
    assert again.returncode==0,again.stdout+again.stderr
    assert (app/"app/main.py").read_bytes()==expected.read_bytes()

with tempfile.TemporaryDirectory(prefix="sf-v1273-rollback-") as d:
    base=Path(d);app=prepare(base)
    before={x:(app/x).read_bytes() for x in FILES}
    result=run(app,base/"backups",fail=True)
    assert result.returncode!=0
    for path,content in before.items():
        assert (app/path).read_bytes()==content,path

with tempfile.TemporaryDirectory(prefix="sf-v1273-old-") as d:
    base=Path(d);app=prepare(base)
    (app/"VERSION").write_text("12.71\n",encoding="utf-8")
    before=(app/"app/main.py").read_bytes()
    result=run(app,base/"backups")
    assert result.returncode!=0 and "requires installed Main v12.72" in result.stderr
    assert (app/"app/main.py").read_bytes()==before
print("PASS: v12.73 staged Main hotfix upgrade, reinstall, rollback, version gate and exact Node parity")
