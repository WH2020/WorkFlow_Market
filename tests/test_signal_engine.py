"""
测试信号引擎
"""

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.signal_engine import (
    SignalEngine,
    OverdueActionRule,
    LongInactiveRule,
    CommitmentDueRule,
    create_engine,
)


class TestSignalEngine(unittest.TestCase):
    """测试信号引擎核心功能"""

    def setUp(self):
        self.engine = SignalEngine()
        self.now = datetime.utcnow()

    def test_overdue_action_rule(self):
        """测试逾期行动规则"""
        rule = OverdueActionRule()

        # 准备测试数据：一个逾期10天的行动
        overdue_date = (self.now - timedelta(days=10)).isoformat()
        account_data = {
            "account_id": "acc_001",
            "account_name": "测试客户A",
            "open_actions": [
                {
                    "action_id": "act_001",
                    "title": "跟进合同签订",
                    "due_at": overdue_date,
                    "status": "open"
                }
            ]
        }

        signals = rule.evaluate(account_data)

        # 验证
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_type, "overdue_action")
        self.assertEqual(signal.severity, "high")  # 超过7天为高优先级
        self.assertIn("10 天", signal.title)
        self.assertEqual(signal.evidence["overdue_days"], 10)

    def test_long_inactive_rule(self):
        """测试长期无互动规则"""
        rule = LongInactiveRule(threshold_days=30)

        # 准备测试数据：45天无互动
        last_activity = (self.now - timedelta(days=45)).isoformat()
        account_data = {
            "account_id": "acc_002",
            "account_name": "测试客户B",
            "last_effective_activity_at": last_activity,
        }

        signals = rule.evaluate(account_data)

        # 验证
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_type, "long_inactive")
        self.assertEqual(signal.severity, "medium")  # 45天为中等优先级
        self.assertIn("45 天", signal.title)

    def test_long_inactive_high_severity(self):
        """测试长期无互动高优先级"""
        rule = LongInactiveRule(threshold_days=30)

        # 准备测试数据：100天无互动
        last_activity = (self.now - timedelta(days=100)).isoformat()
        account_data = {
            "account_id": "acc_003",
            "account_name": "测试客户C",
            "last_effective_activity_at": last_activity,
        }

        signals = rule.evaluate(account_data)

        # 验证
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.severity, "high")  # 超过90天为高优先级

    def test_commitment_due_rule(self):
        """测试承诺临期规则"""
        rule = CommitmentDueRule(warning_days=7)

        # 准备测试数据：3天后到期的承诺
        due_date = (self.now + timedelta(days=3)).isoformat()
        account_data = {
            "account_id": "acc_004",
            "account_name": "测试客户D",
            "open_commitments": [
                {
                    "commitment_id": "com_001",
                    "title": "交付POC演示",
                    "due_at": due_date,
                    "status": "open"
                }
            ]
        }

        signals = rule.evaluate(account_data)

        # 验证
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_type, "commitment_due")
        self.assertEqual(signal.severity, "high")  # 3天内为高优先级
        self.assertEqual(signal.evidence["days_until_due"], 3)

    def test_timezone_aware_iso_dates_are_normalized_without_dropping_signals(self):
        now = datetime.now(timezone.utc)
        account_data = {
            "account_id": "acc_tz",
            "account_name": "时区测试客户",
            "open_actions": [{
                "action_id": "act_tz",
                "title": "UTC 行动",
                "due_at": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            }],
            "last_effective_activity_at": (
                now - timedelta(days=40)
            ).astimezone(timezone(timedelta(hours=8))).isoformat(),
            "open_commitments": [{
                "commitment_id": "com_tz",
                "title": "东八区承诺",
                "due_at": (now + timedelta(days=3)).astimezone(
                    timezone(timedelta(hours=8))
                ).isoformat(),
            }],
        }

        signals = SignalEngine().evaluate_account(account_data)

        self.assertEqual(
            {"overdue_action", "long_inactive", "commitment_due"},
            {signal.signal_type for signal in signals},
        )
        commitment = next(
            signal for signal in signals if signal.signal_type == "commitment_due"
        )
        self.assertEqual(3, commitment.evidence["days_until_due"])

    def test_evaluate_account(self):
        """测试评估单个客户"""
        # 准备包含多个问题的客户数据
        overdue_date = (self.now - timedelta(days=5)).isoformat()
        last_activity = (self.now - timedelta(days=40)).isoformat()

        account_data = {
            "account_id": "acc_005",
            "account_name": "测试客户E",
            "open_actions": [
                {
                    "action_id": "act_002",
                    "title": "提交技术方案",
                    "due_at": overdue_date,
                }
            ],
            "last_effective_activity_at": last_activity,
            "open_commitments": []
        }

        signals = self.engine.evaluate_account(account_data)

        # 应该触发2个信号：逾期行动 + 长期无互动
        self.assertEqual(len(signals), 2)

        # 验证按严重程度排序（逾期5天=medium，40天无互动=medium）
        signal_types = [s.signal_type for s in signals]
        self.assertIn("overdue_action", signal_types)
        self.assertIn("long_inactive", signal_types)

    def test_evaluate_accounts_batch(self):
        """测试批量评估多个客户"""
        accounts_data = [
            {
                "account_id": "acc_001",
                "account_name": "客户A",
                "open_actions": [
                    {
                        "action_id": "act_001",
                        "due_at": (self.now - timedelta(days=10)).isoformat(),
                    }
                ]
            },
            {
                "account_id": "acc_002",
                "account_name": "客户B",
                "last_effective_activity_at": (self.now - timedelta(days=50)).isoformat(),
            },
            {
                "account_id": "acc_003",
                "account_name": "客户C",
                "open_actions": [],  # 无问题
            }
        ]

        results = self.engine.evaluate_accounts(accounts_data)

        # 应该只有2个客户有信号
        self.assertEqual(len(results), 2)
        self.assertIn("acc_001", results)
        self.assertIn("acc_002", results)
        self.assertNotIn("acc_003", results)

    def test_get_high_priority_accounts(self):
        """测试获取高优先级客户"""
        accounts_data = [
            {
                "account_id": "acc_001",
                "account_name": "客户A",
                "open_actions": [
                    {
                        "action_id": "act_001",
                        "due_at": (self.now - timedelta(days=10)).isoformat(),  # high
                    }
                ]
            },
            {
                "account_id": "acc_002",
                "account_name": "客户B",
                "last_effective_activity_at": (self.now - timedelta(days=100)).isoformat(),  # high
            },
            {
                "account_id": "acc_003",
                "account_name": "客户C",
                "last_effective_activity_at": (self.now - timedelta(days=40)).isoformat(),  # medium
            }
        ]

        priority_accounts = self.engine.get_high_priority_accounts(accounts_data, limit=2)

        # 应该返回前2个有高优先级信号的客户
        self.assertEqual(len(priority_accounts), 2)

        # 验证返回的是有高优先级信号的客户
        account_ids = [acc["account_id"] for acc, _ in priority_accounts]
        self.assertIn("acc_001", account_ids)
        self.assertIn("acc_002", account_ids)

    def test_rule_idempotency(self):
        """测试规则幂等性：相同数据多次执行应得到相同结果"""
        account_data = {
            "account_id": "acc_006",
            "account_name": "测试客户F",
            "open_actions": [
                {
                    "action_id": "act_003",
                    "due_at": (self.now - timedelta(days=5)).isoformat(),
                }
            ],
        }

        # 执行3次
        signals1 = self.engine.evaluate_account(account_data)
        signals2 = self.engine.evaluate_account(account_data)
        signals3 = self.engine.evaluate_account(account_data)

        # 验证结果一致
        self.assertEqual(len(signals1), len(signals2))
        self.assertEqual(len(signals2), len(signals3))
        self.assertEqual(signals1[0].signal_type, signals2[0].signal_type)
        self.assertEqual(signals1[0].severity, signals3[0].severity)

    def test_no_signals_for_healthy_account(self):
        """测试健康客户不触发信号"""
        account_data = {
            "account_id": "acc_007",
            "account_name": "健康客户",
            "open_actions": [
                {
                    "action_id": "act_004",
                    "due_at": (self.now + timedelta(days=10)).isoformat(),  # 未来时间
                }
            ],
            "last_effective_activity_at": (self.now - timedelta(days=5)).isoformat(),  # 最近互动
            "open_commitments": []
        }

        signals = self.engine.evaluate_account(account_data)

        # 健康客户不应该有信号
        self.assertEqual(len(signals), 0)

    def test_signal_to_dict(self):
        """测试信号序列化"""
        rule = OverdueActionRule()
        account_data = {
            "account_id": "acc_008",
            "account_name": "测试客户H",
            "open_actions": [
                {
                    "action_id": "act_005",
                    "title": "测试行动",
                    "due_at": (self.now - timedelta(days=5)).isoformat(),
                }
            ]
        }

        signals = rule.evaluate(account_data)
        signal_dict = signals[0].to_dict()

        # 验证所有必要字段都存在
        required_fields = [
            "signal_id", "signal_type", "account_id", "account_name",
            "severity", "title", "description", "triggered_at",
            "evidence", "suggested_actions", "auto_resolve_condition"
        ]
        for field in required_fields:
            self.assertIn(field, signal_dict)

    def test_create_engine_with_config(self):
        """测试使用配置创建引擎"""
        config = {
            "long_inactive_threshold_days": 60,
            "commitment_warning_days": 14,
        }

        engine = create_engine(config)

        # 验证配置已应用
        for rule in engine.rules:
            if rule.rule_id == "long_inactive":
                self.assertEqual(rule.config["threshold_days"], 60)
            elif rule.rule_id == "commitment_due":
                self.assertEqual(rule.config["warning_days"], 14)


