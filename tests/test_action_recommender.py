"""
测试行动建议生成器
"""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import json

from agent_platform.action_recommender import (
    ActionRecommender,
    create_recommender,
)
from agent_platform.signal_engine import (
    Signal,
    OverdueActionRule,
    LongInactiveRule,
)


class TestActionRecommender(unittest.TestCase):
    """测试行动建议生成器"""

    def setUp(self):
        self.recommender = ActionRecommender()
        self.now = datetime.utcnow()

    def test_generate_from_overdue_signal(self):
        """测试基于逾期信号生成建议"""
        # 创建逾期信号
        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_001",
            "account_name": "测试客户A",
            "open_actions": [
                {
                    "action_id": "act_001",
                    "title": "提交方案",
                    "due_at": (self.now - timedelta(days=5)).isoformat(),
                }
            ]
        }
        signals = rule.evaluate(account_data)
        signal = signals[0]

        # 生成建议
        account_context = {"stage": "negotiation", "health_score": 75}
        recommendation = self.recommender.generate_from_signal(signal, account_context)

        # 验证
        self.assertIsNotNone(recommendation.recommendation_id)
        self.assertEqual(recommendation.account_id, "acc_001")
        self.assertEqual(recommendation.signal_type, "overdue_action")
        self.assertEqual(recommendation.priority, "medium")
        self.assertEqual(recommendation.status, "pending")
        self.assertIn("逾期", recommendation.title)
        self.assertGreater(len(recommendation.suggested_actions), 0)

    def test_generate_from_inactive_signal(self):
        """测试基于长期无互动信号生成建议"""
        rule = LongInactiveRule(threshold_days=30)
        account_data = {
            "account_id": "acc_002",
            "account_name": "测试客户B",
            "last_effective_activity_at": (self.now - timedelta(days=45)).isoformat(),
        }
        signals = rule.evaluate(account_data)
        signal = signals[0]

        # 生成建议
        recommendation = self.recommender.generate_from_signal(signal, {})

        # 验证
        self.assertEqual(recommendation.signal_type, "long_inactive")
        self.assertIn("恢复客户联系", recommendation.title)
        self.assertIn("联系客户", recommendation.suggested_actions[0])

    def test_accept_recommendation(self):
        """测试接受建议"""
        # 创建建议
        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_003",
            "account_name": "测试客户C",
            "open_actions": [
                {
                    "action_id": "act_002",
                    "title": "测试行动",
                    "due_at": (self.now - timedelta(days=3)).isoformat(),
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        recommendation = self.recommender.generate_from_signal(signal, {})

        # 接受建议
        due_at = (self.now + timedelta(days=7)).isoformat()
        action = self.recommender.accept_recommendation(
            recommendation.recommendation_id,
            assignee="张三",
            due_at=due_at
        )

        # 验证
        self.assertIsNotNone(action.action_id)
        self.assertEqual(action.recommendation_id, recommendation.recommendation_id)
        self.assertEqual(action.assignee, "张三")
        self.assertEqual(action.due_at, due_at)
        self.assertTrue(action.created_from_signal)
        self.assertEqual(recommendation.status, "accepted")

    def test_accept_with_edits(self):
        """测试编辑后接受建议"""
        # 创建建议
        rule = LongInactiveRule()
        account_data = {
            "account_id": "acc_004",
            "account_name": "测试客户D",
            "last_effective_activity_at": (self.now - timedelta(days=40)).isoformat(),
        }
        signal = rule.evaluate(account_data)[0]
        recommendation = self.recommender.generate_from_signal(signal, {})

        # 编辑后接受
        user_edits = {
            "title": "主动联系客户了解需求",
            "description": "客户长时间未联系，需要主动跟进"
        }
        action = self.recommender.accept_recommendation(
            recommendation.recommendation_id,
            user_edits=user_edits
        )

        # 验证
        self.assertEqual(action.title, user_edits["title"])
        self.assertEqual(action.description, user_edits["description"])
        self.assertEqual(recommendation.status, "edited")

    def test_ignore_recommendation(self):
        """测试忽略建议"""
        # 创建建议
        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_005",
            "account_name": "测试客户E",
            "open_actions": [
                {
                    "action_id": "act_003",
                    "due_at": (self.now - timedelta(days=2)).isoformat(),
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        recommendation = self.recommender.generate_from_signal(signal, {})

        # 忽略建议
        reason = "客户已确认不再需要此行动"
        self.recommender.ignore_recommendation(recommendation.recommendation_id, reason)

        # 验证
        self.assertEqual(recommendation.status, "ignored")
        self.assertEqual(recommendation.user_feedback, reason)

    def test_get_pending_recommendations(self):
        """测试获取待处理建议"""
        # 创建多个建议
        rule = OverdueActionRule()

        # 客户A: 高优先级
        account_data_a = {
            "account_id": "acc_006",
            "account_name": "客户A",
            "open_actions": [
                {
                    "action_id": "act_004",
                    "due_at": (self.now - timedelta(days=10)).isoformat(),  # high
                }
            ]
        }
        signal_a = rule.evaluate(account_data_a)[0]
        rec_a = self.recommender.generate_from_signal(signal_a, {})

        # 客户B: 中优先级
        account_data_b = {
            "account_id": "acc_007",
            "account_name": "客户B",
            "open_actions": [
                {
                    "action_id": "act_005",
                    "due_at": (self.now - timedelta(days=3)).isoformat(),  # medium
                }
            ]
        }
        signal_b = rule.evaluate(account_data_b)[0]
        rec_b = self.recommender.generate_from_signal(signal_b, {})

        # 接受一个建议
        self.recommender.accept_recommendation(rec_b.recommendation_id)

        # 获取待处理建议
        pending = self.recommender.get_pending_recommendations()

        # 验证
        self.assertEqual(len(pending), 1)  # 只有1个待处理
        self.assertEqual(pending[0].recommendation_id, rec_a.recommendation_id)

    def test_get_pending_recommendations_by_account(self):
        """测试按客户获取待处理建议"""
        rule = OverdueActionRule()

        # 客户A
        account_data_a = {
            "account_id": "acc_008",
            "account_name": "客户A",
            "open_actions": [
                {"action_id": "act_006", "due_at": (self.now - timedelta(days=5)).isoformat()}
            ]
        }
        signal_a = rule.evaluate(account_data_a)[0]
        self.recommender.generate_from_signal(signal_a, {})

        # 客户B
        account_data_b = {
            "account_id": "acc_009",
            "account_name": "客户B",
            "open_actions": [
                {"action_id": "act_007", "due_at": (self.now - timedelta(days=5)).isoformat()}
            ]
        }
        signal_b = rule.evaluate(account_data_b)[0]
        self.recommender.generate_from_signal(signal_b, {})

        # 只获取客户A的建议
        pending = self.recommender.get_pending_recommendations(account_id="acc_008")

        # 验证
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].account_id, "acc_008")

    def test_adoption_rate_calculation(self):
        """测试采纳率计算"""
        rule = OverdueActionRule()

        # 创建5个建议
        for i in range(5):
            account_data = {
                "account_id": f"acc_{i:03d}",
                "account_name": f"客户{i}",
                "open_actions": [
                    {
                        "action_id": f"act_{i:03d}",
                        "due_at": (self.now - timedelta(days=5)).isoformat(),
                    }
                ]
            }
            signal = rule.evaluate(account_data)[0]
            rec = self.recommender.generate_from_signal(signal, {})

            # 2个接受，1个编辑后接受，1个忽略，1个待处理
            if i == 0:
                self.recommender.accept_recommendation(rec.recommendation_id)
            elif i == 1:
                self.recommender.accept_recommendation(rec.recommendation_id)
            elif i == 2:
                self.recommender.accept_recommendation(
                    rec.recommendation_id,
                    user_edits={"title": "编辑后的标题"}
                )
            elif i == 3:
                self.recommender.ignore_recommendation(rec.recommendation_id)
            # i == 4 保持 pending

        stats = self.recommender.get_adoption_rate()

        # 验证
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["accepted"], 2)
        self.assertEqual(stats["edited"], 1)
        self.assertEqual(stats["ignored"], 1)
        self.assertEqual(stats["pending"], 1)
        # 采纳率 = (2 + 1) / (5 - 1) = 0.75
        self.assertEqual(stats["adoption_rate"], 0.75)

    def test_adoption_rate_empty(self):
        """测试空采纳率"""
        stats = self.recommender.get_adoption_rate()

        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["adoption_rate"], 0.0)

    def test_proactive_recommendation(self):
        """测试主动建议生成"""
        account_data = {
            "account_id": "acc_010",
            "account_name": "测试客户F",
            "opportunities": [
                {
                    "opportunity_id": "opp_001",
                    "name": "智能制造项目",
                    "stage": "negotiation",
                    # 缺少 last_updated_at，表示长期未更新
                }
            ]
        }

        recommendation = self.recommender.generate_proactive_recommendation(account_data)

        # 验证
        self.assertIsNotNone(recommendation)
        self.assertIsNone(recommendation.signal_id)  # 不基于信号
        self.assertIn("推进商机", recommendation.title)
        self.assertEqual(recommendation.priority, "medium")

    def test_export_recommendations(self):
        """测试导出建议历史"""
        # 创建几个建议
        rule = OverdueActionRule()
        for i in range(3):
            account_data = {
                "account_id": f"acc_{i}",
                "account_name": f"客户{i}",
                "open_actions": [
                    {"action_id": f"act_{i}", "due_at": (self.now - timedelta(days=5)).isoformat()}
                ]
            }
            signal = rule.evaluate(account_data)[0]
            self.recommender.generate_from_signal(signal, {})

        # 导出
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "recommendations.json"
            self.recommender.export_recommendations(output_path)

            # 验证文件存在且内容正确
            self.assertTrue(output_path.exists())

            with open(output_path, encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(len(data), 3)
            self.assertIn("recommendation_id", data[0])
            self.assertIn("account_id", data[0])

    def test_recommendation_priority_sorting(self):
        """测试建议按优先级排序"""
        # 创建不同优先级的建议
        rule_overdue = OverdueActionRule()
        rule_inactive = LongInactiveRule()

        # 高优先级（逾期10天）
        high_data = {
            "account_id": "acc_high",
            "account_name": "高优先级客户",
            "open_actions": [
                {"action_id": "act_high", "due_at": (self.now - timedelta(days=10)).isoformat()}
            ]
        }
        high_signal = rule_overdue.evaluate(high_data)[0]
        self.recommender.generate_from_signal(high_signal, {})

        # 中优先级（逾期5天）
        med_data = {
            "account_id": "acc_med",
            "account_name": "中优先级客户",
            "open_actions": [
                {"action_id": "act_med", "due_at": (self.now - timedelta(days=5)).isoformat()}
            ]
        }
        med_signal = rule_overdue.evaluate(med_data)[0]
        self.recommender.generate_from_signal(med_signal, {})

        # 获取待处理建议
        pending = self.recommender.get_pending_recommendations()

        # 验证排序：高优先级在前
        self.assertEqual(pending[0].account_id, "acc_high")
        self.assertEqual(pending[1].account_id, "acc_med")


if __name__ == "__main__":
    unittest.main()
