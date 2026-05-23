from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

from app.agents.web_research.content_extractor import ContentExtractor

from .browser_executor import BrowserExecutor
from .decision_models import BrowserAvailabilityStatus, RetrievalPlan, WebAccessDecision, WebAccessExecutionResult
from .link_selector import ScoredLink, select_candidate_links
from .source_registry import resolve_source_descriptor
from .visual_reader import VisualReader


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, dict[str, Any]], None]
BrowsePolicyCallback = Callable[..., dict[str, Any]]
SerpLinkSelectionCallback = Callable[..., dict[str, Any]]


class ExecutionLadder:
    def __init__(
        self,
        *,
        search_tool: Callable[..., list[dict[str, Any]]] | None = None,
        search_tool_detailed: Callable[..., dict[str, Any]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        browser_executor: BrowserExecutor | None = None,
        visual_reader: VisualReader | None = None,
        browse_policy_callback: BrowsePolicyCallback | None = None,
        serp_link_selection_callback: SerpLinkSelectionCallback | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._search_tool_detailed = search_tool_detailed
        self._fetch_page = fetch_page
        self._browser = browser_executor or BrowserExecutor()
        self._visual_reader = visual_reader or VisualReader()
        self._browse_policy_callback = browse_policy_callback
        self._serp_link_selection_callback = serp_link_selection_callback

    def execute(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        session_web_context: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        max_results: int = 5,
    ) -> WebAccessExecutionResult:
        context = dict(session_web_context or {})
        self._emit(progress_callback, "execution_started", access_mode=decision.access_mode, intent_type=decision.intent_type)
        self._emit(progress_callback, "web_access_decided", access_mode=decision.access_mode, intent_type=decision.intent_type)
        self._emit(
            progress_callback,
            "retrieval_plan_built",
            primary_queries=list(plan.primary_queries),
            target_urls=list(plan.target_urls),
            browser_actions=[str(action.get("type") or "") for action in plan.browser_actions],
        )
        if not decision.needs_web or decision.access_mode == "none":
            return self._result(success=True, decision=decision, plan=plan, level=0, mode="none")

        if decision.access_mode in {"search_only", "http_fetch"}:
            return self._execute_search_fetch(
                decision=decision,
                plan=plan,
                context=context,
                progress_callback=progress_callback,
                max_results=max_results,
            )
        if decision.access_mode == "rendered_read":
            return self._execute_rendered_read(decision=decision, plan=plan, progress_callback=progress_callback)
        if decision.access_mode == "browser_interaction":
            return self._execute_browser_interaction(
                decision=decision,
                plan=plan,
                context=context,
                progress_callback=progress_callback,
            )
        if decision.access_mode == "visual_read":
            return self._execute_visual_read(
                decision=decision,
                plan=plan,
                context=context,
                progress_callback=progress_callback,
            )
        return self._result(
            success=False,
            decision=decision,
            plan=plan,
            level=0,
            mode=decision.access_mode,
            failure_reason="unsupported_access_mode",
        )

    def _execute_search_fetch(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        context: dict[str, Any],
        progress_callback: ProgressCallback | None,
        max_results: int,
        ) -> WebAccessExecutionResult:
        attempted_queries: list[str] = []
        results: list[dict[str, Any]] = []
        direct_failure: WebAccessExecutionResult | None = None
        browse_failure: WebAccessExecutionResult | None = None
        browser_status = self._browser.availability_status()
        query_strategy = str((plan.source_constraints or {}).get("query_strategy") or "").strip()
        if self._should_try_source_grounded_browse(plan):
            browse = self._execute_source_grounded_browse(
                decision=decision,
                plan=plan,
                context=context,
                progress_callback=progress_callback,
            )
            if browse.success:
                return browse
            if bool((plan.source_constraints or {}).get("force_web_browse")):
                return browse
            browse_failure = browse
            self._emit(
                progress_callback,
                "web_access_fallback_applied",
                stage="browse_to_search",
                reason=browse.failure_reason or "browse_failed",
            )
            self._emit(
                progress_callback,
                "fallback_applied",
                stage="browse_to_search",
                reason=browse.failure_reason or "browse_failed",
            )
        if self._search_tool is None:
            return browse_failure or self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=1,
                mode=plan.access_mode,
                failure_reason="search_tool_not_available",
                browser_availability=browser_status,
            )

        if self._should_try_direct_rendered_read(
            decision=decision,
            plan=plan,
            query_strategy=query_strategy,
        ):
            stage = (
                "source_direct_rendered_read"
                if decision.intent_type == "source_constrained_lookup"
                else "generic_news_direct_rendered_read"
            )
            reason = (
                "source_constrained_direct_read"
                if decision.intent_type == "source_constrained_lookup"
                else "generic_news_direct_read"
            )
            self._emit(
                progress_callback,
                "web_access_fallback_applied",
                stage=stage,
                reason=reason,
            )
            direct = self._execute_rendered_read(decision=decision, plan=plan, progress_callback=progress_callback)
            direct.fallback_stage = stage
            if direct.success:
                return direct
            direct_failure = direct

        search_queries = self._candidate_queries_for_search(plan, query_strategy=query_strategy)
        for query in search_queries:
            attempted_queries.append(query)
            self._emit(progress_callback, "http_fetch_started", query=query, preferred_domains=plan.preferred_domains)
            try:
                results = list(self._search_tool(query, max_results=max_results, preferred_domains=plan.preferred_domains))
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_access_search_failed query=%s error=%s", query[:80], exc)
                results = []
            if results:
                break

        opened_pages: list[dict[str, Any]] = []
        if results and self._fetch_page is not None and plan.access_mode == "http_fetch":
            for item in results[: min(3, len(results))]:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                try:
                    html = self._fetch_page(url)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("web_access_fetch_failed url=%s error=%s", url, exc)
                    continue
                evidence = ContentExtractor.extract(url=url, raw_html=html, fallback_title=str(item.get("title") or ""))
                opened_pages.append(
                    {
                        "url": url,
                        "final_url": url,
                        "title": evidence.title,
                        "visible_text": evidence.main_text,
                        "body_text": evidence.main_text,
                        "publish_date": evidence.publish_date,
                        "domain": evidence.domain,
                        "html": html,
                        "source": "http_fetch",
                    }
                )

        if results:
            return self._result(
                success=True,
                decision=decision,
                plan=plan,
                level=2 if opened_pages else 1,
                mode=plan.access_mode,
                search_results=results,
                opened_pages=opened_pages,
                attempted_queries=attempted_queries,
            )

        if plan.target_urls:
            if browse_failure is not None and browse_failure.failure_reason not in {"", "no_search_results"}:
                browse_failure.attempted_queries = attempted_queries
                browse_failure.fallback_stage = browse_failure.fallback_stage or "browse_to_search"
                return browse_failure
            if direct_failure is not None and direct_failure.failure_reason == "page_not_found":
                direct_failure.attempted_queries = attempted_queries
                direct_failure.fallback_stage = direct_failure.fallback_stage or "search_to_rendered_or_browser"
                return direct_failure
            self._emit(progress_callback, "web_access_fallback_applied", stage="search_to_rendered_or_browser", reason="no_search_results")
            self._emit(progress_callback, "fallback_applied", stage="search_to_rendered_or_browser", reason="no_search_results")
            if decision.needs_interaction:
                fallback = self._execute_browser_interaction(decision=decision, plan=plan, context=context, progress_callback=progress_callback)
            elif decision.needs_visual_reading:
                fallback = self._execute_visual_read(decision=decision, plan=plan, context=context, progress_callback=progress_callback)
            else:
                fallback = self._execute_rendered_read(decision=decision, plan=plan, progress_callback=progress_callback)
            fallback.attempted_queries = attempted_queries
            fallback.fallback_stage = fallback.fallback_stage or "search_to_rendered_or_browser"
            if not fallback.failure_reason and not fallback.success:
                fallback.failure_reason = "no_search_results"
            return fallback

        if browse_failure is not None:
            browse_failure.attempted_queries = attempted_queries
            browse_failure.fallback_stage = browse_failure.fallback_stage or "browse_to_search"
            return browse_failure

        return self._result(
            success=False,
            decision=decision,
            plan=plan,
            level=1,
            mode=plan.access_mode,
            attempted_queries=attempted_queries,
            failure_reason="no_search_results",
            browser_availability=browser_status,
        )

    @staticmethod
    def _should_try_source_grounded_browse(plan: RetrievalPlan) -> bool:
        source_constraints = dict(plan.source_constraints or {})
        strategy = str(source_constraints.get("browse_strategy") or "").strip()
        return bool(source_constraints.get("force_web_browse")) or strategy in {"source_constrained_browse", "forced_web_browse"}

    @staticmethod
    def _candidate_queries_for_search(plan: RetrievalPlan, *, query_strategy: str) -> list[str]:
        primary = list(plan.primary_queries or [])
        fallback = list(plan.fallback_queries or [])
        if query_strategy in {"source_constrained_release", "source_constrained_specs"}:
            return primary[:2] + fallback[:3]
        if query_strategy.startswith("source_constrained"):
            return primary[:1] + fallback[:2]
        if query_strategy.startswith("generic_news"):
            return primary[:2] + fallback[:1]
        if query_strategy == "generic_web_release":
            return primary[:3] + fallback[:3]
        if query_strategy == "generic_web_specs":
            return primary[:3] + fallback[:3]
        if query_strategy == "generic_web_compare":
            return primary[:3] + fallback[:3]
        return primary[:2] + fallback[:2]

    @staticmethod
    def _should_try_direct_rendered_read(
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        query_strategy: str,
    ) -> bool:
        if not plan.target_urls or decision.needs_interaction or decision.needs_visual_reading:
            return False
        if decision.intent_type == "source_constrained_lookup":
            return True
        return query_strategy == "generic_news"

    def _execute_rendered_read(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        progress_callback: ProgressCallback | None,
    ) -> WebAccessExecutionResult:
        if not plan.target_urls:
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=3,
                mode="rendered_read",
                failure_reason="rendered_read_unavailable",
                browser_availability=self._browser.availability_status(),
            )
        url = str(plan.target_urls[0]).strip()
        self._emit(progress_callback, "rendered_read_started", url=url)
        page_payload: dict[str, Any] = {}
        browser_status = self._browser.availability_status()
        if self._browser.available():
            page = self._browser.rendered_read(url)
            browser_status = self._status_from_payload(page.get("availability")) or browser_status
            if not page.get("ok"):
                return self._result(
                    success=False,
                    decision=decision,
                    plan=plan,
                    level=3,
                    mode="rendered_read",
                    failure_reason=str(page.get("error") or "rendered_read_failed"),
                    browser_availability=browser_status,
                )
            page_payload = dict(page.get("page") or {})
        elif self._fetch_page is not None:
            try:
                html = self._fetch_page(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("rendered_read_fetch_fallback_failed url=%s error=%s", url, exc)
                return self._result(
                    success=False,
                    decision=decision,
                    plan=plan,
                    level=3,
                    mode="rendered_read",
                    failure_reason="rendered_read_unavailable",
                    browser_availability=browser_status,
                )
            evidence = ContentExtractor.extract(url=url, raw_html=html, fallback_title="")
            page_payload = {
                "requested_url": url,
                "final_url": url,
                "url": url,
                "title": evidence.title,
                "html": html,
                "visible_text": evidence.main_text,
                "headings": [],
                "links": [],
                "screenshot_path": "",
                "handle_id": "",
            }
        else:
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=3,
                mode="rendered_read",
                failure_reason="rendered_read_unavailable",
                browser_availability=browser_status,
            )
        extract_ok, extract_failure_reason = self._assess_post_open_extraction(page_payload)
        page_payload["post_open_extract_attempted"] = True
        page_payload["post_open_extract_result"] = "success" if extract_ok else "failed"
        page_payload["post_open_extract_failure_reason"] = extract_failure_reason
        if not extract_ok:
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=3,
                mode="rendered_read",
                opened_pages=[page_payload] if page_payload else [],
                browser_result=page_payload,
                failure_reason=extract_failure_reason or "rendered_read_extract_failed",
                browser_availability=browser_status,
            )
        return self._result(
            success=True,
            decision=decision,
            plan=plan,
            level=3,
            mode="rendered_read",
            opened_pages=[page_payload] if page_payload else [],
            browser_result=page_payload,
            browser_availability=browser_status,
        )

    def _execute_source_grounded_browse(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        context: dict[str, Any],
        progress_callback: ProgressCallback | None,
    ) -> WebAccessExecutionResult:
        browser_status = self._browser.availability_status()
        source_constraints = dict(plan.source_constraints or {})
        task_type = self._sanitize_task_type(str(source_constraints.get("task_type") or "").strip() or "general_info")
        entity = str(source_constraints.get("entity") or "").strip()
        user_query = str(
            source_constraints.get("original_user_query")
            or source_constraints.get("user_query")
            or (plan.primary_queries[:1] or [""])[0]
            or ""
        ).strip()
        navigation_targets = [str(item).strip() for item in list(source_constraints.get("navigation_targets") or []) if str(item).strip()]
        max_hops = max(1, int(source_constraints.get("max_hops") or 2))
        max_candidate_links = max(1, int(source_constraints.get("max_candidate_links") or 2))
        descriptor = resolve_source_descriptor(
            f"{decision.source_name or ''} {decision.source_domain or ''} {user_query}".strip(),
            explicit_source=str(decision.source_name or decision.source_domain or ""),
        )
        browse_strategy = str(source_constraints.get("browse_strategy") or "").strip() or "forced_web_browse"
        self._emit(
            progress_callback,
            "understanding_request",
            browse_mode=browse_strategy,
            task_type=task_type,
            entity=entity,
            query_strategy=str(source_constraints.get("query_strategy") or "").strip(),
        )

        browse_entry = self._resolve_browse_entry(
            decision=decision,
            plan=plan,
            context=context,
            query=user_query,
            task_type=task_type,
            entity=entity,
            search_again_used=bool(source_constraints.get("search_again_used")),
            revised_query=str(source_constraints.get("revised_query") or "").strip(),
        )
        candidate_sources = list(browse_entry.get("candidate_sources") or [])
        browse_entry["chosen_source_candidates"] = list(candidate_sources[:3])
        browse_entry["chosen_entry_url"] = str((candidate_sources[:1] or [{}])[0].get("url") or "").strip()
        if not candidate_sources:
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=4,
                mode=plan.access_mode,
                browser_result=self._browse_failure_page(
                    task_type=task_type,
                    entity=entity,
                    selected_links=[],
                    failure_reason="browse_source_discovery_failed",
                    browse_entry=browse_entry,
                ),
                used_browser_interaction=True,
                failure_reason="browse_source_discovery_failed",
                browser_availability=browser_status,
            )

        self._emit(
            progress_callback,
            "source_discovery_completed",
            task_type=task_type,
            source_count=len(candidate_sources),
            current_source_index=int(browse_entry.get("current_source_index") or 0),
            revised_query=str(browse_entry.get("revised_query") or "").strip(),
            search_queries_attempted=list(browse_entry.get("search_queries_attempted") or []),
            raw_search_results_count=int(browse_entry.get("raw_search_results_count") or 0),
            filtered_search_results_count=int(browse_entry.get("filtered_search_results_count") or 0),
            preferred_domains_applied=bool(browse_entry.get("preferred_domains_applied")),
        )

        pending: list[tuple[str, int, int]] = []
        attempted_source_indices = {
            int(item) for item in list(browse_entry.get("attempted_source_indices") or []) if isinstance(item, int) or str(item).isdigit()
        }
        current_source_index = int(browse_entry.get("current_source_index") or 0)
        search_again_used = bool(browse_entry.get("search_again_used"))
        for index, source in enumerate(candidate_sources):
            url = str(source.get("url") or "").strip()
            if not url:
                continue
            if index < current_source_index:
                attempted_source_indices.add(index)
                continue
            pending.append((url, 0, index))
        browse_entry["current_source_index"] = current_source_index
        browse_entry["attempted_source_indices"] = sorted(attempted_source_indices)
        browse_entry["search_again_used"] = search_again_used

        visited: set[str] = set()
        selected_links: list[dict[str, Any]] = []
        visited_pages: list[dict[str, Any]] = []
        final_page: dict[str, Any] = {}
        final_page_type = ""
        stop_reason = ""
        failure_reason = "browse_navigation_exhausted"
        attempted_any_page = False
        opened_any_page = False
        serp_summary: dict[str, Any] = {
            "serp_detected": False,
            "serp_links_extracted_count": 0,
            "ranked_candidates": [],
            "top_ranked_candidate": {},
            "top_ranked_score": 0.0,
            "top_ranked_score_breakdown": {},
            "serp_selection_reason": "",
            "model_selected_candidate": {},
            "final_selected_candidate": {},
            "selection_overridden_by_ranker": False,
            "serp_selected_link": "",
        }

        while pending:
            url, hop, source_index = pending.pop(0)
            normalized_url = str(url or "").strip()
            if not normalized_url or normalized_url in visited:
                continue
            visited.add(normalized_url)
            if hop == 0:
                attempted_source_indices.add(source_index)
                browse_entry["current_source_index"] = source_index
                browse_entry["attempted_source_indices"] = sorted(attempted_source_indices)
            self._emit(
                progress_callback,
                "page_open_attempted",
                url=normalized_url,
                browse_mode=browse_strategy,
                hop=hop,
                task_type=task_type,
                source_index=source_index,
            )
            attempted_any_page = True
            page_result = self._open_browse_page(normalized_url)
            browser_status = self._status_from_payload(page_result.get("availability")) or browser_status
            if not page_result.get("ok"):
                failure_reason = "browse_entry_open_failed" if not opened_any_page else "browse_navigation_exhausted"
                final_page = self._browse_failure_page(
                    task_type=task_type,
                    entity=entity,
                    selected_links=selected_links,
                    failure_reason=failure_reason,
                    browse_entry=browse_entry,
                    final_page_url=normalized_url,
                )
                continue

            opened_any_page = True
            page_payload = dict(page_result.get("page") or {})
            page_type_guess = self._browser.classify_page_type(
                page_payload,
                source_descriptor=descriptor,
                task_type=task_type,
                entity=entity,
            )
            candidate_pool = self._build_browse_candidate_pool(
                page_payload=page_payload,
                source_descriptor=descriptor,
                task_type=task_type,
                entity=entity,
                max_links=max(6, max_candidate_links * 4),
            )
            policy = self._call_browse_policy(
                decision=decision,
                plan=plan,
                source_descriptor=descriptor,
                task_type=task_type,
                entity=entity,
                hop=hop,
                max_hops=max_hops,
                page_snapshot=page_payload,
                page_type_guess=page_type_guess,
                candidate_links=candidate_pool,
                navigation_targets=navigation_targets,
            )
            task_type = self._sanitize_task_type(str(policy.get("task_type") or "").strip() or task_type)
            page_type = self._sanitize_page_type(str(policy.get("page_type") or "").strip() or page_type_guess)
            action = self._sanitize_browse_action(str(policy.get("action") or "").strip())
            policy["action"] = action
            page_payload["page_type"] = page_type
            page_payload["next_action"] = action
            serp_detected = bool(page_payload.get("serp_detected"))
            if serp_detected:
                serp_summary["serp_detected"] = True
                serp_summary["serp_links_extracted_count"] = max(
                    int(serp_summary.get("serp_links_extracted_count") or 0),
                    int(page_payload.get("serp_links_extracted_count") or 0),
                )
                for key in (
                    "ranked_candidates",
                    "top_ranked_candidate",
                    "top_ranked_score",
                    "top_ranked_score_breakdown",
                    "serp_selection_reason",
                    "model_selected_candidate",
                    "final_selected_candidate",
                    "selection_overridden_by_ranker",
                    "serp_selected_link",
                ):
                    value = page_payload.get(key)
                    if value not in (None, "", [], {}):
                        serp_summary[key] = value
            heuristic_stop, heuristic_reason = self._browse_stop_signal(
                page_payload=page_payload,
                task_type=task_type,
                entity=entity,
                page_type=page_type,
            )
            if action in {"open_link", "search_again"} and heuristic_stop and self._browse_extractable(page_payload=page_payload, task_type=task_type):
                action = "stop"
                policy["action"] = "stop"
                policy["stop_reason"] = str(policy.get("stop_reason") or heuristic_reason).strip()
                page_payload["next_action"] = "stop"
            if serp_detected and candidate_pool and action == "search_again":
                action = "open_link"
                policy["action"] = "open_link"
                page_payload["next_action"] = "open_link"
            visited_pages.append(page_payload)
            final_page = page_payload
            final_page_type = page_type
            self._emit(
                progress_callback,
                "understanding_page",
                url=normalized_url,
                hop=hop,
                task_type=task_type,
                page_type=page_type,
                page_type_guess=page_type_guess,
            )
            self._emit(
                progress_callback,
                "model_decision_emitted",
                url=normalized_url,
                hop=hop,
                task_type=task_type,
                action=action,
                confidence=self._policy_confidence(policy),
                selected_link_id=policy.get("selected_link_id"),
            )

            if self._policy_confidence(policy) < 0.35:
                if heuristic_stop:
                    action = "stop"
                    policy["action"] = "stop"
                    policy["stop_reason"] = str(policy.get("stop_reason") or heuristic_reason).strip()
                elif serp_detected and candidate_pool:
                    action = "open_link"
                    policy["action"] = "open_link"

            if action == "stop":
                should_stop, stop_reason = self._browse_stop_signal(
                    page_payload=page_payload,
                    task_type=task_type,
                    entity=entity,
                    page_type=page_type,
                )
                if should_stop:
                    quality_score, quality_reason, low_quality = self._assess_page_quality(
                        page_payload=page_payload,
                        task_type=task_type,
                        entity=entity,
                    )
                    page_payload["final_page_quality_score"] = quality_score
                    page_payload["final_page_quality_reason"] = quality_reason
                    page_payload["stop_blocked_by_low_page_quality"] = bool(low_quality)
                    if low_quality:
                        action = "open_link" if candidate_pool else "search_again"
                        policy["action"] = action
                        page_payload["next_action"] = action
                        stop_reason = "low_page_quality_blocked"
                if should_stop and not bool(page_payload.get("stop_blocked_by_low_page_quality")):
                    self._emit(
                        progress_callback,
                        "extracting_answer",
                        url=normalized_url,
                        hop=hop,
                        task_type=task_type,
                        page_type=page_type,
                        stop_reason=stop_reason,
                    )
                    self._annotate_browse_page(
                        page_payload,
                        task_type=task_type,
                        entity=entity,
                        navigation_hops=hop,
                        selected_links=selected_links,
                        final_page_type=page_type,
                        stop_reason=stop_reason,
                        browse_entry=browse_entry,
                        page_open_attempted=True,
                        model_decision_emitted=True,
                        serp_summary=serp_summary,
                    )
                    return self._result(
                        success=self._browse_extractable(page_payload=page_payload, task_type=task_type),
                        decision=decision,
                        plan=plan,
                        level=4,
                        mode=plan.access_mode,
                        opened_pages=visited_pages,
                        browser_result=page_payload,
                        used_browser_interaction=True,
                        failure_reason="" if self._browse_extractable(page_payload=page_payload, task_type=task_type) else "browse_target_reached_but_extract_failed",
                        browser_availability=browser_status,
                    )
                action = "open_link"

            if action == "search_again" and search_again_used:
                failure_reason = "repeated_search_again_without_new_signal"
                continue

            if action == "search_again" and not search_again_used:
                search_again_used = True
                revised_query = self._revise_query_for_search_again(
                    query=user_query,
                    task_type=task_type,
                    entity=entity,
                    page_payload=page_payload,
                )
                browse_entry = self._resolve_browse_entry(
                    decision=decision,
                    plan=plan,
                    context=context,
                    query=revised_query,
                    task_type=task_type,
                    entity=entity,
                    search_again_used=True,
                    revised_query=revised_query,
                )
                if list(browse_entry.get("candidate_sources") or []):
                    candidate_sources = list(browse_entry.get("candidate_sources") or [])
                    browse_entry["chosen_source_candidates"] = list(candidate_sources[:3])
                    browse_entry["chosen_entry_url"] = str((candidate_sources[:1] or [{}])[0].get("url") or "").strip()
                    pending = []
                    current_source_index = int(browse_entry.get("current_source_index") or 0)
                    attempted_source_indices = set()
                    browse_entry["search_again_used"] = True
                    for index, source in enumerate(candidate_sources):
                        source_url = str(source.get("url") or "").strip()
                        if source_url and index >= current_source_index:
                            pending.append((source_url, 0, index))
                    self._emit(
                        progress_callback,
                        "source_discovery_completed",
                        task_type=task_type,
                        source_count=len(candidate_sources),
                        current_source_index=current_source_index,
                        revised_query=revised_query,
                        search_queries_attempted=list(browse_entry.get("search_queries_attempted") or []),
                        raw_search_results_count=int(browse_entry.get("raw_search_results_count") or 0),
                        filtered_search_results_count=int(browse_entry.get("filtered_search_results_count") or 0),
                        preferred_domains_applied=bool(browse_entry.get("preferred_domains_applied")),
                    )
                    continue

            if hop >= max_hops:
                failure_reason = "browse_target_not_reached"
                continue

            self._emit(
                progress_callback,
                "ranking_links",
                url=normalized_url,
                hop=hop,
                task_type=task_type,
                candidate_count=len(candidate_pool),
            )
            next_links = [
                item
                for item in self._choose_browse_links(
                    policy=policy,
                    candidate_pool=candidate_pool,
                    page_payload=page_payload,
                    source_descriptor=descriptor,
                    task_type=task_type,
                    entity=entity,
                    max_links=max_candidate_links,
                )
                if str(item.url or "").strip() and str(item.url or "").strip() not in visited
            ]
            if not next_links:
                if not search_again_used:
                    search_again_used = True
                    revised_query = self._revise_query_for_search_again(
                        query=user_query,
                        task_type=task_type,
                        entity=entity,
                        page_payload=page_payload,
                    )
                    browse_entry = self._resolve_browse_entry(
                        decision=decision,
                        plan=plan,
                        context=context,
                        query=revised_query,
                        task_type=task_type,
                        entity=entity,
                        search_again_used=True,
                        revised_query=revised_query,
                    )
                    if list(browse_entry.get("candidate_sources") or []):
                        candidate_sources = list(browse_entry.get("candidate_sources") or [])
                        browse_entry["chosen_source_candidates"] = list(candidate_sources[:3])
                        browse_entry["chosen_entry_url"] = str((candidate_sources[:1] or [{}])[0].get("url") or "").strip()
                        pending = []
                        current_source_index = int(browse_entry.get("current_source_index") or 0)
                        attempted_source_indices = set()
                        browse_entry["search_again_used"] = True
                        for index, source in enumerate(candidate_sources):
                            source_url = str(source.get("url") or "").strip()
                            if source_url and index >= current_source_index:
                                pending.append((source_url, 0, index))
                        self._emit(
                            progress_callback,
                            "source_discovery_completed",
                            task_type=task_type,
                            source_count=len(candidate_sources),
                            current_source_index=current_source_index,
                            revised_query=revised_query,
                            search_queries_attempted=list(browse_entry.get("search_queries_attempted") or []),
                            raw_search_results_count=int(browse_entry.get("raw_search_results_count") or 0),
                            filtered_search_results_count=int(browse_entry.get("filtered_search_results_count") or 0),
                            preferred_domains_applied=bool(browse_entry.get("preferred_domains_applied")),
                        )
                        continue
                if serp_detected and int(page_payload.get("serp_links_extracted_count") or 0) <= 0:
                    failure_reason = "serp_extraction_failed"
                elif serp_detected:
                    failure_reason = "no_navigable_candidates"
                elif self._policy_confidence(policy) < 0.35:
                    failure_reason = "invalid_model_output_fallback_exhausted"
                else:
                    failure_reason = "browse_no_candidate_links" if not visited_pages[:-1] else "browse_navigation_exhausted"
                continue

            self._emit(
                progress_callback,
                "navigating_deeper",
                url=normalized_url,
                hop=hop,
                task_type=task_type,
                selected_count=len(next_links),
            )
            if serp_detected:
                page_payload["serp_selected_link"] = str(next_links[0].url or "").strip()
                if not str(page_payload.get("serp_selection_reason") or "").strip():
                    page_payload["serp_selection_reason"] = (
                        "model_selected"
                        if list(policy.get("candidate_link_ids") or [])
                        else "deterministic_task_priority"
                    )
                serp_summary["serp_detected"] = True
                serp_summary["serp_selected_link"] = str(next_links[0].url or "").strip()
                serp_summary["serp_selection_reason"] = str(page_payload.get("serp_selection_reason") or "").strip()
                for key in (
                    "ranked_candidates",
                    "top_ranked_candidate",
                    "top_ranked_score",
                    "top_ranked_score_breakdown",
                    "model_selected_candidate",
                    "final_selected_candidate",
                    "selection_overridden_by_ranker",
                ):
                    value = page_payload.get(key)
                    if value not in (None, "", [], {}):
                        serp_summary[key] = value
            for item in next_links:
                payload = item.to_dict()
                payload["hop"] = hop + 1
                selected_links.append(payload)
                pending.append((item.url, hop + 1, source_index))

        if not opened_any_page:
            failure_reason = "browse_entry_open_failed"
        elif failure_reason not in {
            "browse_no_candidate_links",
            "browse_navigation_exhausted",
            "browse_target_not_reached",
            "browse_target_reached_but_extract_failed",
            "invalid_model_output_fallback_exhausted",
            "serp_extraction_failed",
            "no_navigable_candidates",
            "repeated_search_again_without_new_signal",
        }:
            failure_reason = "browse_navigation_exhausted"

        if final_page:
            if "final_page_quality_score" not in final_page:
                quality_score, quality_reason, low_quality = self._assess_page_quality(
                    page_payload=final_page,
                    task_type=task_type,
                    entity=entity,
                )
                final_page["final_page_quality_score"] = quality_score
                final_page["final_page_quality_reason"] = quality_reason
                final_page["stop_blocked_by_low_page_quality"] = bool(low_quality)
            self._annotate_browse_page(
                final_page,
                task_type=task_type,
                entity=entity,
                navigation_hops=max(0, len(visited_pages) - 1),
                selected_links=selected_links,
                final_page_type=final_page_type,
                stop_reason=stop_reason or failure_reason,
                browse_entry=browse_entry,
                page_open_attempted=attempted_any_page,
                model_decision_emitted=bool(visited_pages),
                serp_summary=serp_summary,
            )
        else:
            final_page = self._browse_failure_page(
                task_type=task_type,
                entity=entity,
                selected_links=selected_links,
                failure_reason=failure_reason,
                browse_entry=browse_entry,
                serp_summary=serp_summary,
            )
            final_page["page_open_attempted"] = attempted_any_page
            final_page["model_decision_emitted"] = bool(visited_pages)
        return self._result(
            success=False,
            decision=decision,
            plan=plan,
            level=4,
            mode=plan.access_mode,
            opened_pages=visited_pages,
            browser_result=final_page,
            used_browser_interaction=True,
            failure_reason=failure_reason,
            browser_availability=browser_status,
        )

    def _resolve_browse_entry(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        context: dict[str, Any],
        query: str,
        task_type: str,
        entity: str,
        search_again_used: bool,
        revised_query: str,
    ) -> dict[str, Any]:
        source_constraints = dict(plan.source_constraints or {})
        continuation_applied = bool(source_constraints.get("continuation_applied"))
        continuation_strategy = str(source_constraints.get("continuation_strategy") or "").strip()
        if continuation_applied and list(context.get("last_sources") or []):
            candidate_sources = [
                dict(item)
                for item in list(context.get("last_sources") or [])
                if str((item or {}).get("url") or "").strip()
            ]
            attempted_source_indices = [
                int(item)
                for item in list(context.get("last_attempted_source_indices") or [])
                if isinstance(item, int) or str(item).isdigit()
            ]
            current_source_index = int(context.get("last_current_source_index") or 0)
            if continuation_strategy == "expand_source":
                next_index = current_source_index + 1
                while next_index < len(candidate_sources) and next_index in attempted_source_indices:
                    next_index += 1
                current_source_index = min(next_index, len(candidate_sources))
            if current_source_index < len(candidate_sources):
                return {
                    "task_type": task_type,
                    "candidate_sources": candidate_sources,
                    "current_source_index": current_source_index,
                    "attempted_source_indices": attempted_source_indices,
                    "search_again_used": bool(context.get("last_search_again_used") or search_again_used),
                    "revised_query": str(context.get("last_revised_query") or revised_query or "").strip(),
                    "search_queries_attempted": list(context.get("last_search_queries_attempted") or []),
                    "raw_search_results_count": int(context.get("last_raw_search_results_count") or 0),
                    "filtered_search_results_count": int(context.get("last_filtered_search_results_count") or 0),
                    "filtered_out_reasons": list(context.get("last_filtered_out_reasons") or []),
                    "raw_search_results": list(context.get("last_raw_search_results") or []),
                    "filtered_search_results": list(context.get("last_filtered_search_results") or []),
                    "filtered_out_items_with_reasons": list(context.get("last_filtered_out_items_with_reasons") or []),
                    "search_provider_diagnostics": list(context.get("last_search_provider_diagnostics") or []),
                    "provider_name": str(context.get("last_provider_name") or "").strip(),
                    "search_provider_failed": bool(context.get("last_search_provider_failed")),
                    "strict_domain_filter_failed": bool(context.get("last_strict_domain_filter_failed")),
                    "relaxed_domain_retry_used": bool(context.get("last_relaxed_domain_retry_used")),
                    "winning_query_variant": str(context.get("last_winning_query_variant") or "").strip(),
                    "chosen_source_candidates": list(context.get("last_chosen_source_candidates") or candidate_sources),
                    "chosen_entry_url": str(context.get("last_chosen_entry_url") or "").strip(),
                    "preferred_domains_applied": bool(context.get("last_preferred_domains_applied") or plan.preferred_domains),
                }
            query = str(context.get("last_query") or query).strip() or query
            search_again_used = bool(context.get("last_search_again_used") or search_again_used)
            revised_query = str(context.get("last_revised_query") or revised_query or "").strip()

        candidate_sources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        force_web_browse = bool(source_constraints.get("force_web_browse"))
        has_explicit_url = bool(re.search(r"https?://", str(query or "").strip(), flags=re.IGNORECASE))
        search_queries_attempted: list[str] = []
        filtered_out_reasons: list[dict[str, Any]] = []
        raw_search_results: list[dict[str, Any]] = []
        filtered_search_results: list[dict[str, Any]] = []
        filtered_out_items_with_reasons: list[dict[str, Any]] = []
        search_provider_diagnostics: list[dict[str, Any]] = []
        provider_results_scored: list[dict[str, Any]] = []
        provider_quality_state = ""
        provider_quality_gate_triggered = False
        provider_results_skipped_as_primary = False
        low_signal_fallback_to_serp_used = False
        provider_top_candidates: list[dict[str, Any]] = []
        provider_top_score = 0.0
        provider_top_score_breakdown: dict[str, Any] = {}
        quality_gate_reason = ""
        raw_search_results_count = 0
        filtered_search_results_count = 0
        strict_domain_filter_failed = False
        relaxed_domain_retry_used = False
        winning_query_variant = ""
        provider_name = ""
        search_provider_failed = False
        preferred_domains = [str(item).strip() for item in list(plan.preferred_domains or []) if str(item).strip()]
        seeded_urls = list(source_constraints.get("entry_urls") or [])
        if not seeded_urls and (not force_web_browse or bool(source_constraints.get("source_constraint_applied")) or has_explicit_url):
            seeded_urls = list(plan.target_urls or [])
        for raw_url in seeded_urls:
            url = str(raw_url or "").strip()
            if not url:
                filtered_out_reasons.append({"reason": "empty_seeded_url", "query": str(query or "").strip()})
                continue
            lowered = url.lower()
            if lowered in seen_urls:
                filtered_out_reasons.append({"reason": "duplicate_seeded_url", "url": url})
                continue
            seen_urls.add(lowered)
            candidate_sources.append(
                {
                    "url": url,
                    "title": "",
                    "snippet": "",
                    "domain": self._domain_from_url(url),
                }
            )

        search_queries: list[str] = []
        if revised_query:
            search_queries.append(revised_query)
        for candidate_query in [*list(plan.primary_queries or []), *list(plan.fallback_queries or []), query]:
            cleaned_query = str(candidate_query or "").strip()
            if cleaned_query and cleaned_query not in search_queries:
                search_queries.append(cleaned_query)

        if not candidate_sources and self._search_tool is not None:
            domain_passes: list[tuple[list[str], bool]] = [(preferred_domains, True)]
            if preferred_domains:
                domain_passes.append(([], False))
            for active_domains, strict_filter in domain_passes:
                if not strict_filter:
                    relaxed_domain_retry_used = True
                for candidate_query in search_queries:
                    search_queries_attempted.append(candidate_query)
                    results, diagnostics = self._search_with_diagnostics(
                        candidate_query,
                        max_results=5,
                        preferred_domains=active_domains,
                    )
                    if diagnostics:
                        search_provider_diagnostics.extend(diagnostics)
                        provider_name = str(
                            (next((item.get("provider_name") for item in diagnostics if int(item.get("raw_provider_results_count") or 0) > 0), "") or "")
                            or provider_name
                        ).strip()
                        if any(bool(item.get("search_provider_failed")) for item in diagnostics):
                            search_provider_failed = True
                    raw_search_results_count += len(results)
                    for item in results[:5]:
                        raw_item = {
                            "query": candidate_query,
                            "url": str(item.get("url") or "").strip(),
                            "title": str(item.get("title") or "").strip(),
                            "snippet": str(item.get("snippet") or item.get("summary") or "").strip(),
                            "domain": self._domain_from_url(str(item.get("url") or "").strip()),
                            "provider": str(item.get("provider") or item.get("provider_name") or provider_name).strip(),
                        }
                        raw_search_results.append(raw_item)
                    accepted_results, rejected_results = self._filter_search_results(
                        results=results,
                        query=candidate_query,
                        preferred_domains=active_domains,
                        seen_urls=seen_urls,
                        strict_domain_filter=strict_filter,
                    )
                    scored_payload = self._score_provider_results(
                        accepted_results=accepted_results,
                        rejected_results=rejected_results,
                        task_type=task_type,
                        entity=entity,
                        preferred_domains=active_domains,
                    )
                    provider_results_scored.extend(list(scored_payload.get("scored_results") or []))
                    if not provider_quality_state or scored_payload.get("quality_state") == "provider_quality_ok":
                        provider_quality_state = str(scored_payload.get("quality_state") or "").strip() or provider_quality_state
                    elif provider_quality_state != "provider_quality_ok" and scored_payload.get("quality_state") == "provider_quality_mixed":
                        provider_quality_state = "provider_quality_mixed"
                    provider_top_score = max(provider_top_score, float(scored_payload.get("top_score") or 0.0))
                    if float(scored_payload.get("top_score") or 0.0) >= float(provider_top_score):
                        provider_top_score_breakdown = dict(scored_payload.get("top_score_breakdown") or {})
                    if scored_payload.get("top_candidates"):
                        provider_top_candidates = list(scored_payload.get("top_candidates") or [])
                    if bool(scored_payload.get("skip_primary")):
                        provider_quality_gate_triggered = True
                        provider_results_skipped_as_primary = True
                        quality_gate_reason = str(scored_payload.get("quality_gate_reason") or "").strip() or quality_gate_reason
                    filtered_search_results.extend(accepted_results)
                    filtered_out_items_with_reasons.extend(rejected_results)
                    filtered_out_reasons.extend(rejected_results)
                    filtered_search_results_count += len(accepted_results)
                    if strict_filter and any(str(item.get("rejection_reason") or "") == "domain_not_preferred" for item in rejected_results):
                        strict_domain_filter_failed = True
                    if not bool(scored_payload.get("skip_primary")):
                        for item in accepted_results:
                            lowered = str(item.get("url") or "").strip().lower()
                            if lowered:
                                seen_urls.add(lowered)
                            candidate_sources.append(
                                {
                                    "url": str(item.get("url") or "").strip(),
                                    "title": str(item.get("title") or "").strip(),
                                    "snippet": str(item.get("snippet") or "").strip(),
                                    "domain": str(item.get("domain") or "").strip(),
                                }
                            )
                        if candidate_sources:
                            winning_query_variant = candidate_query
                            break
                if candidate_sources:
                    break
        if not candidate_sources and search_queries_attempted:
            fallback_candidates = self._search_result_page_candidates(
                search_queries=search_queries_attempted,
                preferred_domains=preferred_domains,
            )
            if fallback_candidates:
                if provider_results_skipped_as_primary:
                    low_signal_fallback_to_serp_used = True
                filtered_out_reasons.append(
                    {
                        "reason": "search_results_page_fallback",
                        "queries": list(search_queries_attempted[:3]),
                    }
                )
                for candidate in fallback_candidates:
                    lowered = str(candidate.get("url") or "").strip().lower()
                    if not lowered or lowered in seen_urls:
                        continue
                    seen_urls.add(lowered)
                    candidate_sources.append(dict(candidate))
        elif (
            force_web_browse
            and search_queries_attempted
            and not any(str(item.get("source_kind") or "").strip() == "search_results_page" for item in candidate_sources)
        ):
            backup_candidates = self._search_result_page_candidates(
                search_queries=search_queries_attempted,
                preferred_domains=preferred_domains,
            )
            if backup_candidates:
                filtered_out_reasons.append(
                    {
                        "reason": "search_results_page_backup_added",
                        "queries": list(search_queries_attempted[:3]),
                    }
                )
                for candidate in backup_candidates:
                    lowered = str(candidate.get("url") or "").strip().lower()
                    if not lowered or lowered in seen_urls:
                        continue
                    seen_urls.add(lowered)
                    candidate_sources.append(dict(candidate))
        current_source_index = 0
        chosen_source_candidates = list(candidate_sources[:3])
        chosen_entry_url = str((chosen_source_candidates[0] if chosen_source_candidates else {}).get("url") or "").strip()
        return {
            "task_type": task_type,
            "candidate_sources": candidate_sources,
            "current_source_index": current_source_index,
            "attempted_source_indices": [],
            "search_again_used": search_again_used,
            "revised_query": revised_query,
            "search_queries_attempted": search_queries_attempted,
            "raw_search_results_count": raw_search_results_count,
            "filtered_search_results_count": filtered_search_results_count,
            "filtered_out_reasons": filtered_out_reasons,
            "raw_search_results": raw_search_results,
            "filtered_search_results": filtered_search_results,
            "filtered_out_items_with_reasons": filtered_out_items_with_reasons,
            "search_provider_diagnostics": search_provider_diagnostics,
            "provider_results_scored": provider_results_scored,
            "provider_quality_state": provider_quality_state or "provider_quality_empty",
            "provider_quality_gate_triggered": provider_quality_gate_triggered,
            "provider_results_skipped_as_primary": provider_results_skipped_as_primary,
            "low_signal_fallback_to_serp_used": low_signal_fallback_to_serp_used,
            "provider_top_candidates": provider_top_candidates,
            "provider_top_score": provider_top_score,
            "provider_top_score_breakdown": provider_top_score_breakdown,
            "quality_gate_reason": quality_gate_reason,
            "provider_name": provider_name,
            "search_provider_failed": search_provider_failed,
            "strict_domain_filter_failed": strict_domain_filter_failed,
            "relaxed_domain_retry_used": relaxed_domain_retry_used,
            "winning_query_variant": winning_query_variant,
            "chosen_source_candidates": chosen_source_candidates,
            "chosen_entry_url": chosen_entry_url,
            "preferred_domains_applied": bool(preferred_domains),
        }

    def _search_with_diagnostics(
        self,
        query: str,
        *,
        max_results: int,
        preferred_domains: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._search_tool_detailed is not None:
            try:
                payload = dict(
                    self._search_tool_detailed(
                        query,
                        max_results=max_results,
                        preferred_domains=preferred_domains,
                    )
                    or {}
                )
            except Exception:
                logger.debug("browse_search_tool_detailed_failed query=%s", query[:120], exc_info=True)
                return [], [
                    {
                        "provider_name": "search_tool_detailed",
                        "request_query": query,
                        "preferred_domains": list(preferred_domains),
                        "max_results": max_results,
                        "provider_request_started": True,
                        "provider_request_succeeded": False,
                        "provider_latency_ms": 0.0,
                        "provider_error_type": "DetailedSearchToolException",
                        "provider_error_message": "search_tool_detailed_exception",
                        "raw_provider_results_count": 0,
                        "raw_provider_results": [],
                        "search_provider_failed": True,
                    }
                ]
            return list(payload.get("results") or []), list(payload.get("diagnostics") or [])
        if self._search_tool is None:
            return [], []
        try:
            payload = self._search_tool(query, max_results=max_results, preferred_domains=preferred_domains)
        except Exception:
            logger.debug("browse_search_tool_failed query=%s", query[:120], exc_info=True)
            return [], [
                {
                    "provider_name": "search_tool",
                    "request_query": query,
                    "preferred_domains": list(preferred_domains),
                    "max_results": max_results,
                    "provider_request_started": True,
                    "provider_request_succeeded": False,
                    "provider_latency_ms": 0.0,
                    "provider_error_type": "SearchToolException",
                    "provider_error_message": "search_tool_exception",
                    "raw_provider_results_count": 0,
                    "raw_provider_results": [],
                    "search_provider_failed": True,
                }
            ]
        if isinstance(payload, dict):
            return list(payload.get("results") or []), list(payload.get("diagnostics") or [])
        return list(payload or []), []

    def _filter_search_results(
        self,
        *,
        results: list[dict[str, Any]],
        query: str,
        preferred_domains: list[str],
        seen_urls: set[str],
        strict_domain_filter: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        preferred = [str(item).strip().lower() for item in list(preferred_domains or []) if str(item).strip()]

        for item in list(results or []):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("snippet") or item.get("summary") or "").strip()
            domain = self._domain_from_url(url)
            lowered = url.lower()
            rejection_reason = ""
            if not url:
                rejection_reason = "invalid_url"
            elif not url.startswith(("http://", "https://")):
                rejection_reason = "non_http"
            elif lowered in seen_urls:
                rejection_reason = "duplicate"
            elif self._is_disallowed_browse_url(lowered):
                rejection_reason = "blocked_domain"
            elif strict_domain_filter and preferred and not any(
                domain == hint or domain.endswith(f".{hint}") or hint in domain
                for hint in preferred
            ):
                rejection_reason = "domain_not_preferred"
            elif self._looks_like_search_result_loop(url):
                rejection_reason = "result_page_loop"
            elif not title and not snippet:
                rejection_reason = "low_signal_title"
            if rejection_reason:
                rejected.append(
                    {
                        "query": query,
                        "url": url,
                        "title": title,
                        "domain": domain,
                        "accepted": False,
                        "rejection_reason": rejection_reason,
                    }
                )
                continue
            accepted.append(
                {
                    "query": query,
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "domain": domain,
                    "accepted": True,
                }
            )
        return accepted, rejected

    def _score_provider_results(
        self,
        *,
        accepted_results: list[dict[str, Any]],
        rejected_results: list[dict[str, Any]],
        task_type: str,
        entity: str,
        preferred_domains: list[str],
    ) -> dict[str, Any]:
        scored_results: list[dict[str, Any]] = []
        scored_candidates: list[dict[str, Any]] = []
        for item in rejected_results:
            scored_results.append(
                {
                    "url": str(item.get("url") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "domain": str(item.get("domain") or "").strip(),
                    "provider_quality_score": 0.0,
                    "provider_quality_breakdown": {},
                    "accepted_as_primary_candidate": False,
                    "downgraded_to_low_signal": True,
                    "rejection_reason": str(item.get("rejection_reason") or "").strip(),
                }
            )
        for item in accepted_results:
            scored = self._score_provider_candidate(
                url=str(item.get("url") or "").strip(),
                title=str(item.get("title") or "").strip(),
                domain=str(item.get("domain") or "").strip(),
                task_type=task_type,
                entity=entity,
                preferred_domains=preferred_domains,
            )
            scored_results.append(scored)
            scored_candidates.append(scored)

        scored_candidates.sort(key=lambda x: float(x.get("provider_quality_score") or 0.0), reverse=True)
        top_candidates = list(scored_candidates[:3])
        top_score = float(top_candidates[0].get("provider_quality_score") or 0.0) if top_candidates else 0.0
        top_score_breakdown = dict(top_candidates[0].get("provider_quality_breakdown") or {}) if top_candidates else {}

        high_quality_threshold = 6.0
        mixed_threshold = 4.0
        quality_state = "provider_quality_empty"
        if scored_candidates:
            if top_score >= high_quality_threshold:
                quality_state = "provider_quality_ok"
            elif top_score >= mixed_threshold:
                quality_state = "provider_quality_mixed"
            else:
                quality_state = "provider_quality_low_signal"

        skip_primary = False
        quality_gate_reason = ""
        if quality_state == "provider_quality_low_signal":
            skip_primary = True
            quality_gate_reason = "provider_low_signal_no_high_quality"
        elif quality_state == "provider_quality_mixed" and top_score < 5.5:
            skip_primary = True
            quality_gate_reason = "provider_mixed_below_threshold"

        for item in scored_candidates:
            score = float(item.get("provider_quality_score") or 0.0)
            item["accepted_as_primary_candidate"] = bool(score >= high_quality_threshold and not skip_primary)
            item["downgraded_to_low_signal"] = bool(skip_primary or score < mixed_threshold)
        return {
            "scored_results": scored_results,
            "quality_state": quality_state,
            "skip_primary": skip_primary,
            "quality_gate_reason": quality_gate_reason,
            "top_candidates": top_candidates,
            "top_score": top_score,
            "top_score_breakdown": top_score_breakdown,
        }

    def _score_provider_candidate(
        self,
        *,
        url: str,
        title: str,
        domain: str,
        task_type: str,
        entity: str,
        preferred_domains: list[str],
    ) -> dict[str, Any]:
        lowered_url = str(url or "").strip().lower()
        lowered_title = str(title or "").strip().lower()
        domain = str(domain or "").strip().lower()
        path = ""
        try:
            path = urlparse(lowered_url).path or ""
        except Exception:
            path = ""

        breakdown: dict[str, float] = {
            "domain_quality_score": 0.0,
            "title_quality_score": 0.0,
            "url_path_quality_score": 0.0,
            "task_alignment_score": 0.0,
            "entity_alignment_score": 0.0,
            "low_signal_penalty": 0.0,
        }
        score_reasons: list[str] = []

        preferred = [str(item).strip().lower() for item in list(preferred_domains or []) if str(item).strip()]
        if preferred and any(domain == hint or domain.endswith(f".{hint}") or hint in domain for hint in preferred):
            breakdown["domain_quality_score"] += 5.0
            score_reasons.append("preferred_domain_match")
        elif domain and not any(marker in domain for marker in ("bing.com", "google.com", "duckduckgo.com")):
            breakdown["domain_quality_score"] += 2.0

        low_signal_domains = {"zhihu.com", "zhihu.cn", "jingyan.baidu.com", "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com"}
        if domain in low_signal_domains:
            breakdown["low_signal_penalty"] -= 3.0
            score_reasons.append("low_signal_domain")
        if domain.startswith("support.") and task_type == "general_info":
            breakdown["low_signal_penalty"] -= 2.0
            score_reasons.append("support_domain_general_info")

        entity_match = self._entity_matches_page_text(entity, f"{title} {url}")
        if entity_match:
            breakdown["entity_alignment_score"] += 2.0
            score_reasons.append("entity_match")

        task_keywords = {
            "specs": ("specs", "specifications", "technical specifications", "参数", "配置"),
            "news": ("news", "newsroom", "press", "blog", "latest", "新闻", "发布"),
            "compare": ("compare", "comparison", "versus", " vs ", "对比", "区别", "差异"),
            "general_info": ("about", "overview", "company", "what is", "介绍", "概览", "关于"),
        }
        for signal in task_keywords.get(task_type, ()):
            if signal in lowered_title:
                breakdown["title_quality_score"] += 3.0
                score_reasons.append("task_title_signal")
                break
        if entity and entity.lower() in lowered_title:
            breakdown["title_quality_score"] += 2.0

        path_signals = {
            "specs": ("/specs", "/specifications", "/tech-specs"),
            "news": ("/news", "/newsroom", "/press", "/blog"),
            "compare": ("/compare", "/comparison"),
            "general_info": ("/about", "/company", "/overview", "/docs"),
        }
        for signal in path_signals.get(task_type, ()):
            if signal in path:
                breakdown["url_path_quality_score"] += 4.0
                score_reasons.append("task_path_signal")
                break

        if task_type in {"specs", "news", "compare"} and (path in ("", "/") or lowered_url.endswith("/")):
            breakdown["task_alignment_score"] -= 1.0
            score_reasons.append("homepage_low_priority")
        if task_type == "general_info" and ("/support" in path or "/help" in path):
            breakdown["task_alignment_score"] -= 2.0
            score_reasons.append("support_low_priority")

        if any(term in lowered_title for term in ("问答", "经验", "教程", "论坛", "社区", "知乎")):
            breakdown["low_signal_penalty"] -= 2.5
            score_reasons.append("low_signal_title")
        if any(term in path for term in ("/question", "/answer", "/forum", "/community", "/jingyan", "/ask")):
            breakdown["low_signal_penalty"] -= 3.0
            score_reasons.append("low_signal_path")

        total = sum(breakdown.values())
        return {
            "url": url,
            "title": title,
            "domain": domain,
            "provider_quality_score": round(total, 2),
            "provider_quality_breakdown": breakdown,
            "accepted_as_primary_candidate": False,
            "downgraded_to_low_signal": False,
            "rejection_reason": "",
            "score_reasons": score_reasons,
        }

    def _open_browse_page(self, url: str) -> dict[str, Any]:
        browser_status = self._browser.availability_status()
        if self._browser.available():
            payload = self._browser.rendered_read(url)
            payload["availability"] = payload.get("availability") or browser_status.to_dict()
            if bool(payload.get("ok")):
                page_payload = dict(payload.get("page") or {})
                self._apply_serp_metadata(url=url, page_payload=page_payload)
                payload["page"] = page_payload
            return payload
        if self._fetch_page is not None:
            try:
                html = self._fetch_page(url)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": str(exc),
                    "page": {"url": url, "final_url": url},
                    "availability": browser_status.to_dict(),
                }
            evidence = ContentExtractor.extract(url=url, raw_html=html, fallback_title="")
            page_payload = {
                "requested_url": url,
                "final_url": url,
                "url": url,
                "title": evidence.title,
                "html": html,
                "visible_text": evidence.main_text,
                "headings": BrowserExecutor.extract_headings_from_html(html),
                "links": BrowserExecutor.extract_links_from_html(url, html),
                "key_values": [],
                "table_rows": list(evidence.tables or []),
                "nav_items": BrowserExecutor.extract_nav_from_html(html),
                "screenshot_path": "",
                "handle_id": "",
            }
            self._apply_serp_metadata(url=url, page_payload=page_payload)
            page_payload["page_type"] = self._browser.classify_page_type(page_payload)
            return {"ok": True, "error": "", "page": page_payload, "availability": browser_status.to_dict()}
        return {"ok": False, "error": "rendered_read_unavailable", "page": {"url": url, "final_url": url}, "availability": browser_status.to_dict()}

    def _apply_serp_metadata(self, *, url: str, page_payload: dict[str, Any]) -> None:
        final_url = str(page_payload.get("final_url") or page_payload.get("url") or url).strip()
        serp_detected = self._is_serp_page(final_url or url, page_payload)
        if not serp_detected:
            page_payload["serp_detected"] = False
            page_payload["serp_links_extracted_count"] = 0
            page_payload["serp_links"] = []
            return
        serp_links = self._extract_serp_links(url=final_url or url, html=str(page_payload.get("html") or ""))
        page_payload["serp_detected"] = True
        page_payload["serp_links_extracted_count"] = len(serp_links)
        page_payload["serp_links"] = list(serp_links)
        if not serp_links:
            return
        merged_links: list[dict[str, str]] = []
        seen_links: set[str] = set()
        for item in [*serp_links, *list(page_payload.get("links") or [])]:
            candidate_url = str(item.get("url") or "").strip()
            candidate_text = str(item.get("text") or item.get("title") or candidate_url).strip()
            lowered = candidate_url.lower()
            if (
                not candidate_url
                or lowered in seen_links
                or self._looks_like_search_result_loop(candidate_url)
                or self._is_search_engine_internal_url(candidate_url)
            ):
                continue
            seen_links.add(lowered)
            entry = {"text": candidate_text, "url": candidate_url}
            snippet = str(item.get("snippet") or "").strip()
            if snippet:
                entry["snippet"] = snippet
            merged_links.append(entry)
        page_payload["links"] = merged_links

    def _extract_serp_links(self, *, url: str, html: str) -> list[dict[str, str]]:
        if not self._is_search_results_page(url):
            return []
        direct_links = [
            {
                "text": str(link.get("text") or link.get("title") or link.get("url") or "").strip(),
                "title": str(link.get("text") or link.get("title") or link.get("url") or "").strip(),
                "url": str(link.get("url") or "").strip(),
                "snippet": "",
            }
            for link in BrowserExecutor.extract_links_from_html(url, html)
            if not self._looks_like_search_result_loop(str(link.get("url") or ""))
            and not self._is_search_engine_internal_url(str(link.get("url") or ""))
        ]
        if direct_links:
            return direct_links[:8]
        query = self._query_from_search_results_url(url)
        if not query or self._search_tool_detailed is None:
            return []
        try:
            payload = dict(
                self._search_tool_detailed(
                    query,
                    max_results=5,
                    preferred_domains=[],
                )
                or {}
            )
        except Exception:
            logger.debug("serp_link_extraction_failed query=%s", query[:120], exc_info=True)
            return []
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in list(payload.get("results") or [])[:5]:
            target_url = str(item.get("url") or "").strip()
            text = str(item.get("title") or target_url).strip()
            lowered = target_url.lower()
            if (
                not target_url
                or lowered in seen
                or self._looks_like_search_result_loop(target_url)
                or self._is_search_engine_internal_url(target_url)
            ):
                continue
            seen.add(lowered)
            links.append(
                {
                    "text": text,
                    "title": text,
                    "url": target_url,
                    "snippet": str(item.get("snippet") or "").strip(),
                }
            )
        return links

    @staticmethod
    def _sanitize_browse_action(action: str) -> str:
        normalized = str(action or "").strip().lower()
        if normalized in {"open_link", "stop", "search_again"}:
            return normalized
        return "open_link"

    @staticmethod
    def _revise_query_for_search_again(*, query: str, task_type: str, entity: str, page_payload: dict[str, Any]) -> str:
        subject = str(entity or query or "").strip()
        headings = [str(item).strip() for item in list(page_payload.get("headings") or [])[:2] if str(item).strip()]
        if task_type == "specs":
            return f"{subject} specs official".strip()
        if task_type == "news":
            return f"{subject or query} latest news".strip()
        if task_type == "compare":
            return f"{subject or query} comparison".strip()
        if task_type == "product_lookup":
            return f"{subject or query} official product".strip()
        return " ".join([subject or query, *headings[:1], "official info"]).strip()

    @staticmethod
    def _browse_failure_page(
        *,
        task_type: str,
        entity: str,
        selected_links: list[dict[str, Any]],
        failure_reason: str,
        browse_entry: dict[str, Any],
        final_page_url: str = "",
        serp_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        serp_summary = dict(serp_summary or {})
        return {
            "browse_mode_used": True,
            "task_type": task_type,
            "entity": entity,
            "navigation_hops": 0,
            "selected_links": list(selected_links),
            "score_reasons": [
                {
                    "text": str(item.get("text") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "score_reasons": list(item.get("score_reasons") or []),
                }
                for item in list(selected_links or [])
            ],
            "final_page_type": "",
            "final_action": "",
            "stop_reason": failure_reason,
            "final_page_url": str(final_page_url or "").strip(),
            "source_candidates": list(browse_entry.get("candidate_sources") or []),
            "current_source_index": int(browse_entry.get("current_source_index") or 0),
            "attempted_source_indices": list(browse_entry.get("attempted_source_indices") or []),
            "search_again_used": bool(browse_entry.get("search_again_used")),
            "revised_query": str(browse_entry.get("revised_query") or "").strip(),
            "source_discovery_completed": bool(list(browse_entry.get("candidate_sources") or [])),
            "search_queries_attempted": list(browse_entry.get("search_queries_attempted") or []),
            "raw_search_results_count": int(browse_entry.get("raw_search_results_count") or 0),
            "filtered_search_results_count": int(browse_entry.get("filtered_search_results_count") or 0),
            "filtered_out_reasons": list(browse_entry.get("filtered_out_reasons") or []),
            "raw_search_results": list(browse_entry.get("raw_search_results") or []),
            "filtered_search_results": list(browse_entry.get("filtered_search_results") or []),
            "filtered_out_items_with_reasons": list(browse_entry.get("filtered_out_items_with_reasons") or []),
            "search_provider_diagnostics": list(browse_entry.get("search_provider_diagnostics") or []),
            "provider_name": str(browse_entry.get("provider_name") or "").strip(),
            "search_provider_failed": bool(browse_entry.get("search_provider_failed")),
            "provider_quality_gate_triggered": bool(browse_entry.get("provider_quality_gate_triggered")),
            "provider_quality_state": str(browse_entry.get("provider_quality_state") or "").strip(),
            "provider_results_scored": list(browse_entry.get("provider_results_scored") or []),
            "provider_top_candidates": list(browse_entry.get("provider_top_candidates") or []),
            "provider_top_score": float(browse_entry.get("provider_top_score") or 0.0),
            "provider_top_score_breakdown": dict(browse_entry.get("provider_top_score_breakdown") or {}),
            "provider_results_skipped_as_primary": bool(browse_entry.get("provider_results_skipped_as_primary")),
            "low_signal_fallback_to_serp_used": bool(browse_entry.get("low_signal_fallback_to_serp_used")),
            "quality_gate_reason": str(browse_entry.get("quality_gate_reason") or "").strip(),
            "strict_domain_filter_failed": bool(browse_entry.get("strict_domain_filter_failed")),
            "relaxed_domain_retry_used": bool(browse_entry.get("relaxed_domain_retry_used")),
            "winning_query_variant": str(browse_entry.get("winning_query_variant") or "").strip(),
            "chosen_source_candidates": list(browse_entry.get("chosen_source_candidates") or []),
            "chosen_entry_url": str(browse_entry.get("chosen_entry_url") or "").strip(),
            "preferred_domains_applied": bool(browse_entry.get("preferred_domains_applied")),
            "final_page_quality_score": float(browse_entry.get("final_page_quality_score") or 0.0),
            "final_page_quality_reason": str(browse_entry.get("final_page_quality_reason") or "").strip(),
            "stop_blocked_by_low_page_quality": bool(browse_entry.get("stop_blocked_by_low_page_quality")),
            "serp_detected": bool(serp_summary.get("serp_detected")),
            "serp_links_extracted_count": int(serp_summary.get("serp_links_extracted_count") or 0),
            "ranked_candidates": list(serp_summary.get("ranked_candidates") or []),
            "top_ranked_candidate": dict(serp_summary.get("top_ranked_candidate") or {}),
            "top_ranked_score": float(serp_summary.get("top_ranked_score") or 0.0),
            "top_ranked_score_breakdown": dict(serp_summary.get("top_ranked_score_breakdown") or {}),
            "serp_selection_reason": str(serp_summary.get("serp_selection_reason") or "").strip(),
            "model_selected_candidate": dict(serp_summary.get("model_selected_candidate") or {}),
            "final_selected_candidate": dict(serp_summary.get("final_selected_candidate") or {}),
            "selection_overridden_by_ranker": bool(serp_summary.get("selection_overridden_by_ranker")),
        }

    @staticmethod
    def _domain_from_url(url: str) -> str:
        text = str(url or "").strip()
        match = re.match(r"^https?://([^/]+)", text, re.IGNORECASE)
        return str(match.group(1) if match else "").strip().lower()

    @staticmethod
    def _is_search_results_page(url: str) -> bool:
        lowered = str(url or "").strip().lower()
        return any(
            marker in lowered
            for marker in (
                "bing.com/search",
                "bing.com/?form=",
                "duckduckgo.com/html",
                "duckduckgo.com/?q=",
                "google.com/search",
                "yahoo.com/search",
            )
        )

    @classmethod
    def _looks_like_search_result_loop(cls, url: str) -> bool:
        return cls._is_search_results_page(url) or cls._is_search_engine_internal_url(url)

    @classmethod
    def _is_serp_page(cls, url: str, page_snapshot: dict[str, Any] | None = None) -> bool:
        effective_url = str(url or "").strip()
        if cls._is_search_results_page(effective_url):
            return True
        snapshot = dict(page_snapshot or {})
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or effective_url).strip()
        if cls._is_search_results_page(final_url):
            return True
        domain = cls._domain_from_url(final_url or effective_url)
        title = str(snapshot.get("title") or "").strip().lower()
        visible_text = str(snapshot.get("visible_text") or "").strip().lower()
        links = list(snapshot.get("links") or [])
        external_link_count = sum(
            1
            for item in links
            if str(item.get("url") or "").strip()
            and not cls._looks_like_search_result_loop(str(item.get("url") or "").strip())
            and not cls._is_search_engine_internal_url(str(item.get("url") or "").strip())
        )
        if any(token in title for token in (" - search", "search results", "results for")):
            return True
        if any(token in visible_text for token in ("results for", "search results", "related searches")):
            return True
        if any(engine in domain for engine in ("bing.com", "google.com", "yahoo.com", "duckduckgo.com")) and external_link_count >= 3:
            return True
        return False

    @classmethod
    def _is_search_engine_internal_url(cls, url: str) -> bool:
        lowered = str(url or "").strip().lower()
        domain = cls._domain_from_url(lowered)
        if not domain:
            return False
        if not any(engine in domain for engine in ("bing.com", "google.com", "yahoo.com", "duckduckgo.com")):
            return False
        if cls._is_search_results_page(lowered):
            return True
        parsed = urlparse(lowered)
        path = str(parsed.path or "/").strip() or "/"
        if path in {"", "/"}:
            return True
        return any(
            token in path
            for token in (
                "/images",
                "/videos",
                "/video",
                "/news",
                "/maps",
                "/search",
                "/visualsearch",
                "/travel",
            )
        )

    @staticmethod
    def _query_from_search_results_url(url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
        except Exception:
            return ""
        query = parse_qs(parsed.query)
        for key in ("q", "query", "wd", "text"):
            values = query.get(key)
            if values:
                return unquote_plus(str(values[0] or "")).strip()
        return ""

    @staticmethod
    def _search_result_page_candidates(
        *,
        search_queries: list[str],
        preferred_domains: list[str],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for raw_query in search_queries[:2]:
            query = str(raw_query or "").strip()
            if not query:
                continue
            search_query = query
            if preferred_domains:
                domain = str(preferred_domains[0] or "").strip()
                if domain and f"site:{domain}" not in search_query.lower():
                    search_query = f"site:{domain} {search_query}"
            encoded = quote_plus(search_query)
            candidates.append(
                {
                    "url": f"https://www.bing.com/search?q={encoded}",
                    "title": f"Search results for {search_query}",
                    "snippet": "Synthetic search results page fallback",
                    "domain": "www.bing.com",
                    "source_kind": "search_results_page",
                }
            )
        return candidates

    def _execute_browser_interaction(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        context: dict[str, Any],
        progress_callback: ProgressCallback | None,
    ) -> WebAccessExecutionResult:
        browser_status = self._browser.availability_status()
        self._emit(
            progress_callback,
            "browser_attempted",
            available=browser_status.available,
            availability_level=browser_status.level,
            reason=browser_status.reason,
            fallback_mode=browser_status.fallback_mode,
        )
        if not browser_status.available:
            self._emit(
                progress_callback,
                "web_access_fallback_applied",
                stage="browser_unavailable_to_rendered",
                reason=browser_status.reason or "browser_automation_unavailable",
            )
            self._emit(
                progress_callback,
                "fallback_applied",
                stage="browser_unavailable_to_rendered",
                reason=browser_status.reason or "browser_automation_unavailable",
            )
            fallback = self._execute_rendered_read(decision=decision, plan=plan, progress_callback=progress_callback)
            fallback.fallback_stage = "browser_unavailable_to_rendered"
            fallback.browser_availability = browser_status
            return fallback
        target_url = str((plan.target_urls or [""])[0]).strip()
        self._emit(progress_callback, "browser_interaction_started", url=target_url, actions=len(plan.browser_actions))
        page = self._browser.execute(
            target_url=target_url,
            actions=plan.browser_actions,
            existing_handle_id=str(context.get("last_handle_id") or "").strip(),
        )
        browser_status = self._status_from_payload(page.get("availability")) or browser_status
        if not page.get("ok"):
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=4,
                mode="browser_interaction",
                failure_reason=str(page.get("error") or "browser_interaction_failed"),
                browser_availability=browser_status,
            )
        page_payload = dict(page.get("page") or {})
        page_payload["post_open_extract_attempted"] = True
        extract_ok, extract_failure_reason = self._assess_post_open_extraction(page_payload)
        page_payload["post_open_extract_result"] = "success" if extract_ok else "failed"
        page_payload["post_open_extract_failure_reason"] = extract_failure_reason
        self._emit(
            progress_callback,
            "post_open_extract_result",
            result=page_payload["post_open_extract_result"],
            reason=extract_failure_reason,
        )
        if not extract_ok:
            fallback_reason = extract_failure_reason or "post_open_extract_failed"
            if target_url:
                self._emit(
                    progress_callback,
                    "web_access_fallback_applied",
                    stage="browser_to_rendered_read",
                    reason=fallback_reason,
                )
                self._emit(
                    progress_callback,
                    "fallback_applied",
                    stage="browser_to_rendered_read",
                    reason=fallback_reason,
                )
                fallback = self._execute_rendered_read(decision=decision, plan=plan, progress_callback=progress_callback)
                fallback.fallback_stage = "browser_to_rendered_read"
                fallback.browser_availability = browser_status
                if fallback.success:
                    fallback.used_browser_interaction = True
                    fallback.browser_result.setdefault("post_open_extract_attempted", True)
                    fallback.browser_result.setdefault("post_open_extract_result", "fallback_success")
                    fallback.browser_result.setdefault("post_open_extract_failure_reason", fallback_reason)
                    return fallback
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=4,
                mode="browser_interaction",
                opened_pages=[page_payload] if page_payload else [],
                browser_result=page_payload,
                used_browser_interaction=True,
                failure_reason=fallback_reason,
                browser_availability=browser_status,
            )
        return self._result(
            success=True,
            decision=decision,
            plan=plan,
            level=4,
            mode="browser_interaction",
            opened_pages=[page_payload] if page_payload else [],
            browser_result=page_payload,
            used_browser_interaction=True,
            browser_availability=browser_status,
        )

    def _execute_visual_read(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        context: dict[str, Any],
        progress_callback: ProgressCallback | None,
    ) -> WebAccessExecutionResult:
        page_context_available = bool(plan.target_urls or context.get("last_url") or context.get("last_handle_id"))
        if not page_context_available:
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=5,
                mode="visual_read",
                failure_reason="no_active_page_context",
                browser_availability=self._browser.availability_status(),
            )
        browser_result = self._execute_browser_interaction(
            decision=decision,
            plan=plan,
            context=context,
            progress_callback=progress_callback,
        )
        if not browser_result.success:
            browser_result.final_mode = "visual_read"
            browser_result.execution_level = 5
            browser_result.fallback_stage = browser_result.fallback_stage or "visual_read_requires_page_context"
            return browser_result
        page = dict(browser_result.browser_result or (browser_result.opened_pages[0] if browser_result.opened_pages else {}))
        page["page_context_available"] = page_context_available
        page["visual_target_region"] = ",".join(str(item.get("region") or "").strip() for item in plan.visual_targets if str(item.get("region") or "").strip())
        page["screenshot_taken"] = bool(str(page.get("screenshot_path") or "").strip())
        self._emit(progress_callback, "visual_read_started", regions=[item.get("region") for item in plan.visual_targets])
        if not str(page.get("screenshot_path") or "").strip():
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=5,
                mode="visual_read",
                opened_pages=browser_result.opened_pages,
                browser_result=page,
                used_browser_interaction=browser_result.browser_interaction_used,
                used_visual_read=False,
                failure_reason="screenshot_failed",
                browser_availability=browser_result.browser_availability,
            )
        visual_results = self._visual_reader.read(page, plan.visual_targets)
        if not visual_results:
            failure_reason = self._visual_failure_reason(page, plan)
            if str(page.get("visible_text") or "").strip():
                self._emit(
                    progress_callback,
                    "web_access_fallback_applied",
                    stage="visual_read_fallback_to_rendered_read",
                    reason=failure_reason,
                )
                self._emit(
                    progress_callback,
                    "fallback_applied",
                    stage="visual_read_fallback_to_rendered_read",
                    reason=failure_reason,
                )
            return self._result(
                success=False,
                decision=decision,
                plan=plan,
                level=5,
                mode="visual_read",
                opened_pages=browser_result.opened_pages,
                browser_result=page,
                used_browser_interaction=browser_result.browser_interaction_used,
                used_visual_read=False,
                failure_reason=failure_reason,
                browser_availability=browser_result.browser_availability,
            )
        return self._result(
            success=bool(visual_results),
            decision=decision,
            plan=plan,
            level=5,
            mode="visual_read",
            opened_pages=browser_result.opened_pages,
            browser_result=page,
            visual_results=visual_results,
            used_browser_interaction=browser_result.browser_interaction_used,
            used_visual_read=True,
            failure_reason="" if visual_results else "visual_read_failed",
            browser_availability=browser_result.browser_availability,
        )

    def _build_browse_candidate_pool(
        self,
        *,
        page_payload: dict[str, Any],
        source_descriptor: Any,
        task_type: str,
        entity: str,
        max_links: int,
    ) -> list[ScoredLink]:
        if bool(page_payload.get("serp_detected")):
            serp_candidates: list[ScoredLink] = []
            seen_serp_urls: set[str] = set()
            for item in list(page_payload.get("serp_links") or page_payload.get("links") or []):
                url = str(item.get("url") or "").strip()
                text = str(item.get("text") or item.get("title") or url).strip()
                lowered = url.lower()
                if (
                    not url
                    or not text
                    or lowered in seen_serp_urls
                    or self._looks_like_search_result_loop(url)
                    or self._is_search_engine_internal_url(url)
                    or self._is_disallowed_browse_url(lowered)
                ):
                    continue
                seen_serp_urls.add(lowered)
                serp_candidates.append(
                    ScoredLink(
                        text=text,
                        url=url,
                        score=0,
                        reasons=["serp_result_link"],
                    )
                )
                if len(serp_candidates) >= max_links:
                    break
            if serp_candidates:
                return serp_candidates[:max_links]
        scored_links = select_candidate_links(
            task_type=task_type,
            entity=entity,
            page_snapshot=page_payload,
            source_descriptor=source_descriptor,
            max_links=max_links,
        )
        ordered: list[ScoredLink] = list(scored_links)
        seen = {item.url.lower() for item in ordered}
        for link in self._browser.extract_links(page_payload):
            url = str(link.get("url") or "").strip()
            text = str(link.get("text") or url).strip()
            if not url or not text:
                continue
            lowered = url.lower()
            if lowered in seen or self._is_disallowed_browse_url(lowered):
                continue
            seen.add(lowered)
            ordered.append(ScoredLink(text=text, url=url, score=0, reasons=["visible_link"]))
            if len(ordered) >= max_links:
                break
        return ordered[:max_links]

    def _choose_browse_links(
        self,
        *,
        policy: dict[str, Any],
        candidate_pool: list[ScoredLink],
        page_payload: dict[str, Any],
        source_descriptor: Any,
        task_type: str,
        entity: str,
        max_links: int,
    ) -> list[ScoredLink]:
        if bool(page_payload.get("serp_detected")):
            ranked_candidates = self._rank_serp_candidates(
                candidate_pool=candidate_pool,
                page_payload=page_payload,
                source_descriptor=source_descriptor,
                task_type=task_type,
                entity=entity,
            )
            page_payload["ranked_candidates"] = list(ranked_candidates[:5])
            top_ranked = ranked_candidates[0] if ranked_candidates else {}
            page_payload["top_ranked_candidate"] = dict(top_ranked) if top_ranked else {}
            page_payload["top_ranked_score"] = float(top_ranked.get("score_total") or 0.0) if top_ranked else 0.0
            page_payload["top_ranked_score_breakdown"] = dict(top_ranked.get("score_breakdown") or {}) if top_ranked else {}
            model_selected = self._serp_model_selected_candidate(policy=policy, ranked_candidates=ranked_candidates)
            page_payload["model_selected_candidate"] = dict(model_selected) if model_selected else {}
            if model_selected and top_ranked:
                model_score = float(model_selected.get("score_total") or 0.0)
                top_score = float(top_ranked.get("score_total") or 0.0)
                if top_score - model_score >= 5.0:
                    page_payload["selection_overridden_by_ranker"] = True
                    page_payload["final_selected_candidate"] = dict(top_ranked)
                    page_payload["serp_selection_reason"] = "top_ranked_override"
                    selected = self._candidate_pool_item(candidate_pool, top_ranked)
                    return [selected] if selected is not None else []
                page_payload["selection_overridden_by_ranker"] = False
                page_payload["final_selected_candidate"] = dict(model_selected)
                page_payload["serp_selection_reason"] = "model_selected_rank_aligned"
                selected = self._candidate_pool_item(candidate_pool, model_selected)
                return [selected] if selected is not None else []
            if top_ranked:
                page_payload["selection_overridden_by_ranker"] = False
                page_payload["final_selected_candidate"] = dict(top_ranked)
                page_payload["serp_selection_reason"] = "top_ranked_candidate"
                selected = self._candidate_pool_item(candidate_pool, top_ranked)
                return [selected] if selected is not None else []
        if candidate_pool:
            chosen_ids = []
            for item in list(policy.get("candidate_link_ids") or []):
                try:
                    chosen_ids.append(int(item))
                except Exception:
                    continue
            selected = [
                candidate_pool[index]
                for index in chosen_ids
                if 0 <= index < len(candidate_pool)
            ]
            if selected:
                return selected[:max_links]

        fallback = select_candidate_links(
            task_type=task_type,
            entity=entity,
            page_snapshot=page_payload,
            source_descriptor=source_descriptor,
            max_links=max_links,
        )
        if fallback:
            return fallback
        return candidate_pool[:max_links]

    def _rank_serp_candidates(
        self,
        *,
        candidate_pool: list[ScoredLink],
        page_payload: dict[str, Any],
        source_descriptor: Any,
        task_type: str,
        entity: str,
    ) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        serp_query = self._query_from_search_results_url(
            str(page_payload.get("final_url") or page_payload.get("url") or "").strip()
        )
        preferred_domains: list[str] = []
        if source_descriptor is not None:
            home_domain = self._domain_from_url(str(getattr(source_descriptor, "home_url", "") or ""))
            if home_domain:
                preferred_domains.append(home_domain)
            for domain in list(getattr(source_descriptor, "domains", ()) or ()):
                cleaned = str(domain or "").strip().lower()
                if cleaned and cleaned not in preferred_domains:
                    preferred_domains.append(cleaned)
        brand_terms = self._serp_entity_terms(entity=entity, serp_query=serp_query)
        for index, candidate in enumerate(candidate_pool):
            ranking = self._score_serp_candidate(
                candidate=candidate,
                task_type=task_type,
                brand_terms=brand_terms,
                preferred_domains=preferred_domains,
            )
            ranking["candidate_index"] = index
            ranked.append(ranking)
        ranked.sort(
            key=lambda item: (
                1 if not item.get("accepted") else 0,
                -float(item.get("score_total") or 0.0),
                len(str(item.get("url") or "")),
            )
        )
        return ranked

    def _score_serp_candidate(
        self,
        *,
        candidate: ScoredLink,
        task_type: str,
        brand_terms: list[str],
        preferred_domains: list[str],
    ) -> dict[str, Any]:
        url = str(candidate.url or "").strip()
        title = str(candidate.text or url).strip()
        domain = self._domain_from_url(url)
        lowered_url = url.lower()
        lowered_title = title.lower()
        score_breakdown = {
            "domain_score": 0.0,
            "title_score": 0.0,
            "url_path_score": 0.0,
            "task_match_score": 0.0,
            "entity_match_score": 0.0,
            "penalty_score": 0.0,
        }
        accepted = True
        rejection_reason = ""

        if not url.startswith(("http://", "https://")):
            accepted = False
            rejection_reason = "non_http"
        elif self._looks_like_search_result_loop(url):
            accepted = False
            rejection_reason = "search_engine_self_link"
        elif any(token in lowered_url for token in ("javascript:void", "#", "login", "signin")):
            accepted = False
            rejection_reason = "invalid_or_login_link"

        if preferred_domains and any(
            domain == preferred or domain.endswith(f".{preferred}") or preferred in domain
            for preferred in preferred_domains
        ):
            score_breakdown["domain_score"] += 5.0
        elif any(term in domain for term in brand_terms if len(term) >= 3):
            score_breakdown["domain_score"] += 4.0
        elif domain and domain.count(".") <= 2 and not self._looks_like_search_result_loop(url):
            score_breakdown["domain_score"] += 2.0

        if task_type == "specs":
            if any(token in lowered_title for token in ("specs", "specifications", "technical specifications", "参数", "配置", "规格")):
                score_breakdown["title_score"] += 4.0
            if any(token in lowered_url for token in ("/specs", "/specifications", "tech-specs")):
                score_breakdown["url_path_score"] += 5.0
            if any(token in lowered_url for token in ("/model", "/product", "/products")):
                score_breakdown["task_match_score"] += 2.0
            if domain and lowered_url.rstrip("/").endswith(domain):
                score_breakdown["task_match_score"] -= 3.0
        elif task_type == "news":
            if any(token in lowered_title for token in ("news", "newsroom", "latest", "press", "blog")):
                score_breakdown["title_score"] += 3.0
            if "newsroom" in lowered_title or "press" in lowered_title:
                score_breakdown["title_score"] += 1.0
            if any(token in lowered_url for token in ("/news", "/newsroom", "/press", "/blog")):
                score_breakdown["url_path_score"] += 5.0
            if any(token in lowered_url for token in ("/newsroom", "/press")):
                score_breakdown["url_path_score"] += 1.0
            if domain and lowered_url.rstrip("/").endswith(domain):
                score_breakdown["task_match_score"] -= 3.0
            if any(token in lowered_url for token in ("/news", "/newsroom", "/press", "/blog")):
                score_breakdown["task_match_score"] += 2.0
        elif task_type == "compare":
            if any(token in lowered_title for token in ("compare", "comparison", " vs ", "区别", "对比")):
                score_breakdown["title_score"] += 3.0
            if any(token in lowered_url for token in ("/compare", "/comparison")):
                score_breakdown["url_path_score"] += 5.0
            if any(token in lowered_url for token in ("/specs", "/specifications")):
                score_breakdown["task_match_score"] += 2.0
            if domain and lowered_url.rstrip("/").endswith(domain):
                score_breakdown["task_match_score"] -= 3.0
        elif task_type == "general_info":
            if any(token in lowered_title for token in ("about", "overview", "what is", "company")):
                score_breakdown["title_score"] += 4.0
            if any(token in lowered_title for token in ("docs", "documentation", "guide", "help")):
                score_breakdown["title_score"] += 1.0
            if any(token in lowered_url for token in ("/about", "/company", "/overview")):
                score_breakdown["url_path_score"] += 4.0
            if any(token in lowered_url for token in ("/docs", "/documentation", "/guide", "/help")):
                score_breakdown["url_path_score"] += 1.0
            if domain and lowered_url.rstrip("/").endswith(domain):
                score_breakdown["task_match_score"] += 2.0
            if any(token in lowered_url for token in ("/news", "/press", "/blog")):
                score_breakdown["task_match_score"] -= 3.0
        elif task_type == "product_lookup":
            if any(token in lowered_url for token in ("/product", "/products")):
                score_breakdown["url_path_score"] += 3.0

        strong_entity_matches = sum(
            1
            for term in brand_terms
            if term and (term in lowered_title or term in lowered_url)
        )
        if strong_entity_matches >= 2:
            score_breakdown["entity_match_score"] += 4.0
        elif strong_entity_matches == 1:
            score_breakdown["entity_match_score"] += 2.0
        elif brand_terms:
            score_breakdown["entity_match_score"] -= 1.0

        if any(token in lowered_title or token in lowered_url for token in ("download", "ad", "promo", "coupon", "redirect", "cache")):
            score_breakdown["penalty_score"] -= 4.0
        if any(token in lowered_url for token in ("utm_", "tracking", "click", "redirect", "cached")):
            score_breakdown["penalty_score"] -= 3.0
        if any(token in lowered_title for token in ("reddit", "forum", "贴吧")) and task_type in {"specs", "general_info"}:
            score_breakdown["penalty_score"] -= 2.0

        score_total = float(sum(score_breakdown.values()))
        return {
            "url": url,
            "title": title,
            "domain": domain,
            "score_total": score_total,
            "score_breakdown": score_breakdown,
            "accepted": accepted,
            "rejected": not accepted,
            "rejection_reason": rejection_reason,
        }

    @staticmethod
    def _serp_entity_terms(*, entity: str, serp_query: str) -> list[str]:
        raw = f"{entity} {serp_query}".strip().lower()
        tokens = [
            token
            for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", raw)
            if token and len(token) >= 2 and token not in {"官网", "今天", "配置", "参数", "区别", "news", "latest", "official", "compare", "comparison"}
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped[:8]

    @staticmethod
    def _serp_model_selected_candidate(*, policy: dict[str, Any], ranked_candidates: list[dict[str, Any]]) -> dict[str, Any]:
        chosen_ids: list[int] = []
        for item in list(policy.get("candidate_link_ids") or []):
            try:
                chosen_ids.append(int(item))
            except Exception:
                continue
        if policy.get("selected_link_id") is not None:
            try:
                chosen_ids.insert(0, int(policy.get("selected_link_id")))
            except Exception:
                pass
        for item in ranked_candidates:
            if int(item.get("candidate_index") or -1) in chosen_ids and bool(item.get("accepted")):
                return item
        return {}

    @staticmethod
    def _candidate_pool_item(candidate_pool: list[ScoredLink], ranked_candidate: dict[str, Any]) -> ScoredLink | None:
        try:
            index = int(ranked_candidate.get("candidate_index"))
        except Exception:
            return None
        if 0 <= index < len(candidate_pool):
            return candidate_pool[index]
        return None

    def _select_best_serp_link(
        self,
        *,
        query: str,
        task_type: str,
        entity: str,
        candidate_pool: list[ScoredLink],
        page_payload: dict[str, Any],
        source_descriptor: Any,
    ) -> dict[str, Any]:
        ranked_candidates = self._rank_serp_candidates(
            candidate_pool=candidate_pool,
            page_payload=page_payload,
            source_descriptor=source_descriptor,
            task_type=task_type,
            entity=entity,
        )
        snippets_by_url = {
            str(item.get("url") or "").strip(): str(item.get("snippet") or "").strip()
            for item in list(page_payload.get("serp_links") or [])
        }
        candidate_links = [
            {
                "id": int(item.get("candidate_index") or idx),
                "url": str(item.get("url") or "").strip(),
                "title": str(item.get("title") or item.get("url") or "").strip(),
                "snippet": snippets_by_url.get(str(item.get("url") or "").strip(), ""),
            }
            for idx, item in enumerate(ranked_candidates[:8])
            if str(item.get("url") or "").strip()
        ]
        model_selected_link: dict[str, Any] = {}
        selection_reason = ""
        fallback_used = False
        if self._serp_link_selection_callback is not None and candidate_links:
            try:
                model_result = dict(
                    self._serp_link_selection_callback(
                        query=str(query or "").strip(),
                        task_type=str(task_type or "").strip(),
                        entity=str(entity or "").strip(),
                        candidate_links=list(candidate_links),
                    )
                    or {}
                )
            except Exception:
                logger.debug("serp_link_selection_callback_failed", exc_info=True)
                model_result = {}
            selected_url = str(model_result.get("selected_url") or "").strip()
            if selected_url:
                lowered_selected = selected_url.lower()
                for item in candidate_links:
                    if str(item.get("url") or "").strip().lower() == lowered_selected:
                        model_selected_link = dict(item)
                        selection_reason = str(model_result.get("reason") or "").strip()
                        break
        if not model_selected_link:
            fallback_used = True
            for item in ranked_candidates:
                if not bool(item.get("accepted")):
                    continue
                selected = self._candidate_pool_item(candidate_pool, item)
                if selected is None:
                    continue
                model_selected_link = {
                    "id": int(item.get("candidate_index") or 0),
                    "url": str(item.get("url") or "").strip(),
                    "title": str(item.get("title") or item.get("url") or "").strip(),
                    "snippet": snippets_by_url.get(str(item.get("url") or "").strip(), ""),
                }
                if not selection_reason:
                    selection_reason = (
                        "official_domain"
                        if float(dict(item.get("score_breakdown") or {}).get("domain_score") or 0.0) >= 4.0
                        else "high_relevance"
                    )
                break
        return {
            "ranked_candidates": ranked_candidates,
            "candidate_links": candidate_links,
            "model_selected_link": model_selected_link,
            "selection_reason": selection_reason,
            "fallback_used": fallback_used,
        }

    def _call_browse_policy(
        self,
        *,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        source_descriptor: Any,
        task_type: str,
        entity: str,
        hop: int,
        max_hops: int,
        page_snapshot: dict[str, Any],
        page_type_guess: str,
        candidate_links: list[ScoredLink],
        navigation_targets: list[str],
    ) -> dict[str, Any]:
        if self._browse_policy_callback is None:
            return {}
        source_constraints = dict(plan.source_constraints or {})
        payload = {
            "query": str(
                source_constraints.get("original_user_query")
                or source_constraints.get("user_query")
                or (plan.primary_queries[:1] or [""])[0]
                or (plan.target_urls[:1] or [""])[0]
            ).strip(),
            "source_name": decision.source_name or "",
            "source_domain": decision.source_domain or "",
            "task_type": task_type,
            "entity": entity,
            "hop": hop,
            "max_hops": max_hops,
            "page_type_guess": page_type_guess,
            "page_snapshot": {
                "title": str(page_snapshot.get("title") or "").strip(),
                "url": str(page_snapshot.get("final_url") or page_snapshot.get("url") or "").strip(),
                "headings": [str(item).strip() for item in list(page_snapshot.get("headings") or [])[:8] if str(item).strip()],
                "nav_items": [str(item).strip() for item in list(page_snapshot.get("nav_items") or [])[:8] if str(item).strip()],
                "visible_text_excerpt": str(page_snapshot.get("visible_text") or "").strip()[:1800],
            },
            "candidate_links": [
                {
                    "id": index,
                    "text": item.text,
                    "url": item.url,
                    "score_reasons": list(item.reasons),
                }
                for index, item in enumerate(candidate_links)
            ],
            "navigation_targets": list(navigation_targets[:8]),
            "source_hints": dict(getattr(source_descriptor, "navigation_hints", {}) or {}) if source_descriptor is not None else {},
        }
        try:
            result = self._browse_policy_callback(**payload)
        except Exception:
            logger.debug("browse_policy_callback_failed", exc_info=True)
            return {}
        data = dict(result or {})
        data["action"] = self._sanitize_browse_action(str(data.get("action") or "").strip())
        try:
            selected_link_id = int(data.get("selected_link_id")) if data.get("selected_link_id") is not None else None
        except Exception:
            selected_link_id = None
        if selected_link_id is not None and not (0 <= selected_link_id < len(candidate_links)):
            selected_link_id = None
        data["selected_link_id"] = selected_link_id
        return data

    @staticmethod
    def _policy_confidence(policy: dict[str, Any]) -> float:
        try:
            return float(policy.get("confidence") or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _sanitize_task_type(task_type: str) -> str:
        normalized = str(task_type or "").strip().lower()
        if normalized in {"specs", "release", "news", "product_lookup", "general_info", "compare"}:
            return normalized
        return "general_info"

    @staticmethod
    def _sanitize_page_type(page_type: str) -> str:
        normalized = str(page_type or "").strip().lower()
        if normalized in {"homepage", "docs", "product", "news", "generic"}:
            return normalized
        return "generic"

    @staticmethod
    def _is_disallowed_browse_url(url: str) -> bool:
        lowered = str(url or "").strip().lower()
        if not lowered:
            return True
        if lowered.startswith(("javascript:", "mailto:", "tel:")):
            return True
        blocked_terms = ("login", "sign-in", "signin", "checkout", "cart", "account", "pay", "purchase")
        return any(term in lowered for term in blocked_terms)

    def _browse_stop_guardrail(
        self,
        *,
        page_payload: dict[str, Any],
        task_type: str,
        entity: str,
        page_type: str,
    ) -> bool:
        heuristic_stop, _ = self._browse_stop_signal(
            page_payload=page_payload,
            task_type=task_type,
            entity=entity,
            page_type=page_type,
        )
        if heuristic_stop:
            return True
        if task_type in {"general_info", "compare"}:
            visible_text = str(page_payload.get("visible_text") or "").strip()
            title = str(page_payload.get("title") or "").strip().lower()
            return bool(
                (
                    entity
                    and self._entity_matches_page_text(
                        entity,
                        f"{title} {visible_text[:1200].lower()}",
                    )
                )
                or len(visible_text) >= 320
                or page_type in {"docs", "product", "news", "generic"}
            )
        return False

    @staticmethod
    def _annotate_browse_page(
        page_payload: dict[str, Any],
        *,
        task_type: str,
        entity: str,
        navigation_hops: int,
        selected_links: list[dict[str, Any]],
        final_page_type: str,
        stop_reason: str,
        browse_entry: dict[str, Any],
        page_open_attempted: bool,
        model_decision_emitted: bool,
        serp_summary: dict[str, Any] | None = None,
    ) -> None:
        serp_summary = dict(serp_summary or {})
        page_payload["browse_mode_used"] = True
        page_payload["task_type"] = task_type
        page_payload["entity"] = entity
        page_payload["navigation_hops"] = navigation_hops
        page_payload["selected_links"] = list(selected_links)
        page_payload["score_reasons"] = [
            {
                "text": str(item.get("text") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "score_reasons": list(item.get("score_reasons") or []),
            }
            for item in selected_links
        ]
        page_payload["final_page_type"] = final_page_type
        page_payload["final_action"] = str(page_payload.get("next_action") or "").strip()
        page_payload["stop_reason"] = stop_reason
        page_payload["final_page_url"] = str(page_payload.get("final_url") or page_payload.get("url") or "").strip()
        page_payload["source_candidates"] = list(browse_entry.get("candidate_sources") or [])
        page_payload["current_source_index"] = int(browse_entry.get("current_source_index") or 0)
        page_payload["attempted_source_indices"] = list(browse_entry.get("attempted_source_indices") or [])
        page_payload["search_again_used"] = bool(browse_entry.get("search_again_used"))
        page_payload["revised_query"] = str(browse_entry.get("revised_query") or "").strip()
        page_payload["source_discovery_completed"] = bool(list(browse_entry.get("candidate_sources") or []))
        page_payload["page_open_attempted"] = bool(page_open_attempted)
        page_payload["model_decision_emitted"] = bool(model_decision_emitted)
        page_payload["search_queries_attempted"] = list(browse_entry.get("search_queries_attempted") or [])
        page_payload["raw_search_results_count"] = int(browse_entry.get("raw_search_results_count") or 0)
        page_payload["filtered_search_results_count"] = int(browse_entry.get("filtered_search_results_count") or 0)
        page_payload["filtered_out_reasons"] = list(browse_entry.get("filtered_out_reasons") or [])
        page_payload["raw_search_results"] = list(browse_entry.get("raw_search_results") or [])
        page_payload["filtered_search_results"] = list(browse_entry.get("filtered_search_results") or [])
        page_payload["filtered_out_items_with_reasons"] = list(browse_entry.get("filtered_out_items_with_reasons") or [])
        page_payload["search_provider_diagnostics"] = list(browse_entry.get("search_provider_diagnostics") or [])
        page_payload["provider_name"] = str(browse_entry.get("provider_name") or "").strip()
        page_payload["search_provider_failed"] = bool(browse_entry.get("search_provider_failed"))
        page_payload["provider_quality_gate_triggered"] = bool(browse_entry.get("provider_quality_gate_triggered"))
        page_payload["provider_quality_state"] = str(browse_entry.get("provider_quality_state") or "").strip()
        page_payload["provider_results_scored"] = list(browse_entry.get("provider_results_scored") or [])
        page_payload["provider_top_candidates"] = list(browse_entry.get("provider_top_candidates") or [])
        page_payload["provider_top_score"] = float(browse_entry.get("provider_top_score") or 0.0)
        page_payload["provider_top_score_breakdown"] = dict(browse_entry.get("provider_top_score_breakdown") or {})
        page_payload["provider_results_skipped_as_primary"] = bool(browse_entry.get("provider_results_skipped_as_primary"))
        page_payload["low_signal_fallback_to_serp_used"] = bool(browse_entry.get("low_signal_fallback_to_serp_used"))
        page_payload["quality_gate_reason"] = str(browse_entry.get("quality_gate_reason") or "").strip()
        page_payload["strict_domain_filter_failed"] = bool(browse_entry.get("strict_domain_filter_failed"))
        page_payload["relaxed_domain_retry_used"] = bool(browse_entry.get("relaxed_domain_retry_used"))
        page_payload["winning_query_variant"] = str(browse_entry.get("winning_query_variant") or "").strip()
        page_payload["chosen_source_candidates"] = list(browse_entry.get("chosen_source_candidates") or [])
        page_payload["chosen_entry_url"] = str(browse_entry.get("chosen_entry_url") or "").strip()
        page_payload["preferred_domains_applied"] = bool(browse_entry.get("preferred_domains_applied"))
        page_payload["final_page_quality_score"] = float(page_payload.get("final_page_quality_score") or 0.0)
        page_payload["final_page_quality_reason"] = str(page_payload.get("final_page_quality_reason") or "").strip()
        page_payload["stop_blocked_by_low_page_quality"] = bool(page_payload.get("stop_blocked_by_low_page_quality"))
        if serp_summary:
            page_payload["serp_detected"] = bool(serp_summary.get("serp_detected"))
            page_payload["serp_links_extracted_count"] = int(serp_summary.get("serp_links_extracted_count") or 0)
            page_payload["ranked_candidates"] = list(serp_summary.get("ranked_candidates") or [])
            page_payload["top_ranked_candidate"] = dict(serp_summary.get("top_ranked_candidate") or {})
            page_payload["top_ranked_score"] = float(serp_summary.get("top_ranked_score") or 0.0)
            page_payload["top_ranked_score_breakdown"] = dict(serp_summary.get("top_ranked_score_breakdown") or {})
            page_payload["serp_selection_reason"] = str(serp_summary.get("serp_selection_reason") or "").strip()
            page_payload["model_selected_candidate"] = dict(serp_summary.get("model_selected_candidate") or {})
            page_payload["final_selected_candidate"] = dict(serp_summary.get("final_selected_candidate") or {})
            page_payload["selection_overridden_by_ranker"] = bool(serp_summary.get("selection_overridden_by_ranker"))

    @staticmethod
    def _browse_extractable(*, page_payload: dict[str, Any], task_type: str) -> bool:
        visible_text = str(page_payload.get("visible_text") or "").strip()
        key_values = list(page_payload.get("key_values") or [])
        table_rows = list(page_payload.get("table_rows") or [])
        headings = list(page_payload.get("headings") or [])
        links = list(page_payload.get("links") or [])
        if task_type == "specs":
            return bool(key_values or table_rows or len(headings) >= 2)
        if task_type == "release":
            return bool(visible_text and re.search(r"\d{4}", visible_text))
        if task_type == "news":
            return bool(len(links) >= 3 or len(headings) >= 3 or len(visible_text) >= 200)
        if task_type == "product_lookup":
            return bool(visible_text or key_values or table_rows or headings)
        if task_type == "compare":
            lowered = visible_text.lower()
            compare_signals = ("compare", "comparison", "versus", " vs ", "区别", "差异", "对比")
            return bool(any(signal in lowered for signal in compare_signals) or len(table_rows) >= 2 or len(key_values) >= 3)
        return bool(visible_text or headings)

    @staticmethod
    def _browse_stop_signal(
        *,
        page_payload: dict[str, Any],
        task_type: str,
        entity: str,
        page_type: str,
    ) -> tuple[bool, str]:
        visible_text = str(page_payload.get("visible_text") or "").strip().lower()
        title = str(page_payload.get("title") or "").strip().lower()
        key_values = list(page_payload.get("key_values") or [])
        table_rows = list(page_payload.get("table_rows") or [])
        headings = [str(item).strip().lower() for item in list(page_payload.get("headings") or []) if str(item).strip()]
        links = list(page_payload.get("links") or [])
        url = str(page_payload.get("final_url") or page_payload.get("url") or "").strip().lower()
        home_like = (not url or url.endswith("/")) and len(links) >= 3 and len(headings) <= 3
        search_results_like = any(
            marker in url
            for marker in (
                "bing.com/search",
                "duckduckgo.com/html",
                "duckduckgo.com/?q=",
                "google.com/search",
            )
        )
        entity_match = ExecutionLadder._entity_matches_page_text(entity, f"{title} {' '.join(headings)} {visible_text[:1600]}")

        if search_results_like:
            return False, ""

        if task_type == "specs":
            if page_type == "docs" and (not entity or entity_match) and not home_like:
                return True, "specs_page_reached"
            spec_signals = ("chip", "display", "storage", "camera", "battery", "dimensions", "参数", "规格", "存储", "显示", "尺寸")
            if (
                not home_like
                and (not entity or entity_match)
                and (
                    sum(1 for signal in spec_signals if signal in visible_text) >= 3
                    or len(table_rows) >= 3
                    or len(key_values) >= 4
                )
            ):
                return True, "specs_signals_detected"
            return False, ""

        if task_type == "release":
            if page_type in {"news", "generic"} and entity and entity_match:
                if re.search(r"\d{4}", visible_text or title):
                    return True, "release_page_reached"
            if entity and entity_match and re.search(r"\d{4}", visible_text):
                return True, "release_signals_detected"
            return False, ""

        if task_type == "news":
            if page_type == "news" and (len(list(page_payload.get("links") or [])) >= 4 or len(headings) >= 4):
                return True, "news_index_ready"
            return False, ""

        if task_type == "product_lookup":
            if page_type == "product" and (not entity or entity_match):
                return True, "product_page_reached"
            if entity and entity_match:
                return True, "entity_page_reached"
            return False, ""

        if task_type == "compare":
            compare_signals = ("compare", "comparison", "versus", " vs ", "区别", "差异", "对比", "哪个好")
            if any(signal in f"{title} {' '.join(headings)} {visible_text}" for signal in compare_signals):
                return True, "compare_signals_detected"
            return False, ""

        if task_type == "general_info":
            explanation_signals = ("about", "overview", "guide", "help", "pricing", "what is", "如何", "说明", "介绍", "文档")
            if len(visible_text) >= 60 and (
                page_type in {"docs", "generic", "product"}
                or entity_match
                or len(headings) >= 2
                or any(signal in f"{title} {' '.join(headings)} {visible_text}" for signal in explanation_signals)
            ):
                return True, "general_info_signals_detected"
            return False, ""

        return False, ""

    @staticmethod
    def _assess_page_quality(
        *,
        page_payload: dict[str, Any],
        task_type: str,
        entity: str,
    ) -> tuple[float, str, bool]:
        url = str(page_payload.get("final_url") or page_payload.get("url") or "").strip().lower()
        title = str(page_payload.get("title") or "").strip().lower()
        visible_text = str(page_payload.get("visible_text") or "").strip().lower()
        page_type = str(page_payload.get("page_type") or "").strip().lower()
        domain = ExecutionLadder._domain_from_url(url)

        score = 0.0
        reasons: list[str] = []
        low_signal_domains = {"zhihu.com", "zhihu.cn", "jingyan.baidu.com", "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com"}
        if domain in low_signal_domains and task_type in {"general_info", "specs", "news", "compare"}:
            score -= 3.0
            reasons.append("low_signal_domain")
        if domain.startswith("support.") and task_type == "general_info":
            score -= 2.0
            reasons.append("support_domain_low_quality")
        if any(marker in url for marker in ("/support", "/help")) and task_type == "general_info":
            score -= 2.0
            reasons.append("support_path_low_quality")
        if any(term in title for term in ("问答", "经验", "教程", "论坛", "社区", "知乎")):
            score -= 2.0
            reasons.append("low_signal_title")
        if any(term in url for term in ("/question", "/answer", "/forum", "/community", "/jingyan", "/ask")):
            score -= 2.5
            reasons.append("low_signal_path")

        task_signals = {
            "specs": ("specs", "specifications", "technical specifications", "参数", "配置", "规格"),
            "news": ("news", "newsroom", "press", "blog", "latest", "新闻", "发布"),
            "compare": ("compare", "comparison", "versus", " vs ", "对比", "区别", "差异"),
            "general_info": ("about", "overview", "company", "what is", "介绍", "概览", "关于"),
        }
        if any(signal in f"{title} {visible_text}" for signal in task_signals.get(task_type, ())):
            score += 2.0
            reasons.append("task_signal_present")
        if page_type in {"docs", "product", "news"}:
            score += 1.0
        if entity and ExecutionLadder._entity_matches_page_text(entity, f"{title} {visible_text[:1200]}"):
            score += 1.0
            reasons.append("entity_match")

        obvious_low_quality_markers = {
            "low_signal_domain",
            "support_domain_low_quality",
            "support_path_low_quality",
            "low_signal_title",
            "low_signal_path",
        }
        low_quality = score <= -2.0 and any(marker in reasons for marker in obvious_low_quality_markers)
        return score, ",".join(reasons), low_quality

    @staticmethod
    def _entity_matches_page_text(entity: str, page_text: str) -> bool:
        cleaned_entity = str(entity or "").strip().lower()
        cleaned_page = str(page_text or "").strip().lower()
        if not cleaned_entity or not cleaned_page:
            return False
        compact_page = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", cleaned_page)
        compact_entity = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", cleaned_entity)
        compact_entity_no_digits = re.sub(r"\d+", "", compact_entity)
        for candidate in (compact_entity, compact_entity_no_digits):
            if candidate and len(candidate) >= 4 and candidate in compact_page:
                return True
        tokens = [
            token
            for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", cleaned_entity)
            if token and (len(token) >= 3 or token.isdigit())
        ]
        if tokens:
            matched = sum(1 for token in tokens if token in cleaned_page)
            if matched >= min(2, len(tokens)):
                return True
        return False

    @staticmethod
    def _assess_post_open_extraction(page_payload: dict[str, Any]) -> tuple[bool, str]:
        title = str(page_payload.get("title") or "").strip()
        visible_text = str(page_payload.get("visible_text") or "").strip()
        headings = [str(item).strip() for item in list(page_payload.get("headings") or []) if str(item).strip()]
        links = [item for item in list(page_payload.get("links") or []) if isinstance(item, dict)]
        lowered_title = title.lower()
        lowered_visible = visible_text.lower()
        if (
            "404" in lowered_title
            or "404" in lowered_visible
            or "没有找到页面" in visible_text
            or "not found" in lowered_title
            or "not found" in lowered_visible
        ):
            return False, "page_not_found"
        if visible_text or headings or links:
            return True, ""
        if any(token in lowered_title for token in ("just a moment", "please wait", "请稍候")):
            return False, "human_verification_interstitial_detected"
        if title:
            return False, "page_loaded_but_main_region_empty"
        return False, "post_open_extract_failed"

    @staticmethod
    def _visual_failure_reason(page: dict[str, Any], plan: RetrievalPlan) -> str:
        if not str(page.get("page_context_available") or "") and not str(page.get("final_url") or page.get("url") or "").strip():
            return "no_active_page_context"
        if not str(page.get("screenshot_path") or "").strip():
            return "screenshot_failed"
        if plan.visual_targets:
            return "target_region_not_detected"
        return "visual_summary_empty"

    def _result(
        self,
        *,
        success: bool,
        decision: WebAccessDecision,
        plan: RetrievalPlan,
        level: int,
        mode: str,
        search_results: list[dict[str, Any]] | None = None,
        opened_pages: list[dict[str, Any]] | None = None,
        browser_result: dict[str, Any] | None = None,
        visual_results: list[Any] | None = None,
        attempted_queries: list[str] | None = None,
        used_browser_interaction: bool = False,
        used_visual_read: bool = False,
        failure_reason: str = "",
        browser_availability: BrowserAvailabilityStatus | None = None,
    ) -> WebAccessExecutionResult:
        return WebAccessExecutionResult(
            success=success,
            execution_level=level,
            final_mode=mode,  # type: ignore[arg-type]
            decision=decision,
            retrieval_plan=plan,
            search_results=list(search_results or []),
            opened_pages=list(opened_pages or []),
            browser_result=dict(browser_result or {}),
            visual_results=list(visual_results or []),
            attempted_queries=list(attempted_queries or []),
            used_browser_interaction=used_browser_interaction,
            used_visual_read=used_visual_read,
            failure_reason=failure_reason,
            browser_availability=browser_availability,
        )

    @staticmethod
    def _emit(callback: ProgressCallback | None, phase: str, **payload: Any) -> None:
        if callback is None:
            return
        try:
            callback("structured_tool_progress", {"phase": phase, **payload})
        except Exception:
            logger.debug("web_access_progress_callback_failed", exc_info=True)

    @staticmethod
    def _status_from_payload(payload: Any) -> BrowserAvailabilityStatus | None:
        if not isinstance(payload, dict):
            return None
        level = str(payload.get("level") or payload.get("availability_level") or "").strip()
        if level not in {"full", "partial", "unavailable"}:
            return None
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        return BrowserAvailabilityStatus(
            available=bool(payload.get("available")),
            level=level,
            reason=str(payload.get("reason") or "").strip(),
            fallback_mode=str(payload.get("fallback_mode") or "").strip() or "rendered_read",
            executable_path=str(payload.get("executable_path") or "").strip(),
            browser_type=str(payload.get("browser_type") or "").strip(),
            diagnostics=dict(diagnostics),
        )
