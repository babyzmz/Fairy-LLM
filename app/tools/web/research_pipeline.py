from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from app.tools.browser.http_page_loader import CrawledPage, crawl_webpage
from app.tools.search.html_search import search_web_queries
from utils.web_content_extractor import extract_web_content
from utils.web_context_compressor import compress_web_text


ProgressCallback = Callable[[str, dict[str, Any] | None], None]


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    query: str
    provider: str
    query_order: int
    result_rank: int


@dataclass(slots=True)
class WebPage:
    title: str
    url: str
    content: str
    query: str
    provider: str


@dataclass(slots=True)
class WebResearchResult:
    query_plan: list[str]
    pages: list[WebPage]


def search_stage(
    query_plan: list[str],
    *,
    max_results: int,
    timeout_sec: int,
    preferred_domains: tuple[str, ...],
    progress_callback: ProgressCallback | None,
) -> list[SearchHit]:
    if progress_callback is not None:
        for item in query_plan:
            progress_callback("searching", {"query": item})

    raw_results = search_web_queries(
        query_plan,
        max_results_per_query=max_results,
        timeout_sec=timeout_sec,
        preferred_domains=preferred_domains,
    )
    if preferred_domains:
        def score(result: dict[str, Any]) -> tuple[int, int, int]:
            domain = urlparse(result["url"]).netloc.lower()
            preferred_hit = 0 if any(hint in domain for hint in preferred_domains) else 1
            return (preferred_hit, int(result.get("query_order", 0)), int(result.get("result_rank", 0)))
        raw_results.sort(key=score)

    return [
        SearchHit(
            title=str(result["title"]),
            url=str(result["url"]),
            snippet=str(result.get("snippet", "")),
            query=str(result.get("query", "")),
            provider=str(result.get("provider", "search")),
            query_order=int(result.get("query_order", 0)),
            result_rank=int(result.get("result_rank", 0)),
        )
        for result in raw_results
    ]


def crawl_stage(
    hit: SearchHit,
    *,
    timeout_sec: int,
    progress_callback: ProgressCallback | None,
) -> CrawledPage | None:
    if progress_callback is not None:
        progress_callback("reading_webpage", {"url": hit.url, "title": hit.title})
    try:
        return crawl_webpage(hit.url, timeout_sec=timeout_sec)
    except Exception:
        return None


def extract_stage(crawled: CrawledPage | None) -> str:
    if crawled is None:
        return ""
    if crawled.text_content.strip():
        return crawled.text_content.strip()
    if not crawled.html.strip():
        return ""
    try:
        extracted = extract_web_content(crawled.html)
    except Exception:
        return ""

    parts: list[str] = []
    if extracted.meta_description:
        parts.append(extracted.meta_description)
    if extracted.body_text:
        parts.append(extracted.body_text)
    return "\n".join(parts).strip()


def run_web_research(
    query: str | Iterable[str],
    *,
    max_results: int = 5,
    max_pages: int = 3,
    max_chars: int = 2500,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> WebResearchResult:
    query_plan = [query] if isinstance(query, str) else [q for q in query if str(q).strip()]
    preferred = tuple(domain.lower() for domain in (preferred_domains or []))
    hits = search_stage(
        query_plan,
        max_results=max_results,
        timeout_sec=timeout_sec,
        preferred_domains=preferred,
        progress_callback=progress_callback,
    )

    pages: list[WebPage] = []
    for hit in hits[:max_pages]:
        crawled = crawl_stage(hit, timeout_sec=timeout_sec, progress_callback=progress_callback)
        text = extract_stage(crawled)

        if not text.strip():
            text = hit.snippet.strip()
        if not text.strip():
            text = f"搜索结果标题：{hit.title}"
        if not text.strip():
            continue

        compressed = compress_web_text(text, max_chars=max_chars)
        if not compressed:
            continue
        pages.append(
            WebPage(
                title=hit.title,
                url=hit.url,
                content=compressed,
                query=hit.query,
                provider=hit.provider,
            )
        )

    return WebResearchResult(query_plan=list(query_plan), pages=pages)
