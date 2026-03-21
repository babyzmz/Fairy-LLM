"""Search planner — generates query variants and strategy from intent."""
from __future__ import annotations
from dataclasses import dataclass, field
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

        if intent.subtype == "news":
            backups = [f"{query} \u6700\u65b0", f"{query} news"]
            depth = 2
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
            backup_queries=backups[:2],
            domain_preference=domains,
            max_results=max(5, max_sources + 3),
            max_pages_to_open=min(max_sources, 4),
            recency_days=recency_days,
            search_depth=depth,
        )
