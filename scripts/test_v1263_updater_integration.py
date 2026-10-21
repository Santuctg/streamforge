#!/usr/bin/env python3
"""v12.63 bounded Main vault updater integration: success/reinstall/rollback."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT/"scripts/force_update_v1263.sh"
FILES = (
    "VERSION", "node_agent/VERSION",
    "app/main.py", "app/models.py", "app/db.py",
    "app/templates/admin_users.html", "app/templates/admin_user_form.html",
)
NEW = "app/panel_password_vault.py"

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def setup(base):
    app=base/"opt/streamforge"
    backup=base/"var/backups/streamforge"
    (app/"app/templates").mkdir(parents=True)
    (app/"app/static").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent", app/"node_agent", dirs_exist_ok=True)
    for f in FILES[2:]:
        (app/f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/f,app/f)
    for name in ("auth.py","config.py","panel_nav.js"):
        src=ROOT/"app"/("static/"+name if name.endswith(".js") else name)
        dst=app/"app"/("static/"+name if name.endswith(".js") else name)
        shutil.copy2(src,dst)
    shutil.copy2(ROOT/"app/templates/node_updates.html", app/"app/templates/node_updates.html")
    (app/"VERSION").write_text("12.62\n")
    (app/"node_agent/VERSION").write_text("12.62\n")
    return app,backup

def run(app,backup,fail=False):
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app),STREAMFORGE_BACKUP_ROOT=str(backup),STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1263_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)], cwd=ROOT, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

def main():
    if not UPDATER.is_file():
        raise SystemExit("force_update_v1263.sh missing")
    with tempfile.TemporaryDirectory(prefix="streamforge-v1263-success-") as tmp:
        app,backup=setup(Path(tmp))
        node_before={p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        changes_before=digest(app/"app/templates/node_updates.html")
        result=run(app,backup)
        if result.returncode: raise SystemExit("Install failed:\n"+result.stdout)
        assert (app/"VERSION").read_text().strip()=="12.63"
        assert (app/"node_agent/VERSION").read_text().strip()=="12.63"
        for f in FILES[2:]+(NEW,):
            assert digest(app/f)==digest(ROOT/f), f
        assert digest(app/"app/templates/node_updates.html")==changes_before
        assert {p.relative_to(app/"node_agent"):digest(p) for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}==node_before
        repeated=run(app,backup)
        if repeated.returncode: raise SystemExit("Reinstall failed:\n"+repeated.stdout)
    with tempfile.TemporaryDirectory(prefix="streamforge-v1263-rollback-") as tmp:
        app,backup=setup(Path(tmp))
        old={f:(app/f).read_bytes() for f in FILES}
        failed=run(app,backup,True)
        assert failed.returncode != 0, "missing controlled rollback"
        for f,value in old.items():
            assert (app/f).read_bytes()==value,f
        assert not (app/NEW).exists(), "new vault module must be removed after rollback"
    print("PASS: v12.63 install/reinstall/rollback, Node runtime byte parity, and unrelated UI preserved")

if __name__=="__main__":
    main()
