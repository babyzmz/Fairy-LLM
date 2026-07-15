from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.providers import ModelExecutionRole


def _now() -> datetime:
    return datetime.now(UTC)


class TraceStepKind(StrEnum):
    ROUTE = "route"
    PLAN = "plan"
    REASONING = "reasoning"
    MODEL = "model"
    TOOL = "tool"
    APPROVAL = "approval"
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    ARTIFACT = "artifact"
    RESPONSE = "response"
    VOICE = "voice"


class TraceStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TraceVisibility(StrEnum):
    USER = "user"
    DEVELOPER = "developer"
    INTERNAL = "internal"


_TERMINAL_STEP_STATUSES = frozenset(
    {
        TraceStepStatus.SUCCEEDED,
        TraceStepStatus.FAILED,
        TraceStepStatus.CANCELLED,
        TraceStepStatus.SKIPPED,
    }
)

_STEP_TRANSITIONS: dict[TraceStepStatus, frozenset[TraceStepStatus]] = {
    TraceStepStatus.PENDING: frozenset(
        {
            TraceStepStatus.RUNNING,
            TraceStepStatus.WAITING,
            TraceStepStatus.SUCCEEDED,
            TraceStepStatus.FAILED,
            TraceStepStatus.CANCELLED,
            TraceStepStatus.SKIPPED,
        }
    ),
    TraceStepStatus.RUNNING: frozenset(
        {
            TraceStepStatus.WAITING,
            TraceStepStatus.SUCCEEDED,
            TraceStepStatus.FAILED,
            TraceStepStatus.CANCELLED,
        }
    ),
    TraceStepStatus.WAITING: frozenset(
        {
            TraceStepStatus.RUNNING,
            TraceStepStatus.SUCCEEDED,
            TraceStepStatus.FAILED,
            TraceStepStatus.CANCELLED,
        }
    ),
    TraceStepStatus.SUCCEEDED: frozenset(),
    TraceStepStatus.FAILED: frozenset(),
    TraceStepStatus.CANCELLED: frozenset(),
    TraceStepStatus.SKIPPED: frozenset(),
}


@dataclass(slots=True)
class TurnTrace:
    id: UUID
    turn_id: UUID
    conversation_id: UUID
    task_id: UUID
    legacy: bool = False
    last_sequence: int = 0
    revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        turn_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        legacy: bool = False,
    ) -> TurnTrace:
        return cls(
            id=new_id(),
            turn_id=turn_id,
            conversation_id=conversation_id,
            task_id=task_id,
            legacy=legacy,
        )

    def __post_init__(self) -> None:
        if self.last_sequence < 0 or self.revision < 0:
            raise ValueError("Turn Trace counters cannot be negative")
        self.created_at = _aware(self.created_at)
        self.updated_at = _aware(self.updated_at)
        self.started_at = _optional_aware(self.started_at)
        self.completed_at = _optional_aware(self.completed_at)
        if self.completed_at is not None and self.started_at is None:
            raise ValueError("completed Turn Trace must have started")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("Turn Trace completion cannot precede its start")

    def start(self) -> None:
        if self.completed_at is not None:
            raise InvalidTransitionError("completed Turn Trace cannot restart")
        if self.started_at is not None:
            return
        now = _now()
        self.started_at = now
        self.updated_at = now
        self.revision += 1

    def complete(self) -> None:
        if self.started_at is None:
            self.start()
        if self.completed_at is not None:
            return
        now = _now()
        self.completed_at = now
        self.updated_at = now
        self.revision += 1


