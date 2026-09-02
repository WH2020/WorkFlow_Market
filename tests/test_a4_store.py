"""
测试 A4 建议持久化存储
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agent_platform.a4_store import (
    A4Store,
    A4StoreError,
    create_store,
    default_store_path,
)
from agent_platform.action_recommender import ActionRecommender
from agent_platform.signal_engine import LongInactiveRule, OverdueActionRule


def _overdue_signal(account_id="acc_001", days=5):
    rule = OverdueActionRule()
    account_data = {
        "account_id": account_id,
        "account_name": f"客户{account_id}",
        "open_actions": [
            {
                "action_id": f"act_{account_id}",
                "due_at": (datetime.utcnow() - timedelta(days=days)).isoformat(),
            }
        ],
    }
    return rule.evaluate(account_data)[0]


class TestA4Store(unittest.TestCase):
    """测试存储层"""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "a4.db"
        self.store = A4Store(self.db_path)

    def tearDown(self):
        self._tempdir.cleanup()

    def test_creates_database_file(self):
        """测试首次初始化创建数据库文件"""
        self.assertTrue(self.db_path.exists())

    def test_creates_parent_directories(self):
        """测试自动创建父目录"""
        nested = Path(self._tempdir.name) / "a" / "b" / "c.db"
        A4Store(nested)
        self.assertTrue(nested.exists())

    def test_records_schema_version(self):
        """测试记录 schema 版本"""
        connection = sqlite3.connect(str(self.db_path))
        try:
            row = connection.execute(
                "SELECT value FROM a4_metadata WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row[0], "1")

    def test_reopen_existing_database(self):
        """测试重新打开已有数据库不报错"""
        reopened = A4Store(self.db_path)
        self.assertEqual(reopened.load_all_recommendations(), [])

    def test_schema_version_mismatch_raises(self):
        """测试 schema 版本不匹配时抛出带错误码的异常"""
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute(
                "UPDATE a4_metadata SET value = '99' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(A4StoreError) as context:
            A4Store(self.db_path)

        self.assertEqual(context.exception.code, "SCHEMA_VERSION_MISMATCH")

    def test_save_and_load_recommendation(self):
        """测试保存并加载建议，JSON 字段正确还原"""
        recommender = ActionRecommender()
        rec = recommender.generate_from_signal(_overdue_signal(), {"stage": "谈判"})

        self.store.save_recommendation(rec.to_dict())
        loaded = self.store.load_all_recommendations()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["recommendation_id"], rec.recommendation_id)
        self.assertEqual(loaded[0]["suggested_actions"], rec.suggested_actions)
        self.assertEqual(loaded[0]["context"]["account_stage"], "谈判")

    def test_save_recommendation_is_idempotent(self):
        """测试重复保存同一建议只更新不重复插入"""
        recommender = ActionRecommender()
        rec = recommender.generate_from_signal(_overdue_signal(), {})

        self.store.save_recommendation(rec.to_dict())
        rec.status = "ignored"
        rec.user_feedback = "客户已确认"
        self.store.save_recommendation(rec.to_dict())

        loaded = self.store.load_all_recommendations()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["status"], "ignored")
        self.assertEqual(loaded[0]["user_feedback"], "客户已确认")

    def test_save_accepted_action(self):
        """测试保存已接受行动"""
        recommender = ActionRecommender()
        rec = recommender.generate_from_signal(_overdue_signal(), {})
        self.store.save_recommendation(rec.to_dict())

        action = recommender.accept_recommendation(
            rec.recommendation_id, assignee="张三"
        )
        self.store.save_accepted_action(action.to_dict())

        connection = sqlite3.connect(str(self.db_path))
        try:
            row = connection.execute(
                "SELECT assignee, created_from_signal FROM a4_accepted_actions WHERE action_id = ?",
                (action.action_id,),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row[0], "张三")
        self.assertEqual(row[1], 1)

    def test_accepted_action_requires_existing_recommendation(self):
        """测试外键约束拒绝孤立行动"""
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_accepted_action(
                {
                    "action_id": "act_orphan",
                    "recommendation_id": "rec_missing",
                    "account_id": "acc_001",
                    "title": "标题",
                    "description": "描述",
                    "priority": "high",
                    "due_at": None,
                    "assignee": None,
                    "created_from_signal": False,
                    "accepted_at": "2026-09-01T00:00:00Z",
                }
            )

    def test_adoption_stats_on_empty_store(self):
        """测试空存储的采纳率统计"""
        stats = self.store.get_adoption_stats()

        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["adoption_rate"], 0.0)

    def test_create_store_factory(self):
        """测试工厂函数"""
        store = create_store(Path(self._tempdir.name) / "factory.db")
        self.assertIsInstance(store, A4Store)

    def test_default_store_path_is_local_runtime(self):
        """测试默认路径位于本机运行时目录"""
        path = default_store_path()

        self.assertEqual(path.name, "a4-recommendations.db")
        self.assertEqual(path.parent.name, "director-runtime")


class TestActionRecommenderPersistence(unittest.TestCase):
    """测试建议生成器的写穿行为"""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "a4.db"

    def tearDown(self):
        self._tempdir.cleanup()

    def test_defaults_to_memory_only(self):
        """测试默认不启用持久化"""
        recommender = ActionRecommender()

        self.assertIsNone(recommender.store)
        self.assertFalse(self.db_path.exists())

    def test_generate_writes_through(self):
        """测试生成建议立即落盘"""
        recommender = ActionRecommender(store=A4Store(self.db_path))
        rec = recommender.generate_from_signal(_overdue_signal(), {})

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        self.assertIn(rec.recommendation_id, reloaded.recommendations)

    def test_restart_restores_pending_recommendations(self):
        """测试重启后恢复待处理建议"""
        recommender = ActionRecommender(store=A4Store(self.db_path))
        recommender.generate_from_signal(_overdue_signal("acc_001"), {})
        recommender.generate_from_signal(_overdue_signal("acc_002"), {})

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        pending = reloaded.get_pending_recommendations()

        self.assertEqual(len(pending), 2)

    def test_restart_preserves_ignored_status(self):
        """测试忽略状态在重启后保留"""
        recommender = ActionRecommender(store=A4Store(self.db_path))
        rec = recommender.generate_from_signal(_overdue_signal(), {})
        recommender.ignore_recommendation(rec.recommendation_id, reason="不需要")

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        restored = reloaded.recommendations[rec.recommendation_id]

        self.assertEqual(restored.status, "ignored")
        self.assertEqual(restored.user_feedback, "不需要")
        self.assertEqual(reloaded.get_pending_recommendations(), [])

    def test_restart_preserves_accepted_status_and_action(self):
        """测试接受状态和行动在重启后保留"""
        recommender = ActionRecommender(store=A4Store(self.db_path))
        rec = recommender.generate_from_signal(_overdue_signal(), {})
        action = recommender.accept_recommendation(rec.recommendation_id, assignee="李四")

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        restored = reloaded.recommendations[rec.recommendation_id]

        self.assertEqual(restored.status, "accepted")
        self.assertIsNotNone(restored.accepted_at)

        connection = sqlite3.connect(str(self.db_path))
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM a4_accepted_actions WHERE action_id = ?",
                (action.action_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(count, 1)

    def test_restart_preserves_edited_status(self):
        """测试编辑后接受的状态在重启后保留"""
        rule = LongInactiveRule()
        signal = rule.evaluate(
            {
                "account_id": "acc_009",
                "account_name": "测试客户I",
                "last_effective_activity_at": (
                    datetime.utcnow() - timedelta(days=40)
                ).isoformat(),
            }
        )[0]

        recommender = ActionRecommender(store=A4Store(self.db_path))
        rec = recommender.generate_from_signal(signal, {})
        recommender.accept_recommendation(
            rec.recommendation_id, user_edits={"title": "新标题"}
        )

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        self.assertEqual(reloaded.recommendations[rec.recommendation_id].status, "edited")

    def test_adoption_rate_reads_from_store(self):
        """测试采纳率跨重启统计正确"""
        recommender = ActionRecommender(store=A4Store(self.db_path))
        accepted = recommender.generate_from_signal(_overdue_signal("acc_001"), {})
        ignored = recommender.generate_from_signal(_overdue_signal("acc_002"), {})
        recommender.generate_from_signal(_overdue_signal("acc_003"), {})

        recommender.accept_recommendation(accepted.recommendation_id)
        recommender.ignore_recommendation(ignored.recommendation_id)

        reloaded = ActionRecommender(store=A4Store(self.db_path))
        stats = reloaded.get_adoption_rate()

        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["ignored"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertAlmostEqual(stats["adoption_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
