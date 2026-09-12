"""Focused host-tool registration tests; no real credentials or tool execution."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_platform import macos_update_engine as engine


@unittest.skipUnless(sys.platform == 'darwin', 'macOS ownership, ACL and symlink semantics')
class MacOSRuntimeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agent4market-tool-test-', dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'node_modules/.bin').mkdir(parents=True)
        self.commands = self.root / 'external/bin'
        self.commands.mkdir(parents=True)
        self.targets = self.root / 'external/lib'
        self.targets.mkdir()
        for name in engine.TOOL_NAMES:
            target = self.targets / (name + '.fixture')
            target.write_text('#!/bin/sh\nexit 0\n')
            target.chmod(0o755)
            (self.commands / name).symlink_to(target)

    def enroll(self):
        with patch.dict(os.environ, {'PATH': str(self.commands)}):
            return engine.enroll_tools(self.root)

    def test_finder_path_uses_registered_commands_and_preserves_pnpm_name(self):
        before = self.enroll()
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}):
            path = engine.runtime_path(self.root)
            self.assertEqual(engine.verified_tools(self.root), before)
            for name in engine.TOOL_NAMES:
                self.assertEqual(engine.shutil.which(name, path=path), str(self.commands / name))
            self.assertIn(str(self.commands), path.split(':'))
            self.assertNotIn(str(self.targets), path.split(':'))

    def test_changed_tool_or_retargeted_link_is_rejected(self):
        self.enroll()
        node = self.targets / 'node.fixture'
        node.write_text('#!/bin/sh\nexit 9\n')
        with self.assertRaisesRegex(engine.UpdateFailure, 'TOOL_CHANGED'):
            engine.runtime_path(self.root)
        self.enroll()
        (self.commands / 'node').unlink()
        (self.commands / 'node').symlink_to(self.targets / 'fd.fixture')
        with self.assertRaisesRegex(engine.UpdateFailure, 'TOOL_CHANGED'):
            engine.runtime_path(self.root)

    def test_writable_tool_directory_and_path_shadowing_are_rejected(self):
        self.enroll()
        self.commands.chmod(0o777)
        with self.assertRaisesRegex(engine.UpdateFailure, 'UNSAFE_OWNER_OR_MODE'):
            engine.runtime_path(self.root)
        self.commands.chmod(0o755)
        shadow = self.root / 'node_modules/.bin/node'
        shadow.write_text('#!/bin/sh\nexit 0\n')
        shadow.chmod(0o755)
        with self.assertRaisesRegex(engine.UpdateFailure, 'TOOL_PATH_COLLISION'):
            engine.runtime_path(self.root)

    def test_foreign_root_and_missing_registration_are_rejected(self):
        with self.assertRaises(engine.UpdateFailure):
            engine.runtime_path(self.root)
        document = self.enroll()
        document['root'] = str(self.root.parent)
        engine.write_json(self.root / engine.TOOLS_FILE, document)
        with self.assertRaisesRegex(engine.UpdateFailure, 'INVALID_TOOL_REGISTRATION'):
            engine.runtime_path(self.root)


if __name__ == '__main__':
    unittest.main()
