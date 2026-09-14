from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "package-private-windows.ps1").read_text(encoding="utf-8")


class WindowsPackageScriptTests(unittest.TestCase):
    def test_only_tracked_and_literal_reviewed_untracked_sources_are_candidates(self) -> None:
        self.assertIn("ls-files --cached", SCRIPT)
        self.assertNotIn("--others", SCRIPT)
        reviewed = {
            "agent_platform/app_updates.py",
            "agent_platform/cli_model_catalog.py",
            "agent_platform/cli_process_host.py",
            "agent_platform/cli_provider.py",
            "agent_platform/local_http_security.py",
            "agent_platform/wechat_privacy.py",
            "agent_platform/wechat_storage.py",
            "agent_platform/wxdecipher.py",
            "agent_platform/wxdecipher_capture.py",
            "agent_platform/wxdecipher_crypto.py",
            "agent_platform/wxdecipher_media.py",
            "agent_platform/wxdecipher_reader.py",
            "agent_platform/wxdecipher_wal.py",
            "pi/extensions/cli-model-provider.ts",
            "ui/wxdecipher.css",
            "requirements-wxdecipher.txt",
            "desktop/private-runtime.marker",
            "desktop/python311._pth",
        }
        block = re.search(r"\$ReviewedUntrackedFiles\s*=\s*@\((.*?)\n\s*\)", SCRIPT, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(reviewed, set(re.findall(r"'([^']+)'", block.group(1))))
        self.assertNotIn("%SystemDrive%", block.group(1))
        self.assertRegex(SCRIPT, r"\(tests\|outputs\|__pycache__\|company\|node_modules\|\\\.pi\)")
        self.assertIn("\\.(db|sqlite|sqlite3)(-wal|-shm)?$", SCRIPT)
        self.assertIn("'^data/.+\\.example\\.(csv|json)$'", SCRIPT)
        self.assertIn("'LICENSE'", SCRIPT)

    def test_embedded_python_is_hash_pinned_and_installed_without_a_venv(self) -> None:
        for value in (
            "PythonEmbedArchive",
            "PythonEmbedSha256",
            "Get-FileHash",
            "Expand-Archive",
            "--only-binary=:all:",
            "--no-compile",
            "--target $SitePackages",
            "desktop\\python311._pth",
            "Embedded Python isolation verified",
            "$PackagePython -I -B -c $SmokeTest $PackageRoot",
            "3.11.9:64",
        ):
            self.assertIn(value, SCRIPT)
        self.assertRegex(SCRIPT, re.compile(r"if \(\$HasEmbedArchive\).*?else \{\s*& \$Python -m venv \.venv", re.S))

    def test_python_path_file_has_only_fixed_package_relative_entries(self) -> None:
        lines = (ROOT / "desktop" / "python311._pth").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            ["python311.zip", ".", "..\\Lib\\site-packages", "..\\.."],
            lines,
        )
        self.assertTrue(all(":" not in line and not line.startswith(("/", "\\")) for line in lines))

    def test_clean_release_can_skip_only_data_initialization(self) -> None:
        self.assertIn("[switch]$SkipDataInitialization", SCRIPT)
        self.assertIn("if (-not $SkipDataInitialization)", SCRIPT)
        self.assertIn("init_local_data.py --project $PackageRoot", SCRIPT)
        self.assertNotRegex(SCRIPT, r"Copy-Item[^\n]+(?:tests|outputs|\\.pi|company)")


if __name__ == "__main__":
    unittest.main()
