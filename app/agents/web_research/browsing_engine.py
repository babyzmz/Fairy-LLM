"""Browsing engine — opens pages and delegates to content extractor."""
from __future__ import annotations
import logging
from typing import Callable
from app.agents.web_research.models import ExtractedEvidence

logger = logging.getLogger(__name__)


class BrowsingEngine:
    """Opens URLs and returns ExtractedEvidence.

    Uses fetch_page_fn (injected) so no direct HTTP dependency here.
    Falls back gracefully if page load fails.
    """

    def __init__(self, fetch_page_fn: Callable[[str], str] | None = None) -> None:
        self._fetch = fetch_page_fn

    def open_url(self, url: str) -> str:
        """Return raw page text or empty string on failure."""
        if self._fetch is None:
            return ""
        try:
            text = self._fetch(url)
            logger.debug("browsing_engine opened url=%s chars=%d", url[:60], len(text or ""))
            return text or ""
        except Exception as exc:
            logger.warning("browsing_engine open_failed url=%s error=%s", url[:60], exc)
            return ""

    def extract(self, url: str, title: str = "", snippet: str = "") -> ExtractedEvidence:
        """Open a URL and return structured evidence."""
        raw = self.open_url(url)
        if not raw:
            return ExtractedEvidence(
                url=url, title=title, main_text=snippet,
                extraction_ok=False, error="page_load_failed",
            )
        from app.agents.web_research.content_extractor import ContentExtractor
        return ContentExtractor.extract(url=url, raw_html=raw, fallback_title=title)
