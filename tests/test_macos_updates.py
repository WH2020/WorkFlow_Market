"""Focused portable checks. These do NOT claim native macOS acceptance."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile

from agent_platform import macos_update_engine as engine
from agent_platform import macos_update_signatures as signatures
from agent_platform import windows_update_signatures as windows_signatures
from agent_platform.macos_updates import MacOSUpdateManager
from agent_platform.app_updates import macos_release_assets


def record(name, content=b'program', mode=0o644):
    return {'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'mode': mode}


class MacOSUpdatesTests(unittest.TestCase):
    def test_release_assets_require_exact_names_ids_sizes_and_digests(self):
        names = ('Agent4Market-1.2.3-macos-programs.zip', 'Agent4Market-1.2.3-macos-universal-app.zip',
                 'macOS-install-manifest.json', 'macOS-update-signature.json')
        rows = [{'id': index + 1, 'name': name, 'size': 50, 'state': 'uploaded', 'digest': 'sha256:' + 'a' * 64}
                for index, name in enumerate(names)]
        self.assertEqual(set(macos_release_assets({'assets': rows}, 'v1.2.3')), {'programs', 'app', 'manifest', 'signature'})
        self.assertIsNone(macos_release_assets({'assets': rows + [rows[0]]}, 'v1.2.3'))
        self.assertIsNone(macos_release_assets({'assets': rows}, 'v1.2.4'))
        rows[0]['digest'] = None
        self.assertIsNone(macos_release_assets({'assets': rows}, 'v1.2.3'))

    def test_mac_signature_is_platform_version_and_both_archives_bound(self):
        from Crypto.PublicKey import ECC
        from Crypto.Signature import eddsa
        with tempfile.TemporaryDirectory(prefix='macos-signature-test-', dir=Path.home()) as directory:
            root = Path(directory)
            key = ECC.generate(curve='Ed25519')  # Ephemeral fixture, never publisher custody.
            (root / 'agent_platform').mkdir()
            (root / signatures.TRUST_FILE).write_text(json.dumps({'format': 1, 'algorithm': 'ed25519',
                'public_key': base64.b64encode(key.public_key().export_key(format='raw')).decode()}))
            assets = {name: {'bytes': 20, 'sha256': letter * 64} for name, letter in (('programs', 'a'), ('app', 'b'), ('manifest', 'c'))}
            signed = signatures.signed_record('1.2.3', assets)
            signature = eddsa.new(key, 'rfc8032').sign(signatures.canonical(signed))
            path = root / 'signature.json'
            path.write_text(json.dumps({'format': 1, 'signed': signed, 'signature': base64.b64encode(signature).decode()}))
            signatures.verify(root, path, '1.2.3', assets)
            with self.assertRaises(engine.UpdateFailure):
                signatures.verify(root, path, '1.2.4', assets)
            changed = {**assets, 'app': {'bytes': 20, 'sha256': 'd' * 64}}
            with self.assertRaises(engine.UpdateFailure):
                signatures.verify(root, path, '1.2.3', changed)
            with self.assertRaises(engine.UpdateFailure):
                windows_signatures.verify(root, path, '1.2.3', {'installer': assets['programs'], 'manifest': assets['manifest']})

    def test_business_data_dependencies_and_bundle_escape_links_are_refused(self):
        for name in ('data/user.json', '.pi/settings.json', 'outputs/report.docx', 'node_modules/pkg/x.js', '.venv/bin/python',
                     'library/templates/company/brand.png', 'ui/../settings.json', 'ui\\server.py'):
            with self.subTest(path=name), self.assertRaises(engine.UpdateFailure):
                engine.program_path(name)
        app_rows = [record('Contents/Info.plist'), record('Contents/MacOS/Agent4Market'),
                    {'path': 'Contents/Resources/escape', 'link': '../../../../outside'}]
        with self.assertRaises(engine.UpdateFailure):
            engine.parse_rows(app_rows, app=True)

    def test_dependency_contract_ignores_app_version_but_not_dependency_changes(self):
        with tempfile.TemporaryDirectory(prefix='macos-dependency-test-', dir=Path.home()) as directory:
            root = Path(directory)
            package = {'version': '1.0.0', 'dependencies': {'example': '1.0.0'}}
            (root / 'package.json').write_text(json.dumps(package))
            for name in ('pnpm-lock.yaml', 'requirements.txt', 'requirements-wxdecipher.txt'):
                (root / name).write_text('synthetic-lock\n')
            before = engine.dependency_contract(root)
            package['version'] = '1.1.0'
            (root / 'package.json').write_text(json.dumps(package))
            self.assertEqual(before, engine.dependency_contract(root))
            package['devDependencies'] = {'runtime-cli': '1.0.0'}
            (root / 'package.json').write_text(json.dumps(package))
            self.assertNotEqual(before, engine.dependency_contract(root))
            del package['devDependencies']
            package['dependencies']['example'] = '1.1.0'
            (root / 'package.json').write_text(json.dumps(package))
            self.assertNotEqual(before, engine.dependency_contract(root))

    def test_zip_only_installs_listed_files_and_refuses_extra_members_before_writes(self):
        with tempfile.TemporaryDirectory(prefix='macos-archive-test-', dir=Path.home()) as directory:
            root = Path(directory)
            archive = root / 'programs.zip'
            content = b'new program\n'
            rows = [record('ui/server.py', content)]
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('ui/server.py', content)
            engine.extract_archive(archive, root / 'good', rows)
            self.assertEqual((root / 'good/ui/server.py').read_bytes(), content)
            for invalid in ('../outside', 'data/user.json', 'ui/server.py'):
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('ui/server.py', content)
                    output.writestr(invalid, b'not authorized')
                target = root / ('bad-' + hashlib.sha256(invalid.encode()).hexdigest()[:8])
                with self.assertRaises(engine.UpdateFailure):
                    engine.extract_archive(archive, target, rows)
                self.assertFalse((target / 'ui/server.py').exists())

    def test_bundle_hash_inventory_and_version_are_checked(self):
        with tempfile.TemporaryDirectory(prefix='macos-bundle-test-', dir=Path.home()) as directory:
            root = Path(directory) / 'Agent4Market.app'
            (root / 'Contents/MacOS').mkdir(parents=True)
            (root / 'Contents/MacOS/Agent4Market').write_bytes(b'SYNTHETIC NOT EXECUTABLE')
            (root / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.workflowmarket.salesdirector',
                'CFBundleExecutable': 'Agent4Market', 'CFBundleShortVersionString': '1.2.3'}))
            rows = engine.bundle_rows(root)
            # Inventory and plist check are portable; codesign/lipo are Mac-only.
            from unittest.mock import patch
            with patch.object(engine.sys, 'platform', 'win32'):
                engine.verify_bundle(root, rows, '1.2.3')
                with self.assertRaises(engine.UpdateFailure):
                    engine.verify_bundle(root, rows, '1.2.4')
                (root / 'Contents/unlisted').write_bytes(b'not in publisher seal')
                with self.assertRaises(engine.UpdateFailure):
                    engine.verify_bundle(root, rows, '1.2.3')

    def test_builder_excludes_authoring_keys_business_data_and_private_dependencies(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/build-macos-update.py'
        spec = importlib.util.spec_from_file_location('macos_update_builder_test', path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        for name in ('scripts/sign-macos-update.py', 'scripts/release_signing_key.py', '.pi/settings.json',
                     'data/account.json', 'outputs/report.pdf', '.venv/lib/package.py', 'node_modules/module.js',
                     'library/templates/company/logo.png', 'agent_platform/private.dpapi.key'):
            self.assertFalse(builder.selected(name), name)
        self.assertTrue(builder.selected('agent_platform/macos_updates.py'))
        self.assertTrue(builder.selected('scripts/enroll-macos-update.py'))
        with tempfile.TemporaryDirectory(prefix='macos-builder-test-', dir=Path.home()) as directory:
            root = Path(directory)
            (root / 'package.json').write_text('{"version":"1.0.0"}')
            (root / 'ui').mkdir()
            (root / 'ui/server.py').write_text('synthetic program')
            self.assertEqual([row['path'] for row in builder.source_rows(root)], ['package.json', 'ui/server.py'])

    def test_partial_handoff_keeps_business_gate_until_native_recovery(self):
        with tempfile.TemporaryDirectory(prefix='macos-handoff-test-', dir=Path.home()) as directory:
            root = Path(directory)
            manager = MacOSUpdateManager(root, None)
            manager.job = root / 'runtime-jobs/job'
            manager.support_job = root / 'support-jobs/job'
            manager.job.mkdir(parents=True)
            manager.support_job.mkdir(parents=True)
            pointer = manager.support_job.parent / 'active'
            pointer.write_text('synthetic job\n1.1.0\n')
            manager._preparation_failed(OSError('second pointer fsync failed'))
            self.assertTrue(manager.frozen)
            self.assertEqual(manager.state['error']['code'], 'RECOVERY_REQUIRED')
            pointer.rename(manager.support_job / 'recovered-active')
            manager._preparation_failed(OSError('failed before handoff'))
            self.assertFalse(manager.frozen)

    def test_space_budget_aggregates_same_volume_instead_of_checking_paths_separately(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='macos-space-test-', dir=Path.home()) as directory:
            root = Path(directory)
            paths = [root / name for name in ('source', 'support', 'applications')]
            for path in paths:
                path.mkdir()
            unit = 1024**2
            with patch.object(engine.shutil, 'disk_usage', return_value=SimpleNamespace(free=300 * unit)):
                with self.assertRaisesRegex(engine.UpdateFailure, 'NOT_ENOUGH_SPACE'):
                    engine.check_space([(path, 100 * unit) for path in paths])
                engine.check_space([(path, 50 * unit) for path in paths])


if __name__ == '__main__':
    unittest.main()
