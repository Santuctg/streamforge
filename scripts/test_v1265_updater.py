#!/usr/bin/env python3
"""v12.65 bounded updater install/reinstall/rollback + Node runtime parity tests."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/force_update_v1265.sh"
FILES = ("VERSION", "node_agent/VERSION", "app/main.py", "app/models.py", "app/permissions.py",
         "app/templates/admin_users.html", "app/templates/admin_user_form.html")
MODULE = "app/panel_password_vault.py"
LOGIN_GUARD_MODULE = "app/panel_login_guard.py"


def file_hash(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


def prepare(base: Path) -> tuple[Path, Path]:
    app = base / "opt/streamforge"
    backup = base / "backups"
    (app/"app/templates").mkdir(parents=True)
    shutil.copytree(ROOT/"node_agent", app/"node_agent", dirs_exist_ok=True)
    (app/"VERSION").write_text("12.64\n", encoding="utf-8")
    (app/"node_agent/VERSION").write_text("12.64\n", encoding="utf-8")
    import runpy
    transform = runpy.run_path(str(ROOT/"scripts/apply_v1264_ip_lock.py"))["transform"]
    source = (ROOT/"app/main.py").read_text(encoding="utf-8")
    (app/"app/main.py").write_text(transform(source), encoding="utf-8")
    (app/"app/models.py").write_text("PREVIOUS_MODELS\n",encoding="utf-8")
    (app/"app/permissions.py").write_text("PREVIOUS_PERMISSIONS\n",encoding="utf-8")
    (app/"app/templates/admin_users.html").write_text("OLD_LISTING\n",encoding="utf-8")
    (app/"app/templates/admin_user_form.html").write_text("OLD_FORM\n",encoding="utf-8")
    return app,backup


def run(app:Path,backup:Path,fail:bool=False) -> subprocess.CompletedProcess:
    env=os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup),
               STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1265_TEST_FAIL_AFTER_WRITE"]="1"
    return subprocess.run(["bash",str(UPDATER)],cwd=ROOT,env=env,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)


def main() -> None:
    assert UPDATER.is_file()
    with tempfile.TemporaryDirectory(prefix="sf-v1265-green-") as tmp:
        app,backups=prepare(Path(tmp))
        node_before={p.relative_to(app/"node_agent"):file_hash(p)
                     for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        result=run(app,backups)
        if result.returncode:
            raise SystemExit("v12.65 updater failed:\n"+result.stdout)
        assert (app/"VERSION").read_text().strip()=="12.65"
        assert (app/"node_agent/VERSION").read_text().strip()=="12.65"
        for rel in FILES[3:]:
            assert file_hash(app/rel)==file_hash(ROOT/rel),rel
        assert file_hash(app/MODULE)==file_hash(ROOT/MODULE)
        assert file_hash(app/LOGIN_GUARD_MODULE)==file_hash(ROOT/LOGIN_GUARD_MODULE)
        main_src=(app/"app/main.py").read_text(encoding="utf-8")
        assert "STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER" in main_src
        assert "record_ip_failure(source_ip, origin=" in main_src
        node_after={p.relative_to(app/"node_agent"):file_hash(p)
                    for p in (app/"node_agent").rglob("*") if p.is_file() and p.name!="VERSION"}
        assert node_before==node_after
        second=run(app,backups)
        if second.returncode:raise SystemExit("v12.65 reinstall failed:\n"+second.stdout)
    with tempfile.TemporaryDirectory(prefix="sf-v1265-rollback-") as tmp:
        app,backups=prepare(Path(tmp))
        originals={rel:(app/rel).read_bytes() for rel in FILES}
        result=run(app,backups,fail=True)
        assert result.returncode!=0
        for rel,value in originals.items():
            assert (app/rel).read_bytes()==value,rel
        assert not (app/MODULE).exists()
        assert not (app/LOGIN_GUARD_MODULE).exists()
    print("PASS: v12.65 install, idempotent reinstall, controlled rollback, Node parity")


if __name__ == "__main__":
    main()
