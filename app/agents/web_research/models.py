"""WebResearch agent data models.

Working types only — no runtime logic here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

@dataclass
class WebResearchRequest:
    """Input to WebResearchAgent."""
    query: str
    session_id: str = ""
    request_id: str = ""
    max_sources: int = 5
    max_depth: int = 2          # 1=snippets only, 2=open pages, 3=deep crawl
    recency_days: int = 0       # 0 = no freshness constraint
    prefer_official: bool = False
    language: str = "zh"        # output language hint
    allowed_tools: list[str] = field(default_factory=lambda: ["search_web", "fetch_page"])
    strict_mode: bool = True


# ---------------------------------------------------------------------------
# Internal working types
# ---------------------------------------------------------------------------

@dataclass
class SearchCandidate:
    """A single raw search result candidate."""
    title: str
    url: str
    snippet: str
    rank: int = 0
    domain: str = ""
    is_official: bool = False
    freshness_score: float = 0.0   # 0–1, higher = more recent
    relevance_score: float = 0.0   # 0–1
    trust_score: float = 0.0       # 0–1
    composite_score: float = 0.0   # weighted final rank score


@dataclass
class ExtractedEvidence:
    """Content extracted from a single web page."""
    url: str
    title: str
    main_text: str
    publish_date: str = ""
    author: str = ""
    domain: str = ""
    language: str = ""
    word_count: int = 0
    numbers: list[str] = field(default_factory=list)
    tables: list[list[str]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    extraction_ok: bool = True
    error: str = ""


@dataclass
class RankedSource:
    """A search candidate after ranking + optional evidence extraction."""
    candidate: SearchCandidate
    evidence: ExtractedEvidence | None = None
    trust_score: float = 0.0
    relevance_score: float = 0.0
    agreement_score: float = 0.0   # agreement with other sources
    evidence_strength: float = 0.0 # overall evidence quality
    final_score: float = 0.0


@dataclass
class Citation:
    """A citable source in the final result."""
    index: int
    title: str
    url: str
    domain: str
    snippet: str = ""
    publish_date: str = ""
    is_official: bool = False


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class WebResearchResult:
    """Output from WebResearchAgent."""
    success: bool
    answer: str                              # direct answer / summary
    speech_text: str                         # TTS-ready text
    card_type: str = "web_research_card"    # UI card type
    card_payload: dict[str, Any] = field(default_factory=dict)
    citations: list[Citation] = field(default_factory=list)
    sources_searched: int = 0
    sources_used: int = 0
    confidence: float = 0.0
    has_conflict: bool = False
    conflict_note: str = ""
    error_message: str = ""
    subtype: str = "general"                 # news/product/docs/comparison/general
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "answer": self.answer,
            "speech_text": self.speech_text,
            "card_type": self.card_type,
            "card": self.card_payload,
            "citations": [{"index": c.index, "title": c.title, "url": c.url,
                           "domain": c.domain, "snippet": c.snippet,
                           "publish_date": c.publish_date, "is_official": c.is_official}
                          for c in self.citations],
            "sources_searched": self.sources_searched,
            "sources_used": self.sources_used,
            "confidence": self.confidence,
            "has_conflict": self.has_conflict,
            "conflict_note": self.conflict_note,
            "error_message": self.error_message,
            "subtype": self.subtype,
            "request_id": self.request_id,
            "tool_lock": True,
        }


@dataclass
class WebResearchError:
    """Structured failure for WebResearchAgent."""
    reason: str
    retryable: bool = False
    query: str = ""

    def to_result(self) -> WebResearchResult:
        msg = "\u641c\u7d22\u6682\u65f6\u65e0\u6cd5\u83b7\u53d6\u7ed3\u679c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002" if self.retryable else "\u641c\u7d22\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u8fde\u63a5\u3002"
        return WebResearchResult(
            success=False,
            answer=msg,
            speech_text=msg,
            error_message=self.reason,
            card_payload={"type": "error_card", "error": self.reason},
        )
