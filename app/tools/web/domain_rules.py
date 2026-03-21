"""Domain rules — trust tiers and domain-based policy."""
from __future__ import annotations

HIGH_TRUST = frozenset([
    "wikipedia.org", "arxiv.org", "github.com", "stackoverflow.com",
    "reuters.com", "apnews.com", "bbc.com", "gov.cn", ".gov", ".edu",
    "developer.mozilla.org", "docs.python.org",
])

LOW_TRUST = frozenset([
    "content-farm", "clickbait", "fake-news",
])


def domain_trust(domain: str) -> float:
    d = (domain or "").lower()
    for h in HIGH_TRUST:
        if h in d:
            return 0.9
    for l in LOW_TRUST:
        if l in d:
            return 0.1
    return 0.5
