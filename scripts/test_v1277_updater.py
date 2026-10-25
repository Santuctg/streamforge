#!/usr/bin/env python3
"""v12.77 updater: staged Main patch, pinned bundled Node app.py, reinstall and rollback."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/force_update_v1277.sh"
PATCH = json.loads((ROOT / "scripts/apply_v1277_security.json").read_text(encoding="utf-8"))
PATCH_FILES = list(PATCH["files"])
OLD_NODE_BLOB = "0f466c3fc32240997e5acec508a5cf5aa904d8ec"


def v1276_source(relative: str) -> str:
    """The repo file with every v12.77 hunk reverted (installed v12.76 content)."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    for original, updated in reversed(PATCH["files"][relative]["hunks"]):
        assert source.count(updated) == 1, (relative, updated[:60])
        source = source.replace(updated, original)
    assert PATCH["files"][relative]["marker"] not in source
    return source


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def old_node_app() -> bytes:
    try:
        content = subprocess.run(["git", "show", "v12.76:node_agent/app.py"], cwd=ROOT,
                                 capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        content = b""
    if blob_sha(content) == OLD_NODE_BLOB:
        return content
    # Shallow checkouts without the tag: rebuild v12.76 app.py by reverting this release's edits.
    current = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
    start = current.index("class NodeSecurityHeadersMiddleware:")
    end = current.index('app = FastAPI(title="StreamForge Node Agent"')
    reverted = current[:start] + current[end:]
    reverted = reverted.replace("app.add_middleware(NodeSecurityHeadersMiddleware)\n", "")
    reverted = reverted.replace('minlength="12" autocomplete="new-password" required><small class="field-help">Minimum 12 characters.</small>',
                                'minlength="8" autocomplete="new-password" required><small class="field-help">Minimum 8 characters.</small>')
    reverted = reverted.replace('name="new_password_confirm" minlength="12"', 'name="new_password_confirm" minlength="8"')
    reverted = reverted.replace('''    # STREAMFORGE_PANEL_PASSWORD_POLICY_V1277: Main enforces the same rule.
    if len(new_password) < 12:
        return node_panel_account_password_page(request, error="New password must contain at least 12 characters.")''',
                                '''    if len(new_password) < 8:
        return node_panel_account_password_page(request, error="New password must contain at least 8 characters.")''')
    content = reverted.encode("utf-8")
    assert blob_sha(content) == OLD_NODE_BLOB, "could not reconstruct the v12.76 Node app.py baseline"
    return content


OLD_NODE = old_node_app()


def prepare(base: Path):
    app = base / "installed"
    shutil.copytree(ROOT / "node_agent", app / "node_agent", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "node_agent/app.py").write_bytes(OLD_NODE)
    (app / "VERSION").write_text("12.76\n", encoding="utf-8")
    (app / "node_agent/VERSION").write_text("12.76\n", encoding="utf-8")
    for relative in PATCH_FILES:
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(v1276_source(relative), encoding="utf-8")
    return app


def run(app: Path, backup: Path, fail: bool = False):
    env = os.environ.copy()
    env.update(STREAMFORGE_ROOT=str(app), STREAMFORGE_BACKUP_ROOT=str(backup), STREAMFORGE_HOTFIX_TEST_MODE="1")
    if fail:
        env["STREAMFORGE_V1277_TEST_FAIL_AFTER_WRITE"] = "1"
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True)


def snapshot(app: Path):
    return {f: (app / f).read_bytes() for f in PATCH_FILES + ["node_agent/app.py", "VERSION", "node_agent/VERSION"]}


with tempfile.TemporaryDirectory(prefix="sf-v1277-install-") as d:
    base = Path(d)
    app = prepare(base)
    result = run(app, base / "backups")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "VERSION").read_text().strip() == "12.77"
    assert (app / "node_agent/VERSION").read_text().strip() == "12.77"
    for relative in PATCH_FILES + ["node_agent/app.py"]:
        assert (app / relative).read_bytes() == (ROOT / relative).read_bytes(), relative
    again = run(app, base / "backups")
    assert again.returncode == 0, again.stdout + again.stderr

with tempfile.TemporaryDirectory(prefix="sf-v1277-rollback-") as d:
    base = Path(d)
    app = prepare(base)
    before = snapshot(app)
    result = run(app, base / "backups", fail=True)
    assert result.returncode != 0
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1277-node-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "node_agent/app.py").write_text("# locally modified\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "Unexpected bundled Node app.py" in (result.stdout + result.stderr)
    assert snapshot(app) == before

with tempfile.TemporaryDirectory(prefix="sf-v1277-old-") as d:
    base = Path(d)
    app = prepare(base)
    (app / "VERSION").write_text("12.75\n", encoding="utf-8")
    before = snapshot(app)
    result = run(app, base / "backups")
    assert result.returncode != 0 and "requires installed Main v12.76" in result.stderr
    assert snapshot(app) == before
print("PASS: v12.77 staged Main patch, pinned bundled Node app.py, reinstall, rollback, Node baseline and version gates")
