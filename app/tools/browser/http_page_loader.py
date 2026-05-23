from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ai.llm_client_file_processor import FileProcessor
from app.config import llm_config
from app.tools.browser.web_fetch import fetch_http_resource
from utils.web_content_extractor import extract_web_content


@dataclass(slots=True)
class CrawledPage:
    url: str
    final_url: str
    html: str
    status_code: int | None = None
    title: str = ""
    blocked_reason: str = ""
    content_type: str = ""
    text_content: str = ""
    persisted_path: str = ""
    persisted_size: int | None = None
    redirect_url: str = ""
    redirect_status_code: int | None = None
    cache_hit: bool = False


def crawl_webpage(url: str, timeout_sec: int = 12) -> CrawledPage:
    fetched = fetch_http_resource(url, timeout_sec=timeout_sec)
    return CrawledPage(
        url=fetched.requested_url,
        final_url=fetched.final_url,
        html=fetched.html,
        status_code=fetched.status_code,
        title=fetched.title,
        blocked_reason=fetched.blocked_reason,
        content_type=fetched.content_type,
        text_content=fetched.text_content,
        persisted_path=fetched.persisted_path,
        persisted_size=fetched.persisted_size,
        redirect_url=fetched.redirect_url,
        redirect_status_code=fetched.redirect_status_code,
        cache_hit=fetched.cache_hit,
    )


def read_webpage(url: str, timeout_sec: int = 12) -> str:
    try:
        crawled = crawl_webpage(url, timeout_sec=timeout_sec)
        if crawled.text_content.strip():
            return crawled.text_content[:6000]

        extracted = extract_web_content(crawled.html)
        parts: list[str] = []
        if extracted.meta_description:
            parts.append(extracted.meta_description)
        if extracted.body_text:
            parts.append(extracted.body_text)
        merged = "\n".join(parts).strip()
        if merged:
            return merged[:6000]

        if crawled.persisted_path:
            text = FileProcessor(llm_config).extract_file_text(Path(crawled.persisted_path)).strip()
            if text:
                return text[:6000]
    except Exception:
        pass
    return ""
