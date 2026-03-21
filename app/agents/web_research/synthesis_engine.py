"""Synthesis engine — produces answer summary from ranked sources."""
from __future__ import annotations
import logging
from app.agents.web_research.models import RankedSource
from app.agents.web_research.query_analyzer import ResearchIntent

logger = logging.getLogger(__name__)


class SynthesisEngine:
    """Build a direct answer from scored sources."""

    def synthesize(
        self,
        query: str,
        sources: list[RankedSource],
        intent: ResearchIntent,
        max_sources: int = 5,
    ) -> tuple[str, bool, str]:
        """Return (answer_text, has_conflict, conflict_note)."""
        top = [s for s in sources if s.final_score > 0.1][:max_sources]
        if not top:
            return "\u672a\u627e\u5230\u76f8\u5173\u7ed3\u679c\u3002", False, ""

        avg_agreement = sum(s.agreement_score for s in top) / len(top)
        has_conflict = avg_agreement < 0.25 and len(top) > 1
        conflict_note = ""
        if has_conflict:
            conflict_note = "\u6ce8\u610f\uff1a\u591a\u4e2a\u6765\u6e90\u5bf9\u6b64\u95ee\u9898\u5b58\u5728\u4e0d\u540c\u770b\u6cd5\u3002"
            logger.info("synthesis_conflict_detected query=%s avg_agreement=%.2f", query[:40], avg_agreement)

        parts: list[str] = []
        for i, s in enumerate(top[:3]):
            excerpt = ""
            if s.evidence and s.evidence.main_text:
                excerpt = s.evidence.main_text[:300].strip()
            else:
                excerpt = s.candidate.snippet[:200].strip()
            if excerpt:
                parts.append(f"[{i+1}] {excerpt}")

        answer = "\n\n".join(parts) if parts else "\u672a\u627e\u5230\u8db3\u591f\u4fe1\u606f\u3002"
        logger.info("synthesis_complete query=%s sources=%d conflict=%s", query[:40], len(top), has_conflict)
        return answer, has_conflict, conflict_note
