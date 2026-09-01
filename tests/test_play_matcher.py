"""
测试 Play 匹配器
"""

import unittest
from agent_platform.play_matcher import (
    PlayMatcher,
    create_play_matcher,
)


class TestPlayMatcher(unittest.TestCase):
    """测试 Play 匹配器核心功能"""

    def setUp(self):
        self.matcher = PlayMatcher()

    def test_match_sales_review(self):
        """测试匹配客户复盘"""
        user_input = "我想做个客户复盘，看看最近的跟进情况"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到复盘
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "sales_review")
        self.assertGreater(score, 0)

    def test_match_government_proposal(self):
        """测试匹配政府合作方案"""
        user_input = "需要给某市政府准备一个智慧城市合作方案"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到政府方案
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "government_proposal")

    def test_match_industry_research(self):
        """测试匹配行业研究"""
        user_input = "帮我研究一下制造业的数字化转型趋势"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到研究
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "industry_research")

    def test_match_presentation(self):
        """测试匹配演示文稿"""
        user_input = "需要做一个PPT给客户汇报"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到演示
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "presentation")

    def test_match_resource_coordination(self):
        """测试匹配资源申请"""
        user_input = "我需要申请技术支持资源"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到资源协调
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "resource_coordination")

    def test_no_match(self):
        """测试无匹配情况"""
        user_input = "今天天气真好"
        matches = self.matcher.match_plays(user_input)

        # 验证无匹配
        self.assertEqual(len(matches), 0)

    def test_account_context_boost(self):
        """测试客户上下文加分"""
        user_input = "复盘"

        # 无客户上下文
        matches_without = self.matcher.match_plays(user_input)
        score_without = matches_without[0][1] if matches_without else 0

        # 有客户上下文
        account_context = {"account_id": "acc_001"}
        matches_with = self.matcher.match_plays(user_input, account_context)
        score_with = matches_with[0][1] if matches_with else 0

        # 有客户上下文的分数应该更高
        self.assertGreater(score_with, score_without)

    def test_get_missing_context_all_missing(self):
        """测试缺失所有必需上下文"""
        play = self.matcher.plays["sales_review"]
        provided_context = {}

        missing = self.matcher.get_missing_context(play, provided_context)

        # 验证缺失字段
        self.assertIn("account_id", missing)

    def test_get_missing_context_partial(self):
        """测试部分缺失上下文"""
        play = self.matcher.plays["government_proposal"]
        provided_context = {"region": "北京市"}

        missing = self.matcher.get_missing_context(play, provided_context)

        # 验证只缺失cooperation_type
        self.assertIn("cooperation_type", missing)
        self.assertNotIn("region", missing)

    def test_get_missing_context_complete(self):
        """测试完整上下文"""
        play = self.matcher.plays["sales_review"]
        provided_context = {"account_id": "acc_001"}

        missing = self.matcher.get_missing_context(play, provided_context)

        # 验证无缺失
        self.assertEqual(len(missing), 0)

    def test_generate_questions(self):
        """测试生成补问问题"""
        missing_fields = ["account_id", "region"]
        questions = self.matcher.generate_questions(missing_fields)

        # 验证生成了问题
        self.assertEqual(len(questions), 2)
        self.assertIn("客户", questions[0])
        self.assertIn("地区", questions[1])

    def test_create_execution_plan(self):
        """测试生成执行计划"""
        play = self.matcher.plays["sales_review"]
        context = {"account_id": "acc_001"}

        plan = self.matcher.create_execution_plan(play, context)

        # 验证计划结构
        self.assertEqual(plan["play_id"], "sales_review")
        self.assertEqual(plan["play_name"], "客户推进与销售复盘")
        self.assertIn("workflow_id", plan)
        self.assertIn("steps", plan)
        self.assertGreater(len(plan["steps"]), 0)

    def test_execution_plan_steps_vary_by_play(self):
        """测试不同Play生成不同步骤"""
        play_sales = self.matcher.plays["sales_review"]
        play_gov = self.matcher.plays["government_proposal"]

        plan_sales = self.matcher.create_execution_plan(play_sales, {})
        plan_gov = self.matcher.create_execution_plan(play_gov, {"region": "上海"})

        # 验证步骤不同
        self.assertNotEqual(
            plan_sales["steps"][0]["description"],
            plan_gov["steps"][0]["description"]
        )

    def test_process_user_intent_no_match(self):
        """测试处理无匹配意图"""
        result = self.matcher.process_user_intent("今天天气真好")

        # 验证返回无匹配状态
        self.assertEqual(result["status"], "no_match")
        self.assertIn("suggestions", result)
        self.assertGreater(len(result["suggestions"]), 0)

    def test_process_user_intent_need_more_info(self):
        """测试处理需要补问的意图"""
        result = self.matcher.process_user_intent("我想做客户复盘")

        # 验证返回需要更多信息
        self.assertEqual(result["status"], "need_more_info")
        self.assertEqual(result["play_id"], "sales_review")
        self.assertIn("missing_fields", result)
        self.assertIn("questions", result)
        self.assertIn("account_id", result["missing_fields"])

    def test_process_user_intent_ready(self):
        """测试处理完整意图"""
        account_context = {"account_id": "acc_001"}
        result = self.matcher.process_user_intent("客户复盘", account_context)

        # 验证返回就绪状态
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["play_id"], "sales_review")
        self.assertIn("execution_plan", result)
        self.assertIn("alternatives", result)

    def test_process_user_intent_with_alternatives(self):
        """测试返回备选Play"""
        # 使用可能匹配多个Play的输入
        result = self.matcher.process_user_intent("研究")

        if result["status"] == "need_more_info" or result["status"] == "ready":
            # 如果有匹配，检查是否有备选
            # （注意：单个关键词可能只匹配一个，这里主要测试结构）
            self.assertIn("alternatives", result)

    def test_multiple_keyword_matches(self):
        """测试多关键词匹配"""
        user_input = "需要做客户跟进复盘，分析销售阶段和风险"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到复盘且分数较高
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "sales_review")
        # 多个关键词应该得更高分
        self.assertGreater(score, 10)

    def test_exact_name_match(self):
        """测试精确名称匹配"""
        user_input = "销售演示文稿工作室"
        matches = self.matcher.match_plays(user_input)

        # 验证匹配到演示且分数很高
        self.assertGreater(len(matches), 0)
        best_play, score = matches[0]
        self.assertEqual(best_play.play_id, "presentation")
        # 名称匹配应该加20分
        self.assertGreaterEqual(score, 20)

    def test_create_play_matcher_factory(self):
        """测试工厂函数"""
        matcher = create_play_matcher()

        # 验证创建成功且可用
        self.assertIsInstance(matcher, PlayMatcher)
        self.assertGreater(len(matcher.plays), 0)

    def test_all_plays_have_required_fields(self):
        """测试所有Play都有必需字段"""
        for play in self.matcher.plays.values():
            # 验证必需字段存在
            self.assertIsNotNone(play.play_id)
            self.assertIsNotNone(play.name)
            self.assertIsNotNone(play.workflow_id)
            self.assertIsNotNone(play.skill_id)
            self.assertIsInstance(play.required_context, list)
            self.assertIsInstance(play.keywords, list)
            self.assertGreater(len(play.keywords), 0)


if __name__ == "__main__":
    unittest.main()
