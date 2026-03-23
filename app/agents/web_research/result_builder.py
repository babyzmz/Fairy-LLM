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
        query: str = "",
        attempted_queries: list[str] | None = None,
    ) -> WebResearchResult:
        attempted = [item for item in list(attempted_queries or []) if str(item or "").strip()]
        if reason == "cancelled":
            msg = "\u672c\u6b21\u68c0\u7d22\u5df2\u53d6\u6d88\u3002"
            card_payload: dict[str, Any] = {
                "type": "generic_info",
                "data": {
                    "title": "\u5df2\u53d6\u6d88",
                    "summary": msg,
                    "fields": [{"label": "\u539f\u56e0", "value": "cancelled"}],
                },
            }
        elif reason == "no_search_results":
            msg = "\u5df2\u5c1d\u8bd5\u591a\u79cd\u68c0\u7d22\u65b9\u5f0f\uff0c\u4f46\u6682\u672a\u627e\u5230\u53ef\u9760\u7ed3\u679c\u3002"
            fields = []
            if query.strip():
                fields.append({"label": "\u539f\u59cb\u67e5\u8be2", "value": query.strip()})
            if attempted:
                fields.append({"label": "\u5df2\u5c1d\u8bd5\u67e5\u8be2", "value": " | ".join(attempted[:4])})
            card_payload = {
                "type": "generic_info",
                "data": {
                    "title": "\u672a\u627e\u5230\u53ef\u9760\u7ed3\u679c",
                    "summary": msg,
                    "fields": fields,
                },
            }
        else:
            msg = "\u641c\u7d22\u672a\u80fd\u83b7\u5f97\u6709\u6548\u7ed3\u679c\u3002"
            card_payload = {
                "type": "generic_info",
                "data": {
                    "title": "\u68c0\u7d22\u5931\u8d25",
                    "summary": msg,
                    "fields": [{"label": "reason", "value": reason}],
                },
            }
        return WebResearchResult(
            success=False,
            answer=msg,
            speech_text=msg,
            card_type="generic_info",
            card_payload=card_payload,
            error_message=reason,
            subtype=subtype,
            request_id=request_id,
        )
