from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("app_updates_hotfix", ROOT / "scripts/build-windows-app-updates-hotfix.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BUILDER = MODULE.BUILDER


class WindowsAppUpdatesHotfixTests(unittest.TestCase):
    def test_cumulative_closed_allowlist_includes_both_new_runtime_imports(self):
        self.assertEqual(len(BUILDER.PATCH_FILES), 17)
        self.assertEqual(len(set(BUILDER.PATCH_FILES)), 17)
        self.assertEqual(BUILDER.HOTFIX_ID, "0.20.2-app-updates-20260910")
        self.assertIn("agent_platform/app_updates.py", BUILDER.PATCH_FILES)
        self.assertIn("agent_platform/cli_model_catalog.py", BUILDER.PATCH_FILES)
        self.assertIn("ui/styles.css", BUILDER.PATCH_FILES)
        for relative in BUILDER.PATCH_FILES:
            BUILDER.BASE.POLICY.safe_relative(relative)
            self.assertNotIn(relative.split("/")[0], {"data", ".pi", "outputs", "library", ".venv"})

    def test_build_writes_exact_program_records_and_verified_archive(self):
        # Synthetic inventory AND reviewed source snapshot. A new application
        # fix must not alter the old production builder's frozen fingerprint.
        rows = [{"path": "package.json", "bytes": 2, "sha256": "a" * 64}]
        with tempfile.TemporaryDirectory() as directory, patch.object(BUILDER.BASE, "read_manifest", return_value=(b"{}", rows)):
            source = Path(directory) / "synthetic-source"
            fingerprints = []
            for relative in BUILDER.PATCH_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = ("synthetic reviewed program: " + relative).encode()
                path.write_bytes(content)
                fingerprints.append(relative + ":" + hashlib.sha256(content).hexdigest() + "\n")
            notes = source / "notes.md"
            notes.write_text("Synthetic installation notes", encoding="utf-8")
            expected = hashlib.sha256("".join(fingerprints).encode()).hexdigest()
            output = Path(directory) / "bundle"
            with patch.object(BUILDER, "ROOT", source), patch.object(BUILDER, "NOTES_PATH", notes), patch.object(BUILDER, "EXPECTED_SOURCE_SHA256", expected):
                result = BUILDER.build(output, Path("synthetic-manifest"))
            self.assertFalse(result["installed_application_modified"])
            self.assertFalse(result["existing_config_or_business_data_read"])
            self.assertTrue(result["requires_closed_application"])
            self.assertEqual(len(result["changes"]), 18)
            inventory = json.loads((output / "program/runtime/install-manifest.json").read_bytes())
            self.assertEqual({row["path"] for row in inventory["files"]}, {*BUILDER.PATCH_FILES, "package.json"})
            with zipfile.ZipFile(BUILDER.archive_path(output)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(len(archive.namelist()), 20)
                for change in result["changes"]:
                    path = "program/" + change["path"]
                    self.assertEqual(archive.read(path), (output / path).read_bytes())

    def test_existing_output_or_replaced_base_manifest_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(BUILDER.BASE, "read_manifest") as read:
                with self.assertRaises(FileExistsError):
                    BUILDER.build(root, Path("not-read"))
                read.assert_not_called()
            manifest = root / "invalid.json"
            manifest.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                BUILDER.build(root / "not-created", manifest)
            self.assertFalse((root / "not-created").exists())

    def test_current_builder_also_rejects_a_drifted_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(BUILDER.BASE, "read_manifest", return_value=(b"{}", [])), patch.object(BUILDER, "EXPECTED_SOURCE_SHA256", "0" * 64):
            output = Path(directory) / "new-drift"
            with self.assertRaisesRegex(ValueError, "source fingerprint has changed"):
                BUILDER.build(output, Path("synthetic-manifest"))
            self.assertFalse(output.exists())
            self.assertFalse(BUILDER.archive_path(output).exists())


if __name__ == "__main__":
    unittest.main()
