"""macOS update preparation. Recovery itself is implemented by the native helper.

Only the public program manifest and sealed .app are updated. The runtime root,
Keychain bindings, node_modules, .venv, data and user configuration never move.
"""
from __future__ import annotations

import hashlib
import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import zipfile

from . import windows_update_engine as common

UpdateFailure = common.UpdateFailure
require = common.require
digest = common.digest
VERSION = common.VERSION
JOB_ID = common.JOB_ID
SHA256 = common.SHA256
PROTOCOL_FILE = 'agent_platform/macos_update_protocol.json'
MANIFEST_FILE = 'runtime/macos-install-manifest.json'
RUNTIME_FILE = '.pi/app-updates/macos-runtime.json'
LIMIT = 32 * 1024**2
PROGRAM_DIRS = {'agent_platform', 'profiles', 'vertical_plugins', 'pi', 'plugin', 'ui', 'scripts', 'library'}
PROGRAM_FILES = common.PROGRAM_FILES - {'Agent4Market.exe'}
_acl_api = None


def macos_acl(path, mode):
    """POSIX mode bits do not describe macOS extended ACL grants."""
    if sys.platform != 'darwin':
        return
    global _acl_api
    if _acl_api is None:
        api = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        api.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        api.acl_get_file.restype = ctypes.c_void_p
        api.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        api.acl_get_entry.restype = ctypes.c_int
        api.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        api.acl_get_tag_type.restype = ctypes.c_int
        api.acl_get_permset_mask_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
        api.acl_get_permset_mask_np.restype = ctypes.c_int
        api.acl_free.argtypes = [ctypes.c_void_p]
        api.acl_free.restype = ctypes.c_int
        _acl_api = api
    api = _acl_api
    ctypes.set_errno(0)
    acl = api.acl_get_file(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED, Apple Libc sys/acl.h.
    if not acl:
        require(ctypes.get_errno() in {0, errno.ENOENT}, 'UNSAFE_ACL')
        return  # No extended ACL property (ordinary mode-bit-only object).
    try:
        for index in range(129):
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            if api.acl_get_entry(acl, index, ctypes.byref(entry)) != 0:
                require(ctypes.get_errno() == errno.EINVAL, 'UNSAFE_ACL')
                return  # Apple returns -1/EINVAL at the end, NOT Linux's 0.
            require(index < 128, 'UNSAFE_ACL')
            tag, mask = ctypes.c_int(), ctypes.c_uint64()
            require(api.acl_get_tag_type(entry, ctypes.byref(tag)) == 0
                    and api.acl_get_permset_mask_np(entry, ctypes.byref(mask)) == 0 and tag.value in {1, 2}, 'UNSAFE_ACL')
            if tag.value == 1:
                # Public ancestors/programs may have read/search ACLs. Private
                # controls may not gain any extra access except synchronize.
                writes = sum(1 << bit for bit in (2, 4, 5, 6, 8, 10, 12, 13))
                require(not mask.value & writes and (mode & 0o077 != 0 or not mask.value & ~(1 << 20)), 'UNSAFE_ACL')
    finally:
        api.acl_free(acl)


def safe_path(root, name, *, missing=False, directory=False):
    require(isinstance(name, str) and 0 < len(name) <= 4096 and '\\' not in name and ':' not in name,
            'INVALID_PATH')
    parts = name.split('/')
    require(all(part not in {'', '.', '..'} and not part.endswith((' ', '.'))
                and not re.search(r'[\x00-\x1f\x7f]', part) for part in parts), 'INVALID_PATH')
    current = Path(root)
    for index, part in enumerate(parts):
        current /= part
        try:
            value = current.lstat()
        except FileNotFoundError:
            require(missing, 'MISSING_FILE')
            continue
        require(not stat.S_ISLNK(value.st_mode) and not getattr(value, 'st_file_attributes', 0) & 0x400, 'LINK_REFUSED')
        if index < len(parts) - 1 or directory:
            require(stat.S_ISDIR(value.st_mode), 'INVALID_PARENT')
        else:
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, 'NON_REGULAR_FILE')
        if os.name == 'posix':
            require(value.st_uid == os.getuid() and not value.st_mode & 0o022, 'UNSAFE_OWNER_OR_MODE')
            macos_acl(current, value.st_mode)
    return current


