from __future__ import annotations

from datetime import datetime

from app.news.news_models import NewsAnalysis, NewsArticle, NewsBriefing, NewsBriefingItem


def build_briefing(
    provider: str,
    analyzed_items: list[tuple[NewsArticle, NewsAnalysis]],
    *,
    top_n: int = 5,
) -> NewsBriefing:
    top_items = [to_briefing_item(article, analysis) for article, analysis in analyzed_items if analysis.action != "skip"][:top_n]
    project_related = [to_briefing_item(article, analysis) for article, analysis in analyzed_items if analysis.action == "project_related"][:5]
    observed = [to_briefing_item(article, analysis) for article, analysis in analyzed_items if analysis.action == "observe"][:5]
    skipped = [to_briefing_item(article, analysis) for article, analysis in analyzed_items if analysis.action == "skip"][:5]
    return NewsBriefing(
        provider=provider,
        generated_at=datetime.utcnow(),
        total_count=len(analyzed_items),
        top_items=top_items,
        project_related=project_related,
        skipped_items=skipped,
        observed_items=observed,
    )


def to_briefing_item(article: NewsArticle, analysis: NewsAnalysis) -> NewsBriefingItem:
    return NewsBriefingItem(
        article_id=article.article_id,
        title=article.title,
        url=article.url,
        source=article.source,
        published_at=article.published_at,
        action=analysis.action,
        tags=list(article.tags),
        reasons=list(analysis.reasons),
        short_comment=analysis.short_comment,
        relevance_score=analysis.relevance_score,
        project_relevance_score=analysis.project_relevance_score,
        observe_score=analysis.observe_score,
        hype_score=analysis.hype_score,
    )


def briefing_from_dict(data: dict) -> NewsBriefing:
    def _item(payload: dict) -> NewsBriefingItem:
        published_at = datetime.fromisoformat(payload["published_at"]) if payload.get("published_at") else None
        return NewsBriefingItem(
            article_id=str(payload.get("article_id", "")),
            title=str(payload.get("title", "")),
            url=str(payload.get("url", "")),
            source=str(payload.get("source", "")),
            published_at=published_at,
            action=str(payload.get("action", "")),
            tags=list(payload.get("tags", [])),
            reasons=list(payload.get("reasons", [])),
            short_comment=str(payload.get("short_comment", "")),
            relevance_score=float(payload.get("relevance_score", 0.0)),
            project_relevance_score=float(payload.get("project_relevance_score", 0.0)),
            observe_score=float(payload.get("observe_score", 0.0)),
            hype_score=float(payload.get("hype_score", 0.0)),
        )

    return NewsBriefing(
        provider=str(data.get("provider", "")),
        generated_at=datetime.fromisoformat(data["generated_at"]) if data.get("generated_at") else datetime.utcnow(),
        total_count=int(data.get("total_count", 0)),
        top_items=[_item(item) for item in data.get("top_items", [])],
        project_related=[_item(item) for item in data.get("project_related", [])],
        skipped_items=[_item(item) for item in data.get("skipped_items", [])],
        observed_items=[_item(item) for item in data.get("observed_items", [])],
    )
