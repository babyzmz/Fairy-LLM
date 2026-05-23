"""Unit tests for WebResearchAgent — Part 1: components."""
from __future__ import annotations
import pytest
from app.agents.web_research.models import (
    WebResearchRequest, SearchCandidate, RankedSource,
)
from app.agents.web_research.query_analyzer import QueryAnalyzer
from app.agents.web_research.search_planner import SearchPlanner
from app.agents.web_research.candidate_ranker import CandidateRanker
from app.agents.web_research.content_extractor import ContentExtractor
from app.agents.web_research.evidence_scorer import EvidenceScorer
from app.agents.web_research.synthesis_engine import SynthesisEngine
from app.agents.web_research.safety_policy import SafetyPolicy
from app.agents.web_research.agent import WebResearchAgent

FAKE_RESULTS = [
    {"title": "Python 3.12 Release Notes", "url": "https://docs.python.org/3.12/",
     "snippet": "Python 3.12 introduces new type syntax in 2023."},
    {"title": "Real Python 3.12", "url": "https://realpython.com/python312/",
     "snippet": "Overview of Python 3.12 features released October 2023."},
    {"title": "Python Wikipedia", "url": "https://en.wikipedia.org/wiki/Python",
     "snippet": "Python 3.12 released 2023 with performance improvements."},
]

def _fake_search(query, max_results=8):
    return FAKE_RESULTS[:max_results]

def _fake_fetch(url):
    return (
        "<html><title>Test Page</title><body>"
        "<p>Python 3.12 released in 2023. PEP 695 type aliases. Price: $0.</p>"
        "</body></html>"
    )


class TestQueryAnalyzer:
    def test_news_detected(self):
        intent = QueryAnalyzer().analyze("\u6700\u65b0AI\u76d1\u7ba1\u653f\u7b56\u65b0\u95fb")
        assert intent.subtype == "news"
        assert intent.recency_required

    def test_comparison_detected(self):
        intent = QueryAnalyzer().analyze("iPhone 16 vs Samsung S25")
        assert intent.subtype == "comparison"
        assert intent.is_comparison

    def test_docs_detected(self):
        intent = QueryAnalyzer().analyze("Python 3.12 documentation tutorial")
        assert intent.subtype == "docs"

    def test_general_fallback(self):
        intent = QueryAnalyzer().analyze("\u6210\u90fd\u7684\u5386\u53f2")
        assert intent.subtype == "general"

    def test_verification_detected(self):
        intent = QueryAnalyzer().analyze("\u9a6c\u65af\u514b\u771f\u7684\u6536\u8d2d\u4e86Twitter\u5417")
        assert intent.is_verification

    def test_chinese_language_hint(self):
        assert QueryAnalyzer().analyze("\u5317\u4eac\u5929\u6c14").language_hint == "zh"

    def test_english_language_hint(self):
        assert QueryAnalyzer().analyze("latest AI news").language_hint == "en"


class TestSearchPlanner:
    def test_plan_primary_query(self):
        intent = QueryAnalyzer().analyze("Python 3.12")
        plan = SearchPlanner().plan("Python 3.12", intent)
        assert plan.primary_query == "Python 3.12"

    def test_backup_queries_present(self):
        intent = QueryAnalyzer().analyze("iPhone vs Samsung")
        intent.subtype = "comparison"
        plan = SearchPlanner().plan("iPhone vs Samsung", intent)
        assert len(plan.backup_queries) > 0

    def test_max_results_gte_max_sources(self):
        intent = QueryAnalyzer().analyze("test")
        plan = SearchPlanner().plan("test", intent, max_sources=3)
        assert plan.max_results >= 3


class TestCandidateRanker:
    def _cands(self):
        return [
            SearchCandidate(title="Docs", url="https://docs.python.org/3.12/",
                            snippet="Python 3.12 features 2023", rank=0),
            SearchCandidate(title="Spam", url="https://spam-site.com/click",
                            snippet="buy stuff", rank=1),
            SearchCandidate(title="Wiki", url="https://en.wikipedia.org/wiki/Python",
                            snippet="Python 3.12 released 2023", rank=2),
        ]

    def test_spam_not_top(self):
        intent = QueryAnalyzer().analyze("Python 3.12")
        ranked = CandidateRanker().rank(self._cands(), "Python 3.12", intent)
        assert "spam" not in ranked[0].domain

    def test_composite_score_set(self):
        intent = QueryAnalyzer().analyze("Python")
        for c in CandidateRanker().rank(self._cands(), "Python", intent):
            assert c.composite_score >= 0

    def test_sorted_descending(self):
        intent = QueryAnalyzer().analyze("Python")
        scores = [c.composite_score for c in
                  CandidateRanker().rank(self._cands(), "Python", intent)]
        assert scores == sorted(scores, reverse=True)


class TestContentExtractor:
    def test_title_extracted(self):
        ev = ContentExtractor.extract("https://example.com",
                                      "<title>Test</title><p>Hello</p>")
        assert ev.title == "Test"

    def test_tags_stripped(self):
        ev = ContentExtractor.extract("https://x.com", "<p>Hello <b>World</b></p>")
        assert "<b>" not in ev.main_text
        assert "Hello" in ev.main_text

    def test_numbers_extracted(self):
        ev = ContentExtractor.extract("https://shop.com", "<p>Price: $1,299.99 USD</p>")
        assert len(ev.numbers) > 0

    def test_date_extracted(self):
        ev = ContentExtractor.extract("https://x.com", "<p>2023-10-15</p>")
        assert "2023" in ev.publish_date


