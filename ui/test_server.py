import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from ui import server


class ControlCentreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runtime, self.old_tasks, self.old_requests = server.RUNTIME, server.TASKS, server.REQUESTS
        server.RUNTIME = Path(self.temporary.name)
        server.TASKS = server.RUNTIME / "tasks"
        server.REQUESTS = server.RUNTIME / "requests"

    def tearDown(self):
        server.RUNTIME, server.TASKS, server.REQUESTS = self.old_runtime, self.old_tasks, self.old_requests
        self.temporary.cleanup()

    def test_atomic_json_round_trip(self):
        target = server.TASKS / "task-a.json"
        server.atomic_json(target, {"task_id": "task-a", "version": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 1)
        self.assertFalse(list(server.TASKS.glob("*.tmp")))

    def test_safe_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            server.safe_id("../task")

    def test_exclusive_task_rejects_a_concurrent_writer(self):
        target = server.TASKS / "task-a.json"
        with server.exclusive_task(target):
            with self.assertRaisesRegex(RuntimeError, "正在由 Pi"):
                with server.exclusive_task(target):
                    pass
        self.assertFalse(Path(f"{target}.lock").exists())

    def test_exclusive_task_never_deletes_a_replaced_lock(self):
        target = server.TASKS / "task-a.json"
        lock = Path(f"{target}.lock")
        with server.exclusive_task(target):
            lock.write_text(json.dumps({"pid": 999, "nonce": "replacement"}), encoding="utf-8")
        self.assertTrue(lock.exists())

    def test_file_summary_does_not_return_csv_content(self):
        data = Path(self.temporary.name) / "data" / "sales"
        data.mkdir(parents=True)
        target = data / "customers.csv"
        target.write_text("id,name\n1,private-name\n", encoding="utf-8")
        previous_root = server.ROOT
        try:
            server.ROOT = Path(self.temporary.name)
            summary = server.file_summary("data/sales/customers.csv")
        finally:
            server.ROOT = previous_root
        self.assertEqual(summary["records"], 1)
        self.assertNotIn("private-name", json.dumps(summary, ensure_ascii=False))

    def test_file_summary_counts_multiline_csv_as_one_record(self):
        data = Path(self.temporary.name) / "data" / "knowledge"
        data.mkdir(parents=True)
        target = data / "source-register.csv"
        target.write_text('id,notes\n1,"line one\nline two"\n', encoding="utf-8")
        previous_root = server.ROOT
        try:
            server.ROOT = Path(self.temporary.name)
            summary = server.file_summary("data/knowledge/source-register.csv")
        finally:
            server.ROOT = previous_root
        self.assertEqual(summary["records"], 1)

    def test_approval_must_bind_the_frozen_write_hash(self):
        task = {
            "task_id": "task-a", "version": 7, "status": "waiting_approval",
            "pending_write": {
                "intent_id": "intent-a", "payload_sha256": "a" * 64,
                "status": "prepared", "canonical_payload": "{}",
            },
        }
        path = server.TASKS / "task-a.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        handler.decide("task-a", {"decision": "approve", "version": 7})
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertNotIn("approval_request", server.load_json(path))

        handler.decide("task-a", {
            "decision": "approve", "version": 7,
            "intent_id": "intent-a", "payload_sha256": "a" * 64,
        })
        saved = server.load_json(path)
        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        self.assertEqual(saved["approval_request"]["intent_id"], "intent-a")
        self.assertEqual(saved["approval_request"]["payload_sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
