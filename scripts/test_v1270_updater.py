#!/usr/bin/env python3
"""v12.70 exact 10-second reveal + compact Node-form upgrade/rollback tests."""
from pathlib import Path
import os,shutil,subprocess,tempfile,hashlib

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1270.sh"
PREVIOUS_SHA="a65a53a4dc8f071cb4f906d18b242ae11b864a97"
NEW_MARKER="    # STREAMFORGE_V1270_NODE_PASSWORD_FORM_CLEANUP: no extra paragraph; concise in heading."
OLD_MARKER="    # STREAMFORGE_V1269_COMPACT_NODE_PASSWORD_NOTICE: concise visible disclosure + aligned form."
NEW_HEADING="        + '. The credential is changed on the Main Server and is immediately valid on this Node. As a managed account, authorized administrators can recover changed passwords.</p></div></div>'"
OLD_HEADING="        + '. The credential is changed on the Main Server and is immediately valid on this Node.</p></div></div>'"
NEW_END="        + '</div></section>'\n        + '<div class=\"form-actions\"><button class=\"btn primary\" type=\"submit\">Change password</button></div>'"
OLD_END="        + '</div><p class=\"field-help\" role=\"note\">New passwords are encrypted on Main and can be viewed by authorized administrators.</p></section>'\n        + '<div class=\"form-actions\"><button class=\"btn primary\" type=\"submit\">Change password</button></div>'"

def git_blob(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def before_node():
    source=(ROOT/"node_agent/app.py").read_text(encoding="utf-8")
    for old,new in ((NEW_MARKER,OLD_MARKER),(NEW_HEADING,OLD_HEADING),(NEW_END,OLD_END)):
        assert source.count(old)==1,"Missing reconstruction anchor: "+old[:90]
        source=source.replace(old,new)
    assert git_blob(source.encode("utf-8"))==PREVIOUS_SHA,"Old Node source hash mismatch"
    return source

def prepare(base):
    root=base/"installed"
    shutil.copytree(ROOT/"node_agent",root/"node_agent")
    (root/"node_agent/app.py").write_text(before_node(),encoding="utf-8")
    (root/"node_agent/VERSION").write_text("12.69\n")
    (root/"VERSION").write_text("12.69\n")
    p=root/"app/templates/admin_users.html"
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text("old-v12.69-admin-users-template\n")
    return root

def run(root,backup,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(root),STREAMFORGE_BACKUP_ROOT=str(backup),STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail: env["STREAMFORGE_V1270_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

with tempfile.TemporaryDirectory(prefix="sf-v1270-up-") as tmp:
    base=Path(tmp);root=prepare(base)
    previous={p.relative_to(root/"node_agent").as_posix():p.read_bytes()
              for p in (root/"node_agent").rglob("*") if p.is_file() and p.name not in {"app.py","VERSION"}}
    r=run(root,base/"backups")
    assert r.returncode==0,r.stdout
    assert (root/"VERSION").read_text().strip()=="12.70"
    assert (root/"node_agent/VERSION").read_text().strip()=="12.70"
    assert (root/"node_agent/app.py").read_bytes()==(ROOT/"node_agent/app.py").read_bytes()
    assert (root/"app/templates/admin_users.html").read_bytes()==(ROOT/"app/templates/admin_users.html").read_bytes()
    after={p.relative_to(root/"node_agent").as_posix():p.read_bytes()
           for p in (root/"node_agent").rglob("*") if p.is_file() and p.name not in {"app.py","VERSION"}}
    assert previous==after
    again=run(root,base/"backups")
    assert again.returncode==0,again.stdout
with tempfile.TemporaryDirectory(prefix="sf-v1270-rollback-") as tmp:
    base=Path(tmp);root=prepare(base)
    files=("VERSION","node_agent/VERSION","node_agent/app.py","app/templates/admin_users.html")
    original={p:(root/p).read_bytes() for p in files}
    bad=run(root,base/"backups",fail=True)
    assert bad.returncode!=0,bad.stdout
    for p,contents in original.items():assert (root/p).read_bytes()==contents,p

print("PASS: v12.70 baseline SHA, updater, reinstall, rollback and unchanged Node payload")
