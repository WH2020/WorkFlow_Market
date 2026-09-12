"""Offline publisher signing. Does not create keys, upload or publish assets."""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_platform import windows_update_engine as engine
from agent_platform import windows_update_signatures as signatures
from release_signing_key import load_key


def main():
    parser = argparse.ArgumentParser(description="Sign reviewed Windows release assets with an existing private Ed25519 key")
    parser.add_argument("--private-key", type=Path, required=True, help="DPAPI envelope or private PEM key with private file permissions")
    parser.add_argument("--ask-passphrase", action="store_true", help="Read encrypted PEM passphrase interactively, never on argv")
    parser.add_argument("--trust-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage = "SIGNING_DEPENDENCY_UNAVAILABLE"
    try:
        from Crypto.Signature import eddsa
        stage = "SIGNING_TARGET_EXISTS"
        engine.require(not args.output.exists(), "TARGET_EXISTS")
        stage = "SIGNING_TRUST_INVALID"
        expected = signatures.public_key(args.trust_root)
        passphrase = getpass.getpass("Release signing key passphrase: ") if args.ask_passphrase else None
        stage = "SIGNING_PRIVATE_KEY_LOAD_FAILED"
        key = load_key(args.private_key, passphrase)
        stage = "SIGNING_KEY_MISMATCH"
        engine.require(key.has_private() and key.curve == "Ed25519"
                       and key.public_key().export_key(format="raw") == expected.export_key(format="raw"), "SIGNING_KEY_MISMATCH")
        stage = "SIGNING_ASSET_INVALID"
        engine.manifest(engine.read_json(args.manifest), args.version)
        assets = {}
        for name, path in (("installer", args.installer), ("manifest", args.manifest)):
            engine.regular(path.parent, path.name)
            assets[name] = {"bytes": path.stat().st_size, "sha256": engine.digest(path)}
        signed = signatures.signed_record(args.version, assets)
        stage = "SIGNING_FAILED"
        signature = eddsa.new(key, "rfc8032").sign(signatures.canonical(signed))
        encoded = (json.dumps({"format": 1, "signed": signed, "signature": base64.b64encode(signature).decode()}, sort_keys=True, indent=2) + "\n").encode()
        engine.require(len(encoded) <= 4096, "FILE_TOO_LARGE")
        stage = "SIGNING_OUTPUT_WRITE_FAILED"
        with args.output.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        stage = "SIGNING_OUTPUT_VERIFY_FAILED"
        signatures.verify(args.trust_root, args.output, args.version, assets)
        print(json.dumps({"status": "signed", "version": args.version, "sha256": engine.digest(args.output)}))
        return 0
    except Exception:
        print("Release signing refused: " + stage + ". No private key or native error details are printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
