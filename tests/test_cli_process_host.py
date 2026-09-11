"""Synthetic boundary and lifecycle tests for the constrained CLI host."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time
import unittest
import uuid

from agent_platform import cli_process_host as host
from agent_platform import wechat_privacy as privacy


_HOST_PATH = Path(host.__file__).resolve()
_TEST_EXECUTABLE = (
    str((Path(sys.base_prefix) / "python.exe").resolve())
    if os.name == "nt"
    else str(Path(sys.executable).resolve())
)


class CliProcessHostTests(unittest.TestCase):
    def setUp(self) -> None:
        # Put the synthetic LOCALAPPDATA/HOME below a current-user location
        # whose native ancestor policy is known to the production helper.
        parent = Path.home()
        self.base = parent / f"Agent4MarketCliHostTests-{uuid.uuid4().hex}"
        privacy.ensure_private_directory(self.base)
        self.root = self.base / host._RUNTIME_DIRECTORY
        self.addCleanup(self._cleanup_private_base)

    def _environment(
        self, launch: list[str], schema: object, model_catalog: object | None = None
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALAPPDATA": str(self.base),
                "HOME": str(self.base),
                host._LAUNCH_ENV: json.dumps(launch, ensure_ascii=False),
                host._SCHEMA_ENV: json.dumps(schema, ensure_ascii=False),
            }
        )
        if model_catalog is not None:
            environment[host._MODEL_CATALOG_ENV] = json.dumps(
                model_catalog, ensure_ascii=False
            )
        else:
            environment.pop(host._MODEL_CATALOG_ENV, None)
        return environment

    def _invoke(
        self,
        launch: list[str],
        schema: object,
        *,
        model_catalog: object | None = None,
        prompt: bytes = b"",
        timeout: float = 20,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [_TEST_EXECUTABLE, "-I", "-B", str(_HOST_PATH)],
            input=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment(launch, schema, model_catalog),
            timeout=timeout,
            check=False,
        )

    def _cleanup_private_base(self) -> None:
        # Cleanup is intentionally non-recursive.  Only production-shaped,
        # verified request directories are eligible for deletion.
        try:
            if os.path.lexists(self.root):
                privacy.verify_private_directory(self.root)
                for entry in list(self.root.iterdir()):
                    if entry.name.startswith(host._REQUEST_PREFIX):
                        host._delete_owned_request(entry, privacy)
                if not any(self.root.iterdir()):
                    os.rmdir(self.root)
            if os.path.lexists(self.base):
                privacy.verify_private_directory(self.base)
                if not any(self.base.iterdir()):
                    os.rmdir(self.base)
        except Exception:
            # A malformed residue must never make teardown broaden deletion.
            pass

    def _make_request(self, suffix: str = "0" * 32) -> Path:
        if not os.path.lexists(self.root):
            privacy.ensure_private_directory(self.root)
        request = self.root / f"{host._REQUEST_PREFIX}{suffix}"
        privacy.ensure_private_directory(request)
        return request

    def test_stdin_stdout_schema_cwd_and_environment_are_direct_and_private(self) -> None:
        child_code = r"""
import json, os, pathlib, sys
schema_path = pathlib.Path(sys.argv[1])
prompt = sys.stdin.buffer.read()
sys.stderr.write("CHILD_SECRET_MUST_BE_DISCARDED")
sys.stdout.buffer.write(json.dumps({
    "prompt_hex": prompt.hex(),
    "schema": json.loads(schema_path.read_text(encoding="utf-8")),
    "schema_parent": str(schema_path.parent),
    "cwd": os.getcwd(),
    "launch_env": os.environ.get("A4M_CLI_LAUNCH"),
    "schema_env": os.environ.get("A4M_CLI_SCHEMA"),
}, ensure_ascii=False).encode("utf-8"))
"""
        prompt = "直接输入\n二进制:\u0000".encode("utf-8")
        schema = {
            "type": "object",
            "properties": {"摘要": {"type": "string"}},
            "required": ["摘要"],
        }
        result = self._invoke(
            [_TEST_EXECUTABLE, "-I", "-c", child_code, host._SCHEMA_PLACEHOLDER],
            schema,
            prompt=prompt,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(prompt.hex(), payload["prompt_hex"])
        self.assertEqual(schema, payload["schema"])
        self.assertEqual(payload["cwd"], payload["schema_parent"])
        self.assertTrue(Path(payload["cwd"]).name.startswith(host._REQUEST_PREFIX))
        self.assertIsNone(payload["launch_env"])
        self.assertIsNone(payload["schema_env"])
        self.assertTrue(self.root.is_dir())
        self.assertEqual([], list(self.root.iterdir()))

    def test_schema_placeholder_is_optional_but_schema_remains_private(self) -> None:
        child_code = r"""
