"""
Agent4Market 阶段 A4: API 端点

为信号、建议和Play匹配提供HTTP接口。
"""

from __future__ import annotations

import json
from typing import Any

from agent_platform.workflow_integration import create_integration


class A4ApiHandler:
    """Stage A4 API 处理器"""

    def __init__(self):
        self.integration = create_integration()

    def handle_match_play(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        处理Play匹配请求

        POST /api/a4/match-play
        Body: {
            "user_input": "客户复盘",
            "account_data": {"account_id": "acc_001", ...}  # 可选
        }
        """
        user_input = request_data.get("user_input")
        if not user_input:
            return {
                "error": "user_input is required",
                "status": 400
            }

        account_data = request_data.get("account_data")

        try:
            result = self.integration.prepare_workflow_context(
                user_input=user_input,
                account_data=account_data
            )
            return {
                "success": True,
                "data": result
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_evaluate_signals(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        评估客户信号

        POST /api/a4/evaluate-signals
        Body: {
            "account_data": {"account_id": "acc_001", "open_actions": [...], ...}
        }
        """
        account_data = request_data.get("account_data")
        if not account_data:
            return {
                "error": "account_data is required",
                "status": 400
            }

        try:
            signals = self.integration.signal_engine.evaluate_account(account_data)
            return {
                "success": True,
                "data": {
                    "account_id": account_data.get("account_id"),
                    "signals": [s.to_dict() for s in signals]
                }
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_get_recommendations(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        获取待处理建议

        GET /api/a4/recommendations?account_id=acc_001
        """
        account_id = request_data.get("account_id")

        try:
            recommendations = self.integration.recommender.get_pending_recommendations(
                account_id=account_id
            )
            return {
                "success": True,
                "data": {
                    "account_id": account_id,
                    "recommendations": [r.to_dict() for r in recommendations]
                }
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_accept_recommendation(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        接受建议

        POST /api/a4/recommendations/accept
        Body: {
            "recommendation_id": "rec_xxx",
            "user_edits": {"title": "...", "description": "..."},  # 可选
            "assignee": "张三",  # 可选
            "due_at": "2026-09-10T10:00:00"  # 可选
        }
        """
        recommendation_id = request_data.get("recommendation_id")
        if not recommendation_id:
            return {
                "error": "recommendation_id is required",
                "status": 400
            }

        user_edits = request_data.get("user_edits")
        assignee = request_data.get("assignee")
        due_at = request_data.get("due_at")

        try:
            action = self.integration.recommender.accept_recommendation(
                recommendation_id=recommendation_id,
                user_edits=user_edits,
                assignee=assignee,
                due_at=due_at
            )
            return {
                "success": True,
                "data": action.to_dict()
            }
        except ValueError as e:
            return {
                "error": str(e),
                "status": 404
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_ignore_recommendation(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        忽略建议

        POST /api/a4/recommendations/ignore
        Body: {
            "recommendation_id": "rec_xxx",
            "reason": "客户已确认不需要"  # 可选
        }
        """
        recommendation_id = request_data.get("recommendation_id")
        if not recommendation_id:
            return {
                "error": "recommendation_id is required",
                "status": 400
            }

        reason = request_data.get("reason")

        try:
            self.integration.recommender.ignore_recommendation(
                recommendation_id=recommendation_id,
                reason=reason
            )
            return {
                "success": True,
                "data": {
                    "recommendation_id": recommendation_id,
                    "status": "ignored"
                }
            }
        except ValueError as e:
            return {
                "error": str(e),
                "status": 404
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_validate_workflow_input(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        验证工作流输入

        POST /api/a4/validate-workflow-input
        Body: {
            "workflow_id": "market.sales.pipeline-review",
            "provided_input": {"account_id": "acc_001", ...}
        }
        """
        workflow_id = request_data.get("workflow_id")
        if not workflow_id:
            return {
                "error": "workflow_id is required",
                "status": 400
            }

        provided_input = request_data.get("provided_input", {})

        try:
            result = self.integration.validate_workflow_input(
                workflow_id=workflow_id,
                provided_input=provided_input
            )
            return {
                "success": True,
                "data": result
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_get_workflow_summary(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        获取工作流摘要

        GET /api/a4/workflow-summary?workflow_id=market.sales.pipeline-review
        """
        workflow_id = request_data.get("workflow_id")
        if not workflow_id:
            return {
                "error": "workflow_id is required",
                "status": 400
            }

        try:
            summary = self.integration.get_workflow_summary(workflow_id)
            return {
                "success": True,
                "data": summary
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }

    def handle_get_adoption_rate(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """
        获取采纳率统计

        GET /api/a4/adoption-rate
        """
        try:
            stats = self.integration.recommender.get_adoption_rate()
            return {
                "success": True,
                "data": stats
            }
        except Exception as e:
            return {
                "error": str(e),
                "status": 500
            }


def create_api_handler() -> A4ApiHandler:
    """创建API处理器"""
    return A4ApiHandler()
