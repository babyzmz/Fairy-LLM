from __future__ import annotations

from typing import Any


def build_orchestration_summary_card(*, step_results: list[dict[str, Any]], text_blocks: list[str]) -> dict[str, Any] | None:
    if len(step_results) <= 1:
        return None
    fields: list[dict[str, str]] = []
    for item in step_results:
        capability = str(item.get("capability") or "").strip()
        success = bool(item.get("success"))
        error = item.get("error")
        label = capability.replace("_", " ").title()
        value = "OK" if success else str((error or {}).get("message") or "Failed").strip()
        if label:
            fields.append({"label": label, "value": value})
    if not fields:
        return None
    summary = "; ".join(block for block in text_blocks if block)[:180]
    return {
        "type": "generic_info",
        "version": "1",
        "layout": "single",
        "data": {
            "title": "Orchestration Summary",
            "summary": summary,
            "fields": fields,
        },
        "metadata": {
            "summary_kind": "orchestration_summary",
        },
    }
