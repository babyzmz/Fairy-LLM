from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from fairy_core.commanding.registry import RiskLevel


class CommandStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EventVisibility(StrEnum):
    USER = "user"
    DEVELOPER = "developer"
    INTERNAL = "internal"


COMMAND_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.CREATED: frozenset(
        {CommandStatus.QUEUED, CommandStatus.WAITING_APPROVAL, CommandStatus.CANCELLED}
    ),
    CommandStatus.QUEUED: frozenset(
        {
            CommandStatus.WAITING_APPROVAL,
            CommandStatus.RUNNING,
            CommandStatus.CANCELLED,
            CommandStatus.INTERRUPTED,
        }
    ),
    CommandStatus.WAITING_APPROVAL: frozenset(
        {
            CommandStatus.QUEUED,
            CommandStatus.RUNNING,
            CommandStatus.REJECTED,
            CommandStatus.CANCELLED,
        }
    ),
    CommandStatus.RUNNING: frozenset(
        {
            CommandStatus.SUCCEEDED,
            CommandStatus.FAILED,
            CommandStatus.INTERRUPTED,
            CommandStatus.CANCELLED,
        }
    ),
    CommandStatus.INTERRUPTED: frozenset({CommandStatus.QUEUED, CommandStatus.FAILED}),
    CommandStatus.SUCCEEDED: frozenset(),
    CommandStatus.FAILED: frozenset(),
    CommandStatus.REJECTED: frozenset(),
    CommandStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CommandRun:
    id: UUID
    command_name: str
    actor: str
    scope_digest: str
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    input_payload: dict[str, Any]
    risk_level: RiskLevel
    status: CommandStatus
    idempotency_key: str
    lease_owner: str | None
    lease_until: datetime | None
    lease_fence: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    id: UUID
    cursor: int
    run_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    task_sequence: int
    event_type: str
    visibility: EventVisibility
    message: str
    payload: dict[str, Any]
    schema_version: int
    created_at: datetime
