"""Candidate ranker — scores and sorts SearchCandidates."""
from __future__ import annotations
import re
from app.agents.web_research.models import SearchCandidate
from app.agents.web_research.query_analyzer import ResearchIntent

_TRUSTED_DOMAINS = frozenset([
    "wikipedia.org", "github.com", "stackoverflow.com",
    "docs.python.org", "developer.mozilla.org", "arxiv.org",
    "reuters.com", "bbc.com", "ap.org", "xinhua.net",
    "gov.cn", ".gov", ".edu",
])


def _domain_from_url(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def _trust_score(domain: str) -> float:
    for td in _TRUSTED_DOMAINS:
        if td in domain:
            return 0.85
    if any(x in domain for x in ("spam", "click", "ad.", "ads.")):
        return 0.1
    return 0.5


def _freshness_score(snippet: str, recency_required: bool) -> float:
    if re.search(r"202[3-9]|2030", snippet or ""):
        return 0.9
    if re.search(r"202[0-2]", snippet or ""):
        return 0.5
    return 0.1 if recency_required else 0.5


def _relevance_score(query: str, title: str, snippet: str) -> float:
    q_words = set(re.findall(r"\w+", (query or "").lower()))
    text = ((title or "") + " " + (snippet or "")).lower()
    hits = sum(1 for w in q_words if w in text)
    return min(1.0, hits / max(1, len(q_words)))


class CandidateRanker:
    """Rank a list of SearchCandidates by composite score."""

    def rank(
        self,
        candidates: list[SearchCandidate],
        query: str,
        intent: ResearchIntent,
        prefer_official: bool = False,
    ) -> list[SearchCandidate]:
        for c in candidates:
            c.domain = _domain_from_url(c.url)
            c.trust_score = _trust_score(c.domain)
            c.freshness_score = _freshness_score(c.snippet, intent.recency_required)
            c.relevance_score = _relevance_score(query, c.title, c.snippet)
            c.is_official = prefer_official and any(
                d in c.domain for d in (".gov", ".edu", "official", "xinhua")
            )
            official_boost = 0.15 if c.is_official else 0.0
            c.composite_score = (
                c.trust_score * 0.35
                + c.relevance_score * 0.40
                + c.freshness_score * 0.15
                + official_boost
                + (0.10 - c.rank * 0.01)  # slight original-rank bias
            )
        candidates.sort(key=lambda x: x.composite_score, reverse=True)
        return candidates
