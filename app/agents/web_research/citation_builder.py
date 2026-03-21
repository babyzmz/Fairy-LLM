"""Citation builder — produces Citation objects from RankedSources."""
from __future__ import annotations
from app.agents.web_research.models import RankedSource, Citation


class CitationBuilder:
    @staticmethod
    def build(sources: list[RankedSource], max_citations: int = 5) -> list[Citation]:
        citations: list[Citation] = []
        for i, s in enumerate(sources[:max_citations]):
            cand = s.candidate
            date = ""
            if s.evidence:
                date = s.evidence.publish_date
            citations.append(Citation(
                index=i + 1,
                title=cand.title or cand.url,
                url=cand.url,
                domain=cand.domain,
                snippet=cand.snippet[:120],
                publish_date=date,
                is_official=cand.is_official,
            ))
        return citations
