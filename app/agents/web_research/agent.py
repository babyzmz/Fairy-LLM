"""WebResearchAgent — public entry point.

Stateless. All conversation state lives in app.core.conversation_state.
"""
from __future__ import annotations
import logging
from typing import Any, Callable

from app.agents.web_research.models import (
    SearchCandidate, RankedSource,
    WebResearchRequest, WebResearchResult, WebResearchError,
)
from app.agents.web_research.query_analyzer import QueryAnalyzer
from app.agents.web_research.search_planner import SearchPlanner
from app.agents.web_research.candidate_ranker import CandidateRanker
from app.agents.web_research.browsing_engine import BrowsingEngine
from app.agents.web_research.evidence_scorer import EvidenceScorer
from app.agents.web_research.synthesis_engine import SynthesisEngine
from app.agents.web_research.citation_builder import CitationBuilder
from app.agents.web_research.result_builder import ResultBuilder
from app.agents.web_research.safety_policy import SafetyPolicy
from app.agents.web_research.debug import log_pipeline_step

logger = logging.getLogger(__name__)


class WebResearchAgent:
    """Open-web research agent: search → browse → extract → synthesize → cite."""

    def __init__(
        self,
        search_tool: Callable[..., list[dict]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        vision_fallback: Callable[[str], Any] | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._fetch_page = fetch_page
        self._query_analyzer = QueryAnalyzer()
        self._search_planner = SearchPlanner()
        self._ranker = CandidateRanker()
        self._browser = BrowsingEngine(fetch_page_fn=fetch_page)
        self._scorer = EvidenceScorer()
        self._synthesizer = SynthesisEngine()
        self._safety = SafetyPolicy()

    def run(self, request: WebResearchRequest) -> WebResearchResult:
        """Execute full research pipeline. Never raises."""
        try:
            return self._run(request)
        except Exception as exc:
            logger.exception("web_research_agent_error query=%s", request.query[:50])
            return WebResearchError(reason=str(exc), retryable=True).to_result()

    def execute(self, request: WebResearchRequest) -> WebResearchResult:
        """Alias for invocation_service compatibility."""
        return self.run(request)

    def _run(self, request: WebResearchRequest) -> WebResearchResult:
        query = request.query
        intent = self._query_analyzer.analyze(query)
        log_pipeline_step("query_analyzed", query=query[:40], subtype=intent.subtype)

        plan = self._search_planner.plan(
            query, intent,
            max_sources=request.max_sources,
            recency_days=request.recency_days,
            prefer_official=request.prefer_official,
        )
        log_pipeline_step("search_plan_created", depth=plan.search_depth)

        if self._search_tool is None:
            return ResultBuilder.failure("search_tool_not_provided",
                                         subtype=intent.subtype,
                                         request_id=request.request_id)

        raw_results = self._search(plan.primary_query, plan.max_results)
        if not raw_results and plan.backup_queries:
            raw_results = self._search(plan.backup_queries[0], plan.max_results)
        log_pipeline_step("search_results_received", count=len(raw_results))

        if not raw_results:
            return ResultBuilder.failure("no_search_results",
                                         subtype=intent.subtype,
                                         request_id=request.request_id)

        candidates = [
            SearchCandidate(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                rank=i,
            )
            for i, r in enumerate(raw_results)
            if r.get("url")
        ]

        ranked_cands = self._ranker.rank(
            candidates, query, intent,
            prefer_official=request.prefer_official,
        )
        log_pipeline_step("candidates_ranked", count=len(ranked_cands))

        ranked_sources: list[RankedSource] = []
        pages_opened = 0
        for cand in ranked_cands[:plan.max_results]:
            rs = RankedSource(candidate=cand)
            if (plan.search_depth >= 2
                    and pages_opened < plan.max_pages_to_open
                    and self._fetch_page):
                rs.evidence = self._browser.extract(cand.url, cand.title, cand.snippet)
                pages_opened += 1
                log_pipeline_step("page_opened", url=cand.url[:50],
                                   ok=rs.evidence.extraction_ok)
            ranked_sources.append(rs)

        ranked_sources = self._scorer.score(ranked_sources, query)
        log_pipeline_step("evidence_scored", sources=len(ranked_sources))

        ok, reason = self._safety.check(ranked_sources, query)
        if not ok:
            return ResultBuilder.failure(reason, subtype=intent.subtype,
                                         request_id=request.request_id)

        answer, has_conflict, conflict_note = self._synthesizer.synthesize(
            query, ranked_sources, intent, max_sources=request.max_sources
        )
        if has_conflict:
            log_pipeline_step("conflict_detected", query=query[:40])

        top_sources = ranked_sources[:request.max_sources]
        citations = CitationBuilder.build(top_sources)
        log_pipeline_step("synthesis_complete", answer_len=len(answer),
                           citations=len(citations))

        result = ResultBuilder.success(
            query=query, answer=answer, citations=citations,
            sources=top_sources, intent=intent,
            sources_searched=len(raw_results),
            has_conflict=has_conflict, conflict_note=conflict_note,
            request_id=request.request_id,
        )
        log_pipeline_step("result_ready", success=result.success,
                           confidence=result.confidence)
        return result

    def _search(self, query: str, max_results: int) -> list[dict]:
        try:
            results = self._search_tool(query, max_results=max_results)  # type: ignore[misc]
            return results if isinstance(results, list) else []
        except Exception as exc:
            logger.warning("web_research_search_error query=%s error=%s", query[:40], exc)
            return []
