from __future__ import annotations

from abc import ABC, abstractmethod

from app.news.news_models import NewsArticle


class BaseNewsProvider(ABC):
    provider_name: str = ""

    @abstractmethod
    def fetch_headlines(self, limit: int = 50) -> list[NewsArticle]:
        raise NotImplementedError

    @abstractmethod
    def fetch_article_content(self, article: NewsArticle) -> NewsArticle:
        raise NotImplementedError
