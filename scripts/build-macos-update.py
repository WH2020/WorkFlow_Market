"""Create unsigned update assets from an exported public source tree and .app.

Run on macOS after building the universal app. Signing is a separate offline
publisher step. Never walks node_modules, .venv, .pi, data or company templates.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_platform import macos_update_engine as engine

SCRIPTS = {'start-macos.sh', 'setup-macos.sh', 'start-coding-agent.sh', 'coding-agent.sh',
           'sync-coding-agent-skills.py', 'enroll-macos-update.py'}
UI = {'server.py', 'app.js', 'index.html', 'styles.css', 'wxdecipher.css', 'free-chat.css', 'free-chat.js'}


def selected(name):
    parts = name.split('/')
    if any(part.startswith('.') or part in {'tests', '__pycache__', 'company', 'node_modules'} or '%' in part for part in parts):
        return False
    if name.endswith(('.pyc', '.db', '.sqlite', '.sqlite3', '.dpapi.key')):
        return False
    if parts[0] == 'scripts':
        return len(parts) == 2 and parts[1] in SCRIPTS
    if parts[0] == 'ui':
        return len(parts) == 2 and parts[1] in UI
    if parts[0] == 'library':
        return name.startswith('library/templates/')
    return parts[0] in {'agent_platform', 'profiles', 'vertical_plugins', 'pi', 'plugin'} or name in engine.PROGRAM_FILES


def source_rows(source):
    rows = []
    for name in sorted(engine.PROGRAM_DIRS | engine.PROGRAM_FILES):
        candidate = source / name
        if not candidate.exists():
            continue  # Optional release notes/directories need not be present.
        entries = candidate.rglob('*') if candidate.is_dir() else (candidate,)
        for path in entries:
            engine.require(not path.is_symlink(), 'LINK_REFUSED')
            if path.is_dir():
                continue
            relative = path.relative_to(source).as_posix()
            if selected(relative):
                engine.program_path(relative)
                rows.append(engine.file_row(path, relative))
    return sorted(rows, key=lambda row: row['path'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-export', type=Path, required=True)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--app-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    engine.require(sys.platform == 'darwin', 'MACOS_ONLY')
    source = args.source_export.resolve(strict=True)
    engine.require(not (source / '.git').exists(), 'SOURCE_EXPORT_REQUIRED')
    version = json.loads((source / 'package.json').read_bytes())['version']
    engine.require(engine.VERSION.fullmatch(version), 'INVALID_VERSION')
    # Enumerate only allowed top-level program inputs from the git export.
    rows = source_rows(source)
    app_rows = engine.bundle_rows(args.app)
    document = {'format': 1, 'platform': 'macos-universal', 'version': version,
                'dependency_contract': engine.dependency_contract(source), 'files': rows, 'app_files': app_rows}
    engine.manifest(document, version)
    engine.verify_bundle(args.app, app_rows, version)
    output = args.output.resolve(strict=True)
    manifest = output / 'macOS-install-manifest.json'
    programs = output / f'Agent4Market-{version}-macos-programs.zip'
    app = output / f'Agent4Market-{version}-macos-universal-app.zip'
    engine.require(all(not path.exists() for path in (manifest, programs, app)), 'TARGET_EXISTS')
    encoded = (json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    with manifest.open('xb') as stream:
        stream.write(encoded)
    with zipfile.ZipFile(programs, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for row in rows:
            info = zipfile.ZipInfo(row['path'])
            info.create_system = 3
            info.external_attr = (0o100000 | row['mode']) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (source / row['path']).read_bytes())
    with args.app_archive.open('rb') as incoming, app.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    # The manual runtime archive carries the same initial program/app baseline.
    (source / 'runtime').mkdir(exist_ok=True)
    with (source / engine.MANIFEST_FILE).open('xb') as stream:
        stream.write(encoded)
    print(json.dumps({'status': 'unsigned-assets-prepared', 'version': version,
                      'program_files': len(rows), 'bundle_files': len(app_rows), 'signature_required': True,
                      'assets': {path.name: engine.digest(path) for path in (manifest, programs, app)}}))


if __name__ == '__main__':
    main()
