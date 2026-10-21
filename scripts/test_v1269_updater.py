#!/usr/bin/env python3
"""v12.69 bounded UI upgrade from v12.68; rollback without Node runtime drift."""
from pathlib import Path
import os,shutil,subprocess,tempfile,hashlib

ROOT=Path(__file__).resolve().parents[1]
UPDATER=ROOT/"scripts/force_update_v1269.sh"
BEFORE_SHA="256faac6acf0cee0f3ee223063d222881fb7a824"
MARKER="    # STREAMFORGE_V1269_COMPACT_NODE_PASSWORD_NOTICE: concise visible disclosure + aligned form.\n"
COMPACT="        + '</div><p class=\"field-help\" role=\"note\">New passwords are encrypted on Main and can be viewed by authorized administrators.</p></section>'"
LONG="        + '<p class=\"field-help\" role=\"note\"><strong>Managed password retention:</strong> Your new password will be encrypted and retained on the Main Server so authorized administrators can view it after reauthentication. No checkbox is required. If you do not want an administrator-recoverable password, contact your Panel administrator before continuing.</p>'"
NEW_CONFIRM="        + '<label style=\"grid-column:1 / -1\">Confirm new password<input type=\"password\" name=\"new_password_confirm\" minlength=\"8\" autocomplete=\"new-password\" required></label>'"
OLD_CONFIRM="        + '<label>Confirm new password<input type=\"password\" name=\"new_password_confirm\" minlength=\"8\" autocomplete=\"new-password\" required></label>'"

def previous_node_source():
    source=(ROOT/"node_agent/app.py").read_text()
    assert source.count(MARKER)==1 and source.count(COMPACT)==1 and source.count(NEW_CONFIRM)==1
    old=source.replace(MARKER,"").replace(COMPACT,"        + '</div></section>'").replace(NEW_CONFIRM,OLD_CONFIRM)
    anchor="        + '<div class=\"form-grid\">'"
    assert old.count(anchor)==1
    old=old.replace(anchor,LONG+"\n"+anchor)
    return old

def blob_sha(value:bytes):
    return hashlib.sha1(b"blob "+str(len(value)).encode()+b"\0"+value).hexdigest()

def prepare(root):
    app=root/"installed"
    shutil.copytree(ROOT/"node_agent",app/"node_agent")
    orig=previous_node_source()
    assert blob_sha(orig.encode())==BEFORE_SHA, "Baseline reconstruction unexpectedly changed; refuse blind update"
    (app/"node_agent/app.py").write_text(orig,encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.68\n")
    (app/"VERSION").write_text("12.68\n")
    path=app/"app/templates/admin_users.html"
    path.parent.mkdir(parents=True)
    # The old version must have existed; rollback must preserve original.
    path.write_text("PREVIOUS_THEME_PLACEHOLDER\n")
    return app

def run(app,backup,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail: env["STREAMFORGE_V1269_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],env=env,cwd=ROOT,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)

with tempfile.TemporaryDirectory(prefix="sf-v1269-install-") as temp:
    base=Path(temp)
    app=prepare(base)
    before_other={p.relative_to(app/"node_agent").as_posix():p.read_bytes()
                  for p in (app/"node_agent").rglob("*") if p.is_file() and p.name not in ("VERSION","app.py")}
    passed=run(app,base/"backups")
    assert passed.returncode==0,passed.stdout
    assert (app/"VERSION").read_text().strip()=="12.69"
    assert (app/"node_agent/VERSION").read_text().strip()=="12.69"
    assert (app/"node_agent/app.py").read_bytes()==(ROOT/"node_agent/app.py").read_bytes()
    assert (app/"app/templates/admin_users.html").read_bytes()==(ROOT/"app/templates/admin_users.html").read_bytes()
    after_other={p.relative_to(app/"node_agent").as_posix():p.read_bytes()
                 for p in (app/"node_agent").rglob("*") if p.is_file() and p.name not in ("VERSION","app.py")}
    assert before_other==after_other
    repeated=run(app,base/"backups")
    assert repeated.returncode==0,repeated.stdout

with tempfile.TemporaryDirectory(prefix="sf-v1269-rollback-") as temp:
    base=Path(temp)
    app=prepare(base)
    paths=["VERSION","node_agent/VERSION","node_agent/app.py","app/templates/admin_users.html"]
    snap={p:(app/p).read_bytes() for p in paths}
    failed=run(app,base/"backups",fail=True)
    assert failed.returncode!=0,failed.stdout
    for path,data in snap.items(): assert (app/path).read_bytes()==data,path
print("PASS: Main+bundled Node theme/notice upgrade, safe reinstall, rollback, unchanged runtime")
