"""Page Agent adapter — thin tool-layer wrapper around browser automation.

Do NOT call this from UI code.
Do NOT store conversation state here.
This is a pure tool adapter.
"""
from __future__ import annotations
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class PageAgentAdapter:
    """Wraps a fetch_page callable as a structured browser tool.

    All methods return plain data (str / list). No side effects on
    conversation state.
    """

    def __init__(self, fetch_fn: Callable[[str], str] | None = None) -> None:
        self._fetch = fetch_fn

    def open_url(self, url: str) -> str:
        """Open URL and return raw page text."""
        if self._fetch is None:
            return ""
        try:
            return self._fetch(url) or ""
        except Exception as exc:
            logger.warning("page_agent open_url failed url=%s err=%s", url[:60], exc)
            return ""

    def extract_main_text(self, url: str) -> str:
        """Open URL and return stripped main text."""
        raw = self.open_url(url)
        if not raw:
            return ""
        import re
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()[:5000]

    def extract_links(self, url: str) -> list[str]:
        """Return hrefs found on a page."""
        import re
        raw = self.open_url(url)
        return re.findall(r'href=["\']?(https?://[^"\' >]+)', raw)[:30]

    def click(self, url: str, selector: str = "") -> str:
        """Stub: simulate click by re-fetching url (browser automation hook)."""
        logger.debug("page_agent click url=%s selector=%s", url[:60], selector)
        return self.open_url(url)

    def scroll(self, url: str, direction: str = "down") -> str:
        """Stub: scroll is a no-op at this layer."""
        return ""

    def screenshot(self, url: str) -> str:
        """Stub: returns empty string (requires headless browser extension)."""
        return ""
