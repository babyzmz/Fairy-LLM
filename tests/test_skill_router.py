from __future__ import annotations

import unittest

from app.fairy_core import FairyCore
from app.models.skill_result import SkillResult
from app.skill_router import LLMRouteDecision, RouteCandidate, SkillRouter
from app.skills.agent_shell_skill import AgentShellSkill
from app.skills.document_editor_skill import DocumentEditorSkill
from app.skills.news_intelligence_skill import NewsIntelligenceSkill
from app.skills.screen_understanding_skill import ScreenUnderstandingSkill
from app.skills.web_research_skill import WebResearchSkill


class SkillRouterDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = SkillRouter(
            [
                AgentShellSkill.SPEC,
                WebResearchSkill.SPEC,
                DocumentEditorSkill.SPEC,
                NewsIntelligenceSkill.SPEC,
                ScreenUnderstandingSkill.SPEC,
            ]
        )

    def test_weather_query_prefers_weather_not_news(self) -> None:
        decision = self.router.decide("今天天气咋样")
        self.assertEqual(decision.primary_intent, "weather")
        self.assertEqual(decision.candidates[0].name, "weather")
        candidate_names = [item.name for item in decision.candidates]
        self.assertIn("direct_answer", candidate_names)
        self.assertLess(candidate_names.index("weather"), candidate_names.index("direct_answer"))
        self.assertNotEqual(decision.candidates[0].name, "news")

    def test_tech_news_query_prefers_news(self) -> None:
        decision = self.router.decide("最近有什么科技新闻")
        self.assertEqual(decision.primary_intent, "news")
        self.assertEqual(decision.candidates[0].name, "news")
        self.assertIn("web_search", [item.name for item in decision.candidates])

    def test_history_query_prefers_knowledge_lookup(self) -> None:
        decision = self.router.decide("我们之前决定了什么")
        self.assertEqual(decision.primary_intent, "knowledge_lookup")
        self.assertEqual(decision.candidates[0].name, "knowledge_lookup")
        self.assertIn("direct_answer", [item.name for item in decision.candidates])

    def test_pending_notifications_prefers_system_ops(self) -> None:
        decision = self.router.decide("现在有哪些待处理通知")
        self.assertEqual(decision.primary_intent, "system_ops")
        self.assertEqual(decision.candidates[0].name, "system_ops")

    def test_general_brainstorm_prefers_direct_answer(self) -> None:
        decision = self.router.decide("帮我想想这个 UI 怎么改")
        self.assertEqual(decision.primary_intent, "direct_answer")
        self.assertEqual(decision.candidates[0].name, "direct_answer")

    def test_realtime_word_does_not_force_news(self) -> None:
        decision = self.router.decide("最近这个 UI 风格是不是太花了")
        self.assertEqual(decision.candidates[0].name, "direct_answer")
        self.assertNotEqual(decision.primary_intent, "news")

    def test_direct_answer_always_present_even_with_model_bias(self) -> None:
        llm_decision = LLMRouteDecision(
            primary_intent="weather",
            reason="模型判断更像天气查询",
            confidence=0.8,
            tool_needed=True,
            candidate_scores={"weather": 0.92},
        )
        decision = self.router.decide("墨尔本今天会下雨吗", llm_decision=llm_decision)
        self.assertIn("direct_answer", [item.name for item in decision.candidates])

    def test_attempt_sequence_keeps_selected_candidate_first(self) -> None:
        llm_decision = LLMRouteDecision(
            primary_intent="local_action",
            reason="持续屏幕追问",
            confidence=0.9,
            tool_needed=True,
            candidate_scores={"screen_understanding": 0.95, "direct_answer": 0.2},
        )
        decision = self.router.decide("这一步该点哪里", llm_decision=llm_decision)
        sequence = self.router.build_attempt_sequence(decision)
        self.assertEqual(sequence[0].name, decision.selected_tool)


class RoutingFallbackExecutorTest(unittest.TestCase):
    def test_tool_failure_falls_back_to_next_candidate(self) -> None:
        candidates = [
            RouteCandidate(name="weather", score=0.95, reason="weather"),
            RouteCandidate(name="web_search", score=0.55, reason="fallback"),
            RouteCandidate(name="direct_answer", score=0.3, reason="final"),
        ]

        def execute(candidate: RouteCandidate) -> SkillResult:
            if candidate.name == "weather":
                return SkillResult(skill_name="weather", success=False, summary="fail", response_text="天气服务失败")
            if candidate.name == "web_search":
                return SkillResult(skill_name="web_search", success=True, summary="ok", response_text="查到天气结果")
            return SkillResult(skill_name="direct_answer", success=True, summary="ok", response_text="一般性回答")

        final_candidate, result, fallback_used, fallback_reason, path = FairyCore._run_candidate_executor(
            candidates,
            execute,
            lambda route_name, res: bool(res.success and (res.response_text or res.summary)),
        )
        self.assertTrue(fallback_used)
        self.assertEqual(fallback_reason, "tool_failed")
        self.assertEqual(final_candidate.name, "web_search")
        self.assertTrue(result.success)
        self.assertEqual(path, "weather -> web_search")


if __name__ == "__main__":
    unittest.main()
