"""Smart lazy skill router with realtime query detection.

Routes queries to appropriate skill bundles based on intent patterns:
- Realtime queries → realtime_lookup (fast, minimal tools)
- Complex research → web_research (full pipeline)
- News/intelligence → news_intelligence
- Document editing → document_editing
- Terminal tasks → terminal_agent
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing analysis."""

    skill_bundle: str
    confidence: float
    intent_type: str
    reason: str
    allow_fallback: bool = True


class RealtimeQueryDetector:
    """Detects queries that should use realtime_lookup skill."""

    # Weather patterns
    WEATHER_PATTERNS = (
        r"\b(天气|气温|温度|预报|下雨|降雨|weather|forecast|temperature)\b",
        r"(今天|明天|后天|今日|明日)(天气|气温|温度|预报)",
        r"(会不会下雨|下不下雨|要不要带伞)",
    )

    # Exchange rate patterns
    EXCHANGE_PATTERNS = (
        r"\b(汇率|exchange rate|USD|EUR|GBP|CNY|JPY)\b.*\b(to|转|兑|换)\b",
        r"\b(\d+)\s*(USD|EUR|GBP|CNY|JPY)\s*(to|转|兑|换)\s*(USD|EUR|GBP|CNY|JPY)\b",
        r"(美元|欧元|英镑|人民币|日元).*(汇率|兑换|多少)",
    )

    # Stock quote patterns
    STOCK_PATTERNS = (
        r"\b(stock|股票|股价|price)\b.*\b([A-Z]{1,5})\b",
        r"\b([A-Z]{1,5})\b.*(stock|股票|股价|price|多少钱)",
        r"(苹果|微软|谷歌|特斯拉|亚马逊).*(股票|股价|多少钱)",
    )

    # Crypto quote patterns
    CRYPTO_PATTERNS = (
        r"\b(bitcoin|ethereum|BTC|ETH|crypto|cryptocurrency|加密货币)\b",
        r"\b(比特币|以太坊|狗狗币)\b.*(价格|多少|价钱)",
        r"(BTC|ETH|DOGE|XRP)\s*(price|价格|多少)",
    )

    # Sports score patterns
    SPORTS_PATTERNS = (
        r"\b(score|比分|结果|final|game)\b",
        r"(NBA|英超|欧冠|世界杯|奥运).*(比分|结果|赛果)",
        r"(篮球|足球|棒球|冰球).*(比分|结果|赛果)",
    )

    # Fuel price patterns
    FUEL_PATTERNS = (
        r"\b(fuel|petrol|diesel|gas|油价|汽油|柴油)\b.*\b(price|价格|多少)\b",
        r"(汽油|柴油|油价).*(多少|价格|价钱)",
        r"\b(petrol|diesel)\b.*\b(price|Sydney|Melbourne|Brisbane)\b",
    )

    # Time/timezone patterns
    TIME_PATTERNS = (
        r"\b(time|时间|几点|current time)\b.*\b(in|在|的)\b",
        r"(伦敦|纽约|东京|悉尼).*(时间|几点|现在)",
    )

    def detect_intent(self, query: str) -> tuple[str | None, float]:
        """Detect realtime query intent.

        Args:
            query: User query

        Returns:
            Tuple of (intent_type, confidence) or (None, 0.0)
        """
        query_lower = query.lower()

        # Check weather
        if self._match_patterns(query_lower, self.WEATHER_PATTERNS):
            return "weather_lookup", 0.95

        # Check exchange rate
        if self._match_patterns(query_lower, self.EXCHANGE_PATTERNS):
            return "exchange_rate_lookup", 0.90

        # Check stock quote
        if self._match_patterns(query_lower, self.STOCK_PATTERNS):
            return "stock_quote_lookup", 0.85

        # Check crypto quote
        if self._match_patterns(query_lower, self.CRYPTO_PATTERNS):
            return "crypto_quote_lookup", 0.90

        # Check sports score
        if self._match_patterns(query_lower, self.SPORTS_PATTERNS):
            return "sports_score_lookup", 0.80

        # Check fuel price
        if self._match_patterns(query_lower, self.FUEL_PATTERNS):
            return "fuel_price_lookup", 0.85

        # Check time/timezone
        if self._match_patterns(query_lower, self.TIME_PATTERNS):
            return "time_lookup", 0.80

        return None, 0.0

    @staticmethod
    def _match_patterns(text: str, patterns: tuple[str, ...]) -> bool:
        """Check if text matches any pattern."""
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False


