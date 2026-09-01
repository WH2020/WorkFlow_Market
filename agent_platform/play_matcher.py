"""
Agent4Market 阶段 A4: Play 匹配器

自然语言工作入口：用户说目标，系统推荐合适的Play、补问缺失信息并生成执行计划。

Play是预定义的工作流，如：
- 客户复盘
- 政府合作方案
- 行业研究
- 演示文稿制作
- 资源协调
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PlayType = Literal[
    "sales_review",
    "government_proposal",
    "industry_research",
    "presentation",
    "resource_coordination",
    "weekly_report",
    "bid_management",
]


@dataclass
class Play:
    """Play 定义"""
    play_id: str
    play_type: PlayType
    name: str
    description: str
    workflow_id: str
    skill_id: str

    # 必需的上下文字段
    required_context: list[str]
    # 可选的上下文字段
    optional_context: list[str]

    # 关键词（用于匹配）
    keywords: list[str]

    # 预估时间
    estimated_duration: str


class PlayMatcher:
    """Play 匹配器"""

    def __init__(self):
        # 定义所有可用的 Play
        self.plays: dict[str, Play] = {
            "sales_review": Play(
                play_id="sales_review",
                play_type="sales_review",
                name="客户推进与销售复盘",
                description="跟进客户阶段、关键人、风险、下一步动作和销售复盘",
                workflow_id="market.sales.pipeline-review",
                skill_id="manage-market-pipeline",
                required_context=["account_id"],
                optional_context=["time_range", "focus_area"],
                keywords=["复盘", "跟进", "客户进展", "销售阶段", "风险", "下一步"],
                estimated_duration="10-15分钟"
            ),

            "government_proposal": Play(
                play_id="government_proposal",
                play_type="government_proposal",
                name="政府合作方案",
                description="结合地方条件、公开政策与项目资源形成政府合作方案",
                workflow_id="market.government.proposal",
                skill_id="draft-government-program",
                required_context=["region", "cooperation_type"],
                optional_context=["account_id", "budget_range", "timeline"],
                keywords=["政府", "合作", "方案", "政策", "地方", "政企"],
                estimated_duration="20-30分钟"
            ),

            "industry_research": Play(
                play_id="industry_research",
                play_type="industry_research",
                name="客户与行业研究",
                description="由受控只读研究员核验公开来源，主助手综合内部资料",
                workflow_id="shared.research.frontier-subagent",
                skill_id="research-frontier-markets",
                required_context=["research_topic"],
                optional_context=["account_id", "industry", "depth"],
                keywords=["研究", "调研", "分析", "行业", "市场", "竞品"],
                estimated_duration="15-25分钟"
            ),

            "presentation": Play(
                play_id="presentation",
                play_type="presentation",
                name="销售演示文稿工作室",
                description="从需求、客户证据、大纲和逐页策划生成经确认的演示文稿",
                workflow_id="shared.presentation.studio",
                skill_id="plan-director-presentations",
                required_context=["presentation_topic", "audience"],
                optional_context=["account_id", "page_count", "style"],
                keywords=["PPT", "演示", "汇报", "展示", "幻灯片", "讲稿"],
                estimated_duration="15-20分钟"
            ),

            "resource_coordination": Play(
                play_id="resource_coordination",
                play_type="resource_coordination",
                name="资源协调申请",
                description="将客户阶段、决策链、截止时间和业务理由带入资源申请",
                workflow_id="market.sales.resource-request",
                skill_id="coordinate-resources",
                required_context=["resource_type", "reason"],
                optional_context=["account_id", "urgency", "approver"],
                keywords=["资源", "申请", "协调", "支持", "需要", "批准"],
                estimated_duration="5-10分钟"
            ),
        }

    def match_plays(
        self,
        user_input: str,
        account_context: dict[str, Any] | None = None
    ) -> list[tuple[Play, float]]:
        """
        匹配用户输入到Play

        Args:
            user_input: 用户的自然语言输入
            account_context: 当前客户上下文（如果有）

        Returns:
            (Play, 匹配分数) 列表，按分数降序排列
        """
        user_input_lower = user_input.lower()
        matches: list[tuple[Play, float]] = []

        for play in self.plays.values():
            score = 0.0

            # 关键词匹配
            keyword_matches = sum(1 for keyword in play.keywords if keyword in user_input_lower)
            if keyword_matches > 0:
                score += keyword_matches * 10  # 每个关键词匹配加10分

            # 如果有客户上下文且Play支持，加分
            if account_context and "account_id" in play.required_context + play.optional_context:
                score += 5

            # 名称匹配
            if play.name.lower() in user_input_lower:
                score += 20

            if score > 0:
                matches.append((play, score))

        # 按分数降序排列
        matches.sort(key=lambda x: x[1], reverse=True)

        return matches

    def get_missing_context(
        self,
        play: Play,
        provided_context: dict[str, Any]
    ) -> list[str]:
        """
        获取缺失的必需上下文

        Args:
            play: 选定的Play
            provided_context: 已提供的上下文

        Returns:
            缺失的上下文字段列表
        """
        missing = []
        for field in play.required_context:
            if field not in provided_context or not provided_context[field]:
                missing.append(field)
        return missing

    def generate_questions(self, missing_fields: list[str]) -> list[str]:
        """
        为缺失的上下文生成补问问题

        Args:
            missing_fields: 缺失的字段列表

        Returns:
            问题列表
        """
        field_questions = {
            "account_id": "请选择要处理的客户",
            "region": "请指定合作的地区（省/市）",
            "cooperation_type": "请说明合作类型（如：产业园区、智慧城市、数字化转型等）",
            "research_topic": "请说明研究主题或问题",
            "presentation_topic": "请说明演示文稿的主题",
            "audience": "请说明演示的受众（如：管理层、客户、合作伙伴）",
            "resource_type": "请说明需要的资源类型（如：技术支持、售前资源、市场经费）",
            "reason": "请说明申请资源的原因和用途",
        }

        questions = []
        for field in missing_fields:
            question = field_questions.get(field, f"请提供 {field}")
            questions.append(question)

        return questions

    def create_execution_plan(
        self,
        play: Play,
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        生成执行计划

        Args:
            play: 选定的Play
            context: 完整的上下文

        Returns:
            执行计划
        """
        plan = {
            "play_id": play.play_id,
            "play_name": play.name,
            "workflow_id": play.workflow_id,
            "skill_id": play.skill_id,
            "estimated_duration": play.estimated_duration,
            "context": context,
            "steps": self._generate_steps(play, context),
        }

        return plan

    def _generate_steps(self, play: Play, context: dict[str, Any]) -> list[dict[str, Any]]:
        """生成执行步骤预览"""
        # 根据Play类型生成不同的步骤
        if play.play_type == "sales_review":
            return [
                {"step": 1, "description": "读取客户数据和历史互动"},
                {"step": 2, "description": "分析客户阶段和关键人"},
                {"step": 3, "description": "识别风险和机会"},
                {"step": 4, "description": "生成下一步行动建议"},
                {"step": 5, "description": "形成复盘报告并等待审批"},
            ]

        elif play.play_type == "government_proposal":
            return [
                {"step": 1, "description": f"研究{context.get('region', '')}地方政策"},
                {"step": 2, "description": "核验公开来源（受控Subagent）"},
                {"step": 3, "description": "结合内部资源形成方案"},
                {"step": 4, "description": "复核方案合理性"},
                {"step": 5, "description": "生成正式文档并等待审批"},
            ]

        elif play.play_type == "industry_research":
            return [
                {"step": 1, "description": "明确研究范围和关键问题"},
                {"step": 2, "description": "公开研究员搜索和核验来源"},
                {"step": 3, "description": "综合内部资料和专家知识"},
                {"step": 4, "description": "形成研究报告"},
                {"step": 5, "description": "等待审批并写入资料库"},
            ]

        elif play.play_type == "presentation":
            return [
                {"step": 1, "description": "明确演示需求和受众"},
                {"step": 2, "description": "搜集客户证据和资料"},
                {"step": 3, "description": "生成大纲和逐页策划"},
                {"step": 4, "description": "构建PPTX并渲染验证"},
                {"step": 5, "description": "等待审批后输出"},
            ]

        elif play.play_type == "resource_coordination":
            return [
                {"step": 1, "description": "明确资源需求和紧急程度"},
                {"step": 2, "description": "准备申请理由和客户背景"},
                {"step": 3, "description": "生成资源申请单"},
                {"step": 4, "description": "等待审批并跟进"},
            ]

        else:
            return [
                {"step": 1, "description": "准备任务输入"},
                {"step": 2, "description": "执行工作流"},
                {"step": 3, "description": "生成产出"},
                {"step": 4, "description": "等待审批"},
            ]

    def process_user_intent(
        self,
        user_input: str,
        account_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        处理用户意图的完整流程

        Args:
            user_input: 用户输入
            account_context: 当前客户上下文

        Returns:
            处理结果，包含匹配的Play、缺失字段、补问问题等
        """
        # 匹配Play
        matches = self.match_plays(user_input, account_context)

        if not matches:
            return {
                "status": "no_match",
                "message": "未找到匹配的工作流，请尝试更明确的描述",
                "suggestions": [
                    "客户复盘",
                    "政府合作方案",
                    "行业研究",
                    "制作演示文稿",
                    "申请资源支持"
                ]
            }

        # 取得分最高的Play
        best_play, score = matches[0]

        # 准备上下文
        context = account_context.copy() if account_context else {}

        # 检查缺失字段
        missing = self.get_missing_context(best_play, context)

        if missing:
            questions = self.generate_questions(missing)
            return {
                "status": "need_more_info",
                "play": best_play.name,
                "play_id": best_play.play_id,
                "match_score": score,
                "missing_fields": missing,
                "questions": questions,
                "alternatives": [
                    {"play": p.name, "score": s}
                    for p, s in matches[1:3]  # 显示前2个备选
                ] if len(matches) > 1 else []
            }

        # 生成执行计划
        plan = self.create_execution_plan(best_play, context)

        return {
            "status": "ready",
            "play": best_play.name,
            "play_id": best_play.play_id,
            "match_score": score,
            "execution_plan": plan,
            "alternatives": [
                {"play": p.name, "score": s}
                for p, s in matches[1:3]  # 显示前2个备选
            ] if len(matches) > 1 else []
        }


def create_play_matcher() -> PlayMatcher:
    """创建Play匹配器"""
    return PlayMatcher()
