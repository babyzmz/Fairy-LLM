"""app.agents.web_research public API."""
from app.agents.web_research.agent import WebResearchAgent
from app.agents.web_research.contract import (
    WebResearchRequest, WebResearchResult, WebResearchError,
    SearchCandidate, ExtractedEvidence, RankedSource, Citation,
)

__all__ = [
    "WebResearchAgent",
    "WebResearchRequest",
    "WebResearchResult",
    "WebResearchError",
    "SearchCandidate",
    "ExtractedEvidence",
    "RankedSource",
    "Citation",
]
