from __future__ import annotations

import re

from app.models.skill_result import SkillResult
from app.news.news_briefing import to_briefing_item
from app.news.news_service import NewsService
from app.skills.skill_spec import SkillSpec


class NewsIntelligenceSkill:
    SPEC = SkillSpec(
        name="news_intelligence_skill",
        description="Fetch, classify, rank, and brief technology news with project-aware filtering.",
        trigger_hints=("IT之家", "科技简报", "值得看", "观察库", "项目相关的新闻"),
        allowed_tools=(),
        execution_steps=("fetch_news", "classify_news", "rank_news", "build_briefing"),
        output_schema=("summary", "briefing", "project_related", "observations", "sources"),
    )

    def __init__(self, news_service: NewsService) -> None:
        self.news_service = news_service

    def execute(self, user_request: str, allowed_tools: list[str], *, memory_context: str = "") -> SkillResult:
        _ = allowed_tools, memory_context
        lowered = user_request.lower()
        compact = re.sub(r"\s+", "", lowered)
        provider = "ithome"
        force_refresh = any(token in lowered for token in ("刷新", "重新抓取", "最新"))

        url_match = re.search(r"https?://\S+", user_request)
        if url_match:
            article, analysis = self.news_service.fetch_and_analyze_article(url_match.group(0))
            response_text = self.news_service.render_article_judgement(article, analysis)
            return SkillResult(
                skill_name=self.SPEC.name,
                success=True,
                summary=response_text.splitlines()[0] if response_text else article.title,
                structured={"article": article.to_dict(), "analysis": analysis.to_dict()},
                recommendation=analysis.short_comment,
                sources=[{"title": article.title, "url": article.url}],
                response_text=response_text,
            )

        if "观察库" in compact or "技术观察" in compact:
            observations = self.news_service.get_recent_observations(limit=8)
            if not observations:
                return SkillResult(
                    skill_name=self.SPEC.name,
                    success=True,
                    summary="观察库当前为空。",
                    structured={"observations": []},
                    recommendation="先拉取一次新闻简报，再挑出值得长期观察的条目。",
                    response_text="请求已接收。观察库当前为空。建议先刷新一次 IT 之家简报，再沉淀值得长期跟踪的技术变化。",
                )
            lines = ["近期技术观察项："]
            sources: list[dict[str, str]] = []
            for item in observations[:5]:
                lines.append(f"- {item.get('title', '')}")
                url = str(item.get("url", "")).strip()
                if url:
                    sources.append({"title": str(item.get("title", "")).strip(), "url": url})
            return SkillResult(
                skill_name=self.SPEC.name,
                success=True,
                summary="已整理近期观察库。",
                structured={"observations": observations},
                recommendation="建议优先看最近 3 条，再决定是否展开正文。",
                sources=sources,
                response_text="\n".join(lines),
            )

        if "fairy项目相关" in compact or ("项目相关" in compact and "新闻" in compact):
            items = self.news_service.get_project_related_news(provider=provider, limit=5, force_refresh=force_refresh)
            if not items:
                return SkillResult(
                    skill_name=self.SPEC.name,
                    success=True,
                    summary="当前没有明显和 Fairy 项目直接相关的新闻。",
                    structured={"project_related": []},
                    recommendation="可以先查看今日简报，再决定是否加入新的技术观察项。",
                    response_text="请求已接收。当前没有明显和 Fairy 项目直接相关的新闻。建议先看今日简报中的高分条目。",
                )
            briefing_items = [to_briefing_item(article, analysis) for article, analysis in items]
            lines = ["与 Fairy 项目直接相关的新闻："]
            sources: list[dict[str, str]] = []
            for item in briefing_items:
                lines.append(f"- {item.title}：{item.short_comment}")
                sources.append({"title": item.title, "url": item.url})
            return SkillResult(
                skill_name=self.SPEC.name,
                success=True,
                summary="已筛出和 Fairy 项目直接相关的新闻。",
                structured={"project_related": [item.to_dict() for item in briefing_items]},
                recommendation="建议优先打开前两条，看是否需要更新 Fairy 的路线图。",
                sources=sources,
                response_text="\n".join(lines),
            )

        briefing = self.news_service.get_daily_briefing(provider=provider, force_refresh=force_refresh)
        top_sources = [{"title": item.title, "url": item.url} for item in briefing.top_items[:5]]
        return SkillResult(
            skill_name=self.SPEC.name,
            success=True,
            summary=f"已生成今日 {briefing.provider} 科技简报。",
            structured={"briefing": briefing.to_dict()},
            recommendation="如果你要，我可以继续展开某一条的正文和值不值得看判断。",
            sources=top_sources,
            response_text=briefing.render_text(),
        )
