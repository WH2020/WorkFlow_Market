"""
测试工作流集成器
"""

import unittest
from agent_platform.workflow_integration import (
    WorkflowIntegration,
    create_integration,
)


class TestWorkflowIntegration(unittest.TestCase):
    """测试工作流集成器"""

    def setUp(self):
        self.integration = WorkflowIntegration()

    def test_prepare_workflow_context_with_complete_input(self):
        """测试准备完整工作流上下文"""
        account_data = {
            "account_id": "acc_001",
            "account_name": "测试客户A",
            "open_actions": [],
            "last_effective_activity_at": "2026-08-25T10:00:00",
        }

        result = self.integration.prepare_workflow_context(
            user_input="客户复盘",
            account_data=account_data
        )

        # 验证返回完整上下文
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["play_id"], "sales_review")
        self.assertEqual(result["workflow_id"], "market.sales.pipeline-review")
        self.assertIn("execution_plan", result)
        self.assertIn("signals", result)
        self.assertIn("recommendations", result)

    def test_prepare_workflow_context_needs_more_info(self):
        """测试缺失信息时的补问"""
        result = self.integration.prepare_workflow_context(
            user_input="我想做客户复盘",
            account_data=None
        )

        # 验证返回补问状态
        self.assertEqual(result["status"], "need_more_info")
        self.assertEqual(result["play_id"], "sales_review")
        self.assertIn("questions", result)
        self.assertIn("missing_fields", result)
        self.assertIn("account_id", result["missing_fields"])

    def test_prepare_workflow_context_no_match(self):
        """测试无匹配时的处理"""
        result = self.integration.prepare_workflow_context(
            user_input="今天天气真好",
            account_data=None
        )

        # 验证返回无匹配状态
        self.assertEqual(result["status"], "no_match")
        self.assertIn("suggestions", result)

    def test_enrich_workflow_input_with_recommendations(self):
        """测试为工作流输入添加建议"""
        # 先创建一些建议
        from agent_platform.signal_engine import OverdueActionRule
        from datetime import datetime, timedelta

        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_002",
            "account_name": "测试客户B",
            "open_actions": [
                {
                    "action_id": "act_001",
                    "due_at": (datetime.utcnow() - timedelta(days=5)).isoformat(),
                }
            ]
        }
        signal = rule.evaluate(account_data)[0]
        self.integration.recommender.generate_from_signal(signal, {})

        # 增强工作流输入
        base_input = {
            "account_id": "acc_002",
            "focus_area": "risk"
        }

        enriched = self.integration.enrich_workflow_input(
            "market.sales.pipeline-review",
            base_input
        )

        # 验证添加了建议上下文
        self.assertIn("context", enriched)
        self.assertIn("pending_recommendations", enriched["context"])
        self.assertGreater(len(enriched["context"]["pending_recommendations"]), 0)

    def test_enrich_workflow_input_without_account(self):
        """测试无客户时不添加建议"""
        base_input = {"research_topic": "数字化转型"}

        enriched = self.integration.enrich_workflow_input(
            "shared.research.frontier-subagent",
            base_input
        )

        # 验证输入未被修改
        self.assertEqual(enriched, base_input)

    def test_get_workflow_summary_for_known_workflow(self):
        """测试获取已知工作流摘要"""
        summary = self.integration.get_workflow_summary("market.sales.pipeline-review")

        # 验证摘要信息
        self.assertTrue(summary["available"])
        self.assertEqual(summary["play_id"], "sales_review")
        self.assertEqual(summary["name"], "客户推进与销售复盘")
        self.assertIn("required_context", summary)
        self.assertIn("estimated_duration", summary)

    def test_get_workflow_summary_for_unknown_workflow(self):
        """测试获取未知工作流摘要"""
        summary = self.integration.get_workflow_summary("unknown.workflow")

        # 验证返回不可用
        self.assertFalse(summary["available"])
        self.assertIn("reason", summary)

    def test_validate_workflow_input_with_complete_data(self):
        """测试验证完整输入"""
        result = self.integration.validate_workflow_input(
            "market.sales.pipeline-review",
            {"account_id": "acc_001"}
        )

        # 验证通过
        self.assertTrue(result["valid"])
        self.assertEqual(result["workflow_id"], "market.sales.pipeline-review")

    def test_validate_workflow_input_with_missing_fields(self):
        """测试验证缺失字段"""
        result = self.integration.validate_workflow_input(
            "market.government.proposal",
            {"region": "北京市"}
        )

        # 验证不通过
        self.assertFalse(result["valid"])
        self.assertIn("missing_fields", result)
        self.assertIn("cooperation_type", result["missing_fields"])
        self.assertIn("questions", result)

    def test_validate_workflow_input_for_unknown_workflow(self):
        """测试验证未知工作流"""
        result = self.integration.validate_workflow_input(
            "unknown.workflow",
            {"field": "value"}
        )

        # 验证不通过
        self.assertFalse(result["valid"])
        self.assertIn("reason", result)

    def test_workflow_to_play_mapping(self):
        """测试所有工作流ID都能映射到Play"""
        workflow_ids = [
            "market.sales.pipeline-review",
            "market.government.proposal",
            "shared.research.frontier-subagent",
            "shared.presentation.studio",
        ]

        for workflow_id in workflow_ids:
            summary = self.integration.get_workflow_summary(workflow_id)
            self.assertTrue(
                summary["available"],
                f"Workflow {workflow_id} should be available"
            )
            self.assertIn("play_id", summary)

    def test_integration_with_signals_and_recommendations(self):
        """测试完整集成流程：信号→建议→工作流"""
        from datetime import datetime, timedelta

        # 1. 准备有问题的客户数据
        account_data = {
            "account_id": "acc_003",
            "account_name": "测试客户C",
            "open_actions": [
                {
                    "action_id": "act_002",
                    "due_at": (datetime.utcnow() - timedelta(days=10)).isoformat(),
                }
            ],
            "last_effective_activity_at": (datetime.utcnow() - timedelta(days=50)).isoformat(),
        }

        # 2. 准备工作流上下文
        result = self.integration.prepare_workflow_context(
            "客户复盘",
            account_data
        )

        # 3. 验证完整流程
        self.assertEqual(result["status"], "ready")
        self.assertGreater(len(result["signals"]), 0)  # 应该有信号
        self.assertGreater(len(result["recommendations"]), 0)  # 应该有建议

        # 4. 获取第一个建议
        first_rec = result["recommendations"][0]
        self.assertIn("recommendation_id", first_rec)
        self.assertIn("title", first_rec)
        self.assertIn("priority", first_rec)

    def test_create_integration_factory(self):
        """测试工厂函数"""
        integration = create_integration()

        # 验证创建成功
        self.assertIsInstance(integration, WorkflowIntegration)
        self.assertIsNotNone(integration.signal_engine)
        self.assertIsNotNone(integration.recommender)
        self.assertIsNotNone(integration.play_matcher)


if __name__ == "__main__":
    unittest.main()
