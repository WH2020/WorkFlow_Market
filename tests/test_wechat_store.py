from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_platform import wechat_store
from agent_platform.wechat_store import (
    WechatStoreError,
    cleanup_expired,
    create_review_scope,
    dashboard,
    import_export,
    list_conversations,
    read_messages,
    review_scope_summary,
)


class WechatStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wechat-store-test-", dir=Path.home())
        self.root = Path(self.temporary.name)
        self.source = self.root / "wx-export.json"
        self.source.write_text(
            json.dumps(
                {
                    "conversations": [
                        {
                            "username": "client-a",
                            "display_name": "客户甲",
                            "messages": [
                                {
                                    "local_id": "1",
                                    "sender_name": "客户甲",
                                    "sender_wxid": "wxid-client-a",
                                    "is_self": False,
                                    "time": "2026-08-24T01:00:00Z",
                                    "type_label": "文本",
                                    "content": "预算已初步确认，下周二需要技术方案。",
                                },
                                {
                                    "local_id": "2",
                                    "sender_name": "我",
                                    "is_self": True,
                                    "time": "2026-08-24T01:05:00Z",
                                    "content": "我会在周一前发送初版方案。",
                                },
                            ],
                        },
                        {
                            "username": "project-room@chatroom",
                            "display_name": "项目推进群",
                            "is_group": True,
                            "messages": [
                                {
                                    "local_id": "3",
                                    "sender_name": "销售乙",
                                    "time": "2026-08-25T02:00:00Z",
                                    "content": "客户希望补充试点资源清单。",
                                }
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def import_sample(self) -> dict[str, object]:
        return import_export(
            self.root,
            self.source,
            source_name=self.source.name,
            account_label="工作微信",
            retention_days=7,
            auto_cleanup=True,
            ownership_confirmed=True,
        )

    def test_import_groups_conversations_and_preserves_message_locators(self) -> None:
        result = self.import_sample()
        self.assertEqual(3, result["message_count"])
        self.assertEqual(2, result["conversation_count"])

        listed = list_conversations(self.root, date_from="2026-08-24", date_to="2026-08-25")
        self.assertEqual(2, listed["total"])
        direct = next(row for row in listed["rows"] if row["display_name"] == "客户甲")
        group = next(row for row in listed["rows"] if row["display_name"] == "项目推进群")
        self.assertEqual("direct", direct["chat_type"])
        self.assertEqual("group", group["chat_type"])

        messages = read_messages(self.root, direct["conversation_id"], limit=100)
        self.assertEqual(2, messages["returned"])
        self.assertTrue(all(row["source_locator"].startswith("wechat://message/wxmsg-") for row in messages["rows"]))
        self.assertNotIn(str(self.source), json.dumps(messages, ensure_ascii=False))

    def test_duplicate_import_is_idempotent(self) -> None:
        first = self.import_sample()
        second = self.import_sample()
        self.assertFalse(first["duplicate_file"])
        self.assertTrue(second["duplicate_file"])
        self.assertEqual(3, dashboard(self.root)["message_count"])

    def test_first_writable_connection_creates_private_db_wal_and_shm(self) -> None:
        from agent_platform.wechat_privacy import verify_private_file

        previous_umask = os.umask(0o022) if os.name != "nt" else None
        try:
            connection = wechat_store._connect(self.root, writable=True)
        finally:
            if previous_umask is not None:
                os.umask(previous_umask)
        try:
            database = wechat_store.database_path(self.root)
            files = [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
            self.assertTrue(all(candidate.is_file() for candidate in files))
            for candidate in files:
                verify_private_file(candidate)
                if os.name != "nt":
                    self.assertEqual(0o600, candidate.stat().st_mode & 0o777)
        finally:
            connection.close()

    def test_orphan_sqlite_auxiliary_is_never_attached_to_a_new_database(self) -> None:
        from agent_platform.wechat_privacy import create_private_file, ensure_private_directory

        data = self.root / "data"
        data.mkdir()
        private_root = data / "wechat"
        ensure_private_directory(private_root)
        database = private_root / "chat-index.sqlite3"
        orphan = Path(str(database) + "-wal")
        create_private_file(orphan)
        orphan.write_bytes(b"orphan-state")

        with self.assertRaises(WechatStoreError) as raised:
            wechat_store._connect(self.root, writable=True)
        self.assertEqual("UNSAFE_PATH", raised.exception.code)
        self.assertFalse(database.exists())
        self.assertEqual(b"orphan-state", orphan.read_bytes())

    def test_parallel_first_connections_are_linearized(self) -> None:
        from agent_platform import wechat_privacy

        original_create = wechat_privacy.create_private_file_if_absent

        def delayed_create(path):
            time.sleep(0.005)
            return original_create(path)

        project = self.root / "parallel"
        project.mkdir()
        barrier = threading.Barrier(8)

        def open_once(_index):
            barrier.wait(timeout=5)
            connection = wechat_store._connect(project, writable=True)
            try:
                return connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()

        with (patch.object(wechat_privacy, "create_private_file_if_absent",
                           side_effect=delayed_create),
              ThreadPoolExecutor(max_workers=8) as executor):
            versions = list(executor.map(open_once, range(8)))
        self.assertEqual([wechat_store.SCHEMA_VERSION] * 8, versions)

    def test_parallel_first_connections_across_processes(self) -> None:
        project = self.root / "parallel-processes"
        project.mkdir()
        start_at = time.time() + 1.0
        program = (
            "import sys,time; "
            "from agent_platform.wechat_store import _connect; "
            "start=float(sys.argv[2]); "
            "time.sleep(max(0,start-time.time())); "
            "c=_connect(sys.argv[1],writable=True); "
            "print(c.execute('PRAGMA user_version').fetchone()[0]); c.close()"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", program, str(project), str(start_at)],
                cwd=Path.cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            for _index in range(12)
        ]
        results = [process.communicate(timeout=20) for process in processes]
        for process, (stdout, stderr) in zip(processes, results):
            self.assertEqual(0, process.returncode, stderr)
            self.assertEqual(str(wechat_store.SCHEMA_VERSION), stdout.strip())

    def test_exclusive_create_loser_revalidates_cross_process_winner(self) -> None:
        from agent_platform import wechat_privacy

        original_create = wechat_privacy.create_private_file

        def competing_create(path):
            original_create(path)
            return False

        with patch.object(wechat_privacy, "create_private_file_if_absent",
                          side_effect=competing_create):
            connection = wechat_store._connect(self.root, writable=True)
        try:
            self.assertEqual(
                wechat_store.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
        finally:
            connection.close()

    def test_private_create_failure_is_not_treated_as_a_race_loser(self) -> None:
        from agent_platform import wechat_privacy

        failure = WechatStoreError("UNSAFE_PATH", "synthetic rollback failure")
        with (patch.object(wechat_privacy, "create_private_file_if_absent",
                           side_effect=failure),
              patch.object(wechat_store.sqlite3, "connect") as connect):
            with self.assertRaises(WechatStoreError) as raised:
                wechat_store._connect(self.root, writable=True)
        self.assertIs(failure, raised.exception)
        connect.assert_not_called()

    def test_database_identity_change_during_open_is_rejected(self) -> None:
        from agent_platform import wechat_privacy

        identity = wechat_privacy.private_file_identity

        def changed_identity(path):
            actual = identity(path)
            return (*actual, 1)

        with patch.object(wechat_privacy, "private_file_identity",
                          side_effect=changed_identity):
            with self.assertRaises(WechatStoreError) as raised:
                wechat_store._connect(self.root, writable=True)
        self.assertEqual("UNSAFE_PATH", raised.exception.code)

    @unittest.skipIf(os.name == "nt", "POSIX mode regression")
    def test_existing_broad_database_is_rejected_without_repair(self) -> None:
        from agent_platform.wechat_privacy import ensure_private_directory

        data = self.root / "data"
        data.mkdir()
        private_root = data / "wechat"
        ensure_private_directory(private_root)
        database = private_root / "chat-index.sqlite3"
        database.write_bytes(b"legacy")
        database.chmod(0o644)

        with self.assertRaises(WechatStoreError) as raised:
            wechat_store._connect(self.root, writable=True)
        self.assertEqual("UNSAFE_PATH", raised.exception.code)
        self.assertEqual(0o644, database.stat().st_mode & 0o777)
        self.assertEqual(b"legacy", database.read_bytes())

    def test_malformed_version_zero_database_is_not_silently_upgraded(self) -> None:
        from agent_platform.wechat_privacy import create_private_file, ensure_private_directory

        data = self.root / "data"
        data.mkdir()
        private_root = data / "wechat"
        ensure_private_directory(private_root)
        database = private_root / "chat-index.sqlite3"
        create_private_file(database)
        malformed = wechat_store.sqlite3.connect(database)
        try:
            malformed.execute("CREATE TABLE import_batches(wrong_column TEXT)")
            malformed.commit()
        finally:
            malformed.close()

        with self.assertRaises(WechatStoreError):
            wechat_store._connect(self.root, writable=True)
        check = wechat_store.sqlite3.connect(database)
        try:
            self.assertEqual(0, check.execute("PRAGMA user_version").fetchone()[0])
            columns = check.execute("PRAGMA table_info(import_batches)").fetchall()
            self.assertEqual(["wrong_column"], [column[1] for column in columns])
        finally:
            check.close()

    def test_copy_verification_failure_removes_unregistered_copy(self) -> None:
        from agent_platform.wechat_privacy import verify_private_file
        original_source = self.source.read_bytes()

        def fail_published_copy(path):
            if Path(path).name == self.source.name:
                raise RuntimeError("synthetic post-publish failure")
            return verify_private_file(path)

        with patch("agent_platform.wechat_privacy.verify_private_file", side_effect=fail_published_copy):
            with self.assertRaisesRegex(RuntimeError, "synthetic post-publish"):
                self.import_sample()
        self.assertEqual([], list((self.root / "data/wechat/imports").iterdir()))
        self.assertEqual(0, dashboard(self.root)["message_count"])
        self.assertEqual(original_source, self.source.read_bytes())

    def test_copy_failure_before_publish_removes_temporary_file_and_batch(self) -> None:
        with patch.object(wechat_store.shutil, "copyfile", side_effect=OSError("synthetic copy failure")):
            with self.assertRaisesRegex(OSError, "synthetic copy failure"):
                self.import_sample()
        self.assertEqual([], list((self.root / "data/wechat/imports").iterdir()))
        self.assertEqual(0, dashboard(self.root)["message_count"])

    def test_copy_cleanup_failure_is_explicit(self) -> None:
        from agent_platform.wechat_privacy import verify_private_file
        unlink = Path.unlink

        def fail_published_copy(path):
            if Path(path).name == self.source.name:
                raise RuntimeError("synthetic verify")
            return verify_private_file(path)

        def deny_published_unlink(path, *args, **kwargs):
            if path.name == self.source.name and "imports" in path.parts:
                raise PermissionError("synthetic cleanup denial")
            return unlink(path, *args, **kwargs)

        with (patch("agent_platform.wechat_privacy.verify_private_file", side_effect=fail_published_copy),
              patch.object(Path, "unlink", deny_published_unlink)):
            with self.assertRaises(WechatStoreError) as raised:
                self.import_sample()
        self.assertEqual("CLEANUP_FAILED", raised.exception.code)
        self.assertNotIn(str(self.source), str(raised.exception))
        self.assertTrue(self.source.is_file())

    def test_conflicting_message_identity_stops_without_overwriting_existing_text(self) -> None:
        self.import_sample()
        conflicting = self.root / "wx-conflict.jsonl"
        conflicting.write_text(json.dumps({
            "conversation": "client-a", "conversation_name": "客户甲", "local_id": "1",
            "sender_name": "客户甲", "time": "2026-08-24T01:00:00Z", "content": "被替换的正文",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(WechatStoreError, "相同消息编号"):
            import_export(
                self.root, conflicting, source_name=conflicting.name,
                account_label="工作微信", ownership_confirmed=True, retention_days=7, auto_cleanup=True,
            )
        conversations = list_conversations(self.root, query="预算")["rows"]
        self.assertEqual(1, len(conversations))
        messages = read_messages(self.root, conversations[0]["conversation_id"])["rows"]
        self.assertTrue(any("预算已初步确认" in row["content"] for row in messages))
        self.assertFalse(any("被替换" in row["content"] for row in messages))

    def test_import_requires_ownership_and_fixed_weekly_cleanup(self) -> None:
        with self.assertRaisesRegex(WechatStoreError, "本人账号"):
            import_export(
                self.root,
                self.source,
                source_name=self.source.name,
                ownership_confirmed=False,
            )
        with self.assertRaisesRegex(WechatStoreError, "固定保留 7 天"):
            import_export(
                self.root,
                self.source,
                source_name=self.source.name,
                retention_days=30,
                auto_cleanup=True,
                ownership_confirmed=True,
            )

    def test_review_scope_freezes_selected_conversations_dates_and_consent(self) -> None:
        self.import_sample()
        conversations = list_conversations(self.root)["rows"]
        direct = next(row for row in conversations if row["display_name"] == "客户甲")
        payload = {
            "project_id": "project-default",
            "conversation_ids": [direct["conversation_id"]],
            "date_from": "2026-08-24",
            "date_to": "2026-08-24",
            "query": "预算",
            "model_sharing_confirmed": False,
        }
        with self.assertRaisesRegex(WechatStoreError, "当前模型"):
            create_review_scope(self.root, payload)
        payload["model_sharing_confirmed"] = True
        recipient = {"provider_id": "agent4market-test", "base_url": "https://example.com", "api": "openai-responses", "model_id": "test"}
        with self.assertRaisesRegex(WechatStoreError, "具体模型供应商"):
            create_review_scope(self.root, payload)
        payload["model_recipient"] = recipient
        with patch("agent_platform.model_registry.available_models", return_value={"agent4market-test/test": recipient}):
            scope = create_review_scope(self.root, payload)
        self.assertRegex(scope["scope_id"], r"^wechat-scope-[a-f0-9]{20}$")
        self.assertEqual(1, scope["conversation_count"])
        self.assertEqual(1, scope["message_count"])
        self.assertGreater(scope["text_bytes"], 0)
        summary = review_scope_summary(self.root, scope["scope_id"])
        self.assertEqual("project-default", summary["project_id"])
        self.assertEqual(recipient, summary["model_recipient"])
        self.assertNotIn("conversation_ids_json", summary)

    def test_review_scope_rejects_cli_recipients_before_model_lookup_or_database_open(self) -> None:
        for provider_type in ("claude-code", "codex-cli"):
            with self.subTest(provider_type=provider_type):
                recipient = {
                    "provider_id": f"agent4market-{provider_type}",
                    "base_url": "https://example.invalid",
                    "api": provider_type,
                    "model_id": f"{provider_type}-account-model",
                }
                payload = {
                    "project_id": "project-default",
                    "conversation_ids": ["synthetic-conversation"],
                    "date_from": "2026-08-24",
                    "date_to": "2026-08-24",
                    "query": "",
                    "model_sharing_confirmed": True,
                    "model_recipient": recipient,
                }
                with (
                    patch("agent_platform.model_registry.available_models") as available_models,
                    patch.object(wechat_store, "_connect") as connect,
                ):
                    with self.assertRaises(WechatStoreError) as raised:
                        create_review_scope(self.root, payload)
                self.assertEqual("CLI_WECHAT_UNSUPPORTED", raised.exception.code)
                available_models.assert_not_called()
                connect.assert_not_called()

    def test_cleanup_removes_only_app_copy_and_indexed_plaintext(self) -> None:
        self.import_sample()
        before = dashboard(self.root)
        self.assertEqual("retained", before["batches"][0]["raw_status"])
        cleanup = cleanup_expired(
            self.root,
            reference=datetime.now(timezone.utc) + timedelta(days=8),
        )
        self.assertEqual(3, cleanup["purged_messages"])
        self.assertEqual(1, cleanup["purged_files"])
        self.assertTrue(self.source.is_file(), "清理不得触碰用户选择的原始导出文件")
        after = dashboard(self.root)
        self.assertEqual(0, after["message_count"])
        self.assertEqual("purged", after["batches"][0]["raw_status"])
        self.assertNotIn("raw_path", after["batches"][0])


if __name__ == "__main__":
    unittest.main()
