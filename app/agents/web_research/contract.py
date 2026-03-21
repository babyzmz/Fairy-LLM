"""WebResearch agent contract — public re-exports."""
from app.agents.web_research.models import (
    WebResearchRequest,
    WebResearchResult,
    WebResearchError,
    SearchCandidate,
    ExtractedEvidence,
    RankedSource,
    Citation,
)

__all__ = [
    "WebResearchRequest",
    "WebResearchResult",
    "WebResearchError",
    "SearchCandidate",
    "ExtractedEvidence",
    "RankedSource",
    "Citation",
]
