from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TOOL_EVENT_LABELS = {
    "get_active_app": "detect_active_app",
    "capture_screen": "capture_screen",
    "capture_active_window": "capture_active_window",
    "search_web": "browser_search",
    "open_url": "browser_open",
    "extract_page_text": "browser_extract",
    "snapshot_page": "screenshot_captured",
    "browser_interact": "browser_interact",
    "compare_structured_results": "browser_compare",
    "list_dir": "inspect_files",
    "search_files": "inspect_files",
    "read_text_file": "read_file",
    "write_text_file": "write_file",
    "propose_edit": "propose_patch",
    "apply_patch": "patch_applied",
    "run_command": "run_command",
}


@dataclass(slots=True)
class ActionEvent:
    name: str
    title: str
    detail: str = ""
    status: str = "info"
    payload: dict[str, Any] = field(default_factory=dict)


def build_action_event(name: str, payload: dict[str, Any] | None = None) -> ActionEvent:
    payload = dict(payload or {})

    if name == "user_request_received":
        return ActionEvent(name=name, title="task_received", detail=str(payload.get("text", "")).strip(), status="info", payload=payload)

    if name == "skill_routed":
        return ActionEvent(
            name=name,
            title="skill_routed",
            detail=f"{payload.get('chosen_skill', '')} · {payload.get('reason', '')}".strip(" ·"),
            status="info",
            payload=payload,
        )

    if name == "routing_decision":
        detail = (
            f"{payload.get('primary_intent', '')} · selected={payload.get('selected_tool', '')} · "
            f"tool_needed={payload.get('tool_needed', False)}"
        ).strip(" ·")
        return ActionEvent(name=name, title="routing_decision", detail=detail, status="info", payload=payload)

    if name == "route_selected":
        detail = f"{payload.get('route', '')} · {payload.get('reason', '')}".strip(" ·")
        return ActionEvent(name=name, title="route_selected", detail=detail, status="info", payload=payload)

    if name == "route_fallback_used":
        detail = " · ".join(
            part
            for part in (
                str(payload.get("from_route", "")).strip(),
                str(payload.get("to_route", "")).strip(),
                str(payload.get("reason", "")).strip(),
            )
            if part
        )
        return ActionEvent(name=name, title="route_fallback_used", detail=detail, status="failed", payload=payload)

    if name == "provider_request_start":
        route = str(payload.get("route", "")).strip()
        provider = str(payload.get("provider_id", "")).strip()
        model = str(payload.get("model", "")).strip()
        title = "send_image_to_provider" if "vision" in route else "send_text_to_provider"
        detail = " · ".join(part for part in (provider, model, route) if part)
        return ActionEvent(name=name, title=title, detail=detail, status="running", payload=payload)

    if name == "provider_response_received":
        route = str(payload.get("route", "")).strip()
        provider = str(payload.get("provider_id", "")).strip()
        model = str(payload.get("model", "")).strip()
        detail = " · ".join(part for part in (provider, model, route) if part)
        return ActionEvent(name=name, title="cloud_response_received", detail=detail, status="done", payload=payload)

    if name == "provider_fallback_used":
        reason = str(payload.get("reason", "")).strip()
        detail = " · ".join(
            part
            for part in (
                str(payload.get("provider_id", "")).strip(),
                str(payload.get("model", "")).strip(),
                str(payload.get("route", "")).strip(),
                reason,
            )
            if part
        )
        title = "fallback_to_local"
        if "timeout" in reason.lower():
            title = "provider_timeout"
        return ActionEvent(name=name, title=title, detail=detail, status="failed", payload=payload)

    if name == "memory_used":
        detail = (
            f"profile={payload.get('profile', 0)} · project={payload.get('project', 0)} · "
            f"task={payload.get('task', 0)} · semantic={payload.get('semantic', 0)} · "
            f"category={payload.get('task_category', '')}"
        )
        return ActionEvent(name=name, title="memory_used", detail=detail, status="info", payload=payload)

    if name == "memory_write_complete":
        persisted = payload.get('persisted', {}) if isinstance(payload.get('persisted'), dict) else {}
        detail = (
            f"profile={persisted.get('profile', 0)} · project={persisted.get('project', 0)} · "
            f"semantic={persisted.get('semantic', 0)}"
        )
        return ActionEvent(name=name, title="memory_write_complete", detail=detail, status="done", payload=payload)

    if name in {"rag_retrieval_started", "rag_retrieval_finished", "rag_context_injected", "rag_retrieval_visualized"}:
        status_map = {
            "rag_retrieval_started": "running",
            "rag_retrieval_finished": "done",
            "rag_context_injected": "done",
            "rag_retrieval_visualized": "info",
        }
        detail = ""
        if name == "rag_retrieval_started":
            detail = f"top_k={payload.get('top_k', '')}"
        elif name == "rag_retrieval_finished":
            detail = (
                f"hits={payload.get('result_count', 0)} · top_k={payload.get('top_k', '')} · "
                f"kinds={','.join(payload.get('source_kinds') or [])}"
            )
        elif name == "rag_context_injected":
            detail = (
                f"hits={payload.get('result_count', 0)} · chars={payload.get('context_chars', 0)} · "
                f"kinds={','.join(payload.get('source_kinds') or [])}"
            )
        elif name == "rag_retrieval_visualized":
            detail = (
                f"hits={payload.get('result_count', 0)} · provider={payload.get('embedding_provider', '')} · "
                f"backend={payload.get('vector_backend', '')}"
            )
        return ActionEvent(name=name, title=name, detail=detail, status=status_map[name], payload=payload)

    if name in {"embedding_collection_created", "embedding_provider_switched", "chroma_initialized", "chroma_fallback_sqlite"}:
        detail = " · ".join(
            part
            for part in (
                str(payload.get("collection", "")).strip(),
                str(payload.get("fingerprint", "")).strip(),
                str(payload.get("backend", "")).strip(),
                str(payload.get("reason", "")).strip(),
            )
            if part
        )
        status = "done" if name != "chroma_fallback_sqlite" else "failed"
        return ActionEvent(name=name, title=name, detail=detail, status=status, payload=payload)

    if name == "embedding_provider_fallback":
        detail = f"{payload.get('provider', '')} · {payload.get('reason', '')}".strip(" ·")
        return ActionEvent(name=name, title="embedding_provider_fallback", detail=detail, status="failed", payload=payload)

    if name == "knowledge_candidate_scored":
        detail = (
            f"score={payload.get('score', 0)} · "
            f"reasons={','.join(payload.get('reasons') or [])} · "
            f"{str(payload.get('title', ''))[:80]}"
        )
        return ActionEvent(name=name, title="knowledge_candidate_scored", detail=detail, status="info", payload=payload)

    if name == "knowledge_persist_skipped":
        detail = f"{payload.get('reason', '')} · score={payload.get('score', '')}".strip(" ·")
        return ActionEvent(name=name, title="knowledge_persist_skipped", detail=detail, status="info", payload=payload)

    if name == "knowledge_dedup_hit":
        detail = (
            f"{payload.get('memory_type', '')} · "
            f"{payload.get('existing_item_id', '')} · "
            f"{payload.get('reason', '')}"
        ).strip(" ·")
        return ActionEvent(name=name, title="knowledge_dedup_hit", detail=detail, status="done", payload=payload)

    if name == "session_rollup_created":
        detail = (
            f"{payload.get('session_id', '')} · "
            f"{payload.get('reason', '')} · "
            f"{payload.get('item_id', '')}"
        ).strip(" ·")
        return ActionEvent(name=name, title="session_rollup_created", detail=detail, status="done", payload=payload)

    if name in {"decision_candidate_created", "decision_confirmed", "decision_rejected"}:
        detail = " · ".join(
            part
            for part in (
                str(payload.get("item_id", "")).strip(),
                str(payload.get("title", "")).strip(),
                str(payload.get("reason", "")).strip(),
            )
            if part
        )
        status = "done" if name != "decision_rejected" else "failed"
        return ActionEvent(name=name, title=name, detail=detail, status=status, payload=payload)

    if name in {
        "reindex_job_created",
        "reindex_job_started",
        "reindex_job_cancel_requested",
        "reindex_job_cancelled",
        "reindex_chunk_processed",
        "reindex_chunk_failed",
        "reindex_job_completed",
        "reindex_job_failed",
        "reindex_job_promoted",
        "active_collection_switched",
        "injected_context_preview_generated",
        "pending_decision_surface_rendered",
    }:
        detail = " · ".join(
            part
            for part in (
                str(payload.get("job_id", "")).strip(),
                str(payload.get("fingerprint", "")).strip() or str(payload.get("target_fingerprint", "")).strip(),
                str(payload.get("provider", "")).strip() or str(payload.get("target_provider", "")).strip(),
                str(payload.get("collection_name", "")).strip(),
                str(payload.get("progress", "")).strip(),
                str(payload.get("reason", "")).strip(),
            )
            if part
        )
        status_map = {
            "reindex_job_created": "info",
            "reindex_job_started": "running",
            "reindex_job_cancel_requested": "info",
            "reindex_job_cancelled": "failed",
            "reindex_chunk_processed": "running",
            "reindex_chunk_failed": "failed",
            "reindex_job_completed": "done",
            "reindex_job_failed": "failed",
            "reindex_job_promoted": "done",
            "active_collection_switched": "done",
            "injected_context_preview_generated": "done",
            "pending_decision_surface_rendered": "info",
        }
        return ActionEvent(name=name, title=name, detail=detail, status=status_map[name], payload=payload)

    if name == "legacy_preview_scan_completed":
        detail = (
            f"total={payload.get('total_count', 0)} · "
            f"decision_like={payload.get('decision_like_count', 0)} · "
            f"noise={payload.get('noise_estimate', 0)}"
        )
        return ActionEvent(name=name, title=name, detail=detail, status="done", payload=payload)

    if name in {"news_fetch_start", "news_fetch_done", "news_content_fetch_start", "news_content_fetch_done", "news_briefing_ready"}:
        title_map = {
            "news_fetch_start": "news_fetch_start",
            "news_fetch_done": "news_fetch_done",
            "news_content_fetch_start": "news_content_fetch_start",
            "news_content_fetch_done": "news_content_fetch_done",
            "news_briefing_ready": "news_briefing_ready",
        }
        detail = str(
            payload.get("title")
            or payload.get("provider")
            or payload.get("url")
            or payload.get("count")
            or payload.get("total_count")
            or ""
        ).strip()
        status = "running" if name.endswith("_start") else "done"
        return ActionEvent(name=name, title=title_map[name], detail=detail, status=status, payload=payload)

    if name in {"tool_call_start", "tool_call_done", "tool_call_failed"}:
        tool_name = str(payload.get("tool_name", "")).strip()
        title = TOOL_EVENT_LABELS.get(tool_name, tool_name or "tool")
        status = {"tool_call_start": "running", "tool_call_done": "done", "tool_call_failed": "failed"}[name]
        detail = _tool_detail(tool_name, payload)
        return ActionEvent(name=name, title=title, detail=detail, status=status, payload=payload)

    if name in {"command_stdout", "command_stderr"}:
        return ActionEvent(
            name=name,
            title=name,
            detail=str(payload.get("line", "")).rstrip(),
            status="stream",
            payload=payload,
        )

    if name in {"validation_started", "validation_passed", "validation_failed"}:
        status = "running" if name == "validation_started" else "done" if name == "validation_passed" else "failed"
        return ActionEvent(
            name=name,
            title=name,
            detail=str(payload.get("command", "")).strip(),
            status=status,
            payload=payload,
        )

    if name == "skill_result_ready":
        return ActionEvent(
            name=name,
            title="skill_result_ready",
            detail=f"{payload.get('skill_name', '')} · success={payload.get('success', False)}",
            status="done",
            payload=payload,
        )

    if name == "final_response_ready":
        return ActionEvent(name=name, title="final_response_ready", detail="", status="done", payload=payload)

    # --- Lazy Skill Runtime events ---
    if name == "lazy_pipeline_entered":
        return ActionEvent(name=name, title="lazy_pipeline_entered", detail=f"pipeline={payload.get('pipeline', 'new')}", status="running", payload=payload)

    if name == "lazy_routing_started":
        return ActionEvent(name=name, title="lazy_routing_started", detail=str(payload.get("user_request", ""))[:80], status="running", payload=payload)

    if name == "lazy_routing_completed":
        detail = (
            f"skill={payload.get('skill', '')} · "
            f"confidence={payload.get('confidence', 0):.2f} · "
            f"reason={payload.get('reason', '')} · "
            f"pipeline={payload.get('pipeline', 'new')}"
        )
        return ActionEvent(name=name, title="lazy_routing_completed", detail=detail, status="done", payload=payload)

    if name == "lazy_routing_failed":
        return ActionEvent(name=name, title="lazy_routing_failed", detail=str(payload.get("error", "")), status="failed", payload=payload)

    if name == "lazy_tool_exposure_activated":
        detail = (
            f"skill={payload.get('skill', '')} · "
            f"exposed={','.join(payload.get('exposed', []))} · "
            f"missing={','.join(payload.get('missing', []))}"
        )
        return ActionEvent(name=name, title="tool_exposure_activated", detail=detail, status="info", payload=payload)

    if name == "lazy_context_built":
        breakdown = payload.get("token_breakdown", {})
        detail = (
            f"skill={payload.get('skill', '')} · "
            f"total_tokens={breakdown.get('total', 0)} · "
            f"tools={','.join(payload.get('tools', []))}"
        )
        return ActionEvent(name=name, title="lazy_context_built", detail=detail, status="info", payload=payload)

    if name == "lazy_tool_calls_blocked":
        detail = f"skill={payload.get('skill', '')} · blocked={','.join(payload.get('blocked', []))}"
        return ActionEvent(name=name, title="tool_call_blocked", detail=detail, status="failed", payload=payload)

    if name == "lazy_pipeline_fallback":
        detail = f"reason={payload.get('reason', '')} · pipeline={payload.get('pipeline', 'legacy')}"
        return ActionEvent(name=name, title="lazy_pipeline_fallback", detail=detail, status="failed", payload=payload)

    return ActionEvent(name=name, title=name, detail="", status="info", payload=payload)


