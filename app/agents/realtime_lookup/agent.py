"""RealtimeLookupAgent — canonical public entry point.

External callers::

    from app.agents.realtime_lookup.agent import RealtimeLookupAgent
    from app.agents.realtime_lookup.contract import (
        RealtimeLookupRequest, RealtimeLookupResult, RealtimeLookupError
    )

    agent = RealtimeLookupAgent(search_tool=search_web, fetch_page=read_webpage)
    result = agent.execute(RealtimeLookupRequest(query="比特币现在价格多少"))
    if result.success:
        print(result.speech_text)
        print(result.card_payload)

This module wraps the internal 5-stage LookupEngine. Internal components
(query_analyzer, lookup_engine, result_builder, providers) are NOT part
of the public API and must not be imported directly by external code.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app.agents.realtime_lookup.models import (
    RealtimeLookupRequest,
    RealtimeLookupResult,
    RealtimeLookupError,
)

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, dict[str, Any]], None]


class RealtimeLookupAgent:
    """Autonomous 5-stage realtime lookup agent.

    Stages:
      1 — Structured provider (Open-Meteo, CoinGecko, Yahoo, FX API)
      2 — Search snippet extraction
      3 — Webpage open + DOM text parse
      4 — Multi-source cross-validation
      5 — Best-effort fallback snippet
    """

    def __init__(
        self,
        search_tool: Callable[..., list[dict]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        vision_fallback: Callable[[str], Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._fetch_page = fetch_page
        self._vision_fallback = vision_fallback
        self._progress_callback = progress_callback
        self._cancel_event: threading.Event | None = None
        self._engine: Any = None  # lazy-init

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress_callback = callback
        if self._engine is not None and hasattr(self._engine, "set_progress_callback"):
            self._engine.set_progress_callback(callback)

    def set_cancel_event(self, cancel_event: threading.Event | None) -> None:
        self._cancel_event = cancel_event
        if self._engine is not None and hasattr(self._engine, "set_cancel_event"):
            self._engine.set_cancel_event(cancel_event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, request: RealtimeLookupRequest) -> RealtimeLookupResult:
        """Execute a realtime lookup. Never raises — returns error result on failure."""
        if self._search_tool is None:
            err = RealtimeLookupError(
                reason="search_tool_not_provided",
                retryable=False,
            )
            return err.to_result()

        engine = self._get_engine()
        try:
            raw = engine.run(request.query)
        except Exception as exc:
            if str(exc).strip().lower() == "cancelled":
                return RealtimeLookupError(
                    reason="cancelled",
                    retryable=False,
                ).to_result()
            logger.exception(
                "realtime_lookup_agent_error query=%s error=%s",
                request.query[:50], exc,
            )
            return RealtimeLookupError(
                reason=str(exc),
                retryable=True,
            ).to_result()

        # raw is a RealtimeResult from app.agents.realtime_lookup.result_builder
        return RealtimeLookupResult(
            success=raw.success,
            speech_text=raw.speech_text,
            card_payload=raw.card_payload,
            numeric_value=raw.numeric_value,
            confidence=raw.confidence,
            stage_used=raw.stage_used,
            subtype=raw.subtype,
            source_urls=raw.source_urls,
            error_message=raw.error_message,
            tool_lock=True,
        )

    def run_parallel(self, queries: list[str]) -> list[RealtimeLookupResult]:
        """Run multiple lookups concurrently."""
        engine = self._get_engine()
        raw_results = engine.run_parallel(queries)
        return [
            RealtimeLookupResult(
                success=r.success,
                speech_text=r.speech_text,
                card_payload=r.card_payload,
                numeric_value=r.numeric_value,
                confidence=r.confidence,
                stage_used=r.stage_used,
                subtype=r.subtype,
                source_urls=r.source_urls,
                error_message=r.error_message,
                tool_lock=True,
            )
            for r in raw_results
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_engine(self) -> Any:
        """Lazy-init the internal LookupEngine."""
        if self._engine is None:
            from app.agents.realtime_lookup.lookup_engine import LookupEngine
            self._engine = LookupEngine(
                search_fn=self._search_tool,
                fetch_page_fn=self._fetch_page,
                progress_callback=self._progress_callback,
                cancel_event=self._cancel_event,
            )
        return self._engine
