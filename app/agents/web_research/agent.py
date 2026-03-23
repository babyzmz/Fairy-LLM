"""WebResearchAgent — public entry point.

Stateless. All conversation state lives in app.core.conversation_state.
"""
from __future__ import annotations
import logging
import threading
from time import monotonic
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
ProgressCallback = Callable[[str, dict[str, Any]], None]
SEARCH_DEADLINE_SECONDS = 30.0


class WebResearchAgent:
    """Open-web research agent: search → browse → extract → synthesize → cite."""

    def __init__(
        self,
        search_tool: Callable[..., list[dict]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        vision_fallback: Callable[[str], Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._fetch_page = fetch_page
        self._progress_callback = progress_callback
        self._cancel_event: threading.Event | None = None
        self._query_analyzer = QueryAnalyzer()
        self._search_planner = SearchPlanner()
        self._ranker = CandidateRanker()
        self._browser = BrowsingEngine(fetch_page_fn=fetch_page)
        self._scorer = EvidenceScorer()
        self._synthesizer = SynthesisEngine()
        self._safety = SafetyPolicy()

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress_callback = callback

    def set_cancel_event(self, cancel_event: threading.Event | None) -> None:
        self._cancel_event = cancel_event

    def run(self, request: WebResearchRequest) -> WebResearchResult:
        """Execute full research pipeline. Never raises."""
        try:
            return self._run(request)
        except Exception as exc:
            if str(exc).strip().lower() == "cancelled":
                return ResultBuilder.failure(
                    "cancelled",
                    subtype="general",
                    request_id=request.request_id,
                    query=request.query,
                )
            logger.exception("web_research_agent_error query=%s", request.query[:50])
            return WebResearchError(reason=str(exc), retryable=True).to_result()

    def execute(self, request: WebResearchRequest) -> WebResearchResult:
        """Alias for invocation_service compatibility."""
        return self.run(request)

    def _run(self, request: WebResearchRequest) -> WebResearchResult:
        query = request.query
        deadline = monotonic() + SEARCH_DEADLINE_SECONDS
        self._check_cancelled()
        intent = self._query_analyzer.analyze(query)
        log_pipeline_step("query_analyzed", query=query[:40], subtype=intent.subtype)
        self._emit_progress("query_analyzed", subtype=intent.subtype, query=query[:60])

        plan = self._search_planner.plan(
            query, intent,
            max_sources=request.max_sources,
            recency_days=request.recency_days,
            prefer_official=request.prefer_official,
        )
        log_pipeline_step("search_plan_created", depth=plan.search_depth)
        self._emit_progress("search_plan_created", subtype=intent.subtype, search_depth=plan.search_depth)

        if self._search_tool is None:
            return ResultBuilder.failure("search_tool_not_provided",
                                         subtype=intent.subtype,
                                         request_id=request.request_id)

        self._check_cancelled()
        attempted_queries: list[str] = [plan.primary_query]
        raw_results = self._search(plan.primary_query, plan.max_results, deadline=deadline)
        for fallback_query in plan.backup_queries:
            if raw_results:
                break
            if monotonic() >= deadline:
                break
            self._check_cancelled()
            attempted_queries.append(fallback_query)
            self._emit_progress(
                "fallback_search_started",
                subtype=intent.subtype,
                query=fallback_query[:80],
            )
            raw_results = self._search(fallback_query, plan.max_results, deadline=deadline)
            self._emit_progress(
                "fallback_results_received",
                subtype=intent.subtype,
                query=fallback_query[:80],
                count=len(raw_results),
            )
        log_pipeline_step("search_results_received", count=len(raw_results))
        self._emit_progress("search_results_received", subtype=intent.subtype, count=len(raw_results))

        if not raw_results:
            self._emit_progress(
                "search_exhausted",
                subtype=intent.subtype,
                attempted_queries=attempted_queries,
            )
            return ResultBuilder.failure(
                "no_search_results",
                subtype=intent.subtype,
                request_id=request.request_id,
                query=query,
                attempted_queries=attempted_queries,
            )

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
        self._emit_progress("candidates_ranked", subtype=intent.subtype, count=len(ranked_cands))

        ranked_sources: list[RankedSource] = []
        pages_opened = 0
        for cand in ranked_cands[:plan.max_results]:
            if monotonic() >= deadline:
                self._emit_progress("search_exhausted", subtype=intent.subtype, reason="deadline_exceeded")
                break
            self._check_cancelled()
            rs = RankedSource(candidate=cand)
            if (plan.search_depth >= 2
                    and pages_opened < plan.max_pages_to_open
                    and self._fetch_page):
                self._emit_progress(
                    "page_opening",
                    subtype=intent.subtype,
                    url=cand.url,
                    page_index=pages_opened + 1,
                )
                rs.evidence = self._browser.extract(cand.url, cand.title, cand.snippet)
                pages_opened += 1
                log_pipeline_step("page_opened", url=cand.url[:50],
                                   ok=rs.evidence.extraction_ok)
                self._emit_progress(
                    "page_opened",
                    subtype=intent.subtype,
                    url=cand.url,
                    ok=rs.evidence.extraction_ok,
                    pages_opened=pages_opened,
                )
            ranked_sources.append(rs)

        self._check_cancelled()
        ranked_sources = self._scorer.score(ranked_sources, query)
        log_pipeline_step("evidence_scored", sources=len(ranked_sources))
        self._emit_progress("evidence_scored", subtype=intent.subtype, sources=len(ranked_sources))

        ok, reason = self._safety.check(ranked_sources, query)
        if not ok:
            return ResultBuilder.failure(reason, subtype=intent.subtype,
                                         request_id=request.request_id)

        self._check_cancelled()
        self._emit_progress("synthesizing", subtype=intent.subtype, sources=len(ranked_sources))
        answer, has_conflict, conflict_note = self._synthesizer.synthesize(
            query, ranked_sources, intent, max_sources=request.max_sources
        )
        if has_conflict:
            log_pipeline_step("conflict_detected", query=query[:40])

        top_sources = ranked_sources[:request.max_sources]
        citations = CitationBuilder.build(top_sources)
        log_pipeline_step("synthesis_complete", answer_len=len(answer),
                           citations=len(citations))
        self._emit_progress("synthesis_complete", subtype=intent.subtype, answer_len=len(answer), citations=len(citations))

        result = ResultBuilder.success(
            query=query, answer=answer, citations=citations,
            sources=top_sources, intent=intent,
            sources_searched=len(raw_results),
            has_conflict=has_conflict, conflict_note=conflict_note,
            request_id=request.request_id,
        )
        log_pipeline_step("result_ready", success=result.success,
                           confidence=result.confidence)
        self._emit_progress("result_ready", subtype=intent.subtype, success=result.success, confidence=result.confidence)
        return result

    def _search(self, query: str, max_results: int, *, deadline: float | None = None) -> list[dict]:
        if deadline is not None and monotonic() >= deadline:
            return []
        self._check_cancelled()
        try:
            results = self._search_tool(query, max_results=max_results)  # type: ignore[misc]
            self._check_cancelled()
            return results if isinstance(results, list) else []
        except Exception as exc:
            logger.warning("web_research_search_error query=%s error=%s", query[:40], exc)
            return []

    def _emit_progress(self, phase: str, **payload: Any) -> None:
        if self._progress_callback is None:
            return
        event_payload = {"phase": phase, **payload}
        try:
            self._progress_callback("structured_tool_progress", event_payload)
        except Exception:
            logger.debug("web_research_progress_callback_failed", exc_info=True)

    def _check_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise RuntimeError("cancelled")
