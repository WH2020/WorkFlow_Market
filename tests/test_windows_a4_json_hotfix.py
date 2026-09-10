from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("a4_json_hotfix_tests", ROOT / "scripts/apply-windows-a4-json-hotfix.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
SPEC = importlib.util.spec_from_file_location("a4_json_transaction_fixtures", ROOT / "tests/test_windows_app_updates_apply.py")
FIXTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURES)


@contextmanager
def fixture():
    # Reuse the existing UUID/synthetic-only transaction fixture with two paths.
    with patch.object(FIXTURES, "M", H.ENGINE), FIXTURES.fixture() as values:
        yield values


class A4JsonHotfixTests(unittest.TestCase):
    def test_fixed_plan_reconstructs_tested_ui_and_changes_only_its_inventory_row(self):
        manifest, contents = H.load_patch()
        self.assertEqual(tuple(row["path"] for row in manifest["changes"]), H.PROGRAM_FILES)
        self.assertEqual(H.PROGRAM_FILES, ("ui/app.js", "runtime/install-manifest.json"))
        # Historical bundles stay frozen when later features change the working UI.
        self.assertEqual(H.ENGINE.sha(contents["ui/app.js"]), H.NEW_APP_SHA256)
        with zipfile.ZipFile(H.SOURCE_ARCHIVE) as archive:
            old = json.loads(archive.read("program/runtime/install-manifest.json"))
        new = json.loads(contents["runtime/install-manifest.json"])
        self.assertEqual(new["version"], old["version"])
        self.assertEqual(len(new["files"]), len(old["files"]))
        changed = [after["path"] for before, after in zip(old["files"], new["files"], strict=True) if before != after]
        self.assertEqual(changed, ["ui/app.js"])
        self.assertEqual(new["total_bytes"], sum(row["bytes"] for row in new["files"]))
        for row in manifest["changes"]:
            self.assertEqual(H.ENGINE.sha(contents[row["path"]]), row["sha256"])

    def test_unknown_archive_or_frontend_is_refused(self):
        with self.assertRaises(ValueError):
            H.reviewed_frontend(b"unreviewed program")
        with patch.object(H, "SOURCE_ARCHIVE_SHA256", "0" * 64), self.assertRaises(ValueError):
            H.load_patch()
        with patch.object(H, "NEW_APP_SHA256", "0" * 64), self.assertRaises(ValueError):
            H.load_patch()
        with patch.object(H, "NEW_MANIFEST_SHA256", "0" * 64), self.assertRaises(ValueError):
            H.load_patch()
        with self.assertRaises(ValueError):
            H.ENGINE.installed_path(Path("synthetic"), ".pi/settings.json")

    @unittest.skipUnless(os.name == "nt", "Windows deployment guard")
    def test_running_application_is_refused_before_transaction(self):
        with patch.object(H.ENGINE, "validate_target"), patch.object(H.ENGINE.POLICY, "digest", return_value=H.NEW_APP_SHA256), patch.object(H.ENGINE, "reject_running_processes", side_effect=RuntimeError("still running")), patch.object(H.ENGINE, "apply_transaction") as apply:
            with self.assertRaisesRegex(RuntimeError, "still running"):
                H.run(apply=True)
            apply.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows private transaction")
    def test_two_file_apply_and_repeated_recovery_preserve_synthetic_data(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            result = H.ENGINE.apply_transaction(target, manifest, contents, before)
            self.assertEqual(result["program_files_verified"], 2)
            for row in manifest["changes"]:
                self.assertEqual(H.ENGINE.current_digest(target / row["path"]), row["sha256"])
            for _ in range(2):
                H.ENGINE.recover_transaction(target, manifest)
                for row in manifest["changes"]:
                    self.assertEqual(H.ENGINE.current_digest(target / row["path"]), row["previous_sha256"])

    @unittest.skipUnless(os.name == "nt", "Windows private transaction")
    def test_inventory_replace_failure_rolls_back_the_frontend(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            move = H.ENGINE.move_program
            def fail_inventory(source, destination, **options):
                if destination == target / H.ENGINE.MANIFEST_PATH and "stage" in source.parts:
                    raise OSError("synthetic inventory replacement failure")
                return move(source, destination, **options)
            with patch.object(H.ENGINE, "move_program", side_effect=fail_inventory):
                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    H.ENGINE.apply_transaction(target, manifest, contents, before)
            for relative, original in before.items():
                self.assertEqual((target / relative).read_bytes(), original)
            self.assertFalse((target / "runtime" / H.ENGINE.RECEIPT_NAME).exists())

    @unittest.skipUnless(os.name == "nt", "Windows recovery entry point")
    def test_run_recover_entrypoint_handles_each_interrupted_commit_state(self):
        for interrupted in ("frontend_only", "both_files", "receipt_pending_journal"):
            with self.subTest(state=interrupted), fixture() as (target, manifest, contents, before, _hashes):
                H.ENGINE.apply_transaction(target, manifest, contents, before)
                backup = target / "runtime" / H.ENGINE.BACKUP_NAME
                receipt = target / "runtime" / H.ENGINE.RECEIPT_NAME
                unrelated_receipt = target / "runtime/hotfix-synthetic-unrelated.json"
                H.ENGINE.write_new(unrelated_receipt, b"synthetic unrelated receipt must remain")
                journal = json.loads(H.ENGINE.read_program(backup / "transaction.json"))
                if interrupted != "receipt_pending_journal":
                    self.assertEqual(H.ENGINE.current_digest(receipt), journal["completion_receipt_sha256"])
                    receipt.unlink()  # Only this synthetic transaction's verified receipt.
                if interrupted == "frontend_only":
                    staged = backup / "synthetic-interruption-inventory.json"
                    H.ENGINE.write_new(staged, before[H.ENGINE.MANIFEST_PATH])
                    H.ENGINE.move_program(staged, target / H.ENGINE.MANIFEST_PATH)
                    journal.update(phase="applying", attempted=["ui/app.js"])
                elif interrupted == "both_files":
                    journal.update(phase="applying", attempted=list(H.PROGRAM_FILES))
                else:
                    journal["phase"] = "committing_receipt"
                H.ENGINE.save_journal(backup, journal)
                # Exercise the actual wrapper entry, including backup baseline
                # path, executable locks, deployment lock and engine recovery.
                with patch.object(H.ENGINE.POLICY, "profile_path", return_value=target.parent), patch.object(H.ENGINE, "TARGET_NAME", target.name), patch.object(H.ENGINE, "reject_running_processes"), patch.object(H, "load_patch", return_value=(manifest, contents)), patch.object(H, "OLD_MANIFEST_SHA256", H.ENGINE.sha(before[H.ENGINE.MANIFEST_PATH])):
                    for _ in range(2):
                        result = H.run(recover=True)
                        self.assertEqual(result["status"], "restored_backup")
                        for relative, original in before.items():
                            self.assertEqual((target / relative).read_bytes(), original)
                        self.assertFalse(receipt.exists())
                        self.assertEqual(unrelated_receipt.read_bytes(), b"synthetic unrelated receipt must remain")


if __name__ == "__main__":
    unittest.main()
