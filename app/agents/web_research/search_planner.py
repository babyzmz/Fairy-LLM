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

        if intent.subtype == "news":
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
            primary_query=query,
            backup_queries=backups[:4],
            domain_preference=domains,
            max_results=max(5, max_sources + 3),
            max_pages_to_open=max_pages_to_open,
            recency_days=recency_days,
            search_depth=depth,
        )

    def _news_backups(self, query: str) -> list[str]:
        normalized = self._normalize_news_query(query)
        fallbacks: list[str] = []
        if normalized and normalized != query:
            fallbacks.append(normalized)
        if normalized:
            fallbacks.append(f"{normalized} \u6700\u65b0")
            fallbacks.append(f"{normalized} news")
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
        text = re.sub(r"^(?:请|帮我|给我|麻烦|想看|我想看)", "", text)
        text = re.sub(r"(?:今天|今日|现在|最新|看看|一下|有哪些|有什么)$", "", text)
        text = text.strip(" \t\r\n?？!！,，。")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _broader_news_queries(self, original: str, normalized: str) -> list[str]:
        lowered = f"{original} {normalized}".lower()
        if any(token in lowered for token in ("tech", "technology", "\u79d1\u6280", "ai", "artificial intelligence", "\u4eba\u5de5\u667a\u80fd")):
            return [
                "\u79d1\u6280\u65b0\u95fb",
                "technology news today",
                "latest technology news",
            ]
        if any(token in lowered for token in ("finance", "financial", "\u8d22\u7ecf", "\u91d1\u878d")):
            return [
                "\u8d22\u7ecf\u65b0\u95fb",
                "financial news today",
                "latest finance news",
            ]
        return [
            "\u65b0\u95fb",
            "latest news",
            "today news",
        ]
