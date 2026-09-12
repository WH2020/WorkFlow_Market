"""Focused update safety tests. Only fresh synthetic directories/processes."""
from __future__ import annotations

import copy
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from agent_platform import windows_update_engine as engine
from agent_platform import windows_updates as updates
from agent_platform import windows_update_signatures as signatures


def row(name, data):
    return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def load_worker():
    spec = importlib.util.spec_from_file_location("synthetic_update_worker", Path(engine.__file__).with_name("windows_update_worker.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


class TransactionTests(unittest.TestCase):
    def setUp(self):
        base = engine.load_policy("installer").profile_path() if os.name == "nt" else None
        self.temp = tempfile.TemporaryDirectory(prefix="a4m-update-synthetic-", dir=base)
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name)
        engine.load_policy("privacy").ensure_private_directory(parent)
        self.root = engine.private_directory(parent / "old", create=True)
        self.stage = engine.private_directory(parent / "stage", create=True)
        self.job = engine.create_job(self.root, "a" * 32)
        shared = {engine.PROTOCOL_FILE: b'{"data_compatibility":1}',
                  "runtime/private-runtime.marker": b"Agent4Market private runtime v1", "ui/stable.js": b"same"}
        self.old_files = {**shared, "package.json": b'{"version":"1.0.0"}', "Agent4Market.exe": b"old exe", "ui/removed.js": b"old"}
        self.new_files = {**shared, "package.json": b'{"version":"1.0.1"}', "Agent4Market.exe": b"new exe", "ui/added.js": b"new"}
        self.old = self.make_release(self.root, "1.0.0", self.old_files)
        self.new = self.make_release(self.stage, "1.0.1", self.new_files)
        engine.write_json(self.job / "new-manifest.json", self.new)
        self.private = {".pi/config.json": b"synthetic-secret", "data/sales/customers.csv": b"synthetic-company",
                        "inputs/source.txt": b"synthetic-input", "outputs/report.txt": b"synthetic-output",
                        "library/templates/company/template.docx": b"synthetic-template", "ui/untracked.js": b"user-extra"}
        for name, data in self.private.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def make_release(self, root, version, files):
        for name, data in files.items():
            engine.mkdirs(root, str(Path(name).parent).replace("\\", "/"))
            (root / name).write_bytes(data)
        manifest = {"version": version, "files": [row(name, data) for name, data in files.items()]}
        engine.write_json(root / "runtime/install-manifest.json", manifest)
        return manifest

    def assert_private_unchanged(self):
        for name, value in self.private.items():
            self.assertEqual((self.root / name).read_bytes(), value, name)

    def test_success_and_idempotent_rollback_preserve_config_data_templates_and_unknown_files(self):
        engine.backup(self.root, self.stage, self.job, self.old, self.new)
        engine.apply(self.root, self.stage, self.job)
        engine.verify_programs(self.root, engine.manifest(self.new))
        self.assertFalse((self.root / "ui/removed.js").exists())
        self.assert_private_unchanged()
        engine.rollback(self.root, self.job)
        engine.rollback(self.root, self.job)
        engine.verify_programs(self.root, engine.manifest(self.old))
        self.assertFalse((self.root / "ui/added.js").exists())
        self.assert_private_unchanged()

    @unittest.skipUnless(os.name == "nt", "Windows MAX_PATH regression")
    def test_copy_temporary_name_does_not_extend_long_valid_target(self):
        parent = self.job
        while len(str(parent)) < 165:
            parent = engine.private_directory(parent / "synthetic-long-directory", create=True)
        name = "x" * (245 - len(str(parent)) - 1)
        target = parent / name
        source = self.root / "ui/stable.js"
        expected = row(name, source.read_bytes())
        self.assertGreater(len(str(target)) + len(".copying-") + 16, 260)
        engine.copy_new(source, target, expected)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_interruption_after_first_replacement_recovers_without_guessing(self):
        engine.backup(self.root, self.stage, self.job, self.old, self.new)
        replace = engine.durable_replace
        count = 0
        def interrupted(source, target):
            nonlocal count
            if Path(target).is_relative_to(self.root) and not Path(target).is_relative_to(self.job):
                count += 1
                if count == 2:
                    raise OSError("synthetic power loss")
            return replace(source, target)
        with patch.object(engine, "durable_replace", side_effect=interrupted), self.assertRaises(OSError):
            engine.apply(self.root, self.stage, self.job)
        engine.rollback(self.root, self.job)
        engine.verify_programs(self.root, engine.manifest(self.old))
        self.assert_private_unchanged()

    def test_resurrected_removed_file_cannot_be_reported_as_complete(self):
        engine.backup(self.root, self.stage, self.job, self.old, self.new)
        engine.apply(self.root, self.stage, self.job)
        (self.root / "ui/removed.js").write_bytes(b"old")
        with self.assertRaisesRegex(engine.UpdateFailure, "PROGRAM_MODIFIED"):
            engine.verify_outcome(self.root, self.job, "new")
        engine.rollback(self.root, self.job)
        engine.verify_outcome(self.root, self.job, "old")

    def test_oversized_journal_is_rejected_before_backup_or_program_changes(self):
        with patch.object(engine, "MAX_MANIFEST", 1000), self.assertRaisesRegex(engine.UpdateFailure, "PLAN_TOO_LARGE"):
            engine.backup(self.root, self.stage, self.job, self.old, self.new)
        self.assertFalse((self.job / "backup").exists())
        engine.verify_programs(self.root, engine.manifest(self.old))

    def test_changed_program_or_backup_refuses_before_any_restoration(self):
        engine.backup(self.root, self.stage, self.job, self.old, self.new)
        engine.apply(self.root, self.stage, self.job)
        (self.job / "backup/Agent4Market.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(engine.UpdateFailure, "BACKUP_CHANGED"):
            engine.rollback(self.root, self.job)
        engine.verify_programs(self.root, engine.manifest(self.new))
        self.assert_private_unchanged()

    def test_untracked_destination_and_hardlink_are_not_overwritten(self):
        (self.root / "ui/added.js").write_bytes(b"custom")
        with self.assertRaisesRegex(engine.UpdateFailure, "PROGRAM_MODIFIED"):
            engine.backup(self.root, self.stage, self.job, self.old, self.new)
        os.link(self.root / "ui/stable.js", self.root / "ui/linked.js")
        with self.assertRaisesRegex(engine.UpdateFailure, "NON_REGULAR_FILE"):
            engine.verify_programs(self.root, engine.manifest(self.old))
        self.assert_private_unchanged()

    def test_manifest_rejects_data_paths_ambiguous_paths_collisions_downgrades_and_schema_changes(self):
        for name in ["data/customers.csv", ".pi/auth.json", "outputs/file", "library/templates/company/x", "ui/../data/x",
                     "ui/CON.txt", "ui/x:stream", "ui/what?", "ui\\x", "ui/x.", "ui//x", "ui/stable.js/child"]:
            value = copy.deepcopy(self.new)
            value["files"].append(row(name, b"bad"))
            with self.subTest(name=name), self.assertRaises(engine.UpdateFailure):
                engine.manifest(value)
        value = copy.deepcopy(self.new)
        value["files"].append(row("UI/STABLE.JS", b"bad"))
        with self.assertRaisesRegex(engine.UpdateFailure, "DUPLICATE_PATH"):
            engine.manifest(value)
        with self.assertRaisesRegex(engine.UpdateFailure, "NOT_AN_UPGRADE"):
            engine.make_plan(self.new, self.old)
        value = copy.deepcopy(self.new)
        value["files"][0]["sha256"] = "b" * 64
        with self.assertRaisesRegex(engine.UpdateFailure, "DATA_FORMAT_CHANGED"):
            engine.make_plan(self.old, value)

    def test_pending_startup_never_initializes_data_before_own_commit(self):
        callback = Mock()
        checker = Mock()
        manager = updates.WindowsUpdateManager(self.root, checker, pending=self.job.name, on_committed=callback)
        engine.write_atomic(self.job.parent / "active", b"synthetic-active")
        with self.assertRaisesRegex(engine.UpdateFailure, "UPDATING"), manager.operation():
            pass
        callback.assert_not_called()
        engine.write_json(self.job.parent / "last-result.json", {"job_id": self.job.name, "status": "complete"})
        (self.job.parent / "active").unlink()
        with manager.operation():
            self.assertFalse(manager.frozen)
        with manager.operation():
            pass
        callback.assert_called_once_with()

    def test_committed_callback_failure_releases_update_gate_with_visible_error(self):
        for mode in ("exception", "memory-fallback"):
            with self.subTest(mode=mode):
                callback = Mock(side_effect=RuntimeError("synthetic storage failure")) if mode == "exception" else Mock(return_value=False)
                manager = updates.WindowsUpdateManager(self.root, Mock(), pending=self.job.name, on_committed=callback)
                engine.write_json(self.job.parent / "last-result.json", {"job_id": self.job.name, "status": "complete"})
                with manager.operation():
                    self.assertFalse(manager.frozen)
                    self.assertIsNone(manager.pending)
                    self.assertEqual(manager.state["error"]["code"], "PERSISTENCE_INITIALIZATION_FAILED")
                with manager.operation():
                    pass
                callback.assert_called_once_with()

    @unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("Crypto"), "Windows key protection dependency")
    def test_offline_private_key_is_encrypted_exclusive_and_signer_accepts_it(self):
        if subprocess.run([sys.executable, "-I", "-c", "import Crypto"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=15).returncode:
            self.skipTest("Use the packaged interpreter: isolated signer cannot use user-site Crypto")
        source = Path(engine.__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("synthetic_release_signing_key", source / "scripts/release_signing_key.py")
        keys = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(keys)
        directory = Path(self.temp.name) / "synthetic-release-key"
        result = keys.create_key(directory)
        private_path = Path(result["private_key_path"])
        self.assertTrue(private_path.read_bytes().startswith(keys.MAGIC))
        self.assertNotIn(b"PRIVATE KEY", private_path.read_bytes())
        with self.assertRaises(engine.UpdateFailure):
            keys.create_key(directory)
        self.assertEqual(keys.load_key(private_path).public_key().export_key(format="raw"), base64.b64decode(result["public_key"]))
        with self.assertRaises(engine.UpdateFailure):
            keys.dpapi(b"corrupt-synthetic-envelope", decrypt=True)
        engine.write_json(self.root / signatures.TRUST_FILE, engine.read_json(directory / "public-key.json"))
        signed_path = self.job / "publisher-signature.json"
        command = [sys.executable, "-I", "-B", str(source / "scripts/sign-windows-update.py"), "--private-key", str(private_path),
                   "--trust-root", str(self.root), "--installer", str(self.stage / "Agent4Market.exe"),
                   "--manifest", str(self.job / "new-manifest.json"), "--version", "1.0.1", "--output", str(signed_path)]
        completed = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout)["status"], "signed")
        assets = {name: {"bytes": path.stat().st_size, "sha256": engine.digest(path)} for name, path in
                  (("installer", self.stage / "Agent4Market.exe"), ("manifest", self.job / "new-manifest.json"))}
        signatures.verify(self.root, signed_path, "1.0.1", assets)
        self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=20).returncode, 0)

    @unittest.skipUnless(importlib.util.find_spec("Crypto"), "Packaged Windows signing dependency")
    def test_signature_binds_publisher_version_platform_and_both_asset_digests(self):
        from Crypto.PublicKey import ECC
        from Crypto.Signature import eddsa
        key = ECC.generate(curve="Ed25519")  # Synthetic in-memory key only.
        engine.write_json(self.root / signatures.TRUST_FILE, {"format": 1, "algorithm": "ed25519",
            "public_key": base64.b64encode(key.public_key().export_key(format="raw")).decode()})
        assets = {"installer": {"bytes": 100, "sha256": "a" * 64}, "manifest": {"bytes": 200, "sha256": "b" * 64}}
        signed = signatures.signed_record("1.0.1", assets)
        document = {"format": 1, "signed": signed, "signature": base64.b64encode(eddsa.new(key, "rfc8032").sign(signatures.canonical(signed))).decode()}
        path = self.job / "signature.json"
        engine.write_json(path, document)
        signatures.verify(self.root, path, "1.0.1", assets)
        alien = ECC.generate(curve="Ed25519")
        forged = {**document, "signature": base64.b64encode(eddsa.new(alien, "rfc8032").sign(signatures.canonical(signed))).decode()}
        engine.write_json(path, forged)
        with self.assertRaisesRegex(engine.UpdateFailure, "INVALID_SIGNATURE"):
            signatures.verify(self.root, path, "1.0.1", assets)
        for field, value in (("version", "1.0.2"), ("platform", "foreign"), ("installer", {"bytes": 100, "sha256": "c" * 64})):
            bad = copy.deepcopy(document)
            bad["signed"][field] = value
            engine.write_json(path, bad)
            with self.subTest(field=field), self.assertRaisesRegex(engine.UpdateFailure, "INVALID_SIGNATURE"):
                signatures.verify(self.root, path, "1.0.1", assets)
        engine.write_json(self.root / signatures.TRUST_FILE, {"format": 1, "algorithm": "ed25519", "public_key": None})
        with self.assertRaisesRegex(engine.UpdateFailure, "SIGNING_KEY_REQUIRED"):
            signatures.public_key(self.root)

    def worker_trial(self, succeed):
        worker = load_worker()
        info = {"job_id": self.job.name, "nonce": "d" * 64, "from_version": "1.0.0", "to_version": "1.0.1",
                "origin_version": "1.0.0", "old_manifest_sha256": engine.digest(self.root / "runtime/install-manifest.json")}
        engine.write_atomic(self.job.parent / "active", (self.job.name + "\n1.0.1\n").encode())
        engine.write_json(self.job / "stopped.json", {"nonce": info["nonce"]})
        trial = Mock()
        def wait_trial(*_args):
            self.assertTrue((self.job.parent / "active").exists())
            self.assertEqual(engine.read_json(self.job / "journal.json")["phase"], "validating")
            engine.verify_outcome(self.root, self.job, "new")
            if not succeed:
                raise worker.engine.UpdateFailure("STARTUP_TRIAL_FAILED")
        with patch.object(worker, "ProcessTree"), patch.object(worker, "port_free", return_value=True), \
             patch.object(worker.subprocess, "Popen", return_value=Mock(wait=lambda **_kwargs: 0)), \
             patch.object(worker, "start_application", return_value=trial) as start, \
             patch.object(worker, "wait_trial", side_effect=wait_trial), patch.object(worker, "stop_trial") as stop, \
             patch.object(worker, "update_display_version"):
            worker.run(self.root, self.job, self.stage, info, 100, 0, False)
        self.assertEqual(start.call_count, 1 if succeed else 2)
        self.assertEqual(stop.call_count, 0 if succeed else 1)
        self.assertFalse((self.job.parent / "active").exists())
        result = engine.read_json(self.job.parent / "last-result.json")
        self.assertEqual(result["status"], "complete" if succeed else "rolled_back")
        engine.verify_outcome(self.root, self.job, "new" if succeed else "old")
        self.assert_private_unchanged()

    def test_worker_commits_only_after_trial_and_does_not_launch_duplicate_desktop(self):
        self.worker_trial(True)

    def test_worker_trial_failure_stops_own_trial_then_restores_old_programs(self):
        self.worker_trial(False)


class DownloadTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __init__(self, data, url, **headers):
            super().__init__(data)
            self.status, self.url, self.headers = 200, url, headers
        def geturl(self):
            return self.url

    def test_download_size_hash_cancel_and_redirect_boundary(self):
        payload = b"synthetic-installer"
        asset = {**row("Setup.exe", payload), "id": 123}
        url = f"https://api.github.com/repos/{updates.REPOSITORY}/releases/assets/123"
        for case in ("ok", "tampered", "truncated", "oversized", "cancel", "http", "foreign", "fragment", "credentials", "double_redirect"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                cancel = threading.Event()
                if case == "cancel":
                    cancel.set()
                data = {"tampered": b"x" * len(payload), "truncated": payload[:-1], "oversized": payload + b"x"}.get(case, payload)
                seen = []
                def open_request(request, **_kwargs):
                    seen.append(request)
                    locations = {"http": "http://release-assets.githubusercontent.com/a", "foreign": "https://foreign.example/a",
                                 "fragment": "https://release-assets.githubusercontent.com/a#x", "credentials": "https://user@release-assets.githubusercontent.com/a",
                                 "double_redirect": "https://release-assets.githubusercontent.com/a"}
                    if case in locations:
                        raise HTTPError(request.full_url, 302, "redirect", {"Location": locations[case]}, io.BytesIO())
                    return self.Response(data, url)
                target = Path(directory) / "Setup.exe"
                if case == "ok":
                    updates.download_asset(asset, target, cancel, Mock(), opener=open_request)
                    self.assertEqual(target.read_bytes(), payload)
                else:
                    with self.assertRaises(engine.UpdateFailure):
                        updates.download_asset(asset, target, cancel, Mock(), opener=open_request)
                self.assertEqual(seen[0].full_url, url)
                self.assertFalse(any(request.has_header("Authorization") or request.has_header("Cookie") for request in seen))
                self.assertLessEqual(len(seen), 2)


class ManagerAndWorkerTests(unittest.TestCase):
    def test_busy_operation_blocks_update_and_duplicate_start_cannot_launch_again(self):
        manager = updates.WindowsUpdateManager(Path("synthetic"), Mock(snapshot=lambda: {
            "update_available": True, "latest": {"tag": "v1.0.1", "windows_assets": {"synthetic": True}}}))
        manager.capability = lambda: ({"version": "1.0.0"}, None)
        with manager.operation(), patch.object(engine, "create_job") as create:
            with self.assertRaisesRegex(engine.UpdateFailure, "BUSY"):
                manager.start("v1.0.1", True)
            create.assert_not_called()
        manager.frozen = True
        manager.snapshot = lambda: {"phase": "downloading"}
        with patch.object(engine, "create_job") as create:
            self.assertEqual(manager.start("v1.0.1", True), {"phase": "downloading"})
            create.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows held-process identity contract")
    def test_owned_process_handles_wait_for_exit(self):
        worker = load_worker()
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "peer.py").write_bytes(Path(engine.__file__).with_name("local_http_security.py").read_bytes())
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.buffer.read()"],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            tree = worker.ProcessTree(process.pid, 0, job)
            try:
                self.assertIn(process.pid, tree.live_pids)
                with self.assertRaisesRegex(worker.engine.UpdateFailure, "PARENT_STILL_RUNNING"):
                    tree.wait(timeout=0.05)
                process.stdin.close()
                process.wait(timeout=5)
                tree.wait(timeout=1)
            finally:
                tree.close()

    @unittest.skipUnless(os.name == "nt", "Windows late-child shutdown contract")
    def test_late_child_is_captured_after_initial_handoff_snapshot(self):
        worker = load_worker()
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "peer.py").write_bytes(Path(engine.__file__).with_name("local_http_security.py").read_bytes())
            code = ("import subprocess,sys; sys.stdin.readline(); "
                    "child=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(1.5)'],stdin=subprocess.DEVNULL); "
                    "print(child.pid,flush=True); sys.stdin.read()")
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", code], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            tree = worker.ProcessTree(process.pid, 0, job)
            try:
                process.stdin.write(b"spawn\n")
                process.stdin.flush()
                child_pid = int(process.stdout.readline())
                process.stdin.close()
                process.wait(timeout=5)
                with self.assertRaisesRegex(worker.engine.UpdateFailure, "PARENT_STILL_RUNNING"):
                    tree.wait(timeout=0.1)
                self.assertIn(child_pid, tree.live_pids)
                tree.wait(timeout=5)
            finally:
                process.stdout.close()
                tree.close()


if __name__ == "__main__":
    unittest.main()
