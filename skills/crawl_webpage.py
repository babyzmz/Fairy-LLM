from __future__ import annotations

from dataclasses import dataclass

import requests

from app.config import llm_config
from utils.browser_automation import browser_automation


@dataclass(slots=True)
class CrawledPage:
    url: str
    final_url: str
    html: str
    status_code: int | None = None
    title: str = ""
    blocked_reason: str = ""


def crawl_webpage(url: str, timeout_sec: int = 12) -> CrawledPage:
    if llm_config.browser_automation_enabled and browser_automation.available():
        try:
            fetched = browser_automation.open_page(
                url,
                timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                capture_screenshot=False,
            )
            return CrawledPage(
                url=url,
                final_url=fetched.final_url,
                html=fetched.html,
                status_code=fetched.status_code,
                title=fetched.title,
                blocked_reason=fetched.blocked_reason,
            )
        except Exception:
            pass

    request_kwargs = {
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            )
        },
        "timeout": timeout_sec,
    }
    try:
        response = requests.get(url, **request_kwargs)
        response.raise_for_status()
    except requests.exceptions.SSLError:
        response = requests.get(url, verify=False, **request_kwargs)
        response.raise_for_status()
    return CrawledPage(
        url=url,
        final_url=str(response.url),
        html=response.text,
        status_code=response.status_code,
    )
