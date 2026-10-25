#!/usr/bin/env python3
"""v12.76: encrypted backup archives for destinations, decrypted only for restore."""
from __future__ import annotations
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import backup_crypto as crypto  # noqa: E402

d = Path(tempfile.mkdtemp(prefix="sf-v1276-bk-"))

# --- format: round trip, sizes on chunk boundaries, tamper detection ------------
for size in (0, 1, crypto.CHUNK_SIZE - 1, crypto.CHUNK_SIZE, crypto.CHUNK_SIZE * 2 + 17):
    plain = d / f"p{size}"
    plain.write_bytes(os.urandom(size))
    enc, out = d / f"e{size}", d / f"o{size}"
    crypto.encrypt_file(plain, enc, "correct horse battery")
    assert crypto.is_encrypted_backup(enc) and not crypto.is_encrypted_backup(plain)
    assert oct(enc.stat().st_mode & 0o777) == "0o600"
    crypto.decrypt_file(enc, out, "correct horse battery")
    assert out.read_bytes() == plain.read_bytes(), size

big = d / f"e{crypto.CHUNK_SIZE * 2 + 17}"
data = big.read_bytes()


def must_fail(blob: bytes, passphrase: str = "correct horse battery", expect: str = "") -> None:
    bad = d / "bad"
    bad.write_bytes(blob)
    try:
        crypto.decrypt_file(bad, d / "never", passphrase)
    except crypto.BackupDecryptError as exc:
        assert expect in str(exc), str(exc)
        assert not (d / "never").exists()
        return
    raise AssertionError("tampered backup was accepted")


must_fail(data, passphrase="wrong passphrase!!", expect="Wrong backup passphrase")
flipped = bytearray(data); flipped[200] ^= 1
must_fail(bytes(flipped), expect="Wrong backup passphrase or damaged")
header = crypto._HEADER_SIZE
first_len = int.from_bytes(data[header:header + 4], "big")
must_fail(data[:header + 4 + first_len], expect="truncated")          # dropped final chunks
must_fail(data + b"x", expect="trailing")                               # appended data
second = header + 4 + first_len
second_len = int.from_bytes(data[second:second + 4], "big")
third = second + 4 + second_len
swapped = data[:header] + data[second:third] + data[header:second] + data[third:]
must_fail(swapped, expect="Wrong backup passphrase or damaged")         # reordered chunks

# --- offline CLI ---------------------------------------------------------------
cli = subprocess.run(
    [sys.executable, "-B", str(ROOT / "app/backup_crypto.py"), "decrypt", str(big), str(d / "cli.out")],
    env={**os.environ, "STREAMFORGE_BACKUP_PASSPHRASE": "correct horse battery"}, capture_output=True, text=True,
)
assert cli.returncode == 0, cli.stderr
assert (d / "cli.out").read_bytes() == (d / f"p{crypto.CHUNK_SIZE * 2 + 17}").read_bytes()

# --- backup_manager: passphrase storage, target delivery, restore preparation --
import app.backup_manager as bm  # noqa: E402

bm.ARCHIVE_ROOT = d / "archives"
bm.BACKUP_SECRET_KEY = d / "archives/secret.key"
bm.CONFIG_FILE = d / "targets.json"
bm.LOCAL_BACKUP_CONFIG_FILE = d / "local.json"
bm.BACKUP_ENCRYPTION_FILE = d / "backup-encryption.json"
dest = d / "dest"
dest.mkdir()


def fake_archive() -> Path:
    bm.ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    src = d / "payload.txt"
    src.write_text("database bytes " * 100)
    archive = bm.ARCHIVE_ROOT / f"streamforge-main-20261025-{len(list(bm.ARCHIVE_ROOT.glob('*.tar.gz'))):06d}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src, arcname="var/lib/streamforge/streamforge.db")
    return archive


bm._archive = fake_archive
bm._write_probe = lambda folder: None
target = bm.add_target("Disk", "local", str(dest))

assert not bm.backup_encryption_enabled()
try:
    bm.save_backup_encryption_passphrase("short")
    raise AssertionError("short passphrase accepted")
except ValueError:
    pass

bm.run_target(target["id"])                                  # encryption off: plain copy
plain_copy = sorted(dest.glob("*.tar.gz"))[-1]
assert not crypto.is_encrypted_backup(plain_copy)
assert bm.prepare_restore_archive(plain_copy) is False

