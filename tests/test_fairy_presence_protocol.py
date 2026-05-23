from app.runtime.fairy_presence import (
    fairy_meta_for_response,
    fairy_meta_for_runtime_state,
    normalize_fairy_meta,
)


def test_runtime_state_maps_to_fairy_work_state() -> None:
    meta = fairy_meta_for_runtime_state("thinking", reason="request_accepted")

    assert meta["state"] == "thinking"
    assert 0 <= meta["certainty"] <= 1
    assert 0 <= meta["urgency"] <= 1
    assert meta["policy"] == "fairy_presence_v1"


def test_model_fairy_meta_is_validated_and_clamped() -> None:
    meta = normalize_fairy_meta(
        {
            "state": "unknown_state",
            "certainty": 2,
            "urgency": -1,
            "tone": "test",
            "suggested_tools": ["weather", "", 42],
            "next_question": "more?",
        },
        fallback={"state": "focused", "certainty": 0.7, "urgency": 0.3},
        content="answer",
        source="model_validated",
    )

    assert meta["state"] == "focused"
    assert meta["certainty"] == 1
    assert meta["urgency"] == 0
    assert meta["suggested_tools"] == ["weather", "42"]
    assert meta["next_question"] == "more?"
    assert meta["source"] == "model_validated"


def test_response_errors_drive_alert_state() -> None:
    meta = fairy_meta_for_response(
        text="",
        errors=[{"code": "runtime_error", "message": "boom"}],
    )

    assert meta["state"] == "alert"
    assert meta["urgency"] > 0.8


def test_clarification_drives_uncertain_state() -> None:
    meta = fairy_meta_for_response(
        text="Need more context.",
        resolution={"clarification_needed": True, "clarification_message": "Which file?"},
    )

    assert meta["state"] == "uncertain"
    assert meta["next_question"] == "Which file?"
