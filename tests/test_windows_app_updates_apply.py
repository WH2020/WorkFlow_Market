from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("updates_apply_tests", ROOT / "scripts/apply-windows-app-updates-hotfix.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


@contextmanager
def fixture():
    profile = M.POLICY.profile_path()
    with tempfile.TemporaryDirectory(prefix="a4m-apply-test-", dir=profile) as directory:
        parent = Path(directory)
        assert parent.resolve().parent == profile.resolve() and parent.name.startswith("a4m-apply-test-")
        M.PRIVACY.ensure_private_directory(parent)  # Only this newly created synthetic root.
        target = parent / "application"
        M.POLICY.prepare(target, M.PRIVACY)
        launch_rows = []
        for relative in M.LAUNCH_FILES:
            content = ("synthetic-binary:" + relative).encode()
            M.write_new(target / relative, content)
            launch_rows.append({"path": relative, "sha256": M.sha(content), "bytes": len(content)})
        before, contents, rows = {}, {}, []
        for relative in M.PROGRAM_FILES:
            new = ("new-program:" + relative).encode()
            old = None if relative in M.NEW_FILES else ("old-program:" + relative).encode()
            if relative == M.MANIFEST_PATH:
                old = M.encoded({"version": "0.20.2", "files": launch_rows})
                new = M.encoded({"version": "0.20.2", "files": launch_rows, "synthetic_patch": True})
            if old is not None:
                M.write_new(target / relative, old)
                before[relative] = old
            contents[relative] = new
            rows.append({"path": relative, "bytes": len(new), "sha256": M.sha(new),
                         "previous_sha256": None if old is None else M.sha(old)})
        sentinels = {}
        for relative in (".pi/synthetic-config.json", "data/synthetic-business.json", "outputs/synthetic.txt", "library/company/synthetic.txt"):
            content = ("synthetic-preserve:" + relative).encode()
            M.write_new(target / relative, content)
            sentinels[relative] = content
        yield target, {"changes": rows}, contents, before, {r["path"]: r["sha256"] for r in launch_rows}
        for relative, content in sentinels.items():
            assert (target / relative).read_bytes() == content


@unittest.skipUnless(os.name == "nt", "Windows native file locks and private directory policy")
class WindowsAppUpdatesApplyTests(unittest.TestCase):
    def assert_original(self, target, manifest):
        for row in manifest["changes"]:
            self.assertEqual(M.current_digest(M.installed_path(target, row["path"])), row["previous_sha256"])

    def test_target_is_exact_and_payload_is_hash_pinned(self):
        with self.assertRaises(ValueError):
            M.validate_target(M.POLICY.profile_path())
        if M.ZIP_PATH.is_file():
            manifest, contents = M.load_patch()
            self.assertEqual(len(manifest["changes"]), 18)
            self.assertEqual(set(contents), set(M.PROGRAM_FILES))
        with patch.object(M, "ZIP_SHA256", "0" * 64):
            if M.ZIP_PATH.is_file():
                with self.assertRaisesRegex(ValueError, "ZIP hash"):
                    M.load_patch()

    def test_preflight_is_read_only_and_refuses_alien_files_and_low_disk(self):
        with fixture() as (target, manifest, contents, before, hashes):
            original, launches = M.preflight(target, manifest)
            self.assertEqual(original, before)
            self.assertEqual(launches, hashes)
            self.assertFalse((target / "runtime" / M.BACKUP_NAME).exists())
            with patch.object(M.shutil, "disk_usage", return_value=type("Disk", (), {"free": 0})()):
                with self.assertRaisesRegex(RuntimeError, "disk space"):
                    M.preflight(target, manifest)
            new_file = target / next(iter(M.NEW_FILES))
            M.write_new(new_file, b"pre-existing-synthetic-work")
            with self.assertRaisesRegex(ValueError, "already exists"):
                M.preflight(target, manifest)
            self.assertEqual(new_file.read_bytes(), b"pre-existing-synthetic-work")

    def test_native_launch_locks_refuse_each_busy_binary_and_a_second_deployer(self):
        with fixture() as (target, _manifest, _contents, _before, hashes), patch.object(M, "reject_running_processes"):
            for relative in M.LAUNCH_FILES:
                with self.subTest(relative=relative), (target / relative).open("rb"):
                    with self.assertRaisesRegex(RuntimeError, "executable is busy"):
                        with M.locked_program(target, hashes):
                            self.fail("A busy executable must not be accepted")
            with M.locked_program(target, hashes):
                with self.assertRaisesRegex(RuntimeError, "executable is busy"):
                    with M.locked_program(target, hashes):
                        self.fail("A second concurrent deployment must not enter")
            with M.deployment_lock(target):
                with self.assertRaises(RuntimeError):
                    with M.deployment_lock(target):
                        self.fail("A second deployment lock must not enter")
            self.assertFalse((target / "runtime" / M.LOCK_NAME).exists())

    def test_success_backs_up_programs_only_and_commits_inventory_last(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            real_move, program_moves, reads = M.move_program, [], []
            real_read = M.read_program
            def read(path, **options):
                reads.append(path.relative_to(target).as_posix())
                return real_read(path, **options)
            def move(source, destination, **options):
                if destination.relative_to(target).as_posix() in M.PROGRAM_FILES:
                    program_moves.append(destination.relative_to(target).as_posix())
                return real_move(source, destination, **options)
            with patch.object(M, "move_program", side_effect=move), patch.object(M, "read_program", side_effect=read):
                result = M.apply_transaction(target, manifest, contents, before)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(program_moves, list(M.PROGRAM_FILES))
            self.assertEqual(program_moves[-1], M.MANIFEST_PATH)
            self.assertTrue(all(not relative.startswith((".pi/", "data/", "outputs/", "library/")) for relative in reads))
            backup = target / "runtime" / M.BACKUP_NAME
            for relative, content in before.items():
                self.assertEqual((backup / "before" / relative).read_bytes(), content)
            self.assertEqual(json.loads((backup / "transaction.json").read_bytes())["phase"], "applied")
            for relative, content in contents.items():
                self.assertEqual((target / relative).read_bytes(), content)
            with self.assertRaisesRegex(RuntimeError, "existing backup"):
                M.preflight(target, manifest)

    def test_every_program_replace_failure_rolls_back_before_and_after_rename(self):
        real_move = M.move_program
        for relative in M.PROGRAM_FILES:
            for after_rename in (False, True):
                with self.subTest(file=relative, after=after_rename), fixture() as (target, manifest, contents, before, _hashes):
                    fired = False
                    def move(source, destination, **options):
                        nonlocal fired
                        if destination == target / relative and not fired:
                            fired = True
                            if after_rename:
                                real_move(source, destination, **options)
                            raise OSError("Synthetic interruption")
                        return real_move(source, destination, **options)
                    with patch.object(M, "move_program", side_effect=move):
                        with self.assertRaisesRegex(RuntimeError, "were rolled back"):
                            M.apply_transaction(target, manifest, contents, before)
                    self.assertTrue(fired)
                    self.assert_original(target, manifest)
                    journal = json.loads((target / "runtime" / M.BACKUP_NAME / "transaction.json").read_bytes())
                    self.assertEqual(journal["phase"], "rolled_back")

    def test_final_journal_failure_removes_its_receipt_and_restores_programs(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            real_save = M.save_journal
            def save(backup, journal):
                if journal["phase"] == "applied":
                    raise OSError("Synthetic final journal failure")
                return real_save(backup, journal)
            with patch.object(M, "save_journal", side_effect=save):
                with self.assertRaisesRegex(RuntimeError, "were rolled back"):
                    M.apply_transaction(target, manifest, contents, before)
            self.assert_original(target, manifest)
            self.assertFalse((target / "runtime" / M.RECEIPT_NAME).exists())

    def test_recovery_uses_all_fixed_paths_even_when_last_intent_is_missing(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            M.apply_transaction(target, manifest, contents, before)
            backup = target / "runtime" / M.BACKUP_NAME
            journal = json.loads((backup / "transaction.json").read_bytes())
            journal.update(phase="applying", attempted=[])
            M.save_journal(backup, journal)
            result = M.recover_transaction(target, manifest)
            self.assertEqual(result["status"], "restored_backup")
            self.assert_original(target, manifest)
            self.assertFalse((target / "runtime" / M.RECEIPT_NAME).exists())

    def test_unknown_current_file_or_tampered_backup_is_never_overwritten(self):
        for tamper_backup in (False, True):
            with self.subTest(backup=tamper_backup), fixture() as (target, manifest, contents, before, _hashes):
                M.apply_transaction(target, manifest, contents, before)
                relative = "ui/app.js"
                path = target / "runtime" / M.BACKUP_NAME / "before" / relative if tamper_backup else target / relative
                path.write_bytes(b"synthetic-user-edit")
                with self.assertRaisesRegex(RuntimeError, "recovery refused"):
                    M.recover_transaction(target, manifest)
                self.assertEqual(path.read_bytes(), b"synthetic-user-edit")
                self.assertEqual((target / M.MANIFEST_PATH).read_bytes(), contents[M.MANIFEST_PATH])

    def test_recovery_can_restart_with_leftover_restore_staging(self):
        with fixture() as (target, manifest, contents, before, _hashes):
            M.apply_transaction(target, manifest, contents, before)
            class SimulatedHardStop(BaseException):
                pass
            real_move, interrupted_staging = M.move_program, []
            def stop_after_staging(source, destination, **options):
                if destination == target / "ui/app.js":
                    interrupted_staging.append(source)
                    raise SimulatedHardStop("Recovery stops after staging, before the move")
                return real_move(source, destination, **options)
            # Bypass ordinary exception rollback after a real restore staging write.
            with patch.object(M, "move_program", side_effect=stop_after_staging):
                with self.assertRaises(SimulatedHardStop):
                    M.recover_transaction(target, manifest)
            self.assertEqual(len(interrupted_staging), 1)
            stale = interrupted_staging[0]
            self.assertEqual(stale.read_bytes(), before["ui/app.js"])
            self.assertEqual(M.recover_transaction(target, manifest)["status"], "restored_backup")
            self.assert_original(target, manifest)
            self.assertEqual(stale.read_bytes(), before["ui/app.js"])
            self.assertEqual(M.recover_transaction(target, manifest)["status"], "restored_backup")
            self.assert_original(target, manifest)

    def test_strict_root_acl_is_checked_under_locks_and_before_writes(self):
        with fixture() as (target, manifest, contents, before, hashes):
            with patch.object(M.PRIVACY, "_win_check_target", side_effect=RuntimeError("Synthetic root ACL drift")):
                with self.assertRaisesRegex(RuntimeError, "root ACL drift"):
                    with M.locked_program(target, hashes):
                        self.fail("Root must pass the strict target policy under locks")
            with patch.object(M.PRIVACY, "_windows_verify", side_effect=RuntimeError("Synthetic root ACL drift")):
                with self.assertRaisesRegex(RuntimeError, "root ACL drift"):
                    M.apply_transaction(target, manifest, contents, before)
            self.assertFalse((target / "runtime" / M.BACKUP_NAME).exists())
            self.assert_original(target, manifest)


if __name__ == "__main__":
    unittest.main()
