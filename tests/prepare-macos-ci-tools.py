"""Give hosted-CI tools a private synthetic install; never relax runtime policy."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_platform import macos_update_engine as engine


def main():
    engine.require(sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true', 'HOSTED_MACOS_CI_ONLY')
    base = engine.trusted_directory(Path.home().resolve() / ('agent4market-ci-tools-' + secrets.token_hex(16)),
                                    create=True, private=True)
    binary = engine.trusted_directory(base / 'bin', create=True)
    for name in ('node', 'rg', 'fd'):
        source = Path(shutil.which(name)).resolve(strict=True)
        target = binary / name
        shutil.copyfile(source, target)
        target.chmod(0o755)
        engine.require(engine.digest(source) == engine.digest(target), 'CI_TOOL_COPY_CHANGED')
    command = Path(shutil.which('pnpm')).resolve(strict=True)
    candidates = [command.parent.parent / 'pnpm', *command.parents]
    package = next((path for path in candidates if (path / 'package.json').is_file()
                    and json.loads((path / 'package.json').read_bytes()).get('name') == 'pnpm'), None)
    engine.require(package is not None, 'PNPM_PACKAGE_UNAVAILABLE')
    package = package.resolve(strict=True)
    for path in package.rglob('*'):
        engine.require(not path.is_symlink() or path.resolve(strict=True).is_relative_to(package), 'PNPM_LINK_ESCAPES')
    target = base / 'pnpm'
    shutil.copytree(package, target)
    for path in (target, *target.rglob('*')):
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    (binary / 'pnpm').symlink_to(target / 'bin/pnpm.cjs')
    # The hosted system Git/CLT is root-owned. Do not enroll mutable Homebrew
    # runner image directories as production-style trusted tool ancestors.
    (binary / 'git').symlink_to('/usr/bin/git')
    os.environ['PATH'] = str(binary) + ':' + os.environ['PATH']
    engine.enroll_tools(ROOT)
    controlled = {'PATH': engine.runtime_path(ROOT), 'HOME': str(base), 'LANG': 'en_US.UTF-8'}
    for name in engine.TOOL_NAMES:
        check = subprocess.run([name, '--version'], env=controlled, capture_output=True, timeout=20)
        engine.require(check.returncode == 0 and check.stdout.strip(), 'CI_TOOL_CANNOT_RUN')
    print(binary)


if __name__ == '__main__':
    main()