class LazySkillRouter:
    """Routes queries to appropriate lazy skill bundles."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.detector = RealtimeQueryDetector()
        self.use_lazy_skills = self.config.get("use_lazy_skills", True)
        self.use_legacy_fallback = self.config.get("use_legacy_fallback", False)
        self.prefer_realtime = self.config.get("prefer_realtime", True)

    def route(self, query: str, context: dict[str, Any] | None = None) -> RoutingDecision:
        """Route query to appropriate skill bundle.

        Args:
            query: User query
            context: Optional context (user preferences, history, etc.)

        Returns:
            RoutingDecision with skill bundle and metadata
        """
        context = context or {}

        # Check for realtime query if enabled
        if self.prefer_realtime:
            intent_type, confidence = self.detector.detect_intent(query)
            if intent_type and confidence >= 0.75:
                logger.info(
                    "Routing to realtime_lookup. intent=%s confidence=%.2f",
                    intent_type,
                    confidence,
                )
                return RoutingDecision(
                    skill_bundle="realtime_lookup",
                    confidence=confidence,
                    intent_type=intent_type,
                    reason=f"Detected {intent_type} with {confidence:.0%} confidence",
                    allow_fallback=True,
                )

        # Check for news/intelligence queries
        if self._is_news_query(query):
            logger.info("Routing to news_intelligence")
            return RoutingDecision(
                skill_bundle="news_intelligence",
                confidence=0.85,
                intent_type="news_lookup",
                reason="Detected news/intelligence query",
                allow_fallback=True,
            )

        # Check for document editing
        if self._is_document_query(query):
            logger.info("Routing to document_editing")
            return RoutingDecision(
                skill_bundle="document_editing",
                confidence=0.90,
                intent_type="document_edit",
                reason="Detected document editing request",
                allow_fallback=False,
            )

        # Check for terminal/shell tasks
        if self._is_terminal_query(query):
            logger.info("Routing to terminal_agent")
            return RoutingDecision(
                skill_bundle="terminal_agent",
                confidence=0.85,
                intent_type="terminal_task",
                reason="Detected terminal/shell task",
                allow_fallback=False,
            )

        # Default to web_research for general queries
        logger.info("Routing to web_research (default)")
        return RoutingDecision(
            skill_bundle="web_research",
            confidence=0.70,
            intent_type="general_research",
            reason="No specific intent detected, using general research",
            allow_fallback=self.use_legacy_fallback,
        )

    @staticmethod
    def _is_news_query(query: str) -> bool:
        """Check if query is about news/intelligence."""
        patterns = (
            r"\b(新闻|快讯|动态|最新|实时|今日|今天|news|latest|breaking)\b",
            r"(科技新闻|财经新闻|体育新闻|娱乐新闻)",
        )
        return RealtimeQueryDetector._match_patterns(query.lower(), patterns)

    @staticmethod
    def _is_document_query(query: str) -> bool:
        """Check if query is about document editing."""
        patterns = (
            r"\b(编辑|修改|删除|创建|新建|文档|document|edit|create|delete)\b",
            r"(写一份|生成一份|创建一份).*(文档|报告|计划)",
        )
        return RealtimeQueryDetector._match_patterns(query.lower(), patterns)

    @staticmethod
    def _is_terminal_query(query: str) -> bool:
        """Check if query is about terminal/shell tasks."""
        patterns = (
            r"\b(terminal|shell|command|bash|cmd|命令|终端|shell脚本)\b",
            r"(运行|执行|安装|卸载).*(命令|脚本|程序)",
        )
        return RealtimeQueryDetector._match_patterns(query.lower(), patterns)


def create_router(config: dict[str, Any] | None = None) -> LazySkillRouter:
    """Create a lazy skill router with optional config.

    Args:
        config: Optional configuration dict

    Returns:
        Configured LazySkillRouter instance
    """
    return LazySkillRouter(config)
