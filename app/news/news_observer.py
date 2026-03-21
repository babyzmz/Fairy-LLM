from __future__ import annotations

import logging

from app.news.news_cache import NewsCache
from app.news.news_models import NewsAnalysis, NewsArticle


logger = logging.getLogger(__name__)


class NewsObserver:
    def __init__(self, cache: NewsCache) -> None:
        self.cache = cache

    def record(self, article: NewsArticle, analysis: NewsAnalysis) -> bool:
        if analysis.action not in {"observe", "project_related"} and analysis.observe_score < 0.65:
            return False
        self.cache.add_observation(article, analysis)
        logger.info("news_observer_added article_id=%s action=%s", article.article_id, analysis.action)
        return True

    def list_recent(self, limit: int = 10) -> list[dict]:
        return self.cache.list_observations(limit=limit)
