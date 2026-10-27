#!/usr/bin/env python3
"""v12.83 updater: staged hardening patch, Nginx real_ip, secret-key repair,
rollback, idempotence and version gates."""
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.83":
    print("SKIP: v12.83 updater test runs only while VERSION is 12.83")
    raise SystemExit(0)
from pathlib import Path
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1283.sh"
PATCH = json.loads((ROOT / "scripts" / "apply_v1283_hardening.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])
NEW_FILES = ["app/client_ip.py"]

# STREAMFORGE_V1283_UPDATER_RESTARTS_MAIN_WORKERS_V1: sources on disk mean
# nothing until the Gunicorn workers reload. The restart must live inside the
# test-mode guard (tests never call systemctl) and must not touch the channel
# supervisor. Nginx is reloaded — never restarted — because the installed site
# gains the real_ip block.
SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
assert re.search(
    r'if \[\[ "\$TEST" != 1 \]\]; then\n  RESTARTED=1\n  log "Restarting Main HTTP workers',
    SCRIPT_TEXT,
), "restart block missing from the test-mode guard"
assert re.search(r"^  systemctl restart streamforge$", SCRIPT_TEXT, re.M), "streamforge restart missing"
assert re.search(r"^  systemctl restart streamforge-public$", SCRIPT_TEXT, re.M), "streamforge-public restart missing"
assert not re.search(
    r"systemctl[ \t]+restart[ \t]+(nginx|streamforge-channel-supervisor)", SCRIPT_TEXT
), "updater must never restart Nginx or the channel supervisor"
assert re.search(r"^    nginx -t$", SCRIPT_TEXT, re.M), "Nginx configuration must be tested before the reload"
assert re.search(r"^    systemctl reload nginx$", SCRIPT_TEXT, re.M), "Nginx must be reloaded after the site patch"
assert "STREAMFORGE_V1283_HARDENING_UPDATER" in SCRIPT_TEXT
assert "12.82|12.83" in SCRIPT_TEXT
assert 'OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"' in SCRIPT_TEXT
assert "STREAMFORGE_V1283_TEST_FAIL_AFTER_WRITE" in SCRIPT_TEXT
assert "STREAMFORGE_SECRET_KEY repair failed" in SCRIPT_TEXT
assert "secrets.token_hex(32)" in SCRIPT_TEXT


def tagged(relative: str, ref: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (relative, ref, "shallow clone?")
    return result.stdout


def v1282_source(relative: str) -> str:
    """The installed v12.82 content, straight from the previous release tag."""
    source = tagged(relative, "v12.82")
    assert PATCH["files"][relative]["marker"] not in source
    return source


def prepare(base: Path) -> Path:
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "app/static", app / "app/static")
    (app / "app/templates").mkdir(parents=True, exist_ok=True)
    (app / "deploy").mkdir(parents=True, exist_ok=True)
    for relative in PATCH_FILES:
        (app / relative).parent.mkdir(parents=True, exist_ok=True)
        (app / relative).write_text(v1282_source(relative), encoding="utf-8")
    (app / "VERSION").write_text("12.82\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.82\n", encoding="utf-8")
    return app


def run(app: Path, backup: Path, env_file: Path | None = None, nginx_site: Path | None = None, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if env_file is not None:
        env["STREAMFORGE_ENV_FILE"] = str(env_file)
    if nginx_site is not None:
        env["STREAMFORGE_NGINX_SITE"] = str(nginx_site)
    if fail:
        env["STREAMFORGE_V1283_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path, env_file: Path | None = None, nginx_site: Path | None = None):
    files = PATCH_FILES + NEW_FILES + ["VERSION", "node_agent/VERSION", "node_agent/app.py"]
    state = {f: (app / f).read_bytes() if (app / f).exists() else None for f in files}
    for extra in (env_file, nginx_site):
        if extra is not None:
            state[str(extra)] = extra.read_bytes() if extra.exists() else None
    return state


def write_env(path: Path, key: str) -> None:
    path.write_text(
        f"STREAMFORGE_ADMIN_USER=admin\nSTREAMFORGE_SECRET_KEY={key}\nSTREAMFORGE_ADMIN_PASSWORD=x\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


# 1. The staged patch reproduces the release, adds the new module and bumps
#    both VERSION files; a second run is a clean no-op.
with tempfile.TemporaryDirectory(prefix="sf-v1283-install-") as d:
    base = Path(d)
    app = prepare(base)
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "VERSION").read_text().strip() == "12.83"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.83"
    for relative in PATCH_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
        assert PATCH["files"][relative]["marker"] in (app / relative).read_text(encoding="utf-8"), relative
    for relative in NEW_FILES:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
    again = run(app, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

# 2. A controlled failure after every write rolls sources, the new module,
#    the env file and the Nginx site back to their installed state.
with tempfile.TemporaryDirectory(prefix="sf-v1283-rollback-") as d:
    base = Path(d)
    app = prepare(base)
    env_file = base / "streamforge.env"
    write_env(env_file, "dev-change-me-now")
    nginx_site = base / "nginx-site.conf"
    nginx_site.write_text(tagged("deploy/nginx.conf", "v12.82"), encoding="utf-8")
    before = snapshot(app, env_file, nginx_site)
    result = run(app, base / "backups", env_file=env_file, nginx_site=nginx_site, fail=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert snapshot(app, env_file, nginx_site) == before, "controlled failure must roll every file back"
    assert not (app / "app/client_ip.py").exists(), "rollback must remove the module the update created"

# 3. A locally modified source refuses the update before anything is written.
with tempfile.TemporaryDirectory(prefix="sf-v1283-anchor-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "app/models.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "hardening patch preflight failed" in result.stderr, result.stdout + result.stderr
    assert snapshot(app) == before, "a refused anchor must leave the tree untouched"

# 4. The Node payload is unchanged in v12.83: a drifted file is restored to
#    the pinned v12.82 bytes rather than refused.
with tempfile.TemporaryDirectory(prefix="sf-v1283-node-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "node_agent/app.py").read_bytes() == (ROOT / "node_agent/app.py").read_bytes()

# 5. An older installed Main is refused with nothing written.
with tempfile.TemporaryDirectory(prefix="sf-v1283-old-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "VERSION").write_text("12.81\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.82" in result.stderr, result.stdout + result.stderr
    assert snapshot(app) == before

# 6. The secret-key repair: weak keys are replaced with a fresh 64-hex value
#    (the rest of the env file intact), strong keys are never touched, and a
#    host without the env file still updates cleanly.
with tempfile.TemporaryDirectory(prefix="sf-v1283-env-") as d:
    base = Path(d)
    app = prepare(base)
    env_file = base / "streamforge.env"
    write_env(env_file, "dev-change-me-now")
    result = run(app, base / "backups", env_file=env_file)
    assert result.returncode == 0, result.stdout + result.stderr
    repaired = env_file.read_text(encoding="utf-8")
    match = re.search(r"^STREAMFORGE_SECRET_KEY=([0-9a-f]+)$", repaired, re.M)
    assert match and len(match.group(1)) == 64, repaired
    assert "STREAMFORGE_ADMIN_USER=admin" in repaired and "STREAMFORGE_ADMIN_PASSWORD=x" in repaired
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"

    strong = secrets.token_hex(32)
    write_env(env_file, strong)
    result = run(app, base / "backups", env_file=env_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"STREAMFORGE_SECRET_KEY={strong}" in env_file.read_text(encoding="utf-8")

    result = run(app, base / "backups", env_file=base / "absent.env")
    assert result.returncode == 0, result.stdout + result.stderr

# 7. The installed Nginx site: anchor patch, drift prepend and idempotence.
with tempfile.TemporaryDirectory(prefix="sf-v1283-nginx-") as d:
    base = Path(d)
    app = prepare(base)
    nginx_site = base / "nginx-site.conf"
    nginx_site.write_text(tagged("deploy/nginx.conf", "v12.82"), encoding="utf-8")
    result = run(app, base / "backups", nginx_site=nginx_site)
    assert result.returncode == 0, result.stdout + result.stderr
    patched = nginx_site.read_text(encoding="utf-8")
    assert "STREAMFORGE_V1283_NGINX_REAL_IP_V1" in patched
    assert patched.count("set_real_ip_from 127.0.0.1;") == 1
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in patched
    # A site rewritten around the anchor still receives the block, on top.
    drifted = base / "nginx-drifted.conf"
    drifted.write_text("# runtime-managed listeners\nupstream streamforge_main_backend {\n    server 127.0.0.1:8800;\n}\n", encoding="utf-8")
    result = run(app, base / "backups", nginx_site=drifted)
    assert result.returncode == 0, result.stdout + result.stderr
    prepended = drifted.read_text(encoding="utf-8")
    assert prepended.startswith("# STREAMFORGE_V1283_NGINX_REAL_IP_V1"), prepended[:80]
    # Only the real_ip lines travel; hunk context must never be duplicated
    # into a foreign configuration.
    assert "default 0;" not in prepended and "map " not in prepended
    assert "real_ip_recursive on;" in prepended
    assert "upstream streamforge_main_backend {" in prepended

print("PASS: v12.83 staged patch, Nginx real_ip, secret-key repair, rollback, node restore and version gates")
