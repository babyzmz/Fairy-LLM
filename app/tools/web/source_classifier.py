"""Source classifier — categorises URLs into source types."""
from __future__ import annotations
import re

_NEWS_DOMAINS = frozenset(["bbc", "cnn", "reuters", "xinhua", "163.com",
                            "sohu", "sina", "theguardian", "nytimes", "apnews"])
_DOCS_DOMAINS = frozenset(["docs.", "developer.", "readthedocs", "man7.org",
                            "cppreference", "devdocs"])
_WIKI_DOMAINS  = frozenset(["wikipedia", "wikimedia"])


def classify_source(url: str) -> str:
    """Return: news | docs | wiki | social | generic."""
    d = (url or "").lower()
    if any(n in d for n in _NEWS_DOMAINS):
        return "news"
    if any(n in d for n in _DOCS_DOMAINS):
        return "docs"
    if any(n in d for n in _WIKI_DOMAINS):
        return "wiki"
    if any(n in d for n in ("reddit", "twitter", "x.com", "weibo", "zhihu")):
        return "social"
    return "generic"
