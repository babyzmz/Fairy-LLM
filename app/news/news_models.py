from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    return cleaned.lower()


def normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (url or "").strip()
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "", ""))


def build_dedupe_hash(title: str, url: str, published_at: datetime | None, provider: str, source: str) -> str:
    payload = "||".join(
        [
            normalize_title(title),
            normalize_url(url),
            published_at.isoformat() if published_at else "",
            provider.strip().lower(),
            source.strip().lower(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class NewsArticle:
    source: str
    provider: str
    article_id: str
    title: str
    url: str
    published_at: datetime | None
    author: str | None = None
    category: str | None = None
    summary: str | None = None
    content: str | None = None
    raw_html: str | None = None
    tags: list[str] = field(default_factory=list)
    dedupe_hash: str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    def ensure_hash(self) -> None:
        if not self.dedupe_hash:
            self.dedupe_hash = build_dedupe_hash(
                self.title,
                self.url,
                self.published_at,
                self.provider,
                self.source,
            )

    def to_dict(self) -> dict[str, Any]:
        self.ensure_hash()
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        data["fetched_at"] = self.fetched_at.isoformat()
        return data


@dataclass(slots=True)
class NewsAnalysis:
    relevance_score: float
    project_relevance_score: float
    observe_score: float
    hype_score: float
    action: str
    reasons: list[str] = field(default_factory=list)
    matched_interests: list[str] = field(default_factory=list)
    matched_project_topics: list[str] = field(default_factory=list)
    short_comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NewsBriefingItem:
    article_id: str
    title: str
    url: str
    source: str
    published_at: datetime | None
    action: str
    tags: list[str]
    reasons: list[str]
    short_comment: str
    relevance_score: float
    project_relevance_score: float
    observe_score: float
    hype_score: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return data


@dataclass(slots=True)
class NewsBriefing:
    provider: str
    generated_at: datetime
    total_count: int
    top_items: list[NewsBriefingItem] = field(default_factory=list)
    project_related: list[NewsBriefingItem] = field(default_factory=list)
    skipped_items: list[NewsBriefingItem] = field(default_factory=list)
    observed_items: list[NewsBriefingItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "generated_at": self.generated_at.isoformat(),
            "total_count": self.total_count,
            "top_items": [item.to_dict() for item in self.top_items],
            "project_related": [item.to_dict() for item in self.project_related],
            "skipped_items": [item.to_dict() for item in self.skipped_items],
            "observed_items": [item.to_dict() for item in self.observed_items],
        }

    def render_text(self) -> str:
        lines = [
            f"今日 {self.provider} 共抓取 {self.total_count} 条更新。",
            "",
            f"最值得看的 {len(self.top_items)} 条：",
        ]
        for idx, item in enumerate(self.top_items, 1):
            lines.extend(
                [
                    f"{idx}. {item.title}",
                    f"   - 原因：{'；'.join(item.reasons[:3]) or '命中了当前兴趣标签'}",
                    f"   - 建议动作：{item.action}",
                    f"   - Fairy 评语：{item.short_comment or '这条值得快速过一遍。'}",
                ]
            )
        if self.project_related:
            lines.append("")
            lines.append("与 Fairy 项目直接相关：")
            for item in self.project_related[:5]:
                lines.append(f"- {item.title}")
        if self.observed_items:
            lines.append("")
            lines.append("适合加入技术观察库：")
            for item in self.observed_items[:5]:
                lines.append(f"- {item.title}")
        if self.skipped_items:
            lines.append("")
            lines.append("可跳过的流量新闻：")
            for item in self.skipped_items[:5]:
                lines.append(f"- {item.title}")
        return "\n".join(lines).strip()


def article_from_row(row: dict[str, Any]) -> NewsArticle:
    return NewsArticle(
        source=str(row.get("source", "")),
        provider=str(row.get("provider", "")),
        article_id=str(row.get("article_id", "")),
        title=str(row.get("title", "")),
        url=str(row.get("url", "")),
        published_at=datetime.fromisoformat(row["published_at"]) if row.get("published_at") else None,
        author=row.get("author"),
        category=row.get("category"),
        summary=row.get("summary"),
        content=row.get("content"),
        raw_html=row.get("raw_html"),
        tags=json.loads(row["tags"]) if row.get("tags") else [],
        dedupe_hash=str(row.get("dedupe_hash", "")),
        fetched_at=datetime.fromisoformat(row["fetched_at"]) if row.get("fetched_at") else datetime.utcnow(),
    )


def analysis_from_row(row: dict[str, Any]) -> NewsAnalysis:
    return NewsAnalysis(
        relevance_score=float(row.get("relevance_score", 0.0)),
        project_relevance_score=float(row.get("project_relevance_score", 0.0)),
        observe_score=float(row.get("observe_score", 0.0)),
        hype_score=float(row.get("hype_score", 0.0)),
        action=str(row.get("action", "skip")),
        reasons=json.loads(row["reasons"]) if row.get("reasons") else [],
        matched_interests=json.loads(row["matched_interests"]) if row.get("matched_interests") else [],
        matched_project_topics=json.loads(row["matched_project_topics"]) if row.get("matched_project_topics") else [],
        short_comment=str(row.get("short_comment", "")),
    )