import json, pathlib, sys
schema = json.loads(pathlib.Path("schema.json").read_text(encoding="utf-8"))
sys.stdout.buffer.write(json.dumps(schema).encode("utf-8"))
"""
        schema = {"type": "array", "items": {"type": "string"}}
        result = self._invoke(
            [_TEST_EXECUTABLE, "-I", "-c", child_code], schema
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(schema, json.loads(result.stdout))
        self.assertEqual(b"", result.stderr)
        self.assertEqual([], list(self.root.iterdir()))

    def test_model_catalog_uses_only_the_exact_private_path_argument(self) -> None:
        child_code = r"""
import json, os, pathlib, sys
config = sys.argv[1]
assert config.startswith("model_catalog_json=")
catalog_path = pathlib.Path(json.loads(config.split("=", 1)[1]))
payload = {
    "catalog": json.loads(catalog_path.read_text(encoding="utf-8")),
    "cwd": os.getcwd(),
    "parent": str(catalog_path.parent),
    "catalog_env": os.environ.get("A4M_CLI_MODEL_CATALOG"),
}
sys.stdout.buffer.write(json.dumps(payload).encode("utf-8"))
"""
        catalog = {
            "models": [
                {
                    "slug": "gpt-test",
                    "display_name": "gpt-test",
                    "context_window": 32000,
                }
            ]
        }
        result = self._invoke(
            [_TEST_EXECUTABLE, "-I", "-c", child_code, host._MODEL_CATALOG_ARGUMENT],
            {"type": "object"},
            model_catalog=catalog,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(catalog, payload["catalog"])
        self.assertEqual(payload["cwd"], payload["parent"])
        self.assertIsNone(payload["catalog_env"])
        self.assertEqual([], list(self.root.iterdir()))

    def test_child_exit_status_is_preserved_and_child_stderr_is_discarded(self) -> None:
        child_code = (
            "import sys; sys.stderr.write('do-not-leak'); "
            "sys.stdout.buffer.write(b'ok'); raise SystemExit(23)"
        )
        result = self._invoke(
            [_TEST_EXECUTABLE, "-I", "-c", child_code, host._SCHEMA_PLACEHOLDER],
            {"type": "object"},
        )
        self.assertEqual(23, result.returncode)
        self.assertEqual(b"ok", result.stdout)
        self.assertEqual(b"", result.stderr)
        self.assertEqual([], list(self.root.iterdir()))

    def test_invalid_configuration_fails_with_only_a_fixed_code(self) -> None:
        valid = [
            _TEST_EXECUTABLE,
            "-I",
            "-c",
            "raise AssertionError('must not run')",
            host._SCHEMA_PLACEHOLDER,
        ]
        cases: list[tuple[list[str], str]] = [
            (["relative-secret.exe", host._SCHEMA_PLACEHOLDER], '{"type":"object"}'),
            (valid + [host._SCHEMA_PLACEHOLDER], '{"type":"object"}'),
            ([_TEST_EXECUTABLE, "prefix-" + host._SCHEMA_PLACEHOLDER], '{"type":"object"}'),
            (valid, '{"type":"object","type":"array"}'),
            (valid, "NaN"),
        ]
        for launch, schema_text in cases:
            with self.subTest(launch=launch, schema=schema_text):
                environment = self._environment(launch, {"type": "object"})
                environment[host._SCHEMA_ENV] = schema_text
                result = subprocess.run(
                    [_TEST_EXECUTABLE, "-I", "-B", str(_HOST_PATH)],
                    input=b"prompt-secret",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(host._ERROR_EXIT_STATUS, result.returncode)
                self.assertEqual(
                    (host._ERROR_CONFIG + "\n").encode("ascii"), result.stderr
                )
                self.assertEqual(b"", result.stdout)
                self.assertNotIn(b"secret", result.stderr)

    def test_model_catalog_pairing_and_shape_are_strict(self) -> None:
        base_launch = [_TEST_EXECUTABLE, "-I", "-c", "raise SystemExit(0)"]
        valid_catalog = {
            "models": [{"slug": "gpt-test", "display_name": "gpt-test"}]
        }
        cases: list[tuple[list[str], object | None]] = [
            (base_launch, valid_catalog),
            (base_launch + [host._MODEL_CATALOG_ARGUMENT], None),
            (base_launch + [host._MODEL_CATALOG_ARGUMENT], {"models": []}),
            (
                base_launch + [host._MODEL_CATALOG_ARGUMENT],
                {"models": [{"display_name": "missing-slug"}]},
            ),
            (
                base_launch
                + ["prefix-" + host._MODEL_CATALOG_PLACEHOLDER],
                valid_catalog,
            ),
        ]
        for launch, catalog in cases:
            with self.subTest(launch=launch, catalog=catalog):
                result = self._invoke(
                    launch, {"type": "object"}, model_catalog=catalog
                )
                self.assertEqual(host._ERROR_EXIT_STATUS, result.returncode)
                self.assertEqual(
                    (host._ERROR_CONFIG + "\n").encode("ascii"), result.stderr
                )
                self.assertEqual(b"", result.stdout)

    def test_existing_unsafe_runtime_is_rejected_without_repair(self) -> None:
        self.root.mkdir()
        if os.name != "nt":
            self.root.chmod(0o755)
        with self.assertRaises(Exception):
            privacy.verify_private_directory(self.root)

        result = self._invoke(
            [
                _TEST_EXECUTABLE,
                "-I",
                "-c",
                "raise AssertionError('must not run')",
                host._SCHEMA_PLACEHOLDER,
            ],
            {"type": "object"},
        )
        self.assertEqual(host._ERROR_EXIT_STATUS, result.returncode)
        self.assertEqual((host._ERROR_PRIVATE + "\n").encode("ascii"), result.stderr)
        with self.assertRaises(Exception):
            privacy.verify_private_directory(self.root)

        # Repair only the synthetic test-owned directory so strict teardown
        # can remove it after proving production did not repair it.
        privacy.ensure_private_directory(self.root)

    def test_old_owned_request_is_removed_but_active_request_is_not(self) -> None:
        old = time.time() - host._STALE_AFTER_SECONDS - 60

        stale = self._make_request("1" * 32)
        stale_marker = host._open_request_marker(stale)
        os.close(stale_marker)
        host._write_schema(stale, {"type": "object"}, privacy)
        os.utime(stale, (old, old))
        host._cleanup_stale(self.root, privacy)
        self.assertFalse(os.path.lexists(stale))

        active = self._make_request("2" * 32)
        active_marker = host._open_request_marker(active)
        active_holder = [active_marker]
        self.addCleanup(self._close_descriptor_holder, active_holder)
        host._write_schema(active, {"type": "object"}, privacy)
        os.utime(active, (old, old))
        host._cleanup_stale(self.root, privacy)
        self.assertTrue(active.is_dir())
        os.close(active_marker)
        active_holder[0] = -1
        self.assertTrue(host._delete_owned_request(active, privacy))

    def test_cleanup_refuses_unknown_entries(self) -> None:
        request = self._make_request("3" * 32)
        marker = host._open_request_marker(request)
        os.close(marker)
        host._write_schema(request, {"type": "object"}, privacy)
        unknown = request / "not-owned.txt"
        unknown.write_bytes(b"do not delete")
        if os.name != "nt":
            unknown.chmod(0o600)

        self.assertFalse(host._delete_owned_request(request, privacy))
        self.assertEqual(b"do not delete", unknown.read_bytes())

        info = unknown.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertFalse(host._is_reparse(info))
        unknown.unlink()
        self.assertTrue(host._delete_owned_request(request, privacy))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle")
    def test_suspended_child_is_in_job_at_creation(self) -> None:
        import _winapi
        import msvcrt

        marker = self.base / "must-not-run-before-resume.txt"
        child_code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('ran', encoding='utf-8'); "
            "time.sleep(120)"
        )
        kernel, job_handle = host._create_kill_job()
        null_descriptor = os.open(
            os.devnull, os.O_RDWR | getattr(os, "O_BINARY", 0)
        )
        duplicated: list[int] = []
        process_information = None
        try:
            current_process = _winapi.GetCurrentProcess()
            null_handle = msvcrt.get_osfhandle(null_descriptor)
            for _index in range(3):
                duplicated.append(
                    _winapi.DuplicateHandle(
                        current_process,
                        null_handle,
                        current_process,
                        0,
                        True,
                        _winapi.DUPLICATE_SAME_ACCESS,
                    )
                )

            process_information = host._create_suspended_process_in_job(
                kernel,
                [_TEST_EXECUTABLE, "-I", "-c", child_code, str(marker)],
                self.base,
                os.environ.copy(),
                duplicated,
                job_handle,
            )
            for duplicate in duplicated:
                _winapi.CloseHandle(duplicate)
            duplicated.clear()

            in_job = wintypes.BOOL()
            self.assertTrue(
                kernel.IsProcessInJob(
                    process_information.hProcess,
                    job_handle,
                    ctypes.byref(in_job),
                )
            )
            self.assertTrue(in_job.value, "child was returned outside the Job")
            self.assertEqual(
                host._WAIT_TIMEOUT,
                kernel.WaitForSingleObject(process_information.hProcess, 0),
            )
            self.assertFalse(marker.exists(), "suspended child ran before ResumeThread")

            # This models the host being terminated in the formerly unsafe
            # post-CreateProcess/pre-Assign window.  No explicit process kill
            # or ResumeThread is needed: closing the pre-attached Job owns it.
            self.assertTrue(kernel.CloseHandle(job_handle))
            job_handle = 0
            self.assertEqual(
                host._WAIT_OBJECT_0,
                kernel.WaitForSingleObject(process_information.hProcess, 5000),
            )
            self.assertFalse(marker.exists())
        finally:
            for duplicate in duplicated:
                try:
                    _winapi.CloseHandle(duplicate)
                except Exception:
                    pass
            os.close(null_descriptor)
            if job_handle:
                try:
                    kernel.CloseHandle(job_handle)
                except Exception:
                    pass
            if process_information is not None:
                for handle in (
                    process_information.hThread,
                    process_information.hProcess,
                ):
                    if handle:
                        try:
                            kernel.CloseHandle(handle)
                        except Exception:
                            pass

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle")
    def test_terminating_host_kills_cli_descendants(self) -> None:
        child_code = r"""
