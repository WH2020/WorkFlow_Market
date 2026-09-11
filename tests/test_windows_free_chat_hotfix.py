from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARCHIVES = (
    ROOT / "outputs/releases/0.20.2-free-chat-hotfix-20260910-r2.zip",
    ROOT / "outputs/releases/0.20.2-app-updates-hotfix-20260910.zip",
)
RUN_LEGACY_RELEASE_TESTS = (
    os.environ.get("AGENT4MARKET_RUN_LEGACY_RELEASE_TESTS") == "1"
    and all(path.is_file() for path in REQUIRED_ARCHIVES)
)
if RUN_LEGACY_RELEASE_TESTS:
    SPEC = importlib.util.spec_from_file_location("free_chat_hotfix_tests", ROOT / "scripts/apply-windows-free-chat-hotfix.py")
    H = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(H)
    SPEC = importlib.util.spec_from_file_location("free_chat_hotfix_fixtures", ROOT / "tests/test_windows_app_updates_apply.py")
    FIXTURES = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(FIXTURES)
else:
    H = FIXTURES = None


@unittest.skipUnless(
    RUN_LEGACY_RELEASE_TESTS,
    "legacy release validation is explicit opt-in and requires its ignored archives",
)
class FreeChatHotfixTests(unittest.TestCase):
    def test_frozen_bundle_changes_only_seven_program_files_and_inventory(self):
        manifest, contents = H.load_patch()
        self.assertEqual(tuple(row["path"] for row in manifest["changes"]), H.PROGRAM_FILES)
        self.assertEqual(len(contents), 8)
        self.assertEqual(set(H.PROGRAM_FILES[:4]), H.NEW_FILES, "add dependencies before changing their consumers")
        self.assertEqual(H.PROGRAM_FILES[-1], H.ENGINE.MANIFEST_PATH)
        self.assertEqual(sum(row["previous_sha256"] is None for row in manifest["changes"]), 4)
        _base, baseline = H.BASE.load_patch()
        old = json.loads(baseline[H.ENGINE.MANIFEST_PATH])
        new = json.loads(contents[H.ENGINE.MANIFEST_PATH])
        originals = {row["path"]: row for row in old["files"]}
        for row in new["files"]:
            if row["path"] not in H.SOURCE_HASHES:
                self.assertEqual(row, originals[row["path"]])
        self.assertEqual(new["total_bytes"], sum(row["bytes"] for row in new["files"]))
        self.assertEqual(new["version"], "0.20.2")
        for path, digest in H.SOURCE_HASHES.items():
            self.assertEqual(H.ENGINE.sha((ROOT / path).read_bytes()), digest)

    def test_modified_archive_source_or_extra_program_path_is_refused(self):
        with patch.object(H, "ARCHIVE_SHA256", "0" * 64), self.assertRaisesRegex(ValueError, "archive hash"):
            H.load_patch()
        _manifest, contents = H.load_patch()
        with self.assertRaisesRegex(ValueError, "Unexpected program"):
            H.plan({**contents, "data/customer.json": b"not allowed"})
        program = {path: contents[path] for path in H.SOURCE_HASHES}
        program["ui/app.js"] += b"unreviewed"
        with self.assertRaisesRegex(ValueError, "verified source"):
            H.plan(program)

    @unittest.skipUnless(os.name == "nt", "Native Windows deployment")
    def test_running_app_is_refused_without_entering_transaction(self):
        with patch.object(H.ENGINE, "validate_target"), patch.object(H.ENGINE, "reject_running_processes", side_effect=RuntimeError("still running")), patch.object(H.ENGINE, "apply_transaction") as apply:
            with self.assertRaisesRegex(RuntimeError, "still running"):
                H.run(apply=True)
            apply.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Native Windows deployment")
    def test_apply_and_repeated_recovery_preserve_synthetic_user_data(self):
        with patch.object(FIXTURES, "M", H.ENGINE), FIXTURES.fixture() as (target, manifest, contents, before, _hashes):
            for relative in ("agent_platform", "pi", "pi/extensions"):
                H.ENGINE.POLICY.prepare(target / relative, H.ENGINE.PRIVACY)
            result = H.ENGINE.apply_transaction(target, manifest, contents, before)
            self.assertEqual(result["program_files_verified"], 8)
            with patch.object(H.ENGINE.POLICY, "profile_path", return_value=target.parent), patch.object(H.ENGINE, "TARGET_NAME", target.name), patch.object(H.ENGINE, "validate_target"), patch.object(H.ENGINE, "reject_running_processes"), patch.object(H, "load_patch", return_value=(manifest, contents)), patch.object(H.BASE, "NEW_MANIFEST_SHA256", H.ENGINE.sha(before[H.ENGINE.MANIFEST_PATH])):
                for _ in range(2):
                    H.run(recover=True)
                    for row in manifest["changes"]:
                        self.assertEqual(H.ENGINE.current_digest(target / row["path"]), row["previous_sha256"])
