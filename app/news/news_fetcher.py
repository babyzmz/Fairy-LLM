from __future__ import annotations

import logging

from app.news.news_cache import NewsCache
from app.news.news_models import NewsArticle
from app.news.news_provider_base import BaseNewsProvider


logger = logging.getLogger(__name__)


class NewsFetcher:
    def __init__(self, providers: dict[str, BaseNewsProvider], cache: NewsCache) -> None:
        self.providers = providers
        self.cache = cache

    def fetch_headlines(self, provider_name: str, limit: int = 50) -> list[NewsArticle]:
        provider = self.providers[provider_name]
        logger.info("news_fetch_start provider=%s limit=%s", provider_name, limit)
        articles = provider.fetch_headlines(limit=limit)
        deduped: list[NewsArticle] = []
        seen_hashes: set[str] = set()
        for article in articles:
            article.ensure_hash()
            if article.dedupe_hash in seen_hashes:
                continue
            seen_hashes.add(article.dedupe_hash)
            self.cache.upsert_article(article)
            deduped.append(article)
        logger.info("news_fetch_done provider=%s fetched=%s deduped=%s", provider_name, len(articles), len(deduped))
        return deduped

    def fetch_article_content(self, provider_name: str, article: NewsArticle) -> NewsArticle:
        provider = self.providers[provider_name]
        article = provider.fetch_article_content(article)
        self.cache.upsert_article(article)
        return article

    def infer_provider_name(self, url: str) -> str | None:
        lowered = url.lower()
        if "ithome.com" in lowered:
            return "ithome"
        return None
