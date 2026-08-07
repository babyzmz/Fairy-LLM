from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode
from fairy_core.model_catalog.models import ModelSelectionSnapshot


class AssistantScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssistantScheduleTriggerKind(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    INTERVAL = "interval"


class AssistantOccurrenceStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ATTENTION_REQUIRED = "attention_required"
    COALESCED = "coalesced"


@dataclass(frozen=True, slots=True)
class AssistantScheduleClaim:
    schedule_id: UUID
    lease_owner: str
    lease_fence: int
    active_revision: int

    def __post_init__(self) -> None:
        if not self.lease_owner.strip() or len(self.lease_owner) > 128:
            raise ValueError("Assistant schedule claim owner is invalid")
        if self.lease_fence < 1 or self.active_revision < 1:
            raise ValueError("Assistant schedule claim counters must be positive")


_TERMINAL_OCCURRENCE_STATUSES = frozenset(
    {
        AssistantOccurrenceStatus.SUCCEEDED,
        AssistantOccurrenceStatus.FAILED,
        AssistantOccurrenceStatus.CANCELLED,
        AssistantOccurrenceStatus.ATTENTION_REQUIRED,
        AssistantOccurrenceStatus.COALESCED,
    }
)


@dataclass(frozen=True, slots=True)
class AssistantSchedule:
    id: UUID
    conversation_id: UUID
    task_id: UUID | None
    project_id: UUID | None
    workspace_id: UUID
    version_id: UUID | None
    instruction: str
    operation_mode: OperationMode
    trigger_kind: AssistantScheduleTriggerKind
    trigger_rule: Mapping[str, Any]
    timezone: str
    next_fire_at: datetime
    execution_target: ExecutionTarget
    profile_id: str | None
    model_selection: ModelSelectionSnapshot | None
    permission_profile: PermissionProfile
    timeline_sequence: int
    status: AssistantScheduleStatus
    active_revision: int
    consecutive_failures: int
    idempotency_key: str
    lease_owner: str | None
    lease_fence: int
    lease_until: datetime | None
    attention_code: str | None
    created_at: datetime
    updated_at: datetime
    last_fire_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        instruction = self.instruction.strip()
        timezone = self.timezone.strip()
        idempotency_key = self.idempotency_key.strip()
        if not instruction or len(instruction) > 100_000:
            raise ValueError("Assistant schedule instruction is invalid")
        if not timezone or len(timezone) > 255:
            raise ValueError("Assistant schedule timezone is invalid")
        if not idempotency_key or len(idempotency_key) > 512:
            raise ValueError("Assistant schedule idempotency key is invalid")
        if (self.profile_id is None) == (self.model_selection is None):
            raise ValueError("Assistant schedule requires exactly one model source")
        if self.profile_id is not None and not self.profile_id.strip():
            raise ValueError("Assistant schedule profile id is invalid")
        if self.execution_target is not ExecutionTarget.LOCAL:
            raise ValueError("Assistant schedule V1 only supports local execution")
        if self.timeline_sequence < 1 or self.active_revision < 1:
            raise ValueError("Assistant schedule revisions and sequence must be positive")
        if self.consecutive_failures < 0 or self.lease_fence < 0:
            raise ValueError("Assistant schedule counters cannot be negative")
        if (self.lease_owner is None) != (self.lease_until is None):
            raise ValueError("Assistant schedule lease fields must be paired")
        if self.lease_owner is not None and not self.lease_owner.strip():
            raise ValueError("Assistant schedule lease owner is invalid")
        if self.status is AssistantScheduleStatus.PAUSED and self.paused_at is None:
            raise ValueError("Paused Assistant schedule requires paused_at")
        if self.status is AssistantScheduleStatus.COMPLETED and self.completed_at is None:
            raise ValueError("Completed Assistant schedule requires completed_at")
        if self.status is AssistantScheduleStatus.CANCELLED and self.cancelled_at is None:
            raise ValueError("Cancelled Assistant schedule requires cancelled_at")
        for name in (
            "next_fire_at",
            "created_at",
            "updated_at",
            "last_fire_at",
            "lease_until",
            "paused_at",
            "completed_at",
            "cancelled_at",
        ):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "trigger_rule", MappingProxyType(dict(self.trigger_rule)))

    @classmethod
    def create(
        cls,
        *,
        conversation_id: UUID,
        workspace_id: UUID,
        instruction: str,
        trigger_kind: AssistantScheduleTriggerKind,
        trigger_rule: Mapping[str, Any],
        timezone: str,
        next_fire_at: datetime,
        permission_profile: PermissionProfile,
        timeline_sequence: int,
        idempotency_key: str,
        operation_mode: OperationMode = OperationMode.ANSWER,
        profile_id: str | None = None,
        model_selection: ModelSelectionSnapshot | None = None,
        task_id: UUID | None = None,
        project_id: UUID | None = None,
        version_id: UUID | None = None,
        now: datetime | None = None,
    ) -> AssistantSchedule:
        created_at = now or datetime.now(UTC)
        return cls(
            id=new_id(),
            conversation_id=conversation_id,
            task_id=task_id,
            project_id=project_id,
            workspace_id=workspace_id,
            version_id=version_id,
            instruction=instruction,
            operation_mode=OperationMode(operation_mode),
            trigger_kind=trigger_kind,
            trigger_rule=trigger_rule,
            timezone=timezone,
            next_fire_at=next_fire_at,
            execution_target=ExecutionTarget.LOCAL,
            profile_id=profile_id,
            model_selection=model_selection,
            permission_profile=permission_profile,
            timeline_sequence=timeline_sequence,
            status=AssistantScheduleStatus.ACTIVE,
            active_revision=1,
            consecutive_failures=0,
            idempotency_key=idempotency_key,
            lease_owner=None,
            lease_fence=0,
            lease_until=None,
            attention_code=None,
            created_at=created_at,
            updated_at=created_at,
        )

    def pause(self, *, now: datetime, attention_code: str | None = None) -> AssistantSchedule:
        if self.status is AssistantScheduleStatus.PAUSED:
            return self
        if self.status is not AssistantScheduleStatus.ACTIVE:
            raise ValueError("Only an active Assistant schedule can be paused")
        return replace(
            self,
            status=AssistantScheduleStatus.PAUSED,
            paused_at=now,
            attention_code=attention_code,
            lease_owner=None,
            lease_until=None,
            updated_at=now,
        )

    def resume(self, *, next_fire_at: datetime, now: datetime) -> AssistantSchedule:
        if self.status is not AssistantScheduleStatus.PAUSED:
            raise ValueError("Only a paused Assistant schedule can be resumed")
        return replace(
            self,
            status=AssistantScheduleStatus.ACTIVE,
            next_fire_at=next_fire_at,
            consecutive_failures=0,
            paused_at=None,
            attention_code=None,
            updated_at=now,
        )

    def cancel(self, *, now: datetime) -> AssistantSchedule:
        if self.status is AssistantScheduleStatus.CANCELLED:
            return self
        if self.status is AssistantScheduleStatus.COMPLETED:
            raise ValueError("Completed Assistant schedule cannot be cancelled")
        return replace(
            self,
            status=AssistantScheduleStatus.CANCELLED,
            cancelled_at=now,
            lease_owner=None,
            lease_until=None,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class AssistantScheduleOccurrence:
    id: UUID
    schedule_id: UUID
    schedule_revision: int
    scheduled_for: datetime
    status: AssistantOccurrenceStatus
    coalesced_count: int
    turn_id: UUID | None
    workflow_run_id: UUID | None
    public_error: str | None
    created_at: datetime
    idempotency_key: str | None = None
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.schedule_revision < 1 or self.coalesced_count < 0:
            raise ValueError("Assistant occurrence counters are invalid")
        if (self.turn_id is None) != (self.workflow_run_id is None):
            raise ValueError("Assistant occurrence Turn and Workflow bindings must be paired")
        if self.status is AssistantOccurrenceStatus.DISPATCHED and self.dispatched_at is None:
            raise ValueError("Dispatched Assistant occurrence requires dispatched_at")
        if self.status in _TERMINAL_OCCURRENCE_STATUSES and self.completed_at is None:
            raise ValueError("Terminal Assistant occurrence requires completed_at")
        if self.public_error is not None and len(self.public_error) > 500:
            raise ValueError("Assistant occurrence public error is too large")
        if self.idempotency_key is not None:
            normalized_key = self.idempotency_key.strip()
            if not normalized_key or len(normalized_key) > 512:
                raise ValueError("Assistant occurrence idempotency key is invalid")
            object.__setattr__(self, "idempotency_key", normalized_key)
        for name in ("scheduled_for", "created_at", "dispatched_at", "completed_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)

    @classmethod
    def pending(
        cls,
        *,
        schedule_id: UUID,
        schedule_revision: int,
        scheduled_for: datetime,
        coalesced_count: int = 0,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> AssistantScheduleOccurrence:
        return cls(
            id=new_id(),
            schedule_id=schedule_id,
            schedule_revision=schedule_revision,
            scheduled_for=scheduled_for,
            status=AssistantOccurrenceStatus.PENDING,
            coalesced_count=coalesced_count,
            turn_id=None,
            workflow_run_id=None,
            public_error=None,
            created_at=now or datetime.now(UTC),
            idempotency_key=idempotency_key,
        )

    def coalesce(
        self,
        *,
        scheduled_for: datetime,
        merged_count: int,
    ) -> AssistantScheduleOccurrence:
        if self.status is not AssistantOccurrenceStatus.PENDING:
            raise ValueError("Only a pending Assistant occurrence can be coalesced")
        if merged_count < 1 or scheduled_for <= self.scheduled_for:
            raise ValueError("Assistant occurrence coalescing input is invalid")
        return replace(
            self,
            scheduled_for=scheduled_for,
            coalesced_count=self.coalesced_count + merged_count,
        )

    def dispatch(
        self,
        *,
        turn_id: UUID,
        workflow_run_id: UUID,
        now: datetime,
    ) -> AssistantScheduleOccurrence:
        if self.status is not AssistantOccurrenceStatus.PENDING:
            raise ValueError("Only a pending Assistant occurrence can be dispatched")
        return replace(
            self,
            status=AssistantOccurrenceStatus.DISPATCHED,
            turn_id=turn_id,
            workflow_run_id=workflow_run_id,
            dispatched_at=now,
        )

    def settle(
        self,
        *,
        status: AssistantOccurrenceStatus,
        now: datetime,
        public_error: str | None = None,
    ) -> AssistantScheduleOccurrence:
        if self.status is not AssistantOccurrenceStatus.DISPATCHED:
            raise ValueError("Only a dispatched Assistant occurrence can be settled")
        if status not in {
            AssistantOccurrenceStatus.SUCCEEDED,
            AssistantOccurrenceStatus.FAILED,
            AssistantOccurrenceStatus.CANCELLED,
            AssistantOccurrenceStatus.ATTENTION_REQUIRED,
        }:
            raise ValueError("Assistant occurrence outcome is invalid")
        return replace(
            self,
            status=status,
            public_error=public_error,
            completed_at=now,
        )


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "AssistantOccurrenceStatus",
    "AssistantSchedule",
    "AssistantScheduleClaim",
    "AssistantScheduleOccurrence",
    "AssistantScheduleStatus",
    "AssistantScheduleTriggerKind",
]