def trusted_directory(path, *, create=False, private=False):
    path = Path(os.path.abspath(path))
    if create and not path.exists():
        trusted_directory(path.parent)
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    for current in (path, *path.parents):
        value = current.lstat()
        require(stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode), 'LINK_REFUSED')
        if os.name == 'posix':
            require(value.st_uid in {0, os.getuid()} and not value.st_mode & 0o022, 'UNSAFE_OWNER_OR_MODE')
            macos_acl(current, value.st_mode)
    if os.name == 'posix':
        value = path.stat()
        require(value.st_uid == os.getuid() and (not private or value.st_mode & 0o077 == 0), 'PRIVATE_DIRECTORY_REQUIRED')
    return path


def mkdirs(root, relative):
    current = Path(root)
    for part in relative.split('/'):
        require(part not in {'', '.', '..'}, 'INVALID_PATH')
        current = trusted_directory(current / part, create=True)
    return current


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_atomic(path, content, *, mode=0o600):
    path = Path(path)
    trusted_directory(path.parent)
    safe_path(path.parent, path.name, missing=True)
    temporary = path.with_name('.writing-' + secrets.token_hex(16))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        if os.name == 'posix':
            os.fchmod(stream.fileno(), mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def read_json(path, limit=LIMIT):
    path = Path(path)
    safe_path(path.parent, path.name)
    require(path.stat().st_size <= limit, 'FILE_TOO_LARGE')
    try:
        return json.loads(path.read_bytes())
    except (ValueError, UnicodeError):
        raise UpdateFailure('INVALID_JSON') from None


def write_json(path, value):
    content = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    require(len(content) <= LIMIT, 'FILE_TOO_LARGE')
    write_atomic(path, content)


def program_path(name):
    require(isinstance(name, str), 'INVALID_PATH')
    parts = name.split('/')
    require((len(parts) > 1 and parts[0] in PROGRAM_DIRS) or name in PROGRAM_FILES, 'USER_DATA_REFUSED')
    require(not name.casefold().startswith('library/templates/company/')
            and name.casefold() != 'library/templates/company', 'USER_DATA_REFUSED')
    require(not any(part in {'', '.', '..'} or part.startswith('.') or '%' in part for part in parts), 'INVALID_PATH')
    require('\\' not in name and ':' not in name and not re.search(r'[\x00-\x1f\x7f]', name), 'INVALID_PATH')
    return name


def file_row(path, name):
    value = Path(path).lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, 'NON_REGULAR_FILE')
    return {'path': name, 'bytes': value.st_size, 'sha256': digest(path), 'mode': 0o755 if value.st_mode & 0o111 else 0o644}


def parse_rows(rows, *, app=False):
    require(isinstance(rows, list) and 0 < len(rows) <= 100000, 'INVALID_MANIFEST')
    result, folded, total = {}, set(), 0
    for row in rows:
        require(isinstance(row, dict) and set(row) in ({'path', 'bytes', 'sha256', 'mode'}, {'path', 'link'}), 'INVALID_MANIFEST')
        name = row.get('path')
        require(isinstance(name, str) and 0 < len(name) <= 4096 and name.casefold() not in folded, 'DUPLICATE_PATH')
        require(not PurePosixPath(name).is_absolute() and '\\' not in name and ':' not in name
                and all(part not in {'', '.', '..'} and not part.endswith((' ', '.')) for part in name.split('/'))
                and not re.search(r'[\x00-\x1f\x7f]', name), 'INVALID_PATH')
        folded.add(name.casefold())
        if app:
            require(name.startswith('Contents/'), 'INVALID_BUNDLE')
        else:
            program_path(name)
        if 'link' in row:
            require(app and isinstance(row['link'], str) and 0 < len(row['link']) <= 4096, 'LINK_REFUSED')
            target = row['link']
            require(not target.startswith('/') and '\\' not in target and ':' not in target
                    and not re.search(r'[\x00-\x1f\x7f]', target), 'LINK_REFUSED')
            normalized = list(PurePosixPath(name).parent.parts)
            for part in target.split('/'):
                if part == '..':
                    require(len(normalized) > 1, 'LINK_REFUSED')
                    normalized.pop()
                elif part not in {'', '.'}:
                    normalized.append(part)
            require(normalized and normalized[0] == 'Contents', 'LINK_REFUSED')
        else:
            require(type(row['bytes']) is int and 0 <= row['bytes'] <= 1024**3
                    and SHA256.fullmatch(str(row['sha256'])) and type(row['mode']) is int and row['mode'] in {0o644, 0o755}, 'INVALID_MANIFEST')
            total += row['bytes']
            require(total <= 4 * 1024**3, 'PAYLOAD_TOO_LARGE')
        result[name] = row
    require(not any(str(parent).casefold() in folded for name in result for parent in PurePosixPath(name).parents if str(parent) != '.'), 'PATH_COLLISION')
    if app:
        require({'Contents/Info.plist', 'Contents/MacOS/Agent4Market'} <= result.keys(), 'INVALID_BUNDLE')
    return result


