"""
Agent4Market 阶段 A4: 行动建议生成器

基于客户信号和上下文生成可执行的下一步行动建议。
用户可以：接受、编辑后接受、忽略建议。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from agent_platform.a4_store import A4Store
from agent_platform.signal_engine import Signal, SignalType

ActionStatus = Literal["pending", "accepted", "edited", "ignored"]
ActionPriority = Literal["high", "medium", "low"]


@dataclass
class ActionRecommendation:
    """行动建议"""
    recommendation_id: str
    account_id: str
    account_name: str
    signal_id: str | None  # 触发建议的信号ID（如果有）
    signal_type: SignalType | None

    title: str
    description: str
    priority: ActionPriority
    suggested_actions: list[str]
    context: dict[str, Any]  # 生成建议的上下文

    created_at: str  # ISO 8601
    status: ActionStatus = "pending"
    user_feedback: str | None = None
    accepted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "suggested_actions": self.suggested_actions,
            "context": self.context,
            "created_at": self.created_at,
            "status": self.status,
            "user_feedback": self.user_feedback,
            "accepted_at": self.accepted_at,
        }


@dataclass
class AcceptedAction:
    """用户接受的行动"""
    action_id: str
    recommendation_id: str
    account_id: str
    title: str
    description: str
    priority: ActionPriority
    due_at: str | None  # ISO 8601
    assignee: str | None
    created_from_signal: bool
    accepted_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "recommendation_id": self.recommendation_id,
            "account_id": self.account_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "due_at": self.due_at,
            "assignee": self.assignee,
            "created_from_signal": self.created_from_signal,
            "accepted_at": self.accepted_at,
        }


class ActionRecommender:
    """行动建议生成器

    默认使用内存存储。传入 store 时改为写穿模式：内存作为读缓存，
    每次状态变更同步落盘，进程重启后从磁盘恢复。
    """

    def __init__(self, store: A4Store | None = None):
        self.recommendations: dict[str, ActionRecommendation] = {}
        self.store = store
        if store is not None:
            self._load_from_store()

    def _load_from_store(self) -> None:
        """从存储恢复建议到内存缓存"""
        assert self.store is not None
        for row in self.store.load_all_recommendations():
            self.recommendations[row["recommendation_id"]] = ActionRecommendation(
                recommendation_id=row["recommendation_id"],
                account_id=row["account_id"],
                account_name=row["account_name"],
                signal_id=row["signal_id"],
                signal_type=row["signal_type"],
                title=row["title"],
                description=row["description"],
                priority=row["priority"],
                suggested_actions=row["suggested_actions"],
                context=row["context"],
                created_at=row["created_at"],
                status=row["status"],
                user_feedback=row["user_feedback"],
                accepted_at=row["accepted_at"],
            )

    def _persist(self, recommendation: ActionRecommendation) -> None:
        """写穿到存储（未配置 store 时为空操作）"""
        if self.store is not None:
            self.store.save_recommendation(recommendation.to_dict())

    def generate_from_signal(self, signal: Signal, account_context: dict[str, Any]) -> ActionRecommendation:
        """
        基于信号生成行动建议

        Args:
            signal: 触发的客户信号
            account_context: 客户上下文（用于生成更具体的建议）

        Returns:
            行动建议
        """
        recommendation_id = f"rec_{uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()

        # 根据信号类型生成建议
        if signal.signal_type == "overdue_action":
            title = f"处理逾期行动：{signal.evidence.get('action_title', '未命名')}"
            description = f"该行动已逾期 {signal.evidence['overdue_days']} 天，请尽快处理"
            suggested_actions = [
                "更新行动状态为已完成",
                "如仍需继续，调整截止时间并说明原因",
                "如不再必要，取消行动并记录原因"
            ]

        elif signal.signal_type == "long_inactive":
            title = f"恢复客户联系：{signal.account_name}"
            description = f"客户已 {signal.evidence['inactive_days']} 天无有效互动"
            suggested_actions = [
                "电话或邮件主动联系客户",
                "了解客户当前项目进展和需求变化",
                "评估是否需要调整跟进策略",
                "如客户暂无需求，记录原因并调整优先级"
            ]

        elif signal.signal_type == "commitment_due":
            title = f"跟进临期承诺：{signal.evidence.get('commitment_title', '未命名')}"
            description = f"承诺将在 {signal.evidence['days_until_due']} 天后到期"
            suggested_actions = [
                "确认承诺履行进度和完成度",
                "如有风险，立即与客户沟通调整预期",
                "准备交付材料和验收标准",
                "安排交付后的跟进计划"
            ]

        else:
            # 通用建议
            title = f"关注客户：{signal.account_name}"
            description = signal.description
            suggested_actions = signal.suggested_actions

        recommendation = ActionRecommendation(
            recommendation_id=recommendation_id,
            account_id=signal.account_id,
            account_name=signal.account_name,
            signal_id=signal.signal_id,
            signal_type=signal.signal_type,
            title=title,
            description=description,
            priority=signal.severity,  # type: ignore
            suggested_actions=suggested_actions,
            context={
                "signal_evidence": signal.evidence,
                "account_stage": account_context.get("stage"),
                "account_health": account_context.get("health_score"),
            },
            created_at=now,
        )

        self.recommendations[recommendation_id] = recommendation
        self._persist(recommendation)
        return recommendation

    def generate_proactive_recommendation(
        self,
        account_data: dict[str, Any]
    ) -> ActionRecommendation | None:
        """
        生成主动性建议（不基于信号）

        例如：
        - 定期复盘建议
        - 机会推进建议
        - 资源需求建议
        """
        # 检查是否有开放机会且长期未更新
        opportunities = account_data.get("opportunities", [])
        for opp in opportunities:
            if opp.get("stage") == "negotiation" and not opp.get("last_updated_at"):
                recommendation_id = f"rec_{uuid4().hex[:12]}"
                now = datetime.utcnow().isoformat()

                recommendation = ActionRecommendation(
                    recommendation_id=recommendation_id,
                    account_id=account_data["account_id"],
                    account_name=account_data["account_name"],
                    signal_id=None,
                    signal_type=None,
                    title=f"推进商机：{opp.get('name', '未命名')}",
                    description=f"商机处于谈判阶段，建议主动推进",
                    priority="medium",
                    suggested_actions=[
                        "了解当前决策进展",
                        "识别阻塞因素",
                        "制定推进计划"
                    ],
                    context={
                        "opportunity_id": opp.get("opportunity_id"),
                        "opportunity_stage": opp.get("stage"),
                    },
                    created_at=now,
                )

                self.recommendations[recommendation_id] = recommendation
                self._persist(recommendation)
                return recommendation

        return None

    def accept_recommendation(
        self,
        recommendation_id: str,
        user_edits: dict[str, Any] | None = None,
        assignee: str | None = None,
        due_at: str | None = None
    ) -> AcceptedAction:
        """
        接受建议并转换为行动

        Args:
            recommendation_id: 建议ID
            user_edits: 用户修改（标题/描述等）
            assignee: 指定负责人
            due_at: 截止时间

        Returns:
            已接受的行动
        """
        recommendation = self.recommendations.get(recommendation_id)
        if not recommendation:
            raise ValueError(f"Recommendation {recommendation_id} not found")

        now = datetime.utcnow().isoformat()

        # 应用用户编辑
        if user_edits:
            recommendation.status = "edited"
            title = user_edits.get("title", recommendation.title)
            description = user_edits.get("description", recommendation.description)
        else:
            recommendation.status = "accepted"
            title = recommendation.title
            description = recommendation.description

        recommendation.accepted_at = now

        # 创建行动
        action = AcceptedAction(
            action_id=f"act_{uuid4().hex[:12]}",
            recommendation_id=recommendation_id,
            account_id=recommendation.account_id,
            title=title,
            description=description,
            priority=recommendation.priority,
            due_at=due_at,
            assignee=assignee,
            created_from_signal=recommendation.signal_id is not None,
            accepted_at=now,
        )

        self._persist(recommendation)
        if self.store is not None:
            self.store.save_accepted_action(action.to_dict())

        return action

    def ignore_recommendation(
        self,
        recommendation_id: str,
        reason: str | None = None
    ) -> None:
        """
        忽略建议

        Args:
            recommendation_id: 建议ID
            reason: 忽略原因（用于后续优化）
        """
        recommendation = self.recommendations.get(recommendation_id)
        if not recommendation:
            raise ValueError(f"Recommendation {recommendation_id} not found")

        recommendation.status = "ignored"
        recommendation.user_feedback = reason
        self._persist(recommendation)

    def get_pending_recommendations(
        self,
        account_id: str | None = None
    ) -> list[ActionRecommendation]:
        """
        获取待处理的建议

        Args:
            account_id: 可选，只获取特定客户的建议

        Returns:
            待处理建议列表，按优先级排序
        """
        pending = [
            rec for rec in self.recommendations.values()
            if rec.status == "pending" and (account_id is None or rec.account_id == account_id)
        ]

        # 按优先级排序
        priority_order = {"high": 0, "medium": 1, "low": 2}
        pending.sort(key=lambda r: priority_order.get(r.priority, 3))

        return pending

    def get_adoption_rate(self) -> dict[str, Any]:
        """
        计算建议采纳率

        Returns:
            采纳率统计
        """
        if self.store is not None:
            return self.store.get_adoption_stats()

        total = len(self.recommendations)
        if total == 0:
            return {
                "total": 0,
                "accepted": 0,
                "edited": 0,
                "ignored": 0,
                "pending": 0,
                "adoption_rate": 0.0,
            }

        accepted = sum(1 for r in self.recommendations.values() if r.status == "accepted")
        edited = sum(1 for r in self.recommendations.values() if r.status == "edited")
        ignored = sum(1 for r in self.recommendations.values() if r.status == "ignored")
        pending = sum(1 for r in self.recommendations.values() if r.status == "pending")

        # 采纳率 = (接受 + 编辑后接受) / (总数 - 待处理)
        processed = total - pending
        adoption_rate = (accepted + edited) / processed if processed > 0 else 0.0

        return {
            "total": total,
            "accepted": accepted,
            "edited": edited,
            "ignored": ignored,
            "pending": pending,
            "adoption_rate": adoption_rate,
        }

    def export_recommendations(self, output_path: Path) -> None:
        """导出建议历史（用于分析和优化）"""
        data = [rec.to_dict() for rec in self.recommendations.values()]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def create_recommender() -> ActionRecommender:
    """创建行动建议生成器"""
    return ActionRecommender()
