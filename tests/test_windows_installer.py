from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("installer_bootstrap", ROOT / "scripts/windows-installer-bootstrap.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallerPolicyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows ACL contract")
    def test_private_directory_is_atomic_and_existing_target_acl_is_untouched(self):
        from agent_platform import wechat_privacy
        with tempfile.TemporaryDirectory(prefix="a4m-installer-policy-", dir=INSTALLER.profile_path()) as directory:
            parent = Path(directory)
            wechat_privacy.ensure_private_directory(parent)
            target = parent / "new-release"
            INSTALLER.prepare(target, wechat_privacy)
            wechat_privacy.verify_private_directory(target)
            with patch.object(wechat_privacy, "_win_set_private_dacl") as repair:
                with self.assertRaises(RuntimeError):
                    INSTALLER.prepare(target, wechat_privacy)
                repair.assert_not_called()
            wechat_privacy.verify_private_directory(target)

    def test_only_fixed_version_root_and_uuid_verification_root_are_accepted(self):
        with patch.object(INSTALLER, "profile_path", return_value=Path("C:/Users/Synthetic")):
            target = Path("C:/Users/Synthetic") / "Agent4Market-0.20.2"
            self.assertEqual(INSTALLER.target_path(str(target), "0.20.2"), target)
            test_target = Path(str(target) + "-test-" + "a" * 32)
            self.assertEqual(INSTALLER.target_path(str(test_target), "0.20.2", "a" * 32), test_target)
            for invalid in [str(target.parent), str(target) + "-other", str(target.parent / "Agent4Market-0.20.1"), str(target / "..")]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    INSTALLER.target_path(invalid, "0.20.2")
            for invalid in ["../old", "", "1.2", "0.20.2/.."]:
                with self.subTest(version=invalid), self.assertRaises(ValueError):
                    INSTALLER.target_path(str(target), invalid)
            with self.assertRaises(ValueError):
                INSTALLER.target_path(str(target), "0.20.2", "../../outside")

    def test_manifest_blocks_private_data_traversal_and_ambiguous_windows_paths(self):
        for path in [".pi/auth.json", "outputs/report.pdf", "data/wechat/index.db", "data/sales/customers.csv",
                     "agent_platform/%SystemDrive%/cache.db", "agent_platform/../secret.py", "C:/secret", "ui/app.js:ads",
                     "ui\\app.js", "ui//app.js", "ui/./app.js", "library/templates/company/logo.png", "ui/app.js."]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                INSTALLER.safe_relative(path)
        for path in ["agent_platform/cli_provider.py", ".venv/Scripts/python.exe", "node_modules/typebox/index.js",
                     "data/sales/customers.example.csv", "runtime/private-runtime.marker", "LICENSE"]:
            self.assertEqual(str(INSTALLER.safe_relative(path)), path)

    def fixture(self, root: Path):
        (root / "runtime").mkdir()
        (root / "ui").mkdir()
        (root / "data/sales").mkdir(parents=True)
        (root / ".pi").mkdir()
        files = {"ui/app.js": "original", "ui/index.html": "unchanged", "data/sales/customers.example.csv": "example",
                 "runtime/private-runtime.marker": "Agent4Market private runtime v1"}
        rows = []
        for relative, text in files.items():
            path = root / relative
            path.write_text(text)
            rows.append({"path": relative, "sha256": INSTALLER.digest(path)})
        (root / "runtime/install-manifest.json").write_text(json.dumps({"version": "0.20.2", "files": rows}))
        (root / ".pi/user-config.json").write_text("synthetic private config")
        (root / "data/sales/customers.csv").write_text("synthetic user data")
        return rows

    def test_uninstall_only_removes_unchanged_program_files_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "ui/app.js").write_text("user changed program")
            class Privacy:
                @staticmethod
                def _windows_verify(target):
                    assert target == root
            INSTALLER.uninstall(root, "0.20.2", Privacy)
            self.assertFalse((root / "ui/index.html").exists())
            for relative in ["ui/app.js", "data/sales/customers.csv", "data/sales/customers.example.csv", ".pi/user-config.json", "runtime/private-runtime.marker"]:
                self.assertTrue((root / relative).is_file(), relative)
            self.assertTrue(root.is_dir())

    def test_entire_uninstall_plan_is_validated_before_first_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = self.fixture(root)
            rows.append({"path": "../outside", "sha256": "a" * 64})
            (root / "runtime/install-manifest.json").write_text(json.dumps({"version": "0.20.2", "files": rows}))
            with patch.object(INSTALLER, "privacy_module") as privacy:
                with self.assertRaises(ValueError):
                    INSTALLER.uninstall(root, "0.20.2", privacy)
            self.assertTrue((root / "ui/index.html").exists())

    def test_script_has_no_global_install_or_recursive_removal(self):
        source = (ROOT / "desktop/windows-installer.nsi").read_text(encoding="utf-8")
        self.assertIn("RequestExecutionLevel user", source)
        self.assertNotIn("HKLM", source)
        self.assertNotIn("RMDir /r", source)
        self.assertNotIn("taskkill", source)
        self.assertNotIn("ExecShell", source)
        self.assertIn('StrCpy $INSTDIR "$PROFILE\\Agent4Market-${VERSION}$RootSuffix"', source)
        self.assertLess(source.index("verify-staging"), source.index('File "${PAYLOAD_ROOT}\\Agent4Market.exe"'))
        bootstrap = (ROOT / "scripts/windows-installer-bootstrap.py").read_text(encoding="utf-8")
        self.assertIn("CreateDirectoryW(str(target), ctypes.byref(attributes))", bootstrap)
        self.assertNotIn("_win_set_private_dacl", bootstrap)
        self.assertNotIn("shutil.rmtree", bootstrap)


if __name__ == "__main__":
    unittest.main()