class TestSignalRuleEdgeCases(unittest.TestCase):
    """测试边界情况"""

    def setUp(self):
        self.engine = SignalEngine()
        self.now = datetime.utcnow()

    def test_missing_due_date(self):
        """测试缺失截止时间的行动"""
        account_data = {
            "account_id": "acc_009",
            "account_name": "测试客户I",
            "open_actions": [
                {
                    "action_id": "act_006",
                    "title": "无截止时间的行动",
                    # 缺失 due_at
                }
            ]
        }

        signals = self.engine.evaluate_account(account_data)

        # 没有截止时间的行动不应触发逾期信号
        self.assertEqual(len(signals), 0)

    def test_invalid_date_format(self):
        """测试无效的日期格式"""
        account_data = {
            "account_id": "acc_010",
            "account_name": "测试客户J",
            "open_actions": [
                {
                    "action_id": "act_007",
                    "due_at": "invalid-date-format",
                }
            ]
        }

        # 不应该抛出异常
        signals = self.engine.evaluate_account(account_data)
        self.assertEqual(len(signals), 0)

    def test_empty_account_data(self):
        """测试空客户数据"""
        account_data = {
            "account_id": "acc_011",
            "account_name": "空客户",
        }

        signals = self.engine.evaluate_account(account_data)

        # 空数据不触发信号
        self.assertEqual(len(signals), 0)


if __name__ == "__main__":
    unittest.main()
