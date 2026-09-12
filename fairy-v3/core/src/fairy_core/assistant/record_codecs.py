from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fairy_core.assistant.evidence import (
    evidence_receipt_from_record,
)
from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    ImportedMessage,
    Message,
    MessageRole,
    MessageVisibility,
    ProviderAttempt,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.assistant.routing import (
    routing_decision_from_record,
    routing_decision_record,
)
from fairy_core.model_catalog.models import (
    ModelEndpointKind,
    ModelSelectionMode,
    ModelSelectionSnapshot,
)
from fairy_core.providers import (
    ModelExecutionRole,
    ProviderAttemptStatus,
    ProviderErrorCategory,
)


class AssistantRecordCodecMixin:
    @staticmethod
    def _turn_values(turn: AssistantTurn) -> dict[str, object]:
        return {
            "id": str(turn.id),
            "conversation_id": str(turn.conversation_id),
            "task_id": str(turn.task_id),
            "profile_id": turn.profile_id,
            "scope_digest": turn.scope_digest,
            "memory_snapshot_id": str(turn.memory_snapshot_id),
            "memory_snapshot_hash": turn.memory_snapshot_hash,
            "knowledge_snapshot_id": (
                str(turn.knowledge_snapshot_id) if turn.knowledge_snapshot_id else None
            ),
            "knowledge_snapshot_hash": turn.knowledge_snapshot_hash,
            "harness_manifest_id": (
                str(turn.harness_manifest_id) if turn.harness_manifest_id else None
            ),
            "harness_manifest_hash": turn.harness_manifest_hash,
            "idempotency_key": turn.idempotency_key,
            "model_selection": _model_selection_record(turn.model_selection),
            "routing_decision": (
                routing_decision_record(turn.routing_decision)
                if turn.routing_decision is not None
                else None
            ),
            "budget_approval_run_id": (
                str(turn.budget_approval_run_id) if turn.budget_approval_run_id else None
            ),
            "cited_evidence_receipt_ids": [str(value) for value in turn.cited_evidence_receipt_ids],
            "workflow_run_id": str(turn.workflow_run_id) if turn.workflow_run_id else None,
            "execution_engine_version": turn.execution_engine_version,
            "active_interpretation_revision": turn.active_interpretation_revision,
            "status": turn.status.value,
            "cancellation_revision": turn.cancellation_revision,
            "usage": dict(turn.usage),
            "error_code": turn.error_code,
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
            "started_at": turn.started_at,
            "completed_at": turn.completed_at,
        }

    @staticmethod
    def _turn_mutable_values(turn: AssistantTurn) -> dict[str, object]:
        return {
            "status": turn.status.value,
            "cancellation_revision": turn.cancellation_revision,
            "usage": dict(turn.usage),
            "error_code": turn.error_code,
            "routing_decision": (
                routing_decision_record(turn.routing_decision)
                if turn.routing_decision is not None
                else None
            ),
            "active_interpretation_revision": turn.active_interpretation_revision,
            "budget_approval_run_id": (
                str(turn.budget_approval_run_id) if turn.budget_approval_run_id else None
            ),
            "cited_evidence_receipt_ids": [str(value) for value in turn.cited_evidence_receipt_ids],
            "updated_at": turn.updated_at,
            "started_at": turn.started_at,
            "completed_at": turn.completed_at,
        }

    @staticmethod
    def _turn_from_row(row: Mapping[str, Any]) -> AssistantTurn:
        return AssistantTurn(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            profile_id=row["profile_id"],
            scope_digest=row["scope_digest"],
            memory_snapshot_id=UUID(row["memory_snapshot_id"]),
            memory_snapshot_hash=row["memory_snapshot_hash"],
            knowledge_snapshot_id=(
                UUID(row["knowledge_snapshot_id"])
                if row.get("knowledge_snapshot_id") is not None
                else None
            ),
            knowledge_snapshot_hash=row.get("knowledge_snapshot_hash"),
            harness_manifest_id=(
                UUID(row["harness_manifest_id"])
                if row.get("harness_manifest_id") is not None
                else None
            ),
            harness_manifest_hash=row.get("harness_manifest_hash"),
            idempotency_key=row["idempotency_key"],
            model_selection=_model_selection_from_record(row.get("model_selection")),
            routing_decision=routing_decision_from_record(row.get("routing_decision")),
            budget_approval_run_id=(
                UUID(row["budget_approval_run_id"])
                if row.get("budget_approval_run_id") is not None
                else None
            ),
            cited_evidence_receipt_ids=tuple(
                UUID(str(value)) for value in row.get("cited_evidence_receipt_ids", ())
            ),
            workflow_run_id=(
                UUID(row["workflow_run_id"]) if row.get("workflow_run_id") is not None else None
            ),
            execution_engine_version=int(row.get("execution_engine_version", 1)),
            active_interpretation_revision=(
                int(row["active_interpretation_revision"])
                if row.get("active_interpretation_revision") is not None
                else None
            ),
            status=AssistantTurnStatus(row["status"]),
            cancellation_revision=int(row["cancellation_revision"]),
            usage={name: int(value) for name, value in row["usage"].items()},
            error_code=row["error_code"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            started_at=_optional_datetime(row["started_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )

    @staticmethod
    def _provider_attempt_from_row(row: Mapping[str, Any]) -> ProviderAttempt:
        return ProviderAttempt(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            task_id=UUID(row["task_id"]),
            model_round=int(row["model_round"]),
            attempt_number=int(row["attempt_number"]),
            profile_id=row["profile_id"],
            model_id=row["model_id"],
            endpoint_kind=ModelEndpointKind(row["endpoint_kind"]),
            model_role=ModelExecutionRole(row["model_role"]),
            status=ProviderAttemptStatus(row["status"]),
            error_category=(
                ProviderErrorCategory(row["error_category"])
                if row["error_category"] is not None
                else None
            ),
            usage={name: int(value) for name, value in row["usage"].items()},
            usage_cost=row["usage_cost"],
            created_at=_datetime(row["created_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )

    @staticmethod
    def _message_from_row(row: Mapping[str, Any]) -> Message:
        return Message(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            turn_id=UUID(row["turn_id"]) if row["turn_id"] else None,
            sequence=int(row["sequence"]),
            role=MessageRole(row["role"]),
            visibility=MessageVisibility(row["visibility"]),
            content=row["content"],
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _imported_message_from_row(row: Mapping[str, Any]) -> ImportedMessage:
        return ImportedMessage(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            turn_id=UUID(row["turn_id"]) if row["turn_id"] else None,
            sequence=int(row["sequence"]),
            role=MessageRole(row["role"]),
            visibility=MessageVisibility(row["visibility"]),
            content=row["content"],
            created_at=_datetime(row["created_at"]),
            source_conversation_id=UUID(row["source_conversation_id"]),
            source_message_id=UUID(row["source_message_id"]),
            source_hash=row["source_hash"],
            imported_at=_datetime(row["imported_at"]),
        )

    @staticmethod
    def _tool_from_row(row: Mapping[str, Any]) -> ToolInvocation:
        return ToolInvocation(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            task_id=UUID(row["task_id"]),
            model_round=int(row["model_round"]),
            sequence=int(row["sequence"]),
            provider_call_id=row["provider_call_id"],
            tool_name=row["tool_name"],
            scope_digest=row["scope_digest"],
            argument_hash=row["argument_hash"],
            arguments=dict(row["arguments"]),
            workflow_run_id=UUID(row["workflow_run_id"]) if row["workflow_run_id"] else None,
            workflow_plan_revision=int(row["workflow_plan_revision"]),
            workflow_objective_index=int(row["workflow_objective_index"]),
            command_run_id=UUID(row["command_run_id"]) if row["command_run_id"] else None,
            status=ToolInvocationStatus(row["status"]),
            public_summary=row["public_summary"],
            model_content=row["model_content"],
            artifact_ids=tuple(UUID(value) for value in row["artifact_ids"]),
            evidence_receipts=tuple(
                evidence_receipt_from_record(value) for value in row.get("evidence_receipts", ())
            ),
            error_code=row["error_code"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )


def _encode_message_cursor(
    *,
    conversation_id: UUID,
    sequence: int,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> str:
    scope = _message_cursor_scope(conversation_id, allowed_visibilities)
    payload = json.dumps(
        {"v": 1, "c": str(conversation_id), "q": sequence, "s": scope},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_message_cursor(
    cursor: str | None,
    *,
    conversation_id: UUID,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> int:
    if cursor is None:
        return 0
    try:
        if not cursor or len(cursor) > 2048:
            raise ValueError
        decoded = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
        expected_scope = _message_cursor_scope(conversation_id, allowed_visibilities)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "c", "q", "s"}
            or payload["v"] != 1
            or payload["c"] != str(conversation_id)
            or not isinstance(payload["q"], int)
            or isinstance(payload["q"], bool)
            or payload["q"] < 1
            or not isinstance(payload["s"], str)
            or not hmac.compare_digest(payload["s"], expected_scope)
        ):
            raise ValueError
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise ValueError("cursor is invalid for this Conversation") from error
    return payload["q"]


def _message_cursor_scope(
    conversation_id: UUID,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> str:
    visibility_scope = (
        "all"
        if allowed_visibilities is None
        else ",".join(sorted(value.value for value in allowed_visibilities))
    )
    value = f"assistant-messages:{conversation_id}:{visibility_scope}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _model_selection_record(
    selection: ModelSelectionSnapshot | None,
) -> dict[str, object] | None:
    if selection is None:
        return None
    return {
        "mode": selection.mode.value,
        "model_id": selection.model_id,
        "allow_free_fallback": selection.allow_free_fallback,
        "zero_data_retention": selection.zero_data_retention,
        "revision": selection.revision,
        "captured_at": selection.captured_at.isoformat(),
    }


def _model_selection_from_record(record: object) -> ModelSelectionSnapshot | None:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError("stored model selection snapshot is invalid")
    return ModelSelectionSnapshot(
        mode=ModelSelectionMode(record["mode"]),
        model_id=str(record["model_id"]) if record.get("model_id") is not None else None,
        allow_free_fallback=bool(record["allow_free_fallback"]),
        zero_data_retention=bool(record["zero_data_retention"]),
        revision=int(record["revision"]),
        captured_at=_datetime(str(record["captured_at"])),
    )


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None
