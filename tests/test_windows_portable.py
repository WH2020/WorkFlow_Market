from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("portable_builder", ROOT / "scripts/build-windows-portable.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class WindowsPortableTests(unittest.TestCase):
    def manifest(self):
        paths = {"Agent4Market.exe", "runtime/private-runtime.marker", ".venv/Scripts/python311._pth",
                 ".venv/Scripts/python.exe", "runtime/node/node.exe", "agent_platform/cli_process_host.py",
                 "pi/extensions/cli-model-provider.ts", *BUILDER.INITIALIZER.TEMPLATES}
        return {"version": "0.20.2", "total_bytes": 0,
                "files": [{"path": path, "bytes": 0, "sha256": "a" * 64} for path in sorted(paths)]}

    def test_targets_are_fixed_new_portable_directories(self):
        with patch.object(BUILDER.POLICY, "profile_path", return_value=Path("C:/Users/Synthetic")):
            self.assertEqual(BUILDER.target_path(), Path("C:/Users/Synthetic/Agent4Market-0.20.2-portable"))
            self.assertTrue(BUILDER.target_path("a" * 32).name.endswith("-test-" + "a" * 32))
            for value in ("..", "0.20.1", "A" * 32, "a" * 31, "a/" * 16):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    BUILDER.target_path(value)

    def test_existing_versions_are_rejected_before_privacy_or_data_access(self):
        with patch.object(BUILDER.POLICY, "profile_path", return_value=Path("C:/Users/Synthetic")):
            with patch.object(BUILDER.PRIVACY, "_windows_verify") as verify:
                for value in ("C:/Users/Synthetic/Agent4Market-0.20.1", "C:/Users/Synthetic/Agent4Market-0.20.2",
                              "C:/Users/Synthetic/Agent4Market-0.20.2-portable", "C:/Agent4Market-0.20.2-payload-20260910"):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        BUILDER.source_path(Path(value))
                verify.assert_not_called()

    def test_valid_release_contract_is_accepted(self):
        self.assertEqual(len(BUILDER.validated_manifest(self.manifest())), 13)

    def test_replaced_manifest_is_rejected_by_the_pinned_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text('{"version":"0.20.2","files":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                BUILDER.read_manifest(path)

    @unittest.skipUnless(os.name == "nt", "Windows-only builder")
    def test_existing_target_is_refused_without_reading_the_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(BUILDER, "target_path", return_value=Path(directory)):
                with patch.object(BUILDER, "source_path") as read_source:
                    with self.assertRaises(FileExistsError):
                        BUILDER.build(Path("unused-payload"), Path("unused-manifest"))
                    read_source.assert_not_called()

    def test_manifest_rejects_private_data_and_case_collisions(self):
        for path in (".pi/auth.json", "data/sales/customers.csv", "../old/data.json", "Agent4Market.EXE"):
            manifest = self.manifest()
            manifest["files"].append({"path": path, "bytes": 0, "sha256": "a" * 64})
            with self.subTest(path=path), self.assertRaises(ValueError):
                BUILDER.validated_manifest(manifest)

    def test_manifest_rejects_missing_runtime_and_invalid_sizes(self):
        manifest = self.manifest()
        manifest["files"] = [row for row in manifest["files"] if row["path"] != "Agent4Market.exe"]
        with self.assertRaises(ValueError):
            BUILDER.validated_manifest(manifest)
        for value in (-1, True, "100"):
            manifest = self.manifest()
            manifest["files"][0]["bytes"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                BUILDER.validated_manifest(manifest)

    def test_copy_never_overwrites_and_verifies_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.write_bytes(b"synthetic program")
            row = {"bytes": source.stat().st_size, "sha256": BUILDER.POLICY.digest(source)}
            BUILDER.copy_verified(source, target, row)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            with self.assertRaises(FileExistsError):
                BUILDER.copy_verified(source, target, row)
            with self.assertRaises(ValueError):
                BUILDER.copy_verified(source, root / "bad", {**row, "sha256": "a" * 64})


if __name__ == "__main__":
    unittest.main()
