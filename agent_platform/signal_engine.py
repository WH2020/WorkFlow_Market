"""
Agent4Market 阶段 A4: 信号引擎

负责从客户数据中识别需要关注的信号：
- 逾期行动
- 长期无互动
- 承诺临期
- 关键字段缺失
- 资源申请临期

每个信号包含：触发条件、严重程度、建议行动和自动解除条件
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

SignalType = Literal[
    "overdue_action",
    "long_inactive",
    "commitment_due",
    "missing_fields",
    "resource_due"
]

SignalSeverity = Literal["high", "medium", "low"]


def _utc_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp and normalize legacy naive values to UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class Signal:
    """客户信号"""
    signal_id: str
    signal_type: SignalType
    account_id: str
    account_name: str
    severity: SignalSeverity
    title: str
    description: str
    triggered_at: str  # ISO 8601
    evidence: dict[str, Any]
    suggested_actions: list[str]
    auto_resolve_condition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "triggered_at": self.triggered_at,
            "evidence": self.evidence,
            "suggested_actions": self.suggested_actions,
            "auto_resolve_condition": self.auto_resolve_condition,
        }


@dataclass
class SignalRule:
    """信号规则定义"""
    rule_id: str
    signal_type: SignalType
    name: str
    description: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    def evaluate(self, account_data: dict[str, Any]) -> list[Signal]:
        """评估规则，返回触发的信号列表"""
        raise NotImplementedError("Subclass must implement evaluate()")


class OverdueActionRule(SignalRule):
    """逾期行动规则"""

    def __init__(self):
        super().__init__(
            rule_id="overdue_action",
            signal_type="overdue_action",
            name="逾期行动",
            description="识别已过截止时间但未完成的行动",
        )

    def evaluate(self, account_data: dict[str, Any]) -> list[Signal]:
        signals = []
        now = datetime.now(timezone.utc)

        open_actions = account_data.get("open_actions", [])
        for action in open_actions:
            due_at = action.get("due_at")
            if not due_at:
                continue

            due_datetime = _utc_datetime(due_at)
            if due_datetime is None:
                continue

            if due_datetime < now:
                overdue_days = (now - due_datetime).days
                severity: SignalSeverity = "high" if overdue_days > 7 else "medium"

                signal = Signal(
                    signal_id=f"overdue_{action['action_id']}",
                    signal_type="overdue_action",
                    account_id=account_data["account_id"],
                    account_name=account_data["account_name"],
                    severity=severity,
                    title=f"行动逾期 {overdue_days} 天",
                    description=f"行动「{action.get('title', '未命名')}」已逾期 {overdue_days} 天",
                    triggered_at=now.isoformat(),
                    evidence={
                        "action_id": action["action_id"],
                        "action_title": action.get("title"),
                        "due_at": due_at,
                        "overdue_days": overdue_days,
                    },
                    suggested_actions=[
                        "更新行动状态为已完成",
                        "调整截止时间",
                        "取消行动并说明原因"
                    ],
                    auto_resolve_condition="行动完成、取消或修改截止时间"
                )
                signals.append(signal)

        return signals


class LongInactiveRule(SignalRule):
    """长期无互动规则"""

    def __init__(self, threshold_days: int = 30):
        super().__init__(
            rule_id="long_inactive",
            signal_type="long_inactive",
            name="长期无互动",
            description=f"识别超过 {threshold_days} 天无有效互动的客户",
            config={"threshold_days": threshold_days}
        )

    def evaluate(self, account_data: dict[str, Any]) -> list[Signal]:
        signals = []
        now = datetime.now(timezone.utc)
        threshold_days = self.config["threshold_days"]

        last_activity_at = account_data.get("last_effective_activity_at")
        if not last_activity_at:
            return signals

        last_datetime = _utc_datetime(last_activity_at)
        if last_datetime is None:
            return signals

        inactive_days = (now - last_datetime).days

        if inactive_days > threshold_days:
            severity: SignalSeverity = "high" if inactive_days > 90 else "medium"

            signal = Signal(
                signal_id=f"inactive_{account_data['account_id']}",
                signal_type="long_inactive",
                account_id=account_data["account_id"],
                account_name=account_data["account_name"],
                severity=severity,
                title=f"{inactive_days} 天无互动",
                description=f"客户最后有效互动距今 {inactive_days} 天",
                triggered_at=now.isoformat(),
                evidence={
                    "last_activity_at": last_activity_at,
                    "inactive_days": inactive_days,
                    "threshold_days": threshold_days,
                },
                suggested_actions=[
                    "主动联系客户了解现状",
                    "评估是否继续跟进",
                    "记录客户状态变更原因"
                ],
                auto_resolve_condition="新增有效互动"
            )
            signals.append(signal)

        return signals


class CommitmentDueRule(SignalRule):
    """承诺临期规则"""

    def __init__(self, warning_days: int = 7):
        super().__init__(
            rule_id="commitment_due",
            signal_type="commitment_due",
            name="承诺临期",
            description=f"识别距截止时间小于 {warning_days} 天的未履行承诺",
            config={"warning_days": warning_days}
        )

    def evaluate(self, account_data: dict[str, Any]) -> list[Signal]:
        signals = []
        now = datetime.now(timezone.utc)
        warning_days = self.config["warning_days"]

        open_commitments = account_data.get("open_commitments", [])
        for commitment in open_commitments:
            due_at = commitment.get("due_at")
            if not due_at:
                continue

            due_datetime = _utc_datetime(due_at)
            if due_datetime is None:
                continue

            seconds_until_due = (due_datetime - now).total_seconds()
            if seconds_until_due < 0:
                continue
            # A deadline three days from the caller's observation remains
            # "3 days away" even though a few microseconds elapse before this
            # rule evaluates it.  Floor division made that boundary flaky.
            days_until_due = math.ceil(seconds_until_due / 86_400)

            if 0 <= days_until_due <= warning_days:
                severity: SignalSeverity = "high" if days_until_due <= 3 else "medium"

                signal = Signal(
                    signal_id=f"commitment_{commitment['commitment_id']}",
                    signal_type="commitment_due",
                    account_id=account_data["account_id"],
                    account_name=account_data["account_name"],
                    severity=severity,
                    title=f"承诺 {days_until_due} 天内到期",
                    description=f"承诺「{commitment.get('title', '未命名')}」将在 {days_until_due} 天后到期",
                    triggered_at=now.isoformat(),
                    evidence={
                        "commitment_id": commitment["commitment_id"],
                        "commitment_title": commitment.get("title"),
                        "due_at": due_at,
                        "days_until_due": days_until_due,
                    },
                    suggested_actions=[
                        "确认承诺履行进度",
                        "如有延期风险，提前沟通",
                        "准备交付材料"
                    ],
                    auto_resolve_condition="承诺履行、取消或延期"
                )
                signals.append(signal)

        return signals


class SignalEngine:
    """信号引擎 - 负责评估所有规则并生成信号"""

    def __init__(self):
        self.rules: list[SignalRule] = [
            OverdueActionRule(),
            LongInactiveRule(threshold_days=30),
            CommitmentDueRule(warning_days=7),
        ]

    def add_rule(self, rule: SignalRule) -> None:
        """添加自定义规则"""
        self.rules.append(rule)

    def evaluate_account(self, account_data: dict[str, Any]) -> list[Signal]:
        """
        评估单个客户，返回所有触发的信号

        Args:
            account_data: 客户数据字典，包含:
                - account_id: 客户ID
                - account_name: 客户名称
                - open_actions: 开放行动列表
                - last_effective_activity_at: 最后有效互动时间
                - open_commitments: 开放承诺列表

        Returns:
            触发的信号列表，按严重程度排序
        """
        all_signals: list[Signal] = []

        for rule in self.rules:
            if not rule.enabled:
                continue

            try:
                signals = rule.evaluate(account_data)
                all_signals.extend(signals)
            except Exception as e:
                # 规则执行失败不应该中断整个评估流程
                print(f"Rule {rule.rule_id} failed: {e}")
                continue

        # 按严重程度排序: high > medium > low
        severity_order = {"high": 0, "medium": 1, "low": 2}
        all_signals.sort(key=lambda s: severity_order.get(s.severity, 3))

        return all_signals

    def evaluate_accounts(self, accounts_data: list[dict[str, Any]]) -> dict[str, list[Signal]]:
        """
        批量评估多个客户

        Returns:
            字典，键为客户ID，值为该客户的信号列表
        """
        results = {}

        for account_data in accounts_data:
            account_id = account_data.get("account_id")
            if not account_id:
                continue

            signals = self.evaluate_account(account_data)
            if signals:
                results[account_id] = signals

        return results

    def get_high_priority_accounts(
        self,
        accounts_data: list[dict[str, Any]],
        limit: int = 10
    ) -> list[tuple[dict[str, Any], list[Signal]]]:
        """
        获取需要优先关注的客户

        Returns:
            (客户数据, 信号列表) 元组的列表，按高优先级信号数量排序
        """
        account_signals = []

        for account_data in accounts_data:
            signals = self.evaluate_account(account_data)
            if signals:
                high_signals = [s for s in signals if s.severity == "high"]
                account_signals.append((account_data, signals, len(high_signals)))

        # 按高优先级信号数量排序
        account_signals.sort(key=lambda x: x[2], reverse=True)

        return [(account, signals) for account, signals, _ in account_signals[:limit]]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """加载信号引擎配置"""
    if config_path is None:
        config_path = Path(__file__).parent / "signals_config.json"

    if not config_path.exists():
        return {
            "long_inactive_threshold_days": 30,
            "commitment_warning_days": 7,
        }

    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def create_engine(config: dict[str, Any] | None = None) -> SignalEngine:
    """创建配置好的信号引擎"""
    if config is None:
        config = load_config()

    engine = SignalEngine()

    # 根据配置更新规则
    for rule in engine.rules:
        if rule.rule_id == "long_inactive":
            rule.config["threshold_days"] = config.get("long_inactive_threshold_days", 30)
        elif rule.rule_id == "commitment_due":
            rule.config["warning_days"] = config.get("commitment_warning_days", 7)

    return engine
