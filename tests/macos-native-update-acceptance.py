"""Hosted-Mac acceptance: real signed preparation, native swap, probe and Pi trial.

Only fresh synthetic installs under the runner's home are used. Transport and
the user confirmation are local fixtures; the publisher key is NEVER needed.
The actual release app/programs are not changed. No model request is made.
"""
import argparse
import base64
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import zipfile
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from agent_platform import macos_update_engine as engine
from agent_platform import macos_update_signatures as signatures
from agent_platform.macos_updates import MacOSUpdateManager


def require(value, message):
    if not value:
        raise RuntimeError(message)


def wait_for(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise RuntimeError(message)


def port_free():
    with socket.socket() as stream:
        stream.settimeout(0.3)
        return stream.connect_ex(('127.0.0.1', 8765)) != 0


def bundle_copy(source, target, version=None):
    subprocess.run(['/usr/bin/ditto', str(source), str(target)], check=True)
    if version:
        path = target / 'Contents/Info.plist'
        value = plistlib.loads(path.read_bytes())
        value['CFBundleShortVersionString'] = version
        value['CFBundleVersion'] = version
        path.write_bytes(plistlib.dumps(value))
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(target)], check=True)


def stop_synthetic_apps(home, helper):
    # PIDs are insufficient on their own. Check the actual native executable
    # path and dedicated process group before terminating a synthetic app.
    executables = {str(home / 'Applications/Agent4Market.app/Contents/MacOS/Agent4Market'), str(helper)}
    native = ctypes.CDLL('/usr/lib/libproc.dylib')
    native.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    native.proc_pidpath.restype = ctypes.c_int
    listing = subprocess.check_output(['/bin/ps', '-axo', 'pid='], text=True)
    for line in listing.splitlines():
        pid = int(line.strip())
        if pid <= 1:
            continue
        buffer = ctypes.create_string_buffer(4096)
        if native.proc_pidpath(pid, buffer, len(buffer)) <= 0 or os.fsdecode(buffer.value) not in executables:
            continue
        try:
            require(os.getpgid(pid) == pid, 'Synthetic app lacks owned process group')
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_case(base, app_source):
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa
    root, candidate = base / 'runtime', base / 'candidate'
    home = base / 'home'
    os.environ['HOME'] = str(home)
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_AUTH_TOKEN')) or key.startswith(('AGENT4MARKET_', 'CLAUDE_', 'OPENAI_', 'ANTHROPIC_')):
            os.environ.pop(key, None)
    os.environ['PI_CODING_AGENT_DIR'] = str(home / 'pi')
    os.environ['CLAUDE_CONFIG_DIR'] = str(home / 'claude')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['AGENT4MARKET_UPDATE_CONTROL'] = secrets.token_hex(32)
    os.environ['AGENT4MARKET_DESKTOP_PID'] = str(os.getpid())
    os.environ.pop('CODEX_HOME', None)
    support = engine.mkdirs(home, 'Library/Application Support/Agent4Market')
    applications = engine.mkdirs(home, 'Applications')
    engine.write_atomic(support / 'install-root', (str(root) + '\n').encode())
    version = engine.read_json(candidate / 'package.json')['version']
    key = ECC.generate(curve='Ed25519')
    trust = {'format': 1, 'algorithm': 'ed25519',
             'public_key': base64.b64encode(key.public_key().export_key(format='raw')).decode()}
    for directory in (root, candidate):
        engine.write_json(directory / signatures.TRUST_FILE, trust)
        (directory / signatures.TRUST_FILE).chmod(0o644)
    package = engine.read_json(root / 'package.json')
    package['version'] = '0.0.0'
    engine.write_json(root / 'package.json', package)
    (root / 'package.json').chmod(0o644)
    app = applications / 'Agent4Market.app'
    bundle_copy(app_source, app, '0.0.0')
    spec = importlib.util.spec_from_file_location('fixture_builder', SOURCE / 'scripts/build-macos-update.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    old = {'format': 1, 'platform': 'macos-universal', 'version': '0.0.0',
           'dependency_contract': engine.dependency_contract(root),
           'files': builder.source_rows(root), 'app_files': engine.bundle_rows(app)}
    new = {**old, 'version': version, 'files': builder.source_rows(candidate),
           'app_files': engine.bundle_rows(app_source)}
    engine.manifest(old)
    engine.manifest(new)
    engine.mkdirs(root, 'runtime')
    engine.write_json(root / engine.MANIFEST_FILE, old)
    engine.enroll_tools(root)
    baseline = {'format': 1, 'root': str(root), 'dependency_contract': old['dependency_contract'],
                'fingerprint': engine.runtime_fingerprint(root)}
    engine.write_json(root / engine.RUNTIME_FILE, baseline)
    canaries = {'.pi/acceptance-settings.txt': b'synthetic settings', 'data/acceptance-canary.txt': b'synthetic business',
                'outputs/acceptance-canary.txt': b'synthetic output'}
    for name, content in canaries.items():
        engine.mkdirs(root, str(Path(name).parent))
        engine.write_atomic(root / name, content)
    assets_dir = engine.trusted_directory(base / 'assets', create=True, private=True)
    files = {name: assets_dir / (name + '.json' if name in {'manifest', 'signature'} else name + '.zip')
             for name in ('manifest', 'signature', 'programs', 'app')}
    engine.write_json(files['manifest'], new)
    with zipfile.ZipFile(files['programs'], 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for row in new['files']:
            info = zipfile.ZipInfo(row['path'])
            info.create_system = 3
            info.external_attr = (0o100000 | row['mode']) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (candidate / row['path']).read_bytes())
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(app_source), str(files['app'])], check=True)
    assets = {name: {'id': index + 1, 'bytes': path.stat().st_size, 'sha256': engine.digest(path)}
              for index, (name, path) in enumerate(files.items()) if name != 'signature'}
    signed = signatures.signed_record(version, assets)
    engine.write_json(files['signature'], {'format': 1, 'signed': signed,
        'signature': base64.b64encode(eddsa.new(key, 'rfc8032').sign(signatures.canonical(signed))).decode()})
    assets['signature'] = {'id': 99, 'bytes': files['signature'].stat().st_size, 'sha256': engine.digest(files['signature'])}
    del key  # No private fixture key file; the signed public assets are enough.
    selected = {'version': version, 'tag': 'v' + version, 'macos_assets': assets}

    class Checker:
        def snapshot(self):
            return {'update_available': True, 'latest': selected}

    def download(row, target, cancelled, progress):
        name = next(name for name, value in assets.items() if value == row)
        content = files[name].read_bytes()
        require(len(content) == row['bytes'] and engine.digest(files[name]) == row['sha256'], 'Fixture asset changed')
        engine.write_atomic(target, content)

    actual_run = subprocess.run

    def confirmation(command, **kwargs):
        if command[:2] == ['/usr/bin/osascript', '-e']:
            require(command[2].startswith('display dialog "Agent4Market 将升级到 '), 'Unexpected native confirmation')
            return subprocess.CompletedProcess(command, 0, 'button returned:更新, gave up:false'.encode(), b'')
        return actual_run(command, **kwargs)

    # Exercise the real capability/start/preparation/signature/archive checks.
    # Only explicit user approval and HTTPS transport are simulated in this CI.
    manager = MacOSUpdateManager(root, Checker(), downloader=download, fetcher=lambda _: (selected, None))
    with patch('agent_platform.macos_updates.subprocess.run', side_effect=confirmation):
        manager.start(selected['tag'], True)
        manager.worker.join(240)
    require(not manager.worker.is_alive() and manager.state['phase'] == 'ready', 'Preparation failed: ' + str(manager.state))
    job, support_job = manager.job, manager.support_job
    info = engine.read_json(job / 'job.json')
    engine.write_json(job / 'stopped.json', {'nonce': info['nonce']})
    worker_env = {'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                  'LANG': 'en_US.UTF-8', 'TMPDIR': str(engine.mkdirs(home, 'tmp'))}
    # A short-lived actual parent hands off to the copied native helper. The
    # helper itself observes reparenting; no fake PID/receipt replaces that check.
    host_code = ('import subprocess,sys,time; from pathlib import Path\n'
                 'p=subprocess.Popen([sys.argv[1],"--macos-update-worker",sys.argv[2],"apply"],'
                 'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n'
                 'deadline=time.monotonic()+60\n'
                 'while not Path(sys.argv[1]).with_name("ready.json").exists():\n'
                 ' if p.poll() is not None or time.monotonic()>deadline: p.kill(); raise SystemExit(2)\n'
                 ' time.sleep(0.1)\n'
                 'print(p.pid)\n')
    worker_pid = int(subprocess.check_output([sys.executable, '-I', '-B', '-c', host_code,
        str(support_job / 'native-helper'), job.name], env=worker_env, text=True).strip())
    try:
        result = wait_for(lambda: engine.read_json(job / 'result.json') if (job / 'result.json').exists() else None,
                          450, 'Native update did not finish; artifacts retained at ' + str(base))
        require(result['status'] == 'complete', 'Native update failed: ' + str(result))
        for name in ('launch-ready.json', 'core-ready.json'):
            require(engine.read_json(job / name)['nonce'] == info['nonce'], 'Missing real launch/Pi trial receipt')
        engine.verify_programs(root, engine.manifest(new))
        engine.verify_bundle(app, new['app_files'], version)
        require(not (job.parent / 'active').exists() and not (support_job.parent / 'active').exists(), 'Update gate retained')
        for name, value in canaries.items():
            require((root / name).read_bytes() == value, 'User canary changed')
        wait_for(lambda: not port_free(), 60, 'Fresh normal application did not restart')
        require(engine.runtime_fingerprint(root) == baseline['fingerprint'], 'Preserved runtime dependencies changed')
        report = {'status': 'passed', 'version': version, 'real_native_app_swap': True,
                  'real_signed_prepare': True, 'real_workbench_and_pi_trial': True,
                  'finder_minimal_path': True, 'fresh_normal_restart': True, 'data_canaries_unchanged': True,
                  'dependency_fingerprint_unchanged': True, 'transport': 'local signed fixture',
                  'confirmation': 'explicit CI fixture', 'model_requests': 0}
        engine.write_json(base / 'acceptance.json', report)
        print(json.dumps(report), flush=True)
    finally:
        # The worker normally exits after its final spawn. Do not delete test
        # evidence or signal a PID unless it still names this exact helper.
        stop_synthetic_apps(home, support_job / 'native-helper')
        wait_for(port_free, 15, 'Synthetic application port was not released')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--app', type=Path)
    args = parser.parse_args()
    require(sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true', 'Hosted macOS CI only')
    require(port_free(), 'Port 8765 already occupied; refusing to affect another runtime')
    if args.run:
        base = args.run.resolve(strict=True)
        require(base.name.startswith('agent4market-native-acceptance-') and args.app, 'Invalid synthetic workspace')
        run_case(base, args.app.resolve(strict=True))
        return
    require(args.dist, 'Release build directory required')
    dist = args.dist.resolve(strict=True)
    document = engine.read_json(dist / 'macOS-install-manifest.json')
    programs = dist / ('Agent4Market-' + document['version'] + '-macos-programs.zip')
    base = engine.trusted_directory(Path.home().resolve() / ('agent4market-native-acceptance-' + secrets.token_hex(16)),
                                    create=True, private=True)
    for name in ('runtime', 'candidate'):
        engine.extract_archive(programs, base / name, document['files'])
    engine.trusted_directory(base / 'home', create=True, private=True)
    shutil.copytree(SOURCE / 'node_modules', base / 'runtime/node_modules', symlinks=True)
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(base / 'runtime/.venv')], check=True)
    completed = subprocess.run([str(base / 'runtime/.venv/bin/python'), '-B', str(Path(__file__).resolve()),
        '--run', str(base), '--app', str(dist / 'Agent4Market.app')], timeout=900)
    require(completed.returncode == 0, 'Acceptance failed; synthetic evidence retained at ' + str(base))


if __name__ == '__main__':
    main()
