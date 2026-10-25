#!/usr/bin/env python3
"""v12.68 Main-only scoped update, reinstall and failure rollback."""
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.68":
    print("SKIP: v12.68 updater test runs only while VERSION is 12.68")
    raise SystemExit(0)
from pathlib import Path
import os,shutil,subprocess,tempfile
from scripts.apply_v1268_password_modal import transform

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1268.sh"
FILES=("VERSION","node_agent/VERSION","app/main.py",
       "app/panel_password_vault.py","app/templates/admin_users.html")
def prepare(base):
    root=base/"opt/streamforge"
    (root/"app/templates").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent",root/"node_agent")
    (root/"VERSION").write_text("12.67\n")
    (root/"node_agent/VERSION").write_text("12.67\n")
    for path in ("app/panel_password_vault.py","app/templates/admin_users.html"):
        (root/path).write_text("OLD:"+path+"\n",encoding="utf-8")
    (root/"app/main.py").write_text((ROOT/"app/main.py").read_text(encoding="utf-8"))
    return root
def run(root,backups,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(root),STREAMFORGE_BACKUP_ROOT=str(backups),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:env["STREAMFORGE_V1268_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

with tempfile.TemporaryDirectory(prefix="sf-v1268-ok-") as temp:
    root=prepare(Path(temp))
    node_before={(p.relative_to(root/"node_agent")).as_posix():p.read_bytes()
                 for p in (root/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
    first=run(root,Path(temp)/"backup")
    assert first.returncode==0,first.stdout
    assert (root/"VERSION").read_text().strip()=="12.68"
    assert (root/"node_agent/VERSION").read_text().strip()=="12.68"
    assert (root/"app/main.py").read_text()==transform((ROOT/"app/main.py").read_text())
    for path in FILES[3:]:
        assert (root/path).read_bytes()==(ROOT/path).read_bytes(),path
    node_after={(p.relative_to(root/"node_agent")).as_posix():p.read_bytes()
                 for p in (root/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
    assert node_before==node_after
    again=run(root,Path(temp)/"backup")
    assert again.returncode==0,again.stdout
with tempfile.TemporaryDirectory(prefix="sf-v1268-rollback-") as temp:
    root=prepare(Path(temp))
    before={p:(root/p).read_bytes() for p in FILES}
    failure=run(root,Path(temp)/"backup",fail=True)
    assert failure.returncode!=0,failure.stdout
    for path,content in before.items():
        assert (root/path).read_bytes()==content,path
print("PASS: v12.68 install/reinstall/controlled rollback and unchanged Node parity")
