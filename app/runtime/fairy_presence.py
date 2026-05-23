from __future__ import annotations

from typing import Any


FAIRY_WORK_STATES = {"standby", "relaxed", "thinking", "focused", "uncertain", "alert"}

RUNTIME_STATE_SIGNALS: dict[str, dict[str, Any]] = {
    "booting": {"state": "standby", "certainty": 0.45, "urgency": 0.36, "tone": "initializing"},
    "warming_up": {"state": "relaxed", "certainty": 0.58, "urgency": 0.28, "tone": "warming_up"},
    "idle": {"state": "standby", "certainty": 0.9, "urgency": 0.08, "tone": "ready"},
    "thinking": {"state": "thinking", "certainty": 0.58, "urgency": 0.58, "tone": "working"},
    "analyzing": {"state": "focused", "certainty": 0.76, "urgency": 0.52, "tone": "focused"},
    "replying": {"state": "focused", "certainty": 0.9, "urgency": 0.28, "tone": "answering"},
    "error": {"state": "alert", "certainty": 0.62, "urgency": 0.94, "tone": "attention"},
    "sleeping": {"state": "standby", "certainty": 0.84, "urgency": 0.03, "tone": "sleeping"},
}


def _as_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _clamp01(value: Any, fallback: float) -> float:
    if not isinstance(value, (int, float)):
        return fallback
    numeric = float(value)
    if numeric != numeric:
        return fallback
    return max(0.0, min(1.0, numeric))


def _as_string(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _as_string_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _as_string(item, limit=80)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def normalize_fairy_meta(
    value: Any,
    *,
    fallback: dict[str, Any] | None = None,
    content: str = "",
    source: str = "runtime_policy",
) -> dict[str, Any]:
    base = dict(fallback or RUNTIME_STATE_SIGNALS["idle"])
    raw = _as_record(value)
    state = _as_string(raw.get("state") or base.get("state"), limit=40).lower()
    if state not in FAIRY_WORK_STATES:
        state = _as_string(base.get("state") or "standby", limit=40).lower()
    if state not in FAIRY_WORK_STATES:
        state = "standby"

    tone = _as_string(raw.get("tone") or base.get("tone"), limit=60)
    next_question_value = raw.get("next_question", base.get("next_question"))
    next_question = _as_string(next_question_value, limit=240) if next_question_value else None
    suggested_tools = _as_string_list(raw.get("suggested_tools") or base.get("suggested_tools"), limit=8)
    normalized = {
        "content": _as_string(raw.get("content") or content or base.get("content"), limit=500),
        "tone": tone,
        "state": state,
        "certainty": _clamp01(raw.get("certainty"), _clamp01(base.get("certainty"), 0.72)),
        "urgency": _clamp01(raw.get("urgency"), _clamp01(base.get("urgency"), 0.24)),
        "suggested_tools": suggested_tools,
        "next_question": next_question,
        "policy": "fairy_presence_v1",
        "source": source,
    }
    return normalized


def fairy_meta_for_runtime_state(state: str, *, reason: str = "", last_error: str = "") -> dict[str, Any]:
    fallback = dict(RUNTIME_STATE_SIGNALS.get(str(state or "").strip(), RUNTIME_STATE_SIGNALS["idle"]))
    if last_error:
        fallback = dict(RUNTIME_STATE_SIGNALS["error"])
        fallback["content"] = _as_string(last_error, limit=240)
    elif reason:
        fallback["content"] = _as_string(reason, limit=240)
    return normalize_fairy_meta(fallback, fallback=fallback, source="runtime_state")


def fairy_meta_for_response(
    *,
    payload: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    text: str = "",
    cards: list[Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
    resolution: dict[str, Any] | None = None,
    selected_capability: str = "",
) -> dict[str, Any]:
    payload_record = _as_record(payload)
    meta_record = _as_record(meta)
    structured = _as_record(payload_record.get("structured"))
    resolution_record = _as_record(resolution)
    error_items = list(errors or [])
    card_items = list(cards or [])

    if error_items:
        first_error = _as_record(error_items[0])
        fallback = {
            "state": "alert",
            "certainty": 0.62,
            "urgency": 0.92,
            "tone": "attention",
            "content": _as_string(first_error.get("message") or text, limit=240),
        }
    elif bool(resolution_record.get("clarification_needed")):
        fallback = {
            "state": "uncertain",
            "certainty": 0.32,
            "urgency": 0.38,
            "tone": "clarifying",
            "content": _as_string(text or resolution_record.get("clarification_message"), limit=240),
            "next_question": _as_string(resolution_record.get("clarification_message"), limit=240) or None,
        }
    elif text.strip() or card_items:
        fallback = {
            "state": "focused",
            "certainty": 0.88,
            "urgency": 0.26,
            "tone": "answering",
            "content": _as_string(text, limit=500),
        }
    else:
        fallback = {
            "state": "uncertain",
            "certainty": 0.36,
            "urgency": 0.34,
            "tone": "empty_result",
            "content": "",
        }

    if selected_capability:
        fallback["suggested_tools"] = [selected_capability]

    for candidate in (
        meta_record.get("fairy"),
        payload_record.get("fairy"),
        payload_record.get("fairy_meta"),
        structured.get("fairy"),
    ):
        if isinstance(candidate, dict):
            return normalize_fairy_meta(candidate, fallback=fallback, content=text, source="model_validated")
    return normalize_fairy_meta(fallback, fallback=fallback, content=text, source="runtime_policy")
