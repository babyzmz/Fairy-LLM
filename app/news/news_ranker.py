from __future__ import annotations

from app.news.news_models import NewsAnalysis, NewsArticle


ACTION_PRIORITY = {
    "project_related": 0,
    "observe": 1,
    "read": 2,
    "skip": 3,
}


class NewsRanker:
    def rank(self, items: list[tuple[NewsArticle, NewsAnalysis]]) -> list[tuple[NewsArticle, NewsAnalysis]]:
        return sorted(items, key=self._score_key)

    def _score_key(self, item: tuple[NewsArticle, NewsAnalysis]) -> tuple[float, float, float, float]:
        _, analysis = item
        priority = ACTION_PRIORITY.get(analysis.action, 99)
        composite = (
            analysis.project_relevance_score * 1.7
            + analysis.observe_score * 1.25
            + analysis.relevance_score
            - analysis.hype_score * 0.9
        )
        return (priority, -composite, -analysis.relevance_score, analysis.hype_score)
