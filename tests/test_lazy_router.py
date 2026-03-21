"""Tests for lazy skill router and realtime query detection."""

from __future__ import annotations

import pytest

from app.skills.lazy_router import LazySkillRouter, RealtimeQueryDetector, RoutingDecision


class TestRealtimeQueryDetector:
    """Test realtime query intent detection."""

    def setup_method(self) -> None:
        self.detector = RealtimeQueryDetector()

    def test_weather_query_detection(self) -> None:
        """Test weather query detection."""
        queries = [
            "What's the weather in Melbourne?",
            "Melbourne天气怎么样",
            "明天会下雨吗",
            "Sydney weather forecast",
            "气温多少",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "weather_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_exchange_rate_detection(self) -> None:
        """Test exchange rate query detection."""
        queries = [
            "USD to CNY exchange rate",
            "100 EUR to GBP",
            "美元兑人民币汇率",
            "欧元换英镑多少",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "exchange_rate_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_stock_quote_detection(self) -> None:
        """Test stock quote query detection."""
        queries = [
            "AAPL stock price",
            "Tesla stock多少钱",
            "苹果股票价格",
            "Microsoft stock",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "stock_quote_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_crypto_quote_detection(self) -> None:
        """Test crypto quote query detection."""
        queries = [
            "Bitcoin price",
            "BTC to USD",
            "以太坊价格",
            "Ethereum in USD",
            "比特币多少钱",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "crypto_quote_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_sports_score_detection(self) -> None:
        """Test sports score query detection."""
        queries = [
            "Lakers vs Celtics score",
            "NBA比分",
            "英超最新比分",
            "World Cup final score",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "sports_score_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_fuel_price_detection(self) -> None:
        """Test fuel price query detection."""
        queries = [
            "Petrol price in Sydney",
            "汽油价格",
            "柴油多少钱",
            "Diesel price Melbourne",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            assert intent == "fuel_price_lookup", f"Failed for: {query}"
            assert confidence >= 0.75, f"Low confidence for: {query}"

    def test_non_realtime_queries(self) -> None:
        """Test that non-realtime queries return None."""
        queries = [
            "How to learn Python?",
            "What is machine learning?",
            "Tell me about history",
            "搜索一下最新的科技新闻",
        ]
        for query in queries:
            intent, confidence = self.detector.detect_intent(query)
            # These might match news patterns, but shouldn't match realtime patterns
            if intent:
                assert confidence < 0.75 or intent not in [
                    "weather_lookup",
                    "exchange_rate_lookup",
                    "stock_quote_lookup",
                ], f"Incorrectly detected as realtime: {query}"


class TestLazySkillRouter:
    """Test lazy skill router."""

    def setup_method(self) -> None:
        self.config = {
            "use_lazy_skills": True,
            "use_legacy_fallback": False,
            "prefer_realtime": True,
        }
        self.router = LazySkillRouter(self.config)

    def test_realtime_routing(self) -> None:
        """Test routing to realtime_lookup."""
        decision = self.router.route("What's the weather in Melbourne?")
        assert decision.skill_bundle == "realtime_lookup"
        assert decision.intent_type == "weather_lookup"
        assert decision.confidence >= 0.75

    def test_news_routing(self) -> None:
        """Test routing to news_intelligence."""
        decision = self.router.route("最新的科技新闻")
        assert decision.skill_bundle == "news_intelligence"
        assert decision.intent_type == "news_lookup"

    def test_document_routing(self) -> None:
        """Test routing to document_editing."""
        decision = self.router.route("创建一份项目计划文档")
        assert decision.skill_bundle == "document_editing"
        assert decision.intent_type == "document_edit"

    def test_terminal_routing(self) -> None:
        """Test routing to terminal_agent."""
        decision = self.router.route("运行npm install命令")
        assert decision.skill_bundle == "terminal_agent"
        assert decision.intent_type == "terminal_task"

    def test_default_web_research_routing(self) -> None:
        """Test default routing to web_research."""
        decision = self.router.route("How do I learn Python?")
        assert decision.skill_bundle == "web_research"
        assert decision.intent_type == "general_research"

    def test_realtime_disabled(self) -> None:
        """Test that realtime routing is skipped when disabled."""
        config = {
            "use_lazy_skills": True,
            "prefer_realtime": False,
        }
        router = LazySkillRouter(config)
        decision = router.route("What's the weather?")
        # Should still route to web_research since realtime is disabled
        assert decision.skill_bundle == "web_research"

    def test_fallback_allowed_flag(self) -> None:
        """Test fallback allowed flag."""
        # Realtime queries should allow fallback
        decision = self.router.route("Bitcoin price")
        assert decision.allow_fallback is True

        # Document editing should not allow fallback
        decision = self.router.route("编辑这个文档")
        assert decision.allow_fallback is False

    def test_routing_decision_structure(self) -> None:
        """Test RoutingDecision has required fields."""
        decision = self.router.route("Weather in Sydney")
        assert hasattr(decision, "skill_bundle")
        assert hasattr(decision, "confidence")
        assert hasattr(decision, "intent_type")
        assert hasattr(decision, "reason")
        assert hasattr(decision, "allow_fallback")
        assert isinstance(decision.confidence, float)
        assert 0.0 <= decision.confidence <= 1.0


class TestRouterIntegration:
    """Integration tests for router with different configs."""

    def test_legacy_fallback_config(self) -> None:
        """Test router with legacy fallback enabled."""
        config = {
            "use_lazy_skills": True,
            "use_legacy_fallback": True,
            "prefer_realtime": True,
        }
        router = LazySkillRouter(config)
        decision = router.route("Search for information")
        assert decision.allow_fallback is True

    def test_realtime_priority_config(self) -> None:
        """Test router prioritizes realtime when configured."""
        config = {
            "use_lazy_skills": True,
            "prefer_realtime": True,
        }
        router = LazySkillRouter(config)
        decision = router.route("USD to EUR rate")
        assert decision.skill_bundle == "realtime_lookup"

    def test_mixed_intent_query(self) -> None:
        """Test query with multiple possible intents."""
        config = {"use_lazy_skills": True, "prefer_realtime": True}
        router = LazySkillRouter(config)

        # "Latest Apple news" could be news or stock-related
        # Should prefer realtime if it matches
        decision = router.route("Latest Apple stock news")
        # This is ambiguous, but router should make a decision
        assert decision.skill_bundle in [
            "realtime_lookup",
            "news_intelligence",
            "web_research",
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
