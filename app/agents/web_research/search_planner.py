"""Search planner — generates query variants and strategy from intent."""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from app.agents.web_research.query_analyzer import ResearchIntent


@dataclass
class SearchPlan:
    primary_query: str
    backup_queries: list[str] = field(default_factory=list)
    domain_preference: list[str] = field(default_factory=list)  # e.g. ["site:github.com"]
    max_results: int = 8
    max_pages_to_open: int = 3
    recency_days: int = 0
    search_depth: int = 2   # 1=snippets, 2=open top pages, 3=deep


class SearchPlanner:
    """Produce a SearchPlan from a raw query and detected intent."""

    def plan(self, query: str, intent: ResearchIntent, max_sources: int = 5,
             recency_days: int = 0, prefer_official: bool = False) -> SearchPlan:
        backups: list[str] = []
        domains: list[str] = []
        depth = 2
        max_pages_to_open = min(max_sources, 4)
        primary_query = query

        if intent.subtype == "news":
            primary_query = self._news_primary_query(query)
            backups = self._news_backups(query)
            depth = 1
            max_pages_to_open = 0
        elif intent.subtype == "docs":
            backups = [f"{query} documentation", f"{query} official docs"]
            if prefer_official:
                domains = ["site:docs.", "site:developer."]
            depth = 2
        elif intent.subtype == "product":
            backups = [f"{query} \u8bc4\u6d4b", f"{query} review specs"]
            depth = 3
        elif intent.subtype == "comparison":
            backups = [f"{query} comparison", f"{query} \u5bf9\u6bd4\u5206\u6790"]
            depth = 2
        elif intent.subtype == "company":
            backups = [f"{query} company info", f"{query} \u516c\u53f8\u4ecb\u7ecd"]
            depth = 2
        else:
            backups = [f"{query} explained", f"{query} \u8be6\u7ec6\u4ecb\u7ecd"]

        return SearchPlan(
            primary_query=primary_query,
            backup_queries=backups[:4],
            domain_preference=domains,
            max_results=max(5, max_sources + 3),
            max_pages_to_open=max_pages_to_open,
            recency_days=recency_days,
            search_depth=depth,
        )

    def _news_primary_query(self, query: str) -> str:
        normalized = self._normalize_news_query(query)
        source_name = self._detect_source_name(query)
        topic = self._detect_topic(query, normalized)
        if source_name == "IT之家" and topic:
            return f"site:ithome.com {topic} 新闻 今天"
        if source_name == "IT之家":
            return "site:ithome.com 今日新闻"
        if source_name and topic:
            return f"{source_name} {topic}新闻"
        if topic:
            return f"今天 {topic}新闻"
        return normalized or query

    def _news_backups(self, query: str) -> list[str]:
        normalized = self._normalize_news_query(query)
        fallbacks: list[str] = []
        source_name = self._detect_source_name(query)
        topic = self._detect_topic(query, normalized)
        if source_name and topic:
            fallbacks.append(f"{source_name} {topic}\u65b0\u95fb")
            fallbacks.append(f"{source_name} \u4eca\u65e5 {topic}")
        elif source_name:
            fallbacks.append(f"{source_name} \u4eca\u65e5\u65b0\u95fb")
            fallbacks.append(f"{source_name} \u6700\u65b0\u6d88\u606f")
        if topic:
            fallbacks.append(f"\u4eca\u5929 {topic}\u65b0\u95fb")
            fallbacks.append(f"{topic} \u6700\u65b0\u6d88\u606f")
            fallbacks.append(f"{topic} \u65b0\u95fb")
        elif normalized:
            fallbacks.append(normalized)
        broader = self._broader_news_queries(query, normalized)
        fallbacks.extend(broader)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in fallbacks:
            candidate = " ".join(str(item or "").split()).strip()
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _normalize_news_query(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return ""
        text = re.sub(r"^(?:\u8bf7|\u5e2e\u6211|\u7ed9\u6211|\u9ebb\u70e6|\u60f3\u770b|\u6211\u60f3\u770b|\u770b\u770b|\u5e2e\u6211\u770b\u770b)", "", text)
        text = re.sub(r"(?:\u4eca\u5929|\u4eca\u65e5|\u73b0\u5728|\u6700\u65b0|\u4e00\u4e0b|\u6709\u54ea\u4e9b|\u6709\u4ec0\u4e48)$", "", text)
        text = text.strip(" \t\r\n?？!！,，。")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _detect_source_name(self, query: str) -> str:
        lowered = str(query or "").lower()
        if any(alias in lowered for alias in ("it\u4e4b\u5bb6", "it \u4e4b\u5bb6", "ithome", "ithome.com")):
            return "IT\u4e4b\u5bb6"
        if "openai" in lowered:
            return "OpenAI"
        return ""

    def _detect_topic(self, original: str, normalized: str) -> str:
        lowered = f"{original} {normalized}".lower()
        if any(token in lowered for token in ("tech", "technology", "\u79d1\u6280")):
            return "\u79d1\u6280"
        if any(token in lowered for token in ("finance", "financial", "\u8d22\u7ecf", "\u91d1\u878d")):
            return "\u8d22\u7ecf"
        if any(token in lowered for token in ("ai", "\u4eba\u5de5\u667a\u80fd", "\u5927\u6a21\u578b")):
            return "AI"
        return ""

    def _broader_news_queries(self, original: str, normalized: str) -> list[str]:
        lowered = f"{original} {normalized}".lower()
        if any(token in lowered for token in ("tech", "technology", "\u79d1\u6280", "ai", "artificial intelligence", "\u4eba\u5de5\u667a\u80fd")):
            return [
                "\u79d1\u6280\u65b0\u95fb",
                "\u4eca\u65e5\u79d1\u6280\u65b0\u95fb",
                "\u79d1\u6280\u6700\u65b0\u6d88\u606f",
            ]
        if any(token in lowered for token in ("finance", "financial", "\u8d22\u7ecf", "\u91d1\u878d")):
            return [
                "\u8d22\u7ecf\u65b0\u95fb",
                "\u4eca\u65e5\u8d22\u7ecf\u65b0\u95fb",
                "\u8d22\u7ecf\u6700\u65b0\u6d88\u606f",
            ]
        return [
            "\u4eca\u65e5\u65b0\u95fb",
            "\u6700\u65b0\u65b0\u95fb",
            "\u4eca\u65e5\u8981\u95fb",
        ]