bm.save_backup_encryption_passphrase("a long backup passphrase")
assert bm.backup_encryption_enabled()
assert "a long backup passphrase" not in bm.BACKUP_ENCRYPTION_FILE.read_text()
assert oct(bm.BACKUP_ENCRYPTION_FILE.stat().st_mode & 0o777) == "0o600"
result = bm.run_target(target["id"])                         # encryption on
assert result["last_encrypted"] is True
delivered = sorted(dest.glob("*.tar.gz"))[-1]
assert delivered.name.startswith("streamforge-main-") and crypto.is_encrypted_backup(delivered)
retained = bm.ARCHIVE_ROOT / delivered.name
assert retained.is_file() and not crypto.is_encrypted_backup(retained), "Main keeps a readable local copy"
assert not list(bm.ARCHIVE_ROOT.glob(".outgoing-*")), "temporary encrypted copy is removed"

staged = d / "staged.tar.gz"
staged.write_bytes(delivered.read_bytes())
assert bm.prepare_restore_archive(staged) is True           # configured passphrase
with tarfile.open(staged, "r:gz") as tar:
    assert tar.getnames() == ["var/lib/streamforge/streamforge.db"]

bm.clear_backup_encryption_passphrase()
staged.write_bytes(delivered.read_bytes())
try:
    bm.prepare_restore_archive(staged)
    raise AssertionError("encrypted restore without passphrase accepted")
except ValueError as exc:
    assert "passphrase" in str(exc)
try:
    bm.prepare_restore_archive(staged, "not the passphrase")
    raise AssertionError("wrong passphrase accepted")
except ValueError as exc:
    assert "Wrong backup passphrase" in str(exc)
assert bm.prepare_restore_archive(staged, "a long backup passphrase") is True  # e.g. on a new server
# --- Panel routes --------------------------------------------------------------
os.environ.update(
    STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n",
    STREAMFORGE_PANEL_LOGIN_GUARD_DB=f"{d}/guard.sqlite3", STREAMFORGE_SECRET_KEY="x" * 64,
    STREAMFORGE_ADMIN_USER="admin", STREAMFORGE_ADMIN_PASSWORD="BootTest!12345",
)
from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402
m.metrics_history.path = Path(d) / "metrics-history.jsonl"  # default path is under /var/lib/streamforge

m.main_panel_client_ip = lambda request: "198.51.100.7"
m.MAIN_RESTORE_INBOX = d / "restore-inbox"
scheduled = []
m._write_main_system_request = lambda *args, **kwargs: scheduled.append(args)
m._validate_main_restore_archive = lambda path: tarfile.open(path, "r:gz").getnames()
m._restore_recovery_access_url = lambda request: "http://127.0.0.1"
with TestClient(m.app, base_url="http://127.0.0.1") as c:
    assert c.post("/login", data={"username": "admin", "password": "BootTest!12345"}, follow_redirects=False).status_code == 303
    page = c.get("/system/backups").text
    assert "Backup encryption" in page and "currently receive unencrypted archives" in page
    r = c.post("/system/backups/encryption", data={"passphrase": "route passphrase 1", "passphrase_confirm": "route passphrase 2"}, follow_redirects=False)
    assert "does+not+match" in r.headers["location"]
    r = c.post("/system/backups/encryption", data={"passphrase": "route passphrase 1", "passphrase_confirm": "route passphrase 1"}, follow_redirects=False)
    assert "message=" in r.headers["location"] and bm.backup_encryption_enabled()
    assert "Turn off encryption" in c.get("/system/backups").text

    encrypted = delivered.read_bytes()  # sealed with "a long backup passphrase"
    def upload(passphrase: str):
        return c.post("/system/backups/restore", data={"confirm_restore": "yes", "backup_passphrase": passphrase},
                      files={"archive": (delivered.name, encrypted, "application/gzip")}, follow_redirects=False)
    r = upload("")                                     # saved passphrase differs
    assert "error=" in r.headers["location"] and "Wrong" in r.headers["location"] and not scheduled
    r = upload("a long backup passphrase")             # passphrase typed on the form
    assert "message=" in r.headers["location"], r.headers["location"]
    assert len(scheduled) == 1
    staged = Path(scheduled[0][2])
    assert not crypto.is_encrypted_backup(staged), "the privileged restore helper receives a plain archive"
    r = c.post("/system/backups/encryption", data={"clear": "1"}, follow_redirects=False)
    assert not bm.backup_encryption_enabled()

print("PASS: v12.76 backup archives encrypted for destinations (AES-256-GCM, tamper/truncation/reorder detected), local copy readable, restore decrypts")
