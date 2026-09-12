"""Offline release-key custody. Never imported by the application updater.

New keys are DPAPI current-user encrypted; no plaintext private key is written
or printed. This local envelope is NOT a portable/offline recovery backup.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_platform import windows_update_engine as engine

MAGIC = b"Agent4Market Ed25519 DPAPI v1\n"
ENTROPY = b"Agent4Market/ReleaseSigning/Ed25519/v1"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, *, decrypt=False) -> bytes:
    engine.require(os.name == "nt" and 0 < len(data) <= 16384, "INVALID_PRIVATE_KEY")
    source_buffer, entropy_buffer = ctypes.create_string_buffer(data), ctypes.create_string_buffer(ENTROPY)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    try:
        engine.require(function(ctypes.byref(source), None if decrypt else "Agent4Market release signing",
                                ctypes.byref(entropy), None, None, 1, ctypes.byref(output)), "PRIVATE_KEY_PROTECTION_FAILED")
        engine.require(0 < output.size <= 16384, "INVALID_PRIVATE_KEY")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)


def load_key(path, passphrase=None):
    from Crypto.PublicKey import ECC
    privacy = engine.load_policy("privacy")
    path = Path(path)
    with privacy.hold_private_file(path):
        engine.require(0 < path.stat().st_size <= 16384, "INVALID_PRIVATE_KEY")
        encoded = path.read_bytes()
    if encoded.startswith(MAGIC):
        engine.require(passphrase is None, "INVALID_PRIVATE_KEY")
        encoded = dpapi(encoded[len(MAGIC):], decrypt=True)
    key = ECC.import_key(encoded, passphrase=passphrase)
    engine.require(key.has_private() and key.curve == "Ed25519", "INVALID_PRIVATE_KEY")
    return key


def create_key(directory):
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa
    directory = Path(os.path.abspath(directory))
    engine.require(os.name == "nt" and not directory.is_relative_to(ROOT)
                   and not os.path.lexists(directory), "NEW_EXTERNAL_KEY_DIRECTORY_REQUIRED")
    engine.private_directory(directory, create=True)
    key = ECC.generate(curve="Ed25519")
    public = key.public_key().export_key(format="raw")
    encoded = MAGIC + dpapi(key.export_key(format="DER", use_pkcs8=True))
    private_path = directory / "windows-ed25519.dpapi.key"
    privacy = engine.load_policy("privacy")
    privacy.create_private_file(private_path)
    with privacy.hold_private_file(private_path), private_path.open("r+b") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    reopened = load_key(private_path)
    challenge = b"Agent4Market offline release-key round-trip v1"
    eddsa.new(key.public_key(), "rfc8032").verify(challenge, eddsa.new(reopened, "rfc8032").sign(challenge))
    trust = {"format": 1, "algorithm": "ed25519", "public_key": base64.b64encode(public).decode()}
    engine.write_json(directory / "public-key.json", trust)
    return {"status": "created", "private_key_path": str(private_path), "public_key": trust["public_key"],
            "public_key_sha256": hashlib.sha256(public).hexdigest(), "protection": "DPAPI-current-user + private ACL",
            "plaintext_private_key_written": False, "portable_backup_created": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, required=True, help="New private directory outside the source tree")
    args = parser.parse_args()
    try:
        print(json.dumps(create_key(args.directory)))
        return 0
    except Exception:
        print("Key creation refused. Existing paths are never overwritten; no private key is printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
