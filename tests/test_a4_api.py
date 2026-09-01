"""
测试 A4 API 处理器
"""

import unittest
from datetime import datetime, timedelta

from agent_platform.a4_api import A4ApiHandler, create_api_handler


class TestA4ApiHandler(unittest.TestCase):
    """测试 A4 API 处理器"""

    def setUp(self):
        self.handler = A4ApiHandler()

    def test_handle_match_play_with_complete_input(self):
        """测试完整输入的Play匹配"""
        request_data = {
            "user_input": "客户复盘",
            "account_data": {
                "account_id": "acc_001",
                "account_name": "测试客户",
                "open_actions": []
            }
        }

        response = self.handler.handle_match_play(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertIn("data", response)
        self.assertEqual(response["data"]["status"], "ready")

    def test_handle_match_play_missing_user_input(self):
        """测试缺失用户输入"""
        request_data = {}

        response = self.handler.handle_match_play(request_data)

        # 验证错误响应
        self.assertIn("error", response)
        self.assertEqual(response["status"], 400)

    def test_handle_evaluate_signals(self):
        """测试评估信号"""
        request_data = {
            "account_data": {
                "account_id": "acc_002",
                "account_name": "测试客户B",
                "open_actions": [
                    {
                        "action_id": "act_001",
                        "due_at": (datetime.utcnow() - timedelta(days=5)).isoformat()
                    }
                ]
            }
        }

        response = self.handler.handle_evaluate_signals(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertIn("signals", response["data"])
        self.assertGreater(len(response["data"]["signals"]), 0)

    def test_handle_evaluate_signals_missing_account_data(self):
        """测试缺失账户数据"""
        request_data = {}

        response = self.handler.handle_evaluate_signals(request_data)

        # 验证错误响应
        self.assertIn("error", response)
        self.assertEqual(response["status"], 400)

    def test_handle_get_recommendations(self):
        """测试获取建议"""
        # 先创建一个建议
        from agent_platform.signal_engine import OverdueActionRule

        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_003",
            "account_name": "测试客户C",
            "open_actions": [
                {
                    "action_id": "act_002",
                    "due_at": (datetime.utcnow() - timedelta(days=3)).isoformat()
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        self.handler.integration.recommender.generate_from_signal(signal, {})

        # 获取建议
        request_data = {"account_id": "acc_003"}
        response = self.handler.handle_get_recommendations(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertIn("recommendations", response["data"])
        self.assertGreater(len(response["data"]["recommendations"]), 0)

    def test_handle_accept_recommendation(self):
        """测试接受建议"""
        # 创建建议
        from agent_platform.signal_engine import OverdueActionRule

        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_004",
            "account_name": "测试客户D",
            "open_actions": [
                {
                    "action_id": "act_003",
                    "due_at": (datetime.utcnow() - timedelta(days=2)).isoformat()
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        rec = self.handler.integration.recommender.generate_from_signal(signal, {})

        # 接受建议
        request_data = {
            "recommendation_id": rec.recommendation_id,
            "assignee": "张三",
            "due_at": (datetime.utcnow() + timedelta(days=7)).isoformat()
        }
        response = self.handler.handle_accept_recommendation(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertIn("action_id", response["data"])
        self.assertEqual(response["data"]["assignee"], "张三")

    def test_handle_accept_recommendation_with_edits(self):
        """测试编辑后接受建议"""
        # 创建建议
        from agent_platform.signal_engine import LongInactiveRule

        rule = LongInactiveRule()
        account_data = {
            "account_id": "acc_005",
            "account_name": "测试客户E",
            "last_effective_activity_at": (datetime.utcnow() - timedelta(days=40)).isoformat()
        }
        signal = rule.evaluate(account_data)[0]
        rec = self.handler.integration.recommender.generate_from_signal(signal, {})

        # 编辑后接受
        request_data = {
            "recommendation_id": rec.recommendation_id,
            "user_edits": {
                "title": "主动联系客户了解需求",
                "description": "长时间未联系，需要跟进"
            }
        }
        response = self.handler.handle_accept_recommendation(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["title"], "主动联系客户了解需求")

    def test_handle_accept_recommendation_not_found(self):
        """测试接受不存在的建议"""
        request_data = {
            "recommendation_id": "rec_nonexistent"
        }
        response = self.handler.handle_accept_recommendation(request_data)

        # 验证错误响应
        self.assertIn("error", response)
        self.assertEqual(response["status"], 404)

    def test_handle_ignore_recommendation(self):
        """测试忽略建议"""
        # 创建建议
        from agent_platform.signal_engine import OverdueActionRule

        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_006",
            "account_name": "测试客户F",
            "open_actions": [
                {
                    "action_id": "act_004",
                    "due_at": (datetime.utcnow() - timedelta(days=3)).isoformat()
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        rec = self.handler.integration.recommender.generate_from_signal(signal, {})

        # 忽略建议
        request_data = {
            "recommendation_id": rec.recommendation_id,
            "reason": "客户已确认不需要此行动"
        }
        response = self.handler.handle_ignore_recommendation(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["status"], "ignored")

    def test_handle_validate_workflow_input(self):
        """测试验证工作流输入"""
        request_data = {
            "workflow_id": "market.sales.pipeline-review",
            "provided_input": {"account_id": "acc_001"}
        }

        response = self.handler.handle_validate_workflow_input(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertTrue(response["data"]["valid"])

    def test_handle_validate_workflow_input_missing_fields(self):
        """测试验证缺失字段"""
        request_data = {
            "workflow_id": "market.government.proposal",
            "provided_input": {"region": "北京"}
        }

        response = self.handler.handle_validate_workflow_input(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertFalse(response["data"]["valid"])
        self.assertIn("missing_fields", response["data"])

    def test_handle_get_workflow_summary(self):
        """测试获取工作流摘要"""
        request_data = {
            "workflow_id": "market.sales.pipeline-review"
        }

        response = self.handler.handle_get_workflow_summary(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertTrue(response["data"]["available"])
        self.assertEqual(response["data"]["play_id"], "sales_review")

    def test_handle_get_adoption_rate(self):
        """测试获取采纳率"""
        # 创建一些建议并接受
        from agent_platform.signal_engine import OverdueActionRule

        rule = OverdueActionRule()
        for i in range(3):
            account_data = {
                "account_id": f"acc_{i:03d}",
                "account_name": f"客户{i}",
                "open_actions": [
                    {
                        "action_id": f"act_{i:03d}",
                        "due_at": (datetime.utcnow() - timedelta(days=5)).isoformat()
                    }
                ]
            }
            signal = rule.evaluate(account_data)[0]
            rec = self.handler.integration.recommender.generate_from_signal(signal, {})

            # 接受第一个
            if i == 0:
                self.handler.integration.recommender.accept_recommendation(rec.recommendation_id)

        # 获取采纳率
        request_data = {}
        response = self.handler.handle_get_adoption_rate(request_data)

        # 验证响应
        self.assertTrue(response["success"])
        self.assertIn("total", response["data"])
        self.assertIn("adoption_rate", response["data"])

    def test_create_api_handler_factory(self):
        """测试工厂函数"""
        handler = create_api_handler()

        # 验证创建成功
        self.assertIsInstance(handler, A4ApiHandler)
        self.assertIsNotNone(handler.integration)


if __name__ == "__main__":
    unittest.main()
