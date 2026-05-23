from __future__ import annotations

import re
from typing import Any

from app.models.skill_result import SkillResult
from app.news.news_briefing import to_briefing_item
from app.news.news_service import NewsService
from app.skills.bundles.runtime_types import BundleRuntimeServices


BUNDLE_NAME = "news-intelligence"


class NewsIntelligenceRuntime:
    def __init__(self, news_service: NewsService) -> None:
        self.news_service = news_service

    def execute(self, user_request: str) -> SkillResult:
        lowered = user_request.lower()
        compact = re.sub(r"\s+", "", lowered)
        provider = "ithome"
        force_refresh = any(token in lowered for token in ("刷新", "重新抓取", "最新"))

        url_match = re.search(r"https?://\S+", user_request)
        if url_match:
            article, analysis = self.news_service.fetch_and_analyze_article(url_match.group(0))
            response_text = self.news_service.render_article_judgement(article, analysis)
            return SkillResult(
                skill_name=BUNDLE_NAME,
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
                    skill_name=BUNDLE_NAME,
                    success=True,
                    summary="观察库当前为空。",
                    structured={"observations": []},
                    recommendation="先刷新一次新闻简报，再挑出值得长期跟踪的条目。",
                    response_text="请求已接收。观察库当前为空。建议先刷新一次新闻简报，再沉淀值得长期跟踪的技术变化。",
                )
            lines = ["近期技术观察项："]
            sources: list[dict[str, str]] = []
            for item in observations[:5]:
                title = str(item.get("title", "")).strip()
                if title:
                    lines.append(f"- {title}")
                url = str(item.get("url", "")).strip()
                if title and url:
                    sources.append({"title": title, "url": url})
            return SkillResult(
                skill_name=BUNDLE_NAME,
                success=True,
                summary="已整理近期观察库。",
                structured={"observations": observations},
                recommendation="建议先看最前面的 3 条，再决定是否展开正文。",
                sources=sources,
                response_text="\n".join(lines),
            )

        if "项目相关" in compact and "新闻" in compact:
            items = self.news_service.get_project_related_news(provider=provider, limit=5, force_refresh=force_refresh)
            if not items:
                return SkillResult(
                    skill_name=BUNDLE_NAME,
                    success=True,
                    summary="当前没有明显和项目直接相关的新闻。",
                    structured={"project_related": []},
                    recommendation="可以先看今日简报，再决定是否加入新的观察项。",
                    response_text="请求已接收。当前没有明显和项目直接相关的新闻。建议先看今日简报中的高分条目。",
                )
            briefing_items = [to_briefing_item(article, analysis) for article, analysis in items]
            lines = ["和当前项目直接相关的新闻："]
            sources: list[dict[str, str]] = []
            for item in briefing_items:
                lines.append(f"- {item.title}：{item.short_comment}")
                sources.append({"title": item.title, "url": item.url})
            return SkillResult(
                skill_name=BUNDLE_NAME,
                success=True,
                summary="已筛出和项目直接相关的新闻。",
                structured={"project_related": [item.to_dict() for item in briefing_items]},
                recommendation="建议优先打开前两条，判断是否要同步更新路线图。",
                sources=sources,
                response_text="\n".join(lines),
            )

        briefing = self.news_service.get_daily_briefing(provider=provider, force_refresh=force_refresh)
        top_sources = [{"title": item.title, "url": item.url} for item in briefing.top_items[:5]]
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=True,
            summary=f"已生成今日 {briefing.provider} 科技简报。",
            structured={"briefing": briefing.to_dict()},
            recommendation="如果你要，我可以继续展开其中某一条的正文和是否值得看的判断。",
            sources=top_sources,
            response_text=briefing.render_text(),
        )


def run_bundle(
    *,
    user_request: str,
    allowed_tools: list[str],
    attachments: list[str],
    memory_context: str,
    bundle: object,
    prompt_context: object,
    services: BundleRuntimeServices,
    route_context: object | None,
    request_origin: str,
    request_id: str,
) -> SkillResult:
    del allowed_tools, attachments, memory_context, bundle, prompt_context, route_context, request_origin, request_id
    news_service = services.news
    if news_service is None:
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=False,
            summary="News capability unavailable.",
            response_text="请求已接收，但当前新闻能力暂不可用。",
            structured={"bundle_name": BUNDLE_NAME},
        )
    return NewsIntelligenceRuntime(news_service).execute(user_request)
