"""Passphrase encryption for StreamForge backup archives (v12.76).

STREAMFORGE_BACKUP_ARCHIVE_ENCRYPTION_V1276: archives copied to backup targets
(local folder, SMB, SSH, Google Drive) contain the database and the
/etc/streamforge.env snapshot, so they are encrypted when a backup passphrase
is configured. This module has no StreamForge imports so it also works as an
offline tool on any machine with Python 3 and `cryptography`:

    python3 backup_crypto.py decrypt streamforge-main-....tar.gz plain.tar.gz

Format (all integers big-endian):

    header  = b"SFBKENC1" | log2(N) (1 byte) | salt (16) | nonce prefix (8)
    chunk   = length (4) | AES-256-GCM(ciphertext + tag)
    key     = scrypt(passphrase, salt, N=2**log2N, r=8, p=1, 32 bytes)
    nonce   = nonce prefix | chunk index (4)
    aad     = header | chunk index (4) | final flag (1)

Chunks hold up to 1 MiB of plaintext. The last chunk carries final flag 1,
so truncation, reordering and appended data are all detected.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import struct
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"SFBKENC1"
CHUNK_SIZE = 1024 * 1024
SCRYPT_LOG2_N = 15
_HEADER_SIZE = len(MAGIC) + 1 + 16 + 8
_TAG_SIZE = 16


class BackupDecryptError(ValueError):
    pass


def is_encrypted_backup(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _key(passphrase: str, salt: bytes, log2_n: int) -> bytes:
    if not 14 <= log2_n <= 20:
        raise BackupDecryptError("Unsupported backup encryption parameters")
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=1 << log2_n, r=8, p=1,
        maxmem=256 * 1024 * 1024, dklen=32,
    )


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">IB", index, 1 if final else 0)


def encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    if not passphrase:
        raise ValueError("A backup passphrase is required")
    salt = os.urandom(16)
    prefix = os.urandom(8)
    header = MAGIC + bytes([SCRYPT_LOG2_N]) + salt + prefix
    aead = AESGCM(_key(passphrase, salt, SCRYPT_LOG2_N))
    destination = Path(destination)
    temp = destination.with_name(destination.name + ".encrypting")
    try:
        with Path(source).open("rb") as reader, temp.open("wb") as writer:
            os.chmod(temp, 0o600)
            writer.write(header)
            index = 0
            chunk = reader.read(CHUNK_SIZE)
            while True:
                following = reader.read(CHUNK_SIZE) if chunk else b""
                final = not following
                sealed = aead.encrypt(prefix + struct.pack(">I", index), chunk, _aad(header, index, final))
                writer.write(struct.pack(">I", len(sealed)) + sealed)
                if final:
                    break
                chunk = following
                index += 1
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    destination = Path(destination)
    temp = destination.with_name(destination.name + ".decrypting")
    try:
        with Path(source).open("rb") as reader:
            header = reader.read(_HEADER_SIZE)
            if len(header) != _HEADER_SIZE or not header.startswith(MAGIC):
                raise BackupDecryptError("Not an encrypted StreamForge backup")
            log2_n = header[len(MAGIC)]
            salt = header[len(MAGIC) + 1:len(MAGIC) + 17]
            prefix = header[len(MAGIC) + 17:]
            aead = AESGCM(_key(passphrase or "", salt, log2_n))
            with temp.open("wb") as writer:
                os.chmod(temp, 0o600)
                index = 0
                while True:
                    raw_length = reader.read(4)
                    if len(raw_length) != 4:
                        raise BackupDecryptError("Encrypted backup is truncated")
                    (length,) = struct.unpack(">I", raw_length)
                    if not _TAG_SIZE <= length <= CHUNK_SIZE + _TAG_SIZE:
                        raise BackupDecryptError("Encrypted backup is damaged")
                    sealed = reader.read(length)
                    if len(sealed) != length:
                        raise BackupDecryptError("Encrypted backup is truncated")
                    nonce = prefix + struct.pack(">I", index)
                    plain = None
                    final = False
                    for final_flag in (False, True):
                        try:
                            plain = aead.decrypt(nonce, sealed, _aad(header, index, final_flag))
                            final = final_flag
                            break
                        except InvalidTag:
                            continue
                    if plain is None:
                        raise BackupDecryptError("Wrong backup passphrase or damaged archive")
                    writer.write(plain)
                    if final:
                        if reader.read(1):
                            raise BackupDecryptError("Encrypted backup has unexpected trailing data")
                        break
                    index += 1
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in {"encrypt", "decrypt"}:
        print("Usage: backup_crypto.py encrypt|decrypt SOURCE DESTINATION", file=sys.stderr)
        return 2
    passphrase = os.environ.get("STREAMFORGE_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    try:
        (encrypt_file if argv[1] == "encrypt" else decrypt_file)(Path(argv[2]), Path(argv[3]), passphrase)
    except (BackupDecryptError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{argv[1]}ed: {argv[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
