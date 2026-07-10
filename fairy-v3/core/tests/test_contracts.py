from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fairy_core.contracts.models import (
    ErrorCode,
    EventEnvelopeModel,
    EventVisibilityModel,
    ExecutionTarget,
    MemoryProjectionHealthModel,
    MemorySearchDocumentModel,
    MemorySnapshotItemModel,
    MemorySnapshotModel,
    PermissionProfileModel,
    ScopeContractModel,
    TaskCreate,
    TaskModel,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)


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
    assert ErrorCode.IDEMPOTENCY_CONFLICT == "IDEMPOTENCY_CONFLICT"
    assert ErrorCode.SECRET_EGRESS_BLOCKED == "SECRET_EGRESS_BLOCKED"


def test_permission_profile_contract_matches_core_profiles() -> None:
    assert set(PermissionProfileModel) == {
        PermissionProfileModel.OBSERVE,
        PermissionProfileModel.STANDARD,
        PermissionProfileModel.AUTONOMOUS,
    }


def test_memory_snapshot_contract_rejects_tampered_hashes_and_non_finite_scores() -> None:
    source_id = new_id()
    item_payload = {
        "ordinal": 0,
        "source_kind": MemorySourceKind.CLAIM_REVISION,
        "source_id": source_id,
        "source_revision": 1,
        "namespace": MemoryNamespace.PROJECT_CANONICAL,
        "selection_reason": MemorySelectionReason.EXACT_CANONICAL,
        "authority": MemoryAuthority.ACCEPTED_VERSION,
        "score_components": {"authority": 1.0, "exact": 1.0},
        "rendered_text": "React is required.",
        "rendered_text_hash": hashlib.sha256(b"React is required.").hexdigest(),
        "token_count": 18,
    }
    item = MemorySnapshotItemModel.model_validate(item_payload)
    snapshot = MemorySnapshotModel(
        id=new_id(),
        project_id=new_id(),
        conversation_id=new_id(),
        task_id=new_id(),
        base_version_id=new_id(),
        target_version_id=new_id(),
        snapshot_version=1,
        policy_version="hermes-lexical-v1",
        source_watermark_cursor=12,
        projection_generation=1,
        projection_watermark_cursor=12,
        projection_state=ProjectionState.READY,
        status=MemorySnapshotStatus.READY,
        degraded_reason=None,
        content_hash="b" * 64,
        token_count=18,
        items=(item,),
        created_at=datetime.now(UTC),
    )

    assert snapshot.items[0].source_id == source_id
    with pytest.raises(ValidationError):
        MemorySnapshotItemModel.model_validate(
            {**item_payload, "score_components": {"lexical": float("nan")}}
        )
    with pytest.raises(ValidationError):
        MemorySnapshotModel.model_validate({**snapshot.model_dump(), "content_hash": "B" * 64})


def test_projection_health_contract_requires_bounded_watermarks() -> None:
    health = MemoryProjectionHealthModel(
        generation=1,
        state=ProjectionState.STALE,
        source_watermark_cursor=9,
        projected_watermark_cursor=7,
        lag=2,
        last_error_code="MEMORY_PROJECTION_STALE",
        updated_at=datetime.now(UTC),
    )

    assert health.source_watermark_cursor == 9
    with pytest.raises(ValidationError):
        MemoryProjectionHealthModel.model_validate(
            {**health.model_dump(), "projected_watermark_cursor": -1}
        )


def test_memory_contracts_reject_inconsistent_bindings_and_content_hashes(tmp_path) -> None:
    task = TaskModel(
        id=new_id(),
        project_id=None,
        conversation_id=new_id(),
        user_request="Answer",
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        execution_target=ExecutionTarget.LOCAL,
        target_version_id=None,
        memory_snapshot_id=None,
        memory_snapshot_hash=None,
        status="created",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        TaskModel.model_validate({**task.model_dump(), "memory_snapshot_id": new_id()})

    scope_payload = {
        "workspace_type": "chat_scratch",
        "project_id": None,
        "conversation_id": task.conversation_id,
        "task_id": task.id,
        "operation_mode": OperationMode.ANSWER,
        "base_version_id": None,
        "target_version_id": None,
        "project_root": tmp_path,
        "allowed_write_paths": (tmp_path,),
        "forbidden_write_paths": (),
        "execution_target": ExecutionTarget.LOCAL,
        "network_policy": "off",
        "memory_read_scope": ("current_conversation",),
        "memory_write_scope": ("current_conversation_draft",),
        "memory_snapshot_id": None,
        "memory_snapshot_hash": "c" * 64,
        "scope_digest": "d" * 64,
    }
    with pytest.raises(ValidationError):
        ScopeContractModel.model_validate(scope_payload)

    document_payload = {
        "id": new_id(),
        "source_kind": MemorySourceKind.OBSERVATION,
        "source_id": new_id(),
        "source_revision": None,
        "namespace": MemoryNamespace.CONVERSATION_DRAFT,
        "project_id": None,
        "conversation_id": task.conversation_id,
        "task_id": task.id,
        "version_id": None,
        "language": "und",
        "normalized_text": "trusted source",
        "content_hash": "e" * 64,
        "source_cursor": 1,
        "projection_generation": 1,
        "updated_at": datetime.now(UTC),
    }
    with pytest.raises(ValidationError):
        MemorySearchDocumentModel.model_validate(document_payload)

    item_payload = {
        "ordinal": 0,
        "source_kind": MemorySourceKind.OBSERVATION,
        "source_id": new_id(),
        "source_revision": None,
        "namespace": MemoryNamespace.CONVERSATION_DRAFT,
        "selection_reason": MemorySelectionReason.LEXICAL_HISTORY,
        "authority": MemoryAuthority.EXPLICIT_USER,
        "score_components": {"lexical": 0.5},
        "rendered_text": "quoted history",
        "rendered_text_hash": "f" * 64,
        "token_count": 14,
    }
    with pytest.raises(ValidationError):
        MemorySnapshotItemModel.model_validate(item_payload)