class TestEvidenceScorer:
    def _sources(self):
        return [
            RankedSource(candidate=SearchCandidate(
                title="A", url="https://wikipedia.org/a",
                snippet="Python 3.12 features", rank=0,
                composite_score=0.8, trust_score=0.85)),
            RankedSource(candidate=SearchCandidate(
                title="B", url="https://realpython.com/b",
                snippet="Python 3.12 improvements", rank=1,
                composite_score=0.6, trust_score=0.5)),
        ]

    def test_final_score_positive(self):
        for s in EvidenceScorer().score(self._sources(), "Python 3.12"):
            assert s.final_score >= 0

    def test_sorted_descending(self):
        scores = [s.final_score for s in
                  EvidenceScorer().score(self._sources(), "Python")]
        assert scores == sorted(scores, reverse=True)

    def test_agreement_in_range(self):
        for s in EvidenceScorer().score(self._sources(), "Python"):
            assert 0.0 <= s.agreement_score <= 1.0


class TestSynthesisEngine:
    def _sources(self, n=3):
        sources = []
        for i in range(n):
            c = SearchCandidate(
                title=f"Src{i}", url=f"https://example{i}.com",
                snippet=f"Python 3.12 new features {i}", rank=i,
                composite_score=0.8 - i * 0.1, trust_score=0.7,
            )
            sources.append(RankedSource(candidate=c, final_score=0.8 - i * 0.1,
                                         agreement_score=0.6))
        return sources

    def test_returns_answer(self):
        intent = QueryAnalyzer().analyze("Python 3.12")
        answer, _, _ = SynthesisEngine().synthesize("Python 3.12", self._sources(), intent)
        assert len(answer) > 0

    def test_empty_sources_fallback(self):
        intent = QueryAnalyzer().analyze("test")
        answer, conflict, _ = SynthesisEngine().synthesize("test", [], intent)
        assert not conflict
        assert len(answer) > 0

    def test_conflict_detected_low_agreement(self):
        intent = QueryAnalyzer().analyze("test")
        sources = self._sources(3)
        for s in sources:
            s.agreement_score = 0.05
            s.final_score = 0.5
        _, conflict, note = SynthesisEngine().synthesize("test", sources, intent)
        assert conflict
        assert len(note) > 0


class TestSafetyPolicy:
    def test_empty_fails(self):
        ok, reason = SafetyPolicy.check([], "q")
        assert not ok
        assert reason == "no_sources_found"

    def test_weak_evidence_fails(self):
        c = SearchCandidate(title="x", url="https://x.com", snippet="x", rank=0)
        ok, _ = SafetyPolicy.check([RankedSource(candidate=c, final_score=0.0)], "q")
        assert not ok

    def test_good_evidence_passes(self):
        c = SearchCandidate(title="P", url="https://docs.python.org",
                            snippet="Python 3.12", rank=0, trust_score=0.9)
        ok, _ = SafetyPolicy.check([RankedSource(candidate=c, final_score=0.7)], "Python")
        assert ok


class TestWebResearchAgentE2E:
    def test_run_success(self):
        agent = WebResearchAgent(search_tool=_fake_search, fetch_page=_fake_fetch)
        result = agent.run(WebResearchRequest(query="Python 3.12 features"))
        assert result.success
        assert len(result.answer) > 0
        assert result.sources_searched > 0

    def test_run_no_search_tool_fails(self):
        result = WebResearchAgent(search_tool=None).run(WebResearchRequest(query="test"))
        assert not result.success
        assert result.error_message

    def test_run_empty_results_fails(self):
        result = WebResearchAgent(search_tool=lambda q, **kw: []).run(
            WebResearchRequest(query="obscure xyz"))
        assert not result.success

    def test_run_returns_citations(self):
        agent = WebResearchAgent(search_tool=_fake_search, fetch_page=_fake_fetch)
        result = agent.run(WebResearchRequest(query="Python 3.12"))
        assert result.success
        assert len(result.citations) > 0
        for c in result.citations:
            assert c.url.startswith("http")

    def test_run_never_raises(self):
        def bad_search(q, **kw):
            raise RuntimeError("network error")
        result = WebResearchAgent(search_tool=bad_search).run(
            WebResearchRequest(query="test"))
        assert not result.success

    def test_card_type_valid(self):
        agent = WebResearchAgent(search_tool=_fake_search, fetch_page=_fake_fetch)
        result = agent.run(WebResearchRequest(query="Python documentation"))
        valid = {"web_research_card", "news_card", "comparison_card",
                 "product_research_card", "docs_card", "error_card"}
        assert result.card_type in valid

    def test_to_dict_keys(self):
        result = WebResearchAgent(search_tool=_fake_search).run(
            WebResearchRequest(query="Python"))
        d = result.to_dict()
        for key in ("success", "answer", "speech_text", "card_type",
                    "citations", "confidence", "sources_searched"):
            assert key in d

if __name__ == "__main__":
    pytest.main(["-v", __file__])
