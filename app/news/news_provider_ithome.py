from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from hashlib import sha1

import requests
from bs4 import BeautifulSoup

from app.config import news_config
from app.news.news_models import NewsArticle
from app.news.news_provider_base import BaseNewsProvider
from skills.crawl_webpage import crawl_webpage
from utils.web_content_extractor import extract_web_content


logger = logging.getLogger(__name__)


class ITHomeNewsProvider(BaseNewsProvider):
    provider_name = "ithome"

    def __init__(self, rss_url: str | None = None, timeout_sec: int | None = None) -> None:
        self.rss_url = rss_url or news_config.ithome_rss_url
        self.timeout_sec = timeout_sec or news_config.request_timeout_sec

    def fetch_headlines(self, limit: int = 50) -> list[NewsArticle]:
        response = requests.get(
            self.rss_url,
            timeout=self.timeout_sec,
            headers={"User-Agent": "Mozilla/5.0 (Fairy News Fetcher)"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        channel = root.find("channel")
        if channel is None:
            return []
        items = channel.findall("item")
        articles: list[NewsArticle] = []
        for item in items[:limit]:
            try:
                article = self._parse_item(item)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse ITHome item: %s", exc)
                continue
            articles.append(article)
        return articles

    def fetch_article_content(self, article: NewsArticle) -> NewsArticle:
        page = crawl_webpage(article.url, timeout_sec=self.timeout_sec)
        if not page.html.strip():
            return article
        extracted = extract_web_content(page.html)
        summary = article.summary or extracted.meta_description or self._summarize_body(extracted.body_text)
        return NewsArticle(
            source=article.source,
            provider=article.provider,
            article_id=article.article_id,
            title=article.title,
            url=article.url,
            published_at=article.published_at,
            author=article.author,
            category=article.category,
            summary=summary,
            content=extracted.body_text[:20000] or article.content,
            raw_html=page.html[:120000],
            tags=list(article.tags),
            dedupe_hash=article.dedupe_hash,
            fetched_at=datetime.utcnow(),
        )

    def _parse_item(self, item: ET.Element) -> NewsArticle:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or url).strip()
        raw_description = item.findtext("description") or ""
        summary = BeautifulSoup(raw_description, "html.parser").get_text(" ", strip=True)
        pub_date_text = (item.findtext("pubDate") or "").strip()
        published_at = parsedate_to_datetime(pub_date_text).astimezone().replace(tzinfo=None) if pub_date_text else None
        author = (item.findtext("author") or "").strip() or None
        category = (item.findtext("category") or "").strip() or None
        article_id = self._build_article_id(guid or url or title)
        article = NewsArticle(
            source="IT之家",
            provider=self.provider_name,
            article_id=article_id,
            title=title,
            url=url,
            published_at=published_at,
            author=author,
            category=category,
            summary=summary[:1000] or None,
            fetched_at=datetime.utcnow(),
        )
        article.ensure_hash()
        return article

    def _build_article_id(self, raw: str) -> str:
        match = re.search(r"/html/(\d+)\.htm", raw)
        if match:
            return f"ithome-{match.group(1)}"
        return f"ithome-{sha1(raw.encode('utf-8')).hexdigest()[:16]}"

    def _summarize_body(self, body_text: str) -> str:
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        return " ".join(lines[:3])[:800]
