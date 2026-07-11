from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fairy_core.domain.execution import PreviewSession, RuntimeSession
from fairy_core.domain.models import Task
from fairy_core.runtime.models import RuntimeExecutorHealth


@dataclass(frozen=True, slots=True)
class PreviewStartRequest:
    task_id: UUID
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewStopRequest:
    preview_id: UUID
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewResolveRequest:
    conversation_id: UUID
    preview_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PreviewContext:
    task: Task
    runtime: RuntimeSession
    preview: PreviewSession


@dataclass(frozen=True, slots=True)
class RuntimeHealthResult:
    executor: RuntimeExecutorHealth
    runtime: RuntimeSession | None
    preview: PreviewSession | None
