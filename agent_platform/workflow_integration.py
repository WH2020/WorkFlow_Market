"""
Agent4Market 阶段 A4: 工作流集成器

连接信号引擎、行动建议和Play匹配器到现有的DAG工作流系统。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.a4_store import A4Store
from agent_platform.signal_engine import SignalEngine, create_engine
from agent_platform.action_recommender import ActionRecommender
from agent_platform.play_matcher import PlayMatcher, Play


@dataclass
class WorkflowContext:
    """工作流执行上下文"""
    workflow_id: str
    play_id: str
    account_id: str | None
    user_input: str
    provided_context: dict[str, Any]
    signals: list[Any] | None = None
    recommendations: list[Any] | None = None


class WorkflowIntegration:
    """工作流集成器 - 连接A4组件到DAG执行引擎"""

    def __init__(
        self,
        signal_engine: SignalEngine | None = None,
        recommender: ActionRecommender | None = None,
        play_matcher: PlayMatcher | None = None,
        store: A4Store | None = None
    ):
        self.signal_engine = signal_engine or create_engine()
        self.recommender = recommender or ActionRecommender(store=store)
        self.play_matcher = play_matcher or PlayMatcher()

    def prepare_workflow_context(
        self,
        user_input: str,
        account_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        准备工作流执行上下文

        Args:
            user_input: 用户的自然语言输入
            account_data: 当前客户数据（如果有）

        Returns:
            包含Play匹配、信号和建议的完整上下文
        """
        # 1. 匹配Play
        account_context = {"account_id": account_data.get("account_id")} if account_data else {}
        play_result = self.play_matcher.process_user_intent(user_input, account_context)

        if play_result["status"] == "no_match":
            return {
                "status": "no_match",
                "message": play_result["message"],
                "suggestions": play_result["suggestions"]
            }

        if play_result["status"] == "need_more_info":
            return {
                "status": "need_more_info",
                "play": play_result["play"],
                "play_id": play_result["play_id"],
                "questions": play_result["questions"],
                "missing_fields": play_result["missing_fields"]
            }

        # 2. 如果有客户数据，评估信号
        signals = []
        recommendations = []
        if account_data:
            signals = self.signal_engine.evaluate_account(account_data)

            # 3. 基于信号生成建议
            for signal in signals[:3]:  # 只显示前3个高优先级信号
                rec = self.recommender.generate_from_signal(signal, account_context)
                recommendations.append(rec)

        # 4. 返回完整上下文
        return {
            "status": "ready",
            "play": play_result["play"],
            "play_id": play_result["play_id"],
            "workflow_id": play_result["execution_plan"]["workflow_id"],
            "execution_plan": play_result["execution_plan"],
            "signals": [s.to_dict() for s in signals],
            "recommendations": [r.to_dict() for r in recommendations],
            "alternatives": play_result.get("alternatives", [])
        }

    def enrich_workflow_input(
        self,
        workflow_id: str,
        base_input: dict[str, Any]
    ) -> dict[str, Any]:
        """
        为工作流输入添加信号和建议上下文

        Args:
            workflow_id: 工作流ID
            base_input: 基础输入数据

        Returns:
            增强后的工作流输入
        """
        enriched = base_input.copy()

        # 如果输入包含account_id，添加信号和建议
        if "account_id" in base_input and base_input["account_id"]:
            account_id = base_input["account_id"]

            # 获取该客户的待处理建议
            pending_recs = self.recommender.get_pending_recommendations(account_id=account_id)

            enriched["context"] = enriched.get("context", {})
            enriched["context"]["pending_recommendations"] = [
                {
                    "recommendation_id": rec.recommendation_id,
                    "title": rec.title,
                    "priority": rec.priority
                }
                for rec in pending_recs[:5]  # 最多5个
            ]

        return enriched

    def get_workflow_summary(self, workflow_id: str) -> dict[str, Any]:
        """
        获取工作流摘要信息

        Args:
            workflow_id: 工作流ID

        Returns:
            工作流摘要
        """
        # 工作流ID到Play的映射
        workflow_to_play = {
            "market.sales.pipeline-review": "sales_review",
            "market.government.proposal": "government_proposal",
            "shared.research.frontier-subagent": "industry_research",
            "shared.presentation.studio": "presentation",
            "market.sales.resource-request": "resource_coordination",
        }

        play_id = workflow_to_play.get(workflow_id)
        if not play_id:
            return {
                "workflow_id": workflow_id,
                "available": False,
                "reason": "Workflow not mapped to a Play"
            }

        play = self.play_matcher.plays.get(play_id)
        if not play:
            return {
                "workflow_id": workflow_id,
                "available": False,
                "reason": "Play not found"
            }

        return {
            "workflow_id": workflow_id,
            "play_id": play.play_id,
            "name": play.name,
            "description": play.description,
            "required_context": play.required_context,
            "optional_context": play.optional_context,
            "estimated_duration": play.estimated_duration,
            "available": True
        }

    def validate_workflow_input(
        self,
        workflow_id: str,
        provided_input: dict[str, Any]
    ) -> dict[str, Any]:
        """
        验证工作流输入是否完整

        Args:
            workflow_id: 工作流ID
            provided_input: 提供的输入数据

        Returns:
            验证结果
        """
        summary = self.get_workflow_summary(workflow_id)

        if not summary.get("available"):
            return {
                "valid": False,
                "reason": summary.get("reason", "Unknown error")
            }

        required_fields = summary["required_context"]
        missing = []

        for field in required_fields:
            if field not in provided_input or not provided_input[field]:
                missing.append(field)

        if missing:
            questions = self.play_matcher.generate_questions(missing)
            return {
                "valid": False,
                "missing_fields": missing,
                "questions": questions
            }

        return {
            "valid": True,
            "workflow_id": workflow_id,
            "play_id": summary["play_id"]
        }


def create_integration(store: A4Store | None = None) -> WorkflowIntegration:
    """创建工作流集成器

    Args:
        store: 可选的持久化存储。传入时建议在进程重启后可恢复。
    """
    return WorkflowIntegration(store=store)
