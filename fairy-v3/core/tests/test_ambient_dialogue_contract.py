from __future__ import annotations

from datetime import UTC, datetime

from fairy_core.application.ambient_dialogue_service import AmbientDialogueService
from fairy_core.contracts.methods import CORE_METHODS, CoreMethodTransport
from fairy_core.contracts.persona import AmbientDialogueEvaluateInput
from fairy_core.persona import (
    FairyDialogueDirector,
    load_default_dialogue_catalog,
    load_default_persona_authority,
)


def test_ambient_dialogue_method_is_local_only_and_returns_safe_projection() -> None:
    definition = CORE_METHODS["ambient.dialogue.evaluate"]
    assert definition.transport is CoreMethodTransport.LOCAL_ONLY

    request = AmbientDialogueEvaluateInput.model_validate(
        {
            "context": {
                "observed_at": datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
                "locale": "zh-CN",
                "surface": "pet",
                "user_idle_seconds": 0,
                "startup_eligible": True,
            },
            "preferences": {},
            "state": {},
        }
    )
    service = AmbientDialogueService(
        FairyDialogueDirector(
            catalog=load_default_dialogue_catalog(),
            persona=load_default_persona_authority(),
        )
    )

    result = service.evaluate(request)

    assert result.projection is not None
    assert result.projection.dialogue_id == "startup.daily.01"
    assert result.generation_request is None
    assert result.next_state.daily_text_count == 1
    assert not hasattr(result, "context")


def test_suppressed_context_is_not_echoed_or_persisted_in_result() -> None:
    request = AmbientDialogueEvaluateInput.model_validate(
        {
            "context": {
                "observed_at": datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
                "locale": "en",
                "surface": "pet",
                "user_idle_seconds": 1800,
                "typing": True,
            },
            "preferences": {},
            "state": {},
        }
    )
    service = AmbientDialogueService(
        FairyDialogueDirector(
            catalog=load_default_dialogue_catalog(),
            persona=load_default_persona_authority(),
        )
    )

    result = service.evaluate(request)

    assert result.projection is None
    assert result.reason == "suppressed:typing"
    assert set(result.model_dump()) == {
        "projection",
        "generation_request",
        "next_state",
        "reason",
    }
