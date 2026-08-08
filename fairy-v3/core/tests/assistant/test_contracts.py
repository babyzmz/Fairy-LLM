from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from fairy_core.contracts.models import AssistantTurnCreateInput, MessageModel
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.providers import CancellationToken
from fairy_core.transports.stdio import build_local_service


def _scratch_task(service, *, request: str = "Explain Fairy") -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": request,
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": f"task:{conversation['id']}",
        },
    )["task"]


def test_turn_create_input_rejects_every_client_scope_field() -> None:
    payload = {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "profile_id": "local-default",
        "idempotency_key": "turn:1",
    }
    assert set(AssistantTurnCreateInput.model_validate(payload).model_dump()) == {
        "task_id",
        "profile_id",
        "model_selection",
        "idempotency_key",
        "image_attachments",
    }

    for forbidden in (
        "project_id",
        "conversation_id",
        "version_id",
        "scope_digest",
        "memory_snapshot_id",
        "memory_snapshot_hash",
        "project_root",
        "network_policy",
    ):
        with pytest.raises(ValidationError):
            AssistantTurnCreateInput.model_validate({**payload, forbidden: "forged"})


def test_core_service_creates_idempotent_task_bound_turn_and_user_message(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path)
    try:
        task = _scratch_task(service, request="Explain Fairy")
        request = {
            "task_id": task["id"],
            "profile_id": "local-default",
            "idempotency_key": "turn:one",
        }

        created = service.invoke("assistant.turns.create", request)
        replayed = service.invoke("assistant.turns.create", request)
        fetched = service.invoke("assistant.turns.get", {"turn_id": created["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )

        assert replayed == created
        assert fetched == created
        assert created["task_id"] == task["id"]
        assert created["conversation_id"] == task["conversation_id"]
        assert created["scope_digest"]
        assert created["memory_snapshot_id"] == task["memory_snapshot_id"]
        assert created["memory_snapshot_hash"] == task["memory_snapshot_hash"]
        assert created["status"] == "created"
        assert created["execution_engine_version"] == 2
        assert created["workflow_run_id"] is not None
        assert created["workflow_summary"]["status"] == "paused"
        assert created["workflow_summary"]["current_phase"] == "assistant.turn.prepare"
        assert created["workflow_summary"]["total_nodes"] == 2
        assert [(message["role"], message["content"]) for message in messages["items"]] == [
            ("user", "Explain Fairy")
        ]
    finally:
        service.close()


def test_turn_idempotency_rejects_different_task_or_profile(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        first = _scratch_task(service, request="First")
        second = _scratch_task(service, request="Second")
        service.invoke(
            "assistant.turns.create",
            {
                "task_id": first["id"],
                "profile_id": "local-default",
                "idempotency_key": "turn:shared",
            },
        )

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "assistant.turns.create",
                {
                    "task_id": second["id"],
                    "profile_id": "local-default",
                    "idempotency_key": "turn:shared",
                },
            )
        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "assistant.turns.create",
                {
                    "task_id": first["id"],
                    "profile_id": "cloud-fallback",
                    "idempotency_key": "turn:shared",
                },
            )
    finally:
        service.close()


def test_turn_cancel_is_revision_fenced_and_terminal(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        task = _scratch_task(service)
        created = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "local-default",
                "idempotency_key": "turn:cancel",
            },
        )

        cancelled = service.invoke(
            "assistant.turns.cancel",
            {"turn_id": created["id"], "expected_cancellation_revision": 0},
        )

        assert cancelled["status"] == "cancelled"
        assert cancelled["cancellation_revision"] == 1
        with pytest.raises(InvalidTransitionError):
            service.invoke(
                "assistant.turns.cancel",
                {"turn_id": created["id"], "expected_cancellation_revision": 0},
            )
    finally:
        service.close()


def test_active_turn_cancel_tolerates_concurrent_terminal_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_local_service(tmp_path)
    try:
        task = _scratch_task(service)
        created = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "local-default",
                "idempotency_key": "turn:cancel-race",
            },
        )
        workflow_run_id = UUID(str(created["workflow_run_id"]))
        cancellation = CancellationToken()
        scheduler = service._workflow_scheduler  # type: ignore[attr-defined]
        active_key = UUID(int=0)
        scheduler._active[active_key] = SimpleNamespace(  # type: ignore[attr-defined]
            claim=SimpleNamespace(run_id=workflow_run_id),
            cancellation=cancellation,
        )
        original_cancel = service._assistant_ledger.cancel_turn  # type: ignore[attr-defined]

        def concurrent_cancel(**kwargs):
            original_cancel(**kwargs)
            return original_cancel(**kwargs)

        monkeypatch.setattr(
            service._assistant_ledger,  # type: ignore[attr-defined]
            "cancel_turn",
            concurrent_cancel,
        )

        cancelled = service.invoke(
            "assistant.turns.cancel",
            {"turn_id": created["id"], "expected_cancellation_revision": 0},
        )
        scheduler._active.pop(active_key)  # type: ignore[attr-defined]

        assert cancellation.is_cancelled is True
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancellation_revision"] == 1
    finally:
        service.close()


def test_cancel_after_failure_is_an_idempotent_terminal_noop(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        task = _scratch_task(service)
        created = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "local-default",
                "idempotency_key": "turn:failed-before-cancel",
            },
        )
        turn_id = UUID(str(created["id"]))
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            turn = unit_of_work.assistant.get_turn(turn_id)
            assert turn is not None
            expected_status = turn.status
            turn.fail(error_code="PROVIDER_PROTOCOL_ERROR")
            unit_of_work.assistant.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=turn.cancellation_revision,
            )
            unit_of_work.commit()

        result = service.invoke(
            "assistant.turns.cancel",
            {"turn_id": created["id"], "expected_cancellation_revision": 0},
        )

        assert result["status"] == "failed"
        assert result["error_code"] == "PROVIDER_PROTOCOL_ERROR"
        assert result["cancellation_revision"] == 0
    finally:
        service.close()


def test_public_message_contract_rejects_internal_visibility() -> None:
    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "conversation_id": "00000000-0000-0000-0000-000000000002",
        "task_id": "00000000-0000-0000-0000-000000000003",
        "turn_id": None,
        "sequence": 1,
        "role": "system_notice",
        "visibility": "internal",
        "content": "hidden provider context",
        "created_at": "2026-07-11T00:00:00Z",
    }

    with pytest.raises(ValidationError):
        MessageModel.model_validate(payload)
