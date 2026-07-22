from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from fairy_core.domain.execution import PreviewSession, RuntimeSession
from fairy_core.domain.models import Task
from fairy_core.runtime.models import RuntimeExecutorHealth


@dataclass(frozen=True, slots=True)
class PreviewStartRequest:
    task_id: UUID
    idempotency_key: str
    workspace_id: UUID | None = None
    version_id: UUID | None = None
    expected_workspace_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


class PreviewActivationOutcome(StrEnum):
    READY = "ready"
    STARTING = "starting"
    WAITING_FOR_SLOT = "waiting_for_slot"
    NOT_RUNNABLE = "not_runnable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PreviewActivateRequest:
    task_id: UUID
    workspace_id: UUID
    version_id: UUID
    idempotency_key: str
    expected_workspace_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewStopRequest:
    preview_id: UUID
    idempotency_key: str
    task_id: UUID | None = None
    workspace_id: UUID | None = None
    version_id: UUID | None = None
    expected_workspace_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewResolveRequest:
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    workspace_id: UUID | None = None
    version_id: UUID | None = None
    preview_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PreviewContext:
    task: Task
    runtime: RuntimeSession
    preview: PreviewSession


@dataclass(frozen=True, slots=True)
class PreviewActivationResult:
    outcome: PreviewActivationOutcome
    context: PreviewContext | None
    adapter: str | None
    capacity: int
    active_count: int
    evicted_preview_id: UUID | None = None
    public_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeHealthResult:
    executor: RuntimeExecutorHealth
    runtime: RuntimeSession | None
    preview: PreviewSession | None
