"""Result builder — assembles WebResearchResult from synthesis output."""
from __future__ import annotations
from typing import Any
from app.agents.web_research.models import Citation, RankedSource, WebResearchResult
from app.agents.web_research.query_analyzer import ResearchIntent


_CARD_TYPE_MAP = {
    "news": "news_card",
    "product": "product_research_card",
    "docs": "docs_card",
    "comparison": "comparison_card",
    "company": "web_research_card",
    "general": "web_research_card",
}


class ResultBuilder:
    @staticmethod
    def success(
        query: str,
        answer: str,
        citations: list[Citation],
        sources: list[RankedSource],
        intent: ResearchIntent,
        sources_searched: int,
        has_conflict: bool = False,
        conflict_note: str = "",
        request_id: str = "",
    ) -> WebResearchResult:
        card_type = _CARD_TYPE_MAP.get(intent.subtype, "web_research_card")
        confidence = 0.0
        if sources:
            confidence = min(1.0, sum(s.final_score for s in sources[:3]) / 3)

        speech = answer.replace("\n\n", " ").replace("\n", " ").strip()
        # Prefix citation indices for TTS
        speech = speech[:500]

        card: dict[str, Any] = {
            "type": card_type,
            "query": query,
            "answer": answer[:800],
            "citations": [
                {"index": c.index, "title": c.title, "url": c.url, "domain": c.domain}
                for c in citations
            ],
            "confidence": round(confidence, 3),
            "has_conflict": has_conflict,
            "subtype": intent.subtype,
        }
        return WebResearchResult(
            success=True,
            answer=answer,
            speech_text=speech,
            card_type=card_type,
            card_payload=card,
            citations=citations,
            sources_searched=sources_searched,
            sources_used=len(sources),
            confidence=confidence,
            has_conflict=has_conflict,
            conflict_note=conflict_note,
            subtype=intent.subtype,
            request_id=request_id,
        )

    @staticmethod
    def failure(
        reason: str,
        subtype: str = "general",
        request_id: str = "",
    ) -> WebResearchResult:
        msg = "\u641c\u7d22\u672a\u80fd\u83b7\u5f97\u6709\u6548\u7ed3\u679c\u3002"
        return WebResearchResult(
            success=False,
            answer=msg,
            speech_text=msg,
            card_type="error_card",
            card_payload={"type": "error_card", "reason": reason},
            error_message=reason,
            subtype=subtype,
            request_id=request_id,
        )