@dataclass(slots=True)
class TraceStep:
    id: UUID
    trace_id: UUID
    turn_id: UUID
    sequence: int
    kind: TraceStepKind
    status: TraceStepStatus
    public_summary: str
    visibility: TraceVisibility
    parent_step_id: UUID | None = None
    caused_by_step_id: UUID | None = None
    public_detail: str | None = None
    model_id: str | None = None
    model_role: ModelExecutionRole | None = None
    provider_attempt_id: UUID | None = None
    command_run_id: UUID | None = None
    artifact_refs: tuple[UUID, ...] = ()
    revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        trace_id: UUID,
        turn_id: UUID,
        sequence: int,
        kind: TraceStepKind,
        status: TraceStepStatus,
        public_summary: str,
        visibility: TraceVisibility = TraceVisibility.USER,
        parent_step_id: UUID | None = None,
        caused_by_step_id: UUID | None = None,
        public_detail: str | None = None,
        model_id: str | None = None,
        model_role: ModelExecutionRole | None = None,
        provider_attempt_id: UUID | None = None,
        command_run_id: UUID | None = None,
        artifact_refs: tuple[UUID, ...] = (),
    ) -> TraceStep:
        now = _now()
        terminal = status in _TERMINAL_STEP_STATUSES
        return cls(
            id=new_id(),
            trace_id=trace_id,
            turn_id=turn_id,
            sequence=sequence,
            kind=TraceStepKind(kind),
            status=TraceStepStatus(status),
            public_summary=public_summary,
            visibility=TraceVisibility(visibility),
            parent_step_id=parent_step_id,
            caused_by_step_id=caused_by_step_id,
            public_detail=public_detail,
            model_id=model_id,
            model_role=model_role,
            provider_attempt_id=provider_attempt_id,
            command_run_id=command_run_id,
            artifact_refs=artifact_refs,
            started_at=(now if status is not TraceStepStatus.PENDING else None),
            completed_at=(now if terminal else None),
            created_at=now,
            updated_at=now,
        )

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.revision < 0:
            raise ValueError("Trace Step sequence and revision are invalid")
        self.kind = TraceStepKind(self.kind)
        self.status = TraceStepStatus(self.status)
        self.visibility = TraceVisibility(self.visibility)
        self.public_summary = _required_text(
            self.public_summary,
            name="public_summary",
            maximum=512,
        )
        self.public_detail = _optional_text(
            self.public_detail,
            name="public_detail",
            maximum=4_000,
        )
        self.model_id = _optional_text(self.model_id, name="model_id", maximum=255)
        if (self.model_id is None) != (self.model_role is None):
            raise ValueError("Trace Step model id and role must be provided together")
        if self.model_role is not None:
            self.model_role = ModelExecutionRole(self.model_role)
        self.artifact_refs = tuple(dict.fromkeys(self.artifact_refs))
        if len(self.artifact_refs) > 256:
            raise ValueError("Trace Step has too many artifact references")
        self.created_at = _aware(self.created_at)
        self.updated_at = _aware(self.updated_at)
        self.started_at = _optional_aware(self.started_at)
        self.completed_at = _optional_aware(self.completed_at)
        if self.status in _TERMINAL_STEP_STATUSES and self.completed_at is None:
            raise ValueError("terminal Trace Step must have a completion timestamp")
        if self.status not in _TERMINAL_STEP_STATUSES and self.completed_at is not None:
            raise ValueError("active Trace Step cannot have a completion timestamp")

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STEP_STATUSES

    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None:
            return None
        end = self.completed_at or self.updated_at
        return max(0, int((end - self.started_at).total_seconds() * 1_000))

    def transition(
        self,
        status: TraceStepStatus,
        *,
        public_summary: str | None = None,
        public_detail: str | None = None,
        artifact_refs: tuple[UUID, ...] | None = None,
    ) -> None:
        target = TraceStepStatus(status)
        if target is self.status:
            return
        if target not in _STEP_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Trace Step from {self.status.value} to {target.value}"
            )
        now = _now()
        self.status = target
        if public_summary is not None:
            self.public_summary = _required_text(
                public_summary,
                name="public_summary",
                maximum=512,
            )
        if public_detail is not None:
            self.public_detail = _optional_text(
                public_detail,
                name="public_detail",
                maximum=4_000,
            )
        if artifact_refs is not None:
            normalized = tuple(dict.fromkeys(artifact_refs))
            if len(normalized) > 256:
                raise ValueError("Trace Step has too many artifact references")
            self.artifact_refs = normalized
        if self.started_at is None and target is not TraceStepStatus.PENDING:
            self.started_at = now
        if target in _TERMINAL_STEP_STATUSES:
            self.completed_at = now
        self.updated_at = now
        self.revision += 1

    def bind_provider_attempt(self, provider_attempt_id: UUID) -> None:
        if self.kind is not TraceStepKind.MODEL:
            raise InvalidTransitionError("only model Trace Steps bind Provider Attempts")
        if self.is_terminal:
            raise InvalidTransitionError("terminal Trace Step cannot change Provider Attempt")
        self.provider_attempt_id = provider_attempt_id
        self.updated_at = _now()
        self.revision += 1


def _required_text(value: str, *, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"Trace Step {name} is invalid")
    return normalized


def _optional_text(value: str | None, *, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, name=name, maximum=maximum)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Trace timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None


__all__ = [
    "TraceStep",
    "TraceStepKind",
    "TraceStepStatus",
    "TraceVisibility",
    "TurnTrace",
]
