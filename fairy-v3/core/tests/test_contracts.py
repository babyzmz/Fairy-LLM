from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fairy_core.contracts.models import (
    ErrorCode,
    EventEnvelopeModel,
    EventVisibilityModel,
    ExecutionTarget,
    PermissionProfileModel,
    TaskCreate,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode


def test_task_create_rejects_client_supplied_scope_fields() -> None:
    payload = {
        "conversation_id": str(new_id()),
        "user_request": "Add a pricing section",
        "operation_mode": OperationMode.CONTINUE_CURRENT_DRAFT,
        "execution_target": ExecutionTarget.LOCAL,
        "idempotency_key": "device-1:request-1",
        "project_root": "C:/private/project",
        "target_version_id": str(new_id()),
    }

    with pytest.raises(ValidationError):
        TaskCreate.model_validate(payload)


def test_task_create_contains_only_user_controlled_inputs() -> None:
    request = TaskCreate(
        conversation_id=new_id(),
        user_request="Add a pricing section",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        execution_target=ExecutionTarget.CLOUD,
        idempotency_key="device-1:request-2",
    )

    assert set(request.model_dump()) == {
        "conversation_id",
        "user_request",
        "operation_mode",
        "execution_target",
        "idempotency_key",
    }


def test_event_envelope_requires_cursor_scope_visibility_and_schema_version() -> None:
    event = EventEnvelopeModel(
        id=new_id(),
        cursor=42,
        run_id=new_id(),
        project_id=new_id(),
        conversation_id=new_id(),
        task_id=new_id(),
        version_id=new_id(),
        task_sequence=7,
        event_type="command.running",
        visibility=EventVisibilityModel.USER,
        message="Running review",
        payload={"stage": "review"},
        schema_version=1,
        created_at=datetime.now(UTC),
    )

    schema = EventEnvelopeModel.model_json_schema()

    assert event.cursor == 42
    assert {"cursor", "task_sequence", "visibility", "schema_version"}.issubset(
        set(schema["required"])
    )


def test_public_error_codes_are_stable() -> None:
    assert ErrorCode.PATH_OUT_OF_SCOPE == "PATH_OUT_OF_SCOPE"
    assert ErrorCode.SANDBOX_UNAVAILABLE == "SANDBOX_UNAVAILABLE"
    assert ErrorCode.VERSION_CONFLICT == "VERSION_CONFLICT"
    assert ErrorCode.SECRET_EGRESS_BLOCKED == "SECRET_EGRESS_BLOCKED"


def test_permission_profile_contract_matches_core_profiles() -> None:
    assert set(PermissionProfileModel) == {
        PermissionProfileModel.OBSERVE,
        PermissionProfileModel.STANDARD,
        PermissionProfileModel.AUTONOMOUS,
    }