def _tool_detail(tool_name: str, payload: dict[str, Any]) -> str:
    if tool_name in {"read_text_file", "write_text_file", "apply_patch"}:
        result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
        kwargs = payload.get("kwargs", {}) if isinstance(payload.get("kwargs"), dict) else {}
        path = str(result.get("path") or result.get("revised_path") or kwargs.get("path") or "").strip()
        return path
    if tool_name in {"search_files", "list_dir"}:
        kwargs = payload.get("kwargs", {}) if isinstance(payload.get("kwargs"), dict) else {}
        return str(kwargs.get("path", "")).strip()
    if tool_name == "run_command":
        kwargs = payload.get("kwargs", {}) if isinstance(payload.get("kwargs"), dict) else {}
        return str(kwargs.get("command", "")).strip()
    if tool_name in {"open_url", "extract_page_text", "snapshot_page", "search_web"}:
        kwargs = payload.get("kwargs", {}) if isinstance(payload.get("kwargs"), dict) else {}
        result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
        return str(kwargs.get("url") or kwargs.get("query") or result.get("url") or result.get("final_url") or "").strip()
    if tool_name == "browser_interact":
        kwargs = payload.get("kwargs", {}) if isinstance(payload.get("kwargs"), dict) else {}
        return str(kwargs.get("action") or kwargs.get("selector") or "").strip()
    return ""
