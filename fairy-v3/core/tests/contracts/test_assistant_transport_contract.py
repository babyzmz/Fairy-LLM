from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.contracts.models import AssistantTurnRunInput, AssistantTurnStartInput
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_dispatcher
from tests.assistant.support import ScriptedProvider


def test_run_input_rejects_all_client_supplied_scope_fields() -> None:
    payload = {"turn_id": "00000000-0000-0000-0000-000000000001"}
    assert set(AssistantTurnRunInput.model_validate(payload).model_dump()) == {"turn_id"}
    for forbidden in (
        "task_id",
        "project_id",
        "conversation_id",
        "version_id",
        "scope_digest",
        "memory_snapshot_id",
        "project_root",
        "network_policy",
    ):
        with pytest.raises(ValidationError):
            AssistantTurnRunInput.model_validate({**payload, forbidden: "forged"})


def test_start_input_rejects_all_client_supplied_scope_fields() -> None:
    payload = {"turn_id": "00000000-0000-0000-0000-000000000001"}
    assert set(AssistantTurnStartInput.model_validate(payload).model_dump()) == {"turn_id"}
    with pytest.raises(ValidationError):
        AssistantTurnStartInput.model_validate({**payload, "scope_digest": "forged"})


def test_jsonrpc_exposes_start_run_and_retry_over_the_shared_catalog(tmp_path: Path) -> None:
    assert "assistant.turns.start" in CORE_METHODS
    assert "assistant.turns.run" in CORE_METHODS
    assert "assistant.turns.retry" in CORE_METHODS
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="done"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="retried"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    dispatcher = build_local_dispatcher(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        conversation = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "conversations.create",
                "params": {"project_id": None, "workspace_type": "chat_scratch"},
            }
        )["result"]
        task = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks.create",
                "params": {
                    "conversation_id": conversation["id"],
                    "user_request": "Hello",
                    "operation_mode": "answer",
                    "execution_target": "local",
                    "idempotency_key": "task:transport",
                },
            }
        )["result"]["task"]
        turn = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "assistant.turns.create",
                "params": {
                    "task_id": task["id"],
                    "profile_id": "scripted",
                    "idempotency_key": "turn:transport",
                },
            }
        )["result"]
        completed = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "assistant.turns.run",
                "params": {"turn_id": turn["id"]},
            }
        )["result"]
        retried = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "assistant.turns.retry",
                "params": {
                    "turn_id": turn["id"],
                    "idempotency_key": "turn:transport:retry",
                },
            }
        )["result"]
        retried_completed = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "assistant.turns.run",
                "params": {"turn_id": retried["id"]},
            }
        )["result"]
        messages = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "messages.list",
                "params": {"conversation_id": conversation["id"]},
            }
        )["result"]["items"]

        assert completed["status"] == "completed"
        assert retried["status"] == "created"
        assert retried["task_id"] == turn["task_id"]
        assert retried["id"] != turn["id"]
        assert retried_completed["status"] == "completed"
        assert [message["role"] for message in messages] == [
            "user",
            "assistant",
            "assistant",
        ]
        assert provider.requests[-1].messages[-1].content == "Hello"
    finally:
        dispatcher.close()
