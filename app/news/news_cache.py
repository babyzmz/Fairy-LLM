from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import news_config
from app.news.news_models import NewsAnalysis, NewsArticle, NewsBriefing, analysis_from_row, article_from_row


class NewsCache:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or news_config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    author TEXT,
                    category TEXT,
                    summary TEXT,
                    content TEXT,
                    raw_html TEXT,
                    tags TEXT,
                    dedupe_hash TEXT UNIQUE,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analyses (
                    article_id TEXT PRIMARY KEY,
                    relevance_score REAL NOT NULL,
                    project_relevance_score REAL NOT NULL,
                    observe_score REAL NOT NULL,
                    hype_score REAL NOT NULL,
                    action TEXT NOT NULL,
                    reasons TEXT,
                    matched_interests TEXT,
                    matched_project_topics TEXT,
                    short_comment TEXT,
                    analyzed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS briefings (
                    briefing_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    rendered_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    article_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    published_at TEXT,
                    tags TEXT,
                    reasons TEXT,
                    short_comment TEXT,
                    observe_score REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert_article(self, article: NewsArticle) -> None:
        article.ensure_hash()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT article_id FROM articles WHERE dedupe_hash = ? LIMIT 1",
                (article.dedupe_hash,),
            ).fetchone()
            if existing and str(existing["article_id"]) and str(existing["article_id"]) != article.article_id:
                article.article_id = str(existing["article_id"])
            conn.execute(
                """
                INSERT INTO articles (
                    article_id, source, provider, title, url, published_at, author, category,
                    summary, content, raw_html, tags, dedupe_hash, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    source=excluded.source,
                    provider=excluded.provider,
                    title=excluded.title,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    author=excluded.author,
                    category=excluded.category,
                    summary=excluded.summary,
                    content=COALESCE(excluded.content, articles.content),
                    raw_html=COALESCE(excluded.raw_html, articles.raw_html),
                    tags=excluded.tags,
                    dedupe_hash=excluded.dedupe_hash,
                    fetched_at=excluded.fetched_at
                """,
                (
                    article.article_id,
                    article.source,
                    article.provider,
                    article.title,
                    article.url,
                    article.published_at.isoformat() if article.published_at else None,
                    article.author,
                    article.category,
                    article.summary,
                    article.content,
                    article.raw_html,
                    json.dumps(article.tags, ensure_ascii=False),
                    article.dedupe_hash,
                    article.fetched_at.isoformat(),
                ),
            )

    def save_analysis(self, article_id: str, analysis: NewsAnalysis) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses (
                    article_id, relevance_score, project_relevance_score, observe_score, hype_score,
                    action, reasons, matched_interests, matched_project_topics, short_comment, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    relevance_score=excluded.relevance_score,
                    project_relevance_score=excluded.project_relevance_score,
                    observe_score=excluded.observe_score,
                    hype_score=excluded.hype_score,
                    action=excluded.action,
                    reasons=excluded.reasons,
                    matched_interests=excluded.matched_interests,
                    matched_project_topics=excluded.matched_project_topics,
                    short_comment=excluded.short_comment,
                    analyzed_at=excluded.analyzed_at
                """,
                (
                    article_id,
                    analysis.relevance_score,
                    analysis.project_relevance_score,
                    analysis.observe_score,
                    analysis.hype_score,
                    analysis.action,
                    json.dumps(analysis.reasons, ensure_ascii=False),
                    json.dumps(analysis.matched_interests, ensure_ascii=False),
                    json.dumps(analysis.matched_project_topics, ensure_ascii=False),
                    analysis.short_comment,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_article_by_url(self, url: str) -> NewsArticle | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM articles WHERE url = ? LIMIT 1", (url,)).fetchone()
        return article_from_row(dict(row)) if row else None

    def get_recent_articles(self, provider: str, limit: int = 50) -> list[NewsArticle]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM articles
                WHERE provider = ?
                ORDER BY COALESCE(published_at, fetched_at) DESC, fetched_at DESC
                LIMIT ?
                """,
                (provider, limit),
            ).fetchall()
        return [article_from_row(dict(row)) for row in rows]

    def get_analysis(self, article_id: str) -> NewsAnalysis | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM analyses WHERE article_id = ? LIMIT 1", (article_id,)).fetchone()
        return analysis_from_row(dict(row)) if row else None

    def get_recent_analyzed(self, provider: str, limit: int = 100) -> list[tuple[NewsArticle, NewsAnalysis]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, n.relevance_score, n.project_relevance_score, n.observe_score, n.hype_score,
                       n.action, n.reasons, n.matched_interests, n.matched_project_topics, n.short_comment,
                       n.analyzed_at
                FROM articles a
                JOIN analyses n ON n.article_id = a.article_id
                WHERE a.provider = ?
                ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.fetched_at DESC
                LIMIT ?
                """,
                (provider, limit),
            ).fetchall()
        pairs: list[tuple[NewsArticle, NewsAnalysis]] = []
        for row in rows:
            row_dict = dict(row)
            pairs.append((article_from_row(row_dict), analysis_from_row(row_dict)))
        return pairs

    def save_briefing(self, provider: str, day_key: str, briefing: NewsBriefing) -> None:
        key = f"{provider}:{day_key}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO briefings (briefing_key, provider, generated_at, rendered_text, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(briefing_key) DO UPDATE SET
                    generated_at=excluded.generated_at,
                    rendered_text=excluded.rendered_text,
                    payload_json=excluded.payload_json
                """,
                (
                    key,
                    provider,
                    briefing.generated_at.isoformat(),
                    briefing.render_text(),
                    json.dumps(briefing.to_dict(), ensure_ascii=False),
                ),
            )

    def get_briefing(self, provider: str, day_key: str) -> NewsBriefing | None:
        key = f"{provider}:{day_key}"
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM briefings WHERE briefing_key = ? LIMIT 1", (key,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        from app.news.news_briefing import briefing_from_dict

        return briefing_from_dict(payload)

    def add_observation(self, article: NewsArticle, analysis: NewsAnalysis) -> None:
        article.ensure_hash()
        observation_id = article.dedupe_hash or article.article_id
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, article_id, title, url, source, provider, published_at,
                    tags, reasons, short_comment, observe_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    reasons=excluded.reasons,
                    short_comment=excluded.short_comment,
                    observe_score=excluded.observe_score
                """,
                (
                    observation_id,
                    article.article_id,
                    article.title,
                    article.url,
                    article.source,
                    article.provider,
                    article.published_at.isoformat() if article.published_at else None,
                    json.dumps(article.tags, ensure_ascii=False),
                    json.dumps(analysis.reasons, ensure_ascii=False),
                    analysis.short_comment,
                    analysis.observe_score,
                    datetime.utcnow().isoformat(),
                ),
            )

    def list_observations(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                ORDER BY COALESCE(published_at, created_at) DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
