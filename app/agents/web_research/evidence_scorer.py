"""Evidence scorer — scores each RankedSource for trust, relevance, agreement."""
from __future__ import annotations
import re
from app.agents.web_research.models import RankedSource


class EvidenceScorer:
    """Score each source independently, then compute inter-source agreement."""

    def score(self, sources: list[RankedSource], query: str) -> list[RankedSource]:
        for s in sources:
            s.trust_score = s.candidate.trust_score
            s.relevance_score = self._relevance(query, s)
        self._compute_agreement(sources)
        for s in sources:
            s.evidence_strength = s.candidate.composite_score
            s.final_score = (
                s.trust_score * 0.30
                + s.relevance_score * 0.35
                + s.agreement_score * 0.20
                + s.evidence_strength * 0.15
            )
        sources.sort(key=lambda x: x.final_score, reverse=True)
        return sources

    def _relevance(self, query: str, source: RankedSource) -> float:
        q_words = set(re.findall(r"\w+", (query or "").lower()))
        text = ""
        if source.evidence:
            text = (source.evidence.title + " " + source.evidence.main_text[:1000]).lower()
        else:
            text = (source.candidate.title + " " + source.candidate.snippet).lower()
        hits = sum(1 for w in q_words if w in text)
        return min(1.0, hits / max(1, len(q_words)))

    def _compute_agreement(self, sources: list[RankedSource]) -> None:
        """Simple agreement: does the source share keywords with the majority?"""
        if len(sources) < 2:
            for s in sources:
                s.agreement_score = 1.0
            return
        all_texts = []
        for s in sources:
            t = ""
            if s.evidence:
                t = s.evidence.main_text[:500]
            else:
                t = s.candidate.snippet
            all_texts.append(t.lower())
        for i, s in enumerate(sources):
            own = set(re.findall(r"\w{4,}", all_texts[i]))
            others = [set(re.findall(r"\w{4,}", all_texts[j])) for j in range(len(sources)) if j != i]
            if not own or not others:
                s.agreement_score = 0.5
                continue
            avg_overlap = sum(len(own & o) / max(1, len(own)) for o in others) / len(others)
            s.agreement_score = min(1.0, avg_overlap * 2)