import subprocess, sys, time
grandchild = subprocess.Popen([
    sys.executable, "-I", "-c", "import time; time.sleep(120)"
])
print(grandchild.pid, flush=True)
time.sleep(120)
"""
        process = subprocess.Popen(
            [_TEST_EXECUTABLE, "-I", "-B", str(_HOST_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment(
                [
                    _TEST_EXECUTABLE,
                    "-I",
                    "-c",
                    child_code,
                    host._SCHEMA_PLACEHOLDER,
                ],
                {"type": "object"},
            ),
        )
        assert process.stdout is not None
        lines: queue.Queue[bytes] = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        reader.join(10)
        try:
            self.assertFalse(reader.is_alive(), "CLI did not publish its descendant PID")
            line = lines.get_nowait()
            self.assertRegex(line, rb"^[0-9]+\r?\n$")
            grandchild_pid = int(line)
            self.assertTrue(self._windows_process_is_running(grandchild_pid))

            process.kill()
            process.wait(timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and self._windows_process_is_running(
                grandchild_pid
            ):
                time.sleep(0.05)
            self.assertFalse(
                self._windows_process_is_running(grandchild_pid),
                "kill-on-close Job did not terminate the CLI descendant",
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    @staticmethod
    def _close_descriptor_holder(holder: list[int]) -> None:
        descriptor = holder[0]
        if descriptor < 0:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass
        holder[0] = -1

    @staticmethod
    def _windows_process_is_running(process_id: int) -> bool:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, process_id)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
