from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_payload_tests", ROOT / "scripts/build-windows-public-payload.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


requires_frozen_release_inputs = unittest.skipUnless(
    os.environ.get("AGENT4MARKET_RUN_LEGACY_RELEASE_TESTS") == "1"
    and (ROOT / "outputs/releases/0.20.2/install-manifest.json").is_file()
    and M.PATCH.ZIP_PATH.is_file(),
    "legacy release validation is explicit opt-in and requires local ignored inputs",
)


class WindowsPublicPayloadTests(unittest.TestCase):
    def inputs(self):
        _raw, original = M.BASE.read_manifest(ROOT / "outputs/releases/0.20.2/install-manifest.json")
        manifest, contents = M.PATCH.load_patch()
        return original, manifest, contents

    @requires_frozen_release_inputs
    def test_real_closed_plan_contains_updates_and_no_runtime_inventory(self):
        original, manifest, contents = self.inputs()
        plan = M.planned_records(original, manifest, contents, b"Public installation notes")
        rows = {row["path"]: row for row in plan}
        self.assertEqual(len(rows), 28057)
        self.assertNotIn(M.PATCH.MANIFEST_PATH, rows)
        self.assertIn("agent_platform/app_updates.py", rows)
        self.assertIn("agent_platform/cli_model_catalog.py", rows)
        self.assertEqual(rows["Agent4Market.exe"], next(r for r in original if r["path"] == "Agent4Market.exe"))
        for row in plan:
            M.POLICY.safe_relative(row["path"])
        for relative in (".pi/settings.json", "data/private.json", "outputs/result.txt", "library/templates/company/template.pptx"):
            self.assertNotIn(relative, rows)

    @requires_frozen_release_inputs
    def test_changed_baseline_or_patch_bytes_are_refused(self):
        original, manifest, contents = self.inputs()
        changed = copy.deepcopy(manifest)
        changed["changes"][1]["previous_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "baseline"):
            M.planned_records(original, changed, contents, b"notes")
        changed_bytes = {**contents, "ui/app.js": b"unreviewed program"}
        with self.assertRaisesRegex(ValueError, "content"):
            M.planned_records(original, manifest, changed_bytes, b"notes")

    @requires_frozen_release_inputs
    def test_real_working_source_drift_is_refused(self):
        _original, manifest, contents = self.inputs()
        with tempfile.TemporaryDirectory(prefix="a4m-public-source-") as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().name.startswith("a4m-public-source-"))
            for relative in M.PATCH.BUILDER.PATCH_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents[relative])
            M.verify_current_sources(manifest, root=root)
            (root / "ui/app.js").write_bytes(b"unreviewed working tree modification")
            with self.assertRaisesRegex(ValueError, "Working source"):
                M.verify_current_sources(manifest, root=root)

    @unittest.skipUnless(os.name == "nt", "Windows verification targets")
    def test_existing_targets_are_refused_before_running_any_installer(self):
        identifier = "4" * 32
        with tempfile.TemporaryDirectory(prefix="a4m-public-plan-") as directory:
            parent = Path(directory)
            self.assertTrue(parent.resolve().name.startswith("a4m-public-plan-"))
            for name in ("Agent4Market-0.20.2-test-" + identifier, "Agent4Market-0.20.2-release-build-" + identifier):
                target = parent / name
                target.mkdir()
                with patch.object(M.POLICY, "profile_path", return_value=parent), patch.object(M.subprocess, "run") as run:
                    with self.assertRaises(FileExistsError):
                        M.build(identifier)
                    run.assert_not_called()
                target.rmdir()  # This empty synthetic leaf only.

    def test_invalid_identifier_is_refused(self):
        for identifier in ("", "../other", "A" * 32, "4" * 31):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                M.build(identifier)

    def test_installer_inventory_requires_update_modules_and_uses_lf(self):
        spec = importlib.util.spec_from_file_location("public_installer_manifest", ROOT / "scripts/windows-installer-manifest.py")
        inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory)
        with tempfile.TemporaryDirectory(prefix="a4m-public-inventory-") as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().name.startswith("a4m-public-inventory-"))
            payload, output = root / "payload", root / "output"
            payload.mkdir()
            output.mkdir()
            required = ("Agent4Market.exe", "runtime/private-runtime.marker", ".venv/Scripts/python311._pth",
                        "agent_platform/cli_process_host.py", "agent_platform/cli_provider.py",
                        "pi/extensions/cli-model-provider.ts", "agent_platform/app_updates.py", "agent_platform/cli_model_catalog.py")
            for relative in required:
                path = payload / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic public source")
            arguments = ["inventory", "--payload", str(payload), "--output", str(output), "--version", "0.20.2"]
            for relative in required[-2:]:
                path = payload / relative
                path.unlink()
                with patch("sys.argv", arguments), self.assertRaises(ValueError):
                    inventory.main()
                self.assertFalse((output / "install-manifest.json").exists())
                path.write_bytes(b"synthetic public source")
            with patch("sys.argv", arguments):
                inventory.main()
            content = (output / "install-manifest.json").read_bytes()
            self.assertNotIn(b"\r", content)
            self.assertEqual({r["path"] for r in json.loads(content)["files"]}, set(required))


if __name__ == "__main__":
    unittest.main()
