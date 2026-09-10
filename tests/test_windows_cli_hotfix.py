from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cli_hotfix_builder", ROOT / "scripts/build-windows-cli-catalog-hotfix.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class WindowsCliHotfixTests(unittest.TestCase):
    def test_archive_name_preserves_the_dotted_version(self):
        self.assertEqual(BUILDER.archive_path(Path("output/0.20.2-codex-catalog-20260910")), Path("output/0.20.2-codex-catalog-20260910.zip"))

    def test_allowlist_only_contains_expected_program_files(self):
        self.assertEqual(len(BUILDER.PATCH_FILES), 15)
        self.assertEqual(len(set(BUILDER.PATCH_FILES)), 15)
        for relative in BUILDER.PATCH_FILES:
            BUILDER.BASE.POLICY.safe_relative(relative)
            self.assertNotIn(relative.split("/")[0], {"data", ".pi", "outputs", "library", ".venv"})

    def test_existing_output_is_rejected_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(BUILDER.BASE, "read_manifest") as read:
            with self.assertRaises(FileExistsError):
                BUILDER.build(Path(directory), Path("not-read"))
            read.assert_not_called()

    def test_replaced_manifest_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "invalid.json"
            manifest.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                BUILDER.build(root / "not-created", manifest)
            self.assertFalse((root / "not-created").exists())

    def test_legacy_builder_rejects_current_sources_before_creating_any_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(BUILDER.BASE, "read_manifest", return_value=(b"{}", [])):
            output = Path(directory) / "legacy-drift"
            with self.assertRaisesRegex(ValueError, "source fingerprint has changed"):
                BUILDER.build(output, Path("synthetic-manifest"))
            self.assertFalse(output.exists())
            self.assertFalse(BUILDER.archive_path(output).exists())


if __name__ == "__main__":
    unittest.main()
