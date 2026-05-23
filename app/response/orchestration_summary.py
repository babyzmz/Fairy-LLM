from __future__ import annotations

from typing import Any


def build_orchestration_summary_card(
    *,
    step_results: list[dict[str, Any]],
    text_blocks: list[str],
    reasoning: list[dict[str, Any]] | None = None,
    graph_mode: str = "linear",
) -> dict[str, Any] | None:
    if len(step_results) <= 1:
        return None
    fields: list[dict[str, str]] = []
    timeline: list[str] = []
    success_count = 0
    inserted_total = 0
    for item in step_results:
        capability = str(item.get("capability") or "").strip()
        success = bool(item.get("success"))
        if success:
            success_count += 1
        error = item.get("error")
        label = capability.replace("_", " ").title()
        value = "OK" if success else str((error or {}).get("message") or "Failed").strip()
        confidence = item.get("execution_confidence_score")
        if confidence is not None:
            value = f"{value} ({float(confidence):.2f})"
        if label:
            fields.append({"label": label, "value": value})
        inserted_total += len(list(item.get("inserted_steps") or []))
    for decision in list(reasoning or []):
        if decision.get("why_inserted"):
            timeline.append(
                f"Inserted {decision.get('inserted_capability') or 'step'} because {decision.get('why_inserted')}"
            )
        elif decision.get("why_retry"):
            timeline.append(f"Retried {decision.get('capability') or 'step'} because {decision.get('why_retry')}")
        elif decision.get("why_skipped"):
            timeline.append(f"Skipped {decision.get('capability') or 'step'} because {decision.get('why_skipped')}")
        elif decision.get("why_rewritten"):
            timeline.append(f"Rewrote {decision.get('step') or 'step'} because {decision.get('why_rewritten')}")
    if not fields:
        return None
    summary = "; ".join(block for block in text_blocks if block)[:220]
    fields.insert(0, {"label": "Plan", "value": f"{success_count}/{len(step_results)} steps, {graph_mode}"})
    if inserted_total:
        fields.append({"label": "Inserted Steps", "value": str(inserted_total)})
    return {
        "type": "generic_info",
        "version": "1",
        "layout": "single",
        "data": {
            "title": "Orchestration Summary",
            "summary": summary,
            "fields": fields,
            "timeline": timeline[:6],
        },
        "metadata": {
            "summary_kind": "orchestration_summary",
            "graph_mode": graph_mode,
        },
    }
