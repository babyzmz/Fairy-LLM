from __future__ import annotations

from typing import Any

from .access_resolver import WebAccessResolver
from .decision_models import RetrievalPlan, WebAccessDecision
from .execution_ladder import ExecutionLadder
from .retrieval_plan_builder import RetrievalPlanBuilder
from .source_registry import resolve_source_descriptor


class WebRuntimeFacade:
    """Bundle-facing facade for the canonical web browse helpers."""

    def __init__(
        self,
        *,
        access_resolver: WebAccessResolver,
        retrieval_plan_builder: RetrievalPlanBuilder,
        execution_ladder: ExecutionLadder,
    ) -> None:
        self._resolver = access_resolver
        self._plan_builder = retrieval_plan_builder
        self._ladder = execution_ladder

    def discover_sources(
        self,
        query: str,
        task_type: str,
        preferred_domains: list[str] | None = None,
        target_urls: list[str] | None = None,
        context: dict[str, Any] | None = None,
        source_hint: str = "",
        revised_query: str = "",
        search_again_used: bool = False,
    ) -> dict[str, Any]:
        runtime_context = dict(context or {})
        followup_info = {"web_task_type": task_type}
        decision = self._resolver.resolve(
            raw_query=query,
            resolved_query=query,
            resolved_capability="generic_search",
            slots={"query": query},
            context=runtime_context,
            source_hint=source_hint,
            followup_info=followup_info,
        )
        plan = self._plan_builder.build(
            query=query,
            decision=decision,
            slots={"query": query},
            context=runtime_context,
        )
        if preferred_domains:
            cleaned_domains = [str(item).strip() for item in preferred_domains if str(item).strip()]
            plan.preferred_domains = cleaned_domains
            decision.preferred_domains = list(cleaned_domains)
        if target_urls:
            cleaned_urls = [str(item).strip() for item in target_urls if str(item).strip()]
            if cleaned_urls:
                plan.target_urls = list(cleaned_urls)
                plan.source_constraints["entry_urls"] = list(cleaned_urls)
                decision.target_url = cleaned_urls[0]
        plan.source_constraints["task_type"] = task_type
        plan.source_constraints["force_web_browse"] = True
        if revised_query:
            plan.source_constraints["revised_query"] = revised_query
            plan.source_constraints["search_again_used"] = bool(search_again_used)

        browse_entry = self._ladder._resolve_browse_entry(  # noqa: SLF001
            decision=decision,
            plan=plan,
            context=runtime_context,
            query=str(revised_query or query).strip() or query,
            task_type=task_type,
            entity=str(plan.source_constraints.get("entity") or "").strip(),
            search_again_used=bool(search_again_used),
            revised_query=str(revised_query or "").strip(),
        )
        return {
            "decision": decision,
            "plan": plan,
            "task_type": task_type,
            "entity": str(plan.source_constraints.get("entity") or "").strip(),
            "sources": list(browse_entry.get("candidate_sources") or []),
            "provider_diagnostics": list(browse_entry.get("search_provider_diagnostics") or []),
            "quality_gate": {
                "provider_quality_state": str(browse_entry.get("provider_quality_state") or "").strip(),
                "provider_quality_gate_triggered": bool(browse_entry.get("provider_quality_gate_triggered")),
                "provider_results_skipped_as_primary": bool(browse_entry.get("provider_results_skipped_as_primary")),
                "low_signal_fallback_to_serp_used": bool(browse_entry.get("low_signal_fallback_to_serp_used")),
                "provider_top_candidates": list(browse_entry.get("provider_top_candidates") or []),
                "provider_top_score": float(browse_entry.get("provider_top_score") or 0.0),
                "provider_top_score_breakdown": dict(browse_entry.get("provider_top_score_breakdown") or {}),
                "quality_gate_reason": str(browse_entry.get("quality_gate_reason") or "").strip(),
            },
            "serp_diagnostics": {
                "serp_detected": False,
                "serp_links_extracted_count": 0,
            },
            "search_queries_attempted": list(browse_entry.get("search_queries_attempted") or []),
            "chosen_entry_url": str(browse_entry.get("chosen_entry_url") or "").strip(),
            "browse_entry": browse_entry,
        }

    def open_page(
        self,
        url: str,
        *,
        task_type: str,
        entity: str = "",
        decision: WebAccessDecision | None = None,
        plan: RetrievalPlan | None = None,
        max_candidate_links: int = 8,
    ) -> dict[str, Any]:
        page_result = self._ladder._open_browse_page(str(url or "").strip())  # noqa: SLF001
        payload = dict(page_result.get("page") or {})
        initial_serp_detected = bool(payload.get("serp_detected"))
        initial_serp_links_extracted_count = int(payload.get("serp_links_extracted_count") or 0)
        serp_debug: dict[str, Any] = {
            "candidate_links": [],
            "model_selected_link": {},
            "selection_reason": "",
            "fallback_used": False,
            "selected_serp_link": "",
        }
        descriptor = resolve_source_descriptor(
            f"{str(decision.source_name or '')} {str(decision.source_domain or '')} {payload.get('url') or url}".strip(),
            explicit_source=str(decision.source_name or decision.source_domain or "") if decision is not None else "",
        )
        page_type_guess = self._ladder._browser.classify_page_type(  # noqa: SLF001
            payload,
            source_descriptor=descriptor,
            task_type=task_type,
            entity=entity,
        )
        candidate_pool = self._ladder._build_browse_candidate_pool(  # noqa: SLF001
            page_payload=payload,
            source_descriptor=descriptor,
            task_type=task_type,
            entity=entity,
            max_links=max(1, int(max_candidate_links or 8)),
        )
        if initial_serp_detected and candidate_pool:
            source_constraints = dict((plan.source_constraints if plan is not None else {}) or {})
            query = str(
                source_constraints.get("original_user_query")
                or source_constraints.get("user_query")
                or ((plan.primary_queries[:1] or [""])[0] if plan is not None else "")
                or str(url or "")
            ).strip()
            selection = self._ladder._select_best_serp_link(  # noqa: SLF001
                query=query,
                task_type=task_type,
                entity=entity,
                candidate_pool=candidate_pool,
                page_payload=payload,
                source_descriptor=descriptor,
            )
            serp_debug = {
                "candidate_links": list(selection.get("candidate_links") or []),
                "model_selected_link": dict(selection.get("model_selected_link") or {}),
                "selection_reason": str(selection.get("selection_reason") or "").strip(),
                "fallback_used": bool(selection.get("fallback_used")),
                "selected_serp_link": str((selection.get("model_selected_link") or {}).get("url") or "").strip(),
            }
            ranked_candidates = list(selection.get("ranked_candidates") or [])
            if ranked_candidates:
                top_ranked = dict(ranked_candidates[0] or {})
                score_breakdown = dict(top_ranked.get("score_breakdown") or {})
                payload["ranked_candidates"] = list(ranked_candidates[:5])
                payload["top_ranked_candidate"] = dict(top_ranked)
                payload["top_ranked_score"] = float(top_ranked.get("score_total") or 0.0)
                payload["top_ranked_score_breakdown"] = score_breakdown
            payload["candidate_links"] = list(serp_debug["candidate_links"])
            payload["model_selected_link"] = dict(serp_debug["model_selected_link"])
            payload["selected_serp_link"] = serp_debug["selected_serp_link"]
            payload["serp_selection_reason"] = serp_debug["selection_reason"]
            payload["fallback_used"] = bool(serp_debug["fallback_used"])
            followed_url = serp_debug["selected_serp_link"]
            fallback_urls = [
                str(item.get("url") or "").strip()
                for item in ranked_candidates
                if str(item.get("url") or "").strip()
            ]
            opened_real_page = False
            for candidate_url in [followed_url, *fallback_urls]:
                if not candidate_url:
                    continue
                if candidate_url == str(payload.get("url") or "").strip():
                    continue
                followed_result = self._ladder._open_browse_page(candidate_url)  # noqa: SLF001
                if not bool(followed_result.get("ok")):
                    if candidate_url != followed_url:
                        serp_debug["fallback_used"] = True
                    continue
                followed_payload = dict(followed_result.get("page") or {})
                followed_descriptor = resolve_source_descriptor(
                    f"{str(decision.source_name or '')} {str(decision.source_domain or '')} {followed_payload.get('url') or candidate_url}".strip(),
                    explicit_source=str(decision.source_name or decision.source_domain or "") if decision is not None else "",
                )
                followed_page_type = self._ladder._browser.classify_page_type(  # noqa: SLF001
                    followed_payload,
                    source_descriptor=followed_descriptor,
                    task_type=task_type,
                    entity=entity,
                )
                followed_pool = self._ladder._build_browse_candidate_pool(  # noqa: SLF001
                    page_payload=followed_payload,
                    source_descriptor=followed_descriptor,
                    task_type=task_type,
                    entity=entity,
                    max_links=max(1, int(max_candidate_links or 8)),
                )
                followed_payload["candidate_links"] = list(serp_debug["candidate_links"])
                followed_payload["model_selected_link"] = dict(serp_debug["model_selected_link"])
                followed_payload["selected_serp_link"] = candidate_url
                followed_payload["serp_selection_reason"] = serp_debug["selection_reason"]
                followed_payload["fallback_used"] = bool(serp_debug["fallback_used"] or candidate_url != followed_url)
                page_result = followed_result
                payload = followed_payload
                descriptor = followed_descriptor
                page_type_guess = followed_page_type
                candidate_pool = followed_pool
                serp_debug["selected_serp_link"] = candidate_url
                opened_real_page = True
                break
            if not opened_real_page:
                payload["selected_serp_link"] = serp_debug["selected_serp_link"]
                payload["serp_selection_reason"] = serp_debug["selection_reason"]
                payload["fallback_used"] = bool(serp_debug["fallback_used"])
        candidate_links = [
            {
                "id": index,
                "text": item.text,
                "url": item.url,
                "score": item.score,
                "score_reasons": list(item.reasons),
            }
            for index, item in enumerate(candidate_pool)
        ]
        return {
            "url": str(url or "").strip(),
            "final_url": str(payload.get("final_url") or payload.get("url") or url).strip(),
            "status": "ok" if page_result.get("ok") else "error",
            "failure_reason": str(page_result.get("error") or "").strip(),
            "snapshot": payload,
            "candidate_links": candidate_links,
            "page_type_guess": page_type_guess,
            "availability": dict(page_result.get("availability") or {}),
            "serp_detected": initial_serp_detected,
            "serp_links_extracted_count": initial_serp_links_extracted_count,
            "candidate_links_debug": list(serp_debug.get("candidate_links") or []),
            "model_selected_link": dict(serp_debug.get("model_selected_link") or {}),
            "selected_serp_link": str(serp_debug.get("selected_serp_link") or payload.get("selected_serp_link") or "").strip(),
            "selection_reason": str(serp_debug.get("selection_reason") or payload.get("serp_selection_reason") or "").strip(),
            "fallback_used": bool(serp_debug.get("fallback_used") or payload.get("fallback_used")),
            "decision": decision,
            "plan": plan,
        }
