from __future__ import annotations

from typing import Any, Callable, Iterable

from skills.web_research import run_web_research

ProgressCallback = Callable[[str, dict[str, Any] | None], None]


def web_search_skill(
    query: str | Iterable[str],
    *,
    max_results: int = 5,
    max_pages: int = 3,
    max_chars: int = 2500,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, str]]:
    research = run_web_research(
        query,
        max_results=max_results,
        max_pages=max_pages,
        max_chars=max_chars,
        timeout_sec=timeout_sec,
        preferred_domains=preferred_domains,
        progress_callback=progress_callback,
    )
    return [
        {
            "title": page.title,
            "url": page.url,
            "content": page.content,
            "query": page.query,
            "provider": page.provider,
        }
        for page in research.pages
    ]