def manifest(raw, version=None):
    require(isinstance(raw, dict) and raw.get('format') == 1 and raw.get('platform') == 'macos-universal'
            and VERSION.fullmatch(str(raw.get('version', ''))) and (version is None or raw['version'] == version), 'INVALID_MANIFEST')
    require(SHA256.fullmatch(str(raw.get('dependency_contract', ''))), 'DEPENDENCIES_CHANGED')
    rows = parse_rows(raw.get('files'))
    require({'package.json', PROTOCOL_FILE, 'ui/server.py', 'scripts/start-macos.sh'} <= rows.keys(), 'UPDATE_PROTOCOL_UNSUPPORTED')
    parse_rows(raw.get('app_files'), app=True)
    return rows


def dependency_contract(root):
    package = read_json(Path(root) / 'package.json', 65536)
    fields = ('dependencies', 'optionalDependencies', 'peerDependencies', 'engines', 'packageManager', 'pnpm')
    value = {name: package.get(name) for name in fields}
    value['locks'] = {name: hashlib.sha256(safe_path(root, name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                      for name in ('pnpm-lock.yaml', 'requirements.txt', 'requirements-wxdecipher.txt')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_programs(root, rows):
    trusted_directory(root)
    for name, row in rows.items():
        path = safe_path(root, name)
        require(path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], 'PROGRAM_MODIFIED')
        if os.name == 'posix':
            require(stat.S_IMODE(path.stat().st_mode) == row['mode'], 'PROGRAM_MODIFIED')


def bundle_rows(root):
    """Do not traverse bundle links; every real entry belongs to the seal."""
    root = trusted_directory(root)
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result.append({'path': relative, 'link': os.readlink(path)})
            elif path.is_file():
                safe_path(root, relative)
                result.append(file_row(path, relative))
            else:
                require(path.is_dir(), 'NON_REGULAR_FILE')
    result.sort(key=lambda row: row['path'])
    parse_rows(result, app=True)
    for row in result:
        if 'link' in row:
            resolved = (root / row['path']).resolve(strict=True)
            require(resolved.is_relative_to(root / 'Contents'), 'LINK_REFUSED')
    return result


def verify_bundle(root, rows, version=None):
    require(bundle_rows(root) == rows, 'BUNDLE_MODIFIED')
    info = plistlib.loads(safe_path(root, 'Contents/Info.plist').read_bytes())
    require(info.get('CFBundleIdentifier') == 'com.workflowmarket.salesdirector'
            and info.get('CFBundleExecutable') == 'Agent4Market'
            and (version is None or info.get('CFBundleShortVersionString') == version), 'BUNDLE_VERSION_CHANGED')
    if sys.platform == 'darwin':
        checked = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(root)],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        require(checked.returncode == 0, 'BUNDLE_SIGNATURE_INVALID')
        archs = subprocess.run(['/usr/bin/lipo', '-archs', str(Path(root) / 'Contents/MacOS/Agent4Market')],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        require(archs.returncode == 0 and {'arm64', 'x86_64'} <= set(archs.stdout.decode('ascii').split()), 'BUNDLE_ARCHITECTURE_UNSUPPORTED')


def extract_archive(archive, target, rows, *, app=False):
    """Only manifest-listed files; no zip slip, duplicate, bombs or link writes."""
    target = trusted_directory(target, create=True, private=True)
    expected = parse_rows(rows, app=app)
    prefix = 'Agent4Market.app/' if app else ''
    with zipfile.ZipFile(archive) as incoming:
        entries = incoming.infolist()
        require(len(entries) <= 110000, 'PAYLOAD_TOO_LARGE')
        actual = {}
        for item in entries:
            # Resource-fork sidecars are neither executed nor installed. The
            # sealed bundle is checked after extraction; no xattr is removed.
            require(not item.flag_bits & 1 and item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, 'INVALID_ARCHIVE')
            if item.filename.startswith('__MACOSX/'):
                require(app and item.file_size <= 1024**2, 'INVALID_ARCHIVE')
                continue
            require(item.filename.startswith(prefix), 'INVALID_ARCHIVE')
            name = item.filename[len(prefix):]
            if item.is_dir():
                require(not name or any(path.startswith(name) for path in expected), 'INVALID_ARCHIVE')
                continue
            require(name in expected and name not in actual, 'INVALID_ARCHIVE')
            row = expected[name]
            mode = item.external_attr >> 16
            if 'link' in row:
                require(stat.S_ISLNK(mode) and item.file_size <= 4096 and incoming.read(item).decode('utf-8') == row['link'], 'LINK_REFUSED')
            else:
                require(not stat.S_ISLNK(mode) and (stat.S_IFMT(mode) in {0, stat.S_IFREG})
                        and item.file_size == row['bytes'], 'INVALID_ARCHIVE')
            actual[name] = item
        require(actual.keys() == expected.keys(), 'INVALID_ARCHIVE')
        # All syntax/type validation precedes writing. Links are made LAST and
        # can never be used as a parent by a later archive entry.
        for name, row in expected.items():
            if 'link' in row:
                continue
            parent = str(PurePosixPath(name).parent)
            if parent != '.':
                mkdirs(target, parent)
            path = safe_path(target, name, missing=True)
            require(not path.exists(), 'STAGE_EXISTS')
            with incoming.open(actual[name]) as source, path.open('xb') as output:
                if os.name == 'posix':
                    os.fchmod(output.fileno(), row['mode'])
                shutil.copyfileobj(source, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            require(digest(path) == row['sha256'], 'DOWNLOAD_HASH_MISMATCH')
            sync_directory(path.parent)
        for name, row in expected.items():
            if 'link' in row:
                mkdirs(target, str(PurePosixPath(name).parent))
                path = safe_path(target, name, missing=True)
                os.symlink(row['link'], path)
                sync_directory(path.parent)
    verify_bundle(target, rows) if app else verify_programs(target, expected)


def check_space(allocations):
    """Aggregate simultaneous allocations sharing the same device/volume."""
    devices = {}
    for path, needed in allocations:
        require(type(needed) is int and needed >= 0, 'INVALID_MANIFEST')
        device = Path(path).stat().st_dev
        free = shutil.disk_usage(path).free
        before = devices.setdefault(device, {'needed': 0, 'free': free})
        before['needed'] += needed
        before['free'] = min(before['free'], free)
    for usage in devices.values():
        require(usage['free'] >= usage['needed'] + 128 * 1024**2, 'NOT_ENOUGH_SPACE')


def update_space(install, old, new, assets, *, staged=False):
    old_bytes = sum(row['bytes'] for row in old['files'])
    new_bytes = sum(row['bytes'] for row in new['files'])
    largest = max(row['bytes'] for row in old['files'] + new['files'])
    # Backup + possible installed-tree growth + one atomic copy, including
    # rollback's temporary old-file copy. The stage remains until manual cleanup.
    program = old_bytes + new_bytes + largest + 64 * 1024**2
    controls = 96 * 1024**2
    app = 0
    if not staged:
        program += new_bytes
        controls += sum(row['bytes'] for row in assets.values())
        controls += next(row['bytes'] for row in old['app_files'] if row['path'] == 'Contents/MacOS/Agent4Market')
        app = sum(row.get('bytes', 0) for row in new['app_files'])
    check_space([(install['root'], program), (install['support'], controls), (install['app'].parent, app)])


def support_directory():
    require(sys.platform == 'darwin', 'MACOS_ONLY')
    return trusted_directory(Path.home() / 'Library/Application Support/Agent4Market', private=True)


def installation(root):
    require(sys.platform == 'darwin', 'MACOS_ONLY')
    root = trusted_directory(root)
    support = support_directory()
    marker = safe_path(support, 'install-root')
    require(marker.stat().st_mode & 0o077 == 0 and marker.read_text().strip() == str(root), 'INSTALLED_DESKTOP_REQUIRED')
    app = trusted_directory(Path.home() / 'Applications/Agent4Market.app')
    document = read_json(safe_path(root, MANIFEST_FILE))
    version = read_json(safe_path(root, 'package.json'), 65536)['version']
    rows = manifest(document, version)
    baseline = read_json(safe_path(root, RUNTIME_FILE))
    require(baseline.get('format') == 1 and baseline.get('root') == str(root)
            and baseline.get('dependency_contract') == document['dependency_contract'], 'DEPENDENCIES_CHANGED')
    require(read_json(safe_path(root, PROTOCOL_FILE), 4096) == {'application': 'Agent4Market', 'platform': 'macos-universal',
            'update_protocol': 1, 'data_compatibility': 1, 'dependencies': 'preserve-enrolled-runtime-v1'}, 'UPDATE_PROTOCOL_UNSUPPORTED')
    return {'root': root, 'version': version, 'origin_version': version, 'rows': rows,
            'manifest': document, 'app': app, 'support': support, 'runtime': baseline}


def runtime_fingerprint(root):
    """Actual preserved dependencies, not just their requirements declarations."""
    import importlib.metadata
    import sysconfig
    rows = []
    for name in ('node_modules', '.venv'):
        base = Path(root) / name
        require(base.is_dir() and not base.is_symlink(), 'DEPENDENCIES_CHANGED')
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(part for part in dirs if part not in {'__pycache__', '.cache'})
            for part in sorted(dirs + files):
                path = Path(directory) / part
                relative = path.relative_to(root).as_posix()
                if path.suffix == '.pyc':
                    continue
                if path.is_symlink():
                    resolved = path.resolve(strict=True)
                    # venv's Python symlink may point to its retained external
                    # interpreter. Node package links must remain in the tree.
                    require(name == '.venv' and relative.startswith('.venv/bin/python')
                            or resolved.is_relative_to(base), 'DEPENDENCY_LINK_ESCAPES')
                    rows.append((relative, 'link', os.readlink(path), digest(resolved) if resolved.is_file() else 'directory'))
                elif path.is_file():
                    rows.append((relative, 'file', digest(path)))
                else:
                    require(path.is_dir(), 'NON_REGULAR_FILE')
    node = shutil.which('node')
    require(node, 'DEPENDENCIES_CHANGED')
    node = str(Path(node).resolve(strict=True))
    completed = subprocess.run([node, '-p', 'JSON.stringify({version:process.versions.node,arch:process.arch})'],
        env={'PATH': '/usr/bin:/bin', 'HOME': str(root)}, stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    require(completed.returncode == 0, 'DEPENDENCIES_CHANGED')
    packages = {name: importlib.metadata.version(name) for name in ('pycryptodome', 'Pillow', 'zstandard')}
    value = {'python': str(Path(sys.executable).resolve()), 'python_sha256': digest(Path(sys.executable).resolve()),
             'python_version': platform.python_version(), 'python_arch': platform.machine(),
             'python_soabi': sysconfig.get_config_var('SOABI'), 'packages': packages,
             'node': node, 'node_sha256': digest(node), 'node_runtime': json.loads(completed.stdout),
             'dependency_tree_sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()}
    value['runtime_id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value
