from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        self.temporary = tempfile.TemporaryDirectory()
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
        scope = create_review_scope(self.root, payload)
        self.assertRegex(scope["scope_id"], r"^wechat-scope-[a-f0-9]{20}$")
        self.assertEqual(1, scope["conversation_count"])
        self.assertEqual(1, scope["message_count"])
        self.assertGreater(scope["text_bytes"], 0)
        summary = review_scope_summary(self.root, scope["scope_id"])
        self.assertEqual("project-default", summary["project_id"])
        self.assertNotIn("conversation_ids_json", summary)

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
