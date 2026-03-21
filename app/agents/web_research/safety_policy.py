"""Safety policy — enforces no-hallucination and source sufficiency rules."""
from __future__ import annotations
from app.agents.web_research.models import RankedSource


class SafetyPolicy:
    """Gate keeper before synthesis.

    Returns (ok, reason). If ok is False, agent must return failure result.
    """

    MIN_SOURCES = 1
    MIN_EVIDENCE_SCORE = 0.05

    @classmethod
    def check(
        cls,
        sources: list[RankedSource],
        query: str,
    ) -> tuple[bool, str]:
        if not sources:
            return False, "no_sources_found"
        usable = [s for s in sources if s.final_score >= cls.MIN_EVIDENCE_SCORE]
        if len(usable) < cls.MIN_SOURCES:
            return False, "evidence_too_weak"
        return True, ""
