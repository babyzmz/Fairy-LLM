from uuid import UUID

import pytest

from fairy_core.providers import ProviderRegistry
from fairy_core.providers.ports import ProviderProtocolError, ProviderUnavailableError
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import wait_for_turn
from tests.assistant.test_application import _scratch_task
from tests.assistant.test_model_routing import (
    DEEPSEEK_MODEL_ID,
    PricedCatalogSource,
    _auto_turn,
    _provider,
)


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("error_type", [ProviderProtocolError, ProviderUnavailableError])
def test_preparation_failure_publishes_one_scoped_terminal_event(
    tmp_path,
    monkeypatch,
    mode,
    error_type,
):
    provider = _provider(profile_id="coordinator", model_id=DEEPSEEK_MODEL_ID, rounds=[])

    def unavailable(request, cancellation):
        raise error_type("Private provider diagnostic must not be published")

    monkeypatch.setattr(provider, "stream", unavailable)
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        model_catalog_source=PricedCatalogSource(),
        assistant_workflow_engine_version=4,
    )
    try:
        service.invoke("models.catalog.refresh", {})
        service.invoke(
            "models.selection.update",
            {
                "mode": mode,
                "model_id": DEEPSEEK_MODEL_ID if mode == "manual" else None,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "selection",
            },
        )
        task = _scratch_task(service, "Please only say hello, without tools")
        turn = _auto_turn(service, task, "preparation-failure")
        other_task = _scratch_task(service, "Leave this other chat unchanged")
        other = _auto_turn(service, other_task, "other-chat")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        wait_for_turn(service, turn["id"], status="failed")
        service._assistant_application._fail_turn(
            UUID(turn["id"]),
            None,
            error_code="PROVIDER_ERROR",
        )
        with service._unit_of_work_factory() as unit:
            events = [
                event
                for event in unit.commands.events_after(cursor=0)
                if event.event_type == "assistant.turn.failed"
            ]
            assert len(events) == 1
            assert events[0].payload["turn_id"] == turn["id"]
            assert events[0].task_id == UUID(task["id"])
            assert events[0].conversation_id == UUID(turn["conversation_id"])
            assert events[0].visibility == "user"
            assert "Private provider diagnostic" not in str(events[0].payload)
            assert unit.assistant.get_turn(UUID(other["id"])).status == other["status"]
    finally:
        service.close()
