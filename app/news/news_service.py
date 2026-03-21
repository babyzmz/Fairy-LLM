from __future__ import annotations

import logging
from datetime import datetime
from hashlib import sha1
from typing import Callable

from app.config import news_config
from app.news.news_briefing import build_briefing
from app.news.news_cache import NewsCache
from app.news.news_classifier import NewsProfile, RuleBasedNewsClassifier
from app.news.news_fetcher import NewsFetcher
from app.news.news_models import NewsAnalysis, NewsArticle, NewsBriefing
from app.news.news_observer import NewsObserver
from app.news.news_provider_ithome import ITHomeNewsProvider
from app.news.news_ranker import NewsRanker


logger = logging.getLogger(__name__)
NewsEventCallback = Callable[[str, dict], None]


def provider_name_to_label(provider: str) -> str:
    if provider == "ithome":
        return "IT之家"
    return provider


class NewsService:
    def __init__(
        self,
        *,
        cache: NewsCache | None = None,
        profile: NewsProfile | None = None,
        event_callback: NewsEventCallback | None = None,
    ) -> None:
        self.cache = cache or NewsCache(news_config.db_path)
        self.profile = profile or NewsProfile(
            preferred_tags=tuple(news_config.preferred_tags),
            project_topics=tuple(news_config.project_topics),
            skip_keywords=tuple(news_config.skip_keywords),
        )
        self.fetcher = NewsFetcher({"ithome": ITHomeNewsProvider()}, self.cache)
        self.classifier = RuleBasedNewsClassifier(self.profile)
        self.ranker = NewsRanker()
        self.observer = NewsObserver(self.cache)
        self.event_callback = event_callback

    def get_daily_briefing(self, provider: str = "ithome", limit: int | None = None, *, force_refresh: bool = False) -> NewsBriefing:
        day_key = datetime.now().strftime("%Y-%m-%d")
        if not force_refresh:
            cached = self.cache.get_briefing(provider, day_key)
            if cached is not None:
                return cached

        analyzed_items = self.refresh_and_analyze(provider=provider, limit=limit or news_config.headline_limit)
        briefing = build_briefing(provider_name_to_label(provider), analyzed_items, top_n=news_config.briefing_top_n)
        self.cache.save_briefing(provider, day_key, briefing)
        self._emit("news_briefing_ready", {"provider": provider, "total_count": briefing.total_count})
        return briefing

    def refresh_and_analyze(self, provider: str = "ithome", limit: int = 50) -> list[tuple[NewsArticle, NewsAnalysis]]:
        self._emit("news_fetch_start", {"provider": provider, "limit": limit})
        articles = self.fetcher.fetch_headlines(provider, limit=limit)
        self._emit("news_fetch_done", {"provider": provider, "count": len(articles)})

        analyzed: list[tuple[NewsArticle, NewsAnalysis]] = []
        for article in articles:
            analysis = self.classifier.analyze(article)
            self.cache.upsert_article(article)
            self.cache.save_analysis(article.article_id, analysis)
            analyzed.append((article, analysis))

        fetch_full_targets = [article for article, analysis in analyzed if self._should_fetch_full_content(analysis)]
        logger.info("news_content_fetch_count provider=%s count=%s", provider, len(fetch_full_targets))
        for article in fetch_full_targets:
            self._emit("news_content_fetch_start", {"title": article.title, "url": article.url})
            try:
                full_article = self.fetcher.fetch_article_content(provider, article)
            except Exception as exc:  # noqa: BLE001
                logger.warning("news_content_fetch_failed provider=%s url=%s error=%s", provider, article.url, exc)
                continue
            refreshed_analysis = self.classifier.analyze(full_article)
            self.cache.upsert_article(full_article)
            self.cache.save_analysis(full_article.article_id, refreshed_analysis)
            analyzed = [
                (full_article, refreshed_analysis) if existing.article_id == full_article.article_id else (existing, existing_analysis)
                for existing, existing_analysis in analyzed
            ]
            self._emit("news_content_fetch_done", {"title": full_article.title, "url": full_article.url})

        ranked = self.ranker.rank(analyzed)
        observer_count = 0
        for article, analysis in ranked:
            if self.observer.record(article, analysis):
                observer_count += 1
        logger.info("news_observer_count provider=%s count=%s", provider, observer_count)
        return ranked

    def get_project_related_news(self, provider: str = "ithome", limit: int = 5, *, force_refresh: bool = False) -> list[tuple[NewsArticle, NewsAnalysis]]:
        items = self._load_or_refresh(provider, force_refresh=force_refresh)
        return [(article, analysis) for article, analysis in items if analysis.action == "project_related"][:limit]

    def get_observe_candidates(self, provider: str = "ithome", limit: int = 5, *, force_refresh: bool = False) -> list[tuple[NewsArticle, NewsAnalysis]]:
        items = self._load_or_refresh(provider, force_refresh=force_refresh)
        return [(article, analysis) for article, analysis in items if analysis.action == "observe"][:limit]

    def get_recent_observations(self, limit: int = 10) -> list[dict]:
        return self.observer.list_recent(limit=limit)

    def fetch_and_analyze_article(self, url: str) -> tuple[NewsArticle, NewsAnalysis]:
        provider_name = self.fetcher.infer_provider_name(url)
        if provider_name is None:
            raise ValueError("当前只支持 IT 之家文章详情分析。")
        article = self.cache.get_article_by_url(url)
        if article is None:
            article = NewsArticle(
                source=provider_name_to_label(provider_name),
                provider=provider_name,
                article_id=f"{provider_name}:{sha1(url.encode('utf-8')).hexdigest()[:16]}",
                title=url,
                url=url,
                published_at=None,
            )
            article.ensure_hash()
        full_article = self.fetcher.fetch_article_content(provider_name, article)
        analysis = self.classifier.analyze(full_article)
        self.cache.upsert_article(full_article)
        self.cache.save_analysis(full_article.article_id, analysis)
        self.observer.record(full_article, analysis)
        return full_article, analysis

    def render_article_judgement(self, article: NewsArticle, analysis: NewsAnalysis) -> str:
        lines = [
            f"标题：{article.title}",
            f"建议动作：{analysis.action}",
            f"命中标签：{', '.join(article.tags[:6]) or '无'}",
            f"原因：{'；'.join(analysis.reasons[:4]) or '暂无明显规则命中'}",
            f"Fairy 评语：{analysis.short_comment or '这条可以按需再读。'}",
        ]
        if article.summary:
            lines.append(f"摘要：{article.summary[:220]}")
        return "\n".join(lines)

    def _load_or_refresh(self, provider: str, *, force_refresh: bool) -> list[tuple[NewsArticle, NewsAnalysis]]:
        if not force_refresh:
            cached = self.cache.get_recent_analyzed(provider, limit=news_config.headline_limit)
            if cached:
                return self.ranker.rank(cached)
        return self.refresh_and_analyze(provider=provider, limit=news_config.headline_limit)

    def _should_fetch_full_content(self, analysis: NewsAnalysis) -> bool:
        return (
            analysis.relevance_score >= news_config.full_content_relevance_threshold
            or analysis.project_relevance_score >= news_config.full_content_project_threshold
            or analysis.observe_score >= news_config.full_content_observe_threshold
        )

    def _emit(self, name: str, payload: dict) -> None:
        if self.event_callback is not None:
            self.event_callback(name, payload)
