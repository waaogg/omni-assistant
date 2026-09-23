#!/usr/bin/env python3
"""Create or restore an authenticated encrypted state backup.

The password is read from ``OMNI_BACKUP_PASSWORD`` or an interactive prompt and
is never stored in the archive.  Code and credentials remain separate.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import secrets
import shutil
import sqlite3
import struct
import tempfile
from pathlib import Path

MAGIC = b"OMNIBAK1"


def crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as exc:
        raise SystemExit("Install the 'cryptography' package to use encrypted backups") from exc


def password() -> bytes:
    value = os.getenv("OMNI_BACKUP_PASSWORD") or getpass.getpass("Backup password: ")
    if len(value) < 12:
        raise SystemExit("Backup password must be at least 12 characters")
    return value.encode("utf-8")


def snapshot_database(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(source)
    try:
        target_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def create(source: Path, output: Path) -> None:
    AESGCM = crypto()
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = hashlib.scrypt(password(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "state.db"
        snapshot_database(source, snap)
        plaintext = snap.read_bytes()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    output.write_bytes(MAGIC + salt + nonce + struct.pack(">Q", len(plaintext)) + ciphertext)


def restore(source: Path, output: Path) -> None:
    AESGCM = crypto()
    blob = source.read_bytes()
    if not blob.startswith(MAGIC) or len(blob) < 44:
        raise SystemExit("Not an Omni encrypted backup")
    salt, nonce = blob[8:24], blob[24:36]
    expected = struct.unpack(">Q", blob[36:44])[0]
    key = hashlib.scrypt(password(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    plaintext = AESGCM(key).decrypt(nonce, blob[44:], MAGIC)
    if len(plaintext) != expected:
        raise SystemExit("Backup length check failed")
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
        handle.write(plaintext)
        temp_path = Path(handle.name)
    os.replace(temp_path, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("source", type=Path)
    create_parser.add_argument("output", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "create":
        create(args.source, args.output)
    else:
        restore(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
