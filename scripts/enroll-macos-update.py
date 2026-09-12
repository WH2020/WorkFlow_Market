"""Record the actual preserved dependencies after explicit manual Mac setup."""
import argparse
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_platform import macos_update_engine as engine
from agent_platform import macos_update_signatures as signatures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--tools-only', action='store_true', help='Explicit build/setup tool registration only.')
    args = parser.parse_args()
    engine.require(sys.platform == 'darwin', 'MACOS_ONLY')
    root = engine.trusted_directory(args.root.resolve(strict=True))
    if args.tools_only:
        engine.enroll_tools(root)
        print('macOS launch tools registered for this runtime root.')
        return
    engine.require(args.app is not None, 'APP_REQUIRED')
    engine.require(Path(sys.executable).absolute() == root / '.venv/bin/python', 'ENROLLED_VENV_REQUIRED')
    app = engine.trusted_directory(args.app.resolve(strict=True))
    marker = engine.safe_path(engine.support_directory(), 'install-root')
    engine.require(marker.read_text().strip() == str(root) and app == Path.home() / 'Applications/Agent4Market.app', 'INVALID_ROOT')
    path = root / engine.MANIFEST_FILE
    if not path.exists():
        engine.enroll_tools(root)
        print('One-click update enrollment skipped: this source checkout has no release manifest. Use a complete manual runtime release for enrollment.')
        return
    document = engine.read_json(path)
    version = engine.read_json(root / 'package.json', 65536)['version']
    rows = engine.manifest(document, version)
    engine.verify_programs(root, rows)
    engine.verify_bundle(app, document['app_files'], version)
    signatures.public_key(root)
    contract = engine.dependency_contract(root)
    engine.require(contract == document['dependency_contract'], 'DEPENDENCIES_CHANGED')
    parent = engine.mkdirs(root, '.pi/app-updates')
    engine.require(not (parent / 'active').exists(), 'UPDATING')
    engine.enroll_tools(root)
    baseline = {'format': 1, 'root': str(root), 'dependency_contract': contract,
                'fingerprint': engine.runtime_fingerprint(root)}
    target = root / engine.RUNTIME_FILE
    if target.exists():
        previous = engine.read_json(target)
        if previous == baseline:
            print('macOS update baseline already matches this manual installation.')
            return
        # Explicit manual setup may change dependencies. Preserve its previous
        # enrollment rather than silently retaining a stale runtime identity.
        os.replace(target, parent / ('macos-runtime.previous-' + secrets.token_hex(16) + '.json'))
        engine.sync_directory(parent)
    engine.write_json(target, baseline)
    print('macOS program/.app update baseline enrolled; runtime root and Keychain bindings unchanged.')


if __name__ == '__main__':
    main()
