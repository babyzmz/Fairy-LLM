from __future__ import annotations

from fairy_core.contracts.models import ChangesetProposal, TaskCreate
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.domain.execution import Changeset
from fairy_core.domain.models import Task


def normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("idempotency_key is required")
    return normalized


def validate_task_replay(existing: Task, request: TaskCreate) -> None:
    if (
        existing.conversation_id != request.conversation_id
        or existing.user_request != request.user_request.strip()
        or existing.operation_mode is not request.operation_mode
        or existing.execution_target != request.execution_target.value
    ):
        raise IdempotencyConflictError(
            "task idempotency key was already used for a different request"
        )


def validate_changeset_replay(
    existing: Changeset,
    request: ChangesetProposal,
) -> None:
    if (
        existing.task_id != request.task_id
        or existing.files != tuple(mutation.path for mutation in request.files)
        or existing.patches != tuple(mutation.content for mutation in request.files)
        or existing.reason != request.reason.strip()
    ):
        raise IdempotencyConflictError(
            "changeset idempotency key was already used for different mutations"
        )


__all__ = [
    "normalize_idempotency_key",
    "validate_changeset_replay",
    "validate_task_replay",
]
