"""Offline macOS publisher signature. Never creates keys or publishes a release."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_platform import macos_update_engine as engine
from agent_platform import macos_update_signatures as signatures
from release_signing_key import load_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--ask-passphrase', action='store_true')
    parser.add_argument('--trust-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--version', required=True)
    parser.add_argument('--programs', type=Path, required=True)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        from Crypto.Signature import eddsa
        engine.require(not args.output.exists(), 'TARGET_EXISTS')
        expected = signatures.public_key(args.trust_root)
        phrase = getpass.getpass('Release signing key passphrase: ') if args.ask_passphrase else None
        key = load_key(args.private_key, phrase)
        engine.require(key.has_private() and key.curve == 'Ed25519'
                       and key.public_key().export_key(format='raw') == expected.export_key(format='raw'), 'SIGNING_KEY_MISMATCH')
        engine.manifest(engine.read_json(args.manifest), args.version)
        assets = {}
        for name in ('programs', 'app', 'manifest'):
            path = getattr(args, name)
            engine.safe_path(path.parent, path.name)
            assets[name] = {'bytes': path.stat().st_size, 'sha256': engine.digest(path)}
        record = signatures.signed_record(args.version, assets)
        signed = eddsa.new(key, 'rfc8032').sign(signatures.canonical(record))
        encoded = (json.dumps({'format': 1, 'signed': record, 'signature': base64.b64encode(signed).decode()}, sort_keys=True, indent=2) + '\n').encode()
        engine.require(len(encoded) <= 4096, 'FILE_TOO_LARGE')
        with args.output.open('xb') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        signatures.verify(args.trust_root, args.output, args.version, assets)
        print(json.dumps({'status': 'signed', 'platform': 'macos-universal', 'version': args.version, 'sha256': engine.digest(args.output)}))
        return 0
    except Exception:
        print('macOS signing refused. No private key or native error details are printed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
