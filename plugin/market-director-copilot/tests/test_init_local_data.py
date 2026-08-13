from __future__ import annotations

import importlib.util
import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "init_local_data.py"
SPEC = importlib.util.spec_from_file_location("init_local_data", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT}")
init_local_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(init_local_data)


class InitializeLocalDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        for index, source_name in enumerate(init_local_data.TEMPLATES, start=1):
            source = self.project / source_name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"template-{index}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assert_all_targets_match_templates(self) -> None:
        for source_name, target_name in init_local_data.TEMPLATES.items():
            self.assertEqual(
                (self.project / source_name).read_bytes(),
                (self.project / target_name).read_bytes(),
            )

    def assert_no_temporary_files(self) -> None:
        self.assertEqual([], list(self.project.rglob(".*.tmp")))

    def test_partial_failure_preserves_published_files_and_retry_completes(self) -> None:
        original_copyfile = init_local_data.shutil.copyfile
        copy_count = 0

        def fail_third_copy(source: Path, target: Path) -> Path:
            nonlocal copy_count
            copy_count += 1
            if copy_count == 3:
                raise OSError("injected copy failure")
            return original_copyfile(source, target)

        with mock.patch.object(init_local_data.shutil, "copyfile", side_effect=fail_third_copy):
            with self.assertRaisesRegex(OSError, "injected copy failure"):
                init_local_data.initialize(self.project)

        mappings = list(init_local_data.TEMPLATES.items())
        for source_name, target_name in mappings[:2]:
            self.assertEqual(
                (self.project / source_name).read_bytes(),
                (self.project / target_name).read_bytes(),
            )
        self.assertFalse((self.project / mappings[2][1]).exists())
        self.assert_no_temporary_files()

        created, skipped = init_local_data.initialize(self.project)
        self.assertEqual(4, len(created))
        self.assertEqual(2, len(skipped))
        self.assert_all_targets_match_templates()
        self.assert_no_temporary_files()

    def test_concurrent_creator_wins_without_being_overwritten(self) -> None:
        original_link = init_local_data.os.link
        raced = False
        first_target = self.project / next(iter(init_local_data.TEMPLATES.values()))

        def create_target_before_link(source: Path, target: Path) -> None:
            nonlocal raced
            if not raced:
                raced = True
                Path(target).write_text("customer-data\n", encoding="utf-8")
            original_link(source, target)

        with mock.patch.object(init_local_data.os, "link", side_effect=create_target_before_link):
            created, skipped = init_local_data.initialize(self.project)

        self.assertEqual("customer-data\n", first_target.read_text(encoding="utf-8"))
        self.assertEqual(5, len(created))
        self.assertEqual([first_target], skipped)
        self.assert_no_temporary_files()

    def test_link_failure_preserves_the_original_reason(self) -> None:
        with mock.patch.object(
            init_local_data.os,
            "link",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Atomic no-overwrite creation failed.*No space left on device.*errno=28",
            ):
                init_local_data.initialize(self.project)

        self.assertFalse(any((self.project / target).exists() for target in init_local_data.TEMPLATES.values()))
        self.assert_no_temporary_files()

    def test_cleanup_failure_reports_that_published_target_is_preserved(self) -> None:
        original_unlink = Path.unlink

        def reject_temporary_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path.name.endswith(".tmp"):
                raise PermissionError("injected cleanup failure")
            original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", side_effect=reject_temporary_unlink, autospec=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "Initialized .* target is preserved",
            ):
                init_local_data.initialize(self.project)

        first_source, first_target = next(iter(init_local_data.TEMPLATES.items()))
        self.assertEqual(
            (self.project / first_source).read_bytes(),
            (self.project / first_target).read_bytes(),
        )
        self.assertEqual(1, len(list((self.project / first_target).parent.glob(".*.tmp"))))

    def test_two_processes_initialize_without_overwriting(self) -> None:
        command = [sys.executable, os.fspath(SCRIPT), "--project", os.fspath(self.project)]
        processes = [
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=30) for process in processes]

        for process, (_, stderr) in zip(processes, results):
            self.assertEqual(0, process.returncode, stderr)
        output = "\n".join(stdout for stdout, _ in results)
        self.assertEqual(6, output.count("CREATED "))
        self.assertEqual(6, output.count("SKIPPED "))
        self.assert_all_targets_match_templates()
        self.assert_no_temporary_files()


if __name__ == "__main__":
    unittest.main()
