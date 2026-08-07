from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.schedule_models import (
    AssistantOccurrenceStatus,
    AssistantScheduleStatus,
    AssistantScheduleTriggerKind,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ContractModel, ExecutionTarget, JsonValue
from fairy_core.contracts.models import (
    ModelSelectionSnapshotInput,
    ModelSelectionSnapshotModel,
)
from fairy_core.domain.models import OperationMode
from fairy_core.workflow.models import WorkflowBudgetTier


class AssistantBackgroundTaskListInput(ContractModel):
    current_conversation_id: UUID | None = None
    recent_limit: int = Field(default=20, ge=1, le=20)


class AssistantBackgroundTaskModel(ContractModel):
    id: str
    kind: str
    conversation_id: UUID
    conversation_title: str
    project_id: UUID | None
    task_id: UUID | None
    turn_id: UUID | None
    workflow_run_id: UUID | None
    schedule_id: UUID | None
    occurrence_id: UUID | None
    title: str
    status: str
    public_error: str | None
    attention_code: str | None
    current_conversation: bool
    scheduled_for: datetime | None
    next_fire_at: datetime | None
    schedule_revision: int | None = Field(default=None, ge=1)
    turn_status: AssistantTurnStatus | None
    turn_cancellation_revision: int | None = Field(default=None, ge=0)
    workflow_budget_tier: WorkflowBudgetTier | None
    created_at: datetime
    updated_at: datetime
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_run_now: bool


class AssistantBackgroundTaskPageModel(ContractModel):
    current: tuple[AssistantBackgroundTaskModel, ...]
    other: tuple[AssistantBackgroundTaskModel, ...]
    recent: tuple[AssistantBackgroundTaskModel, ...]
    nonterminal_count: int = Field(ge=0)


class AssistantScheduleIdInput(ContractModel):
    schedule_id: UUID


class AssistantScheduleRevisionInput(AssistantScheduleIdInput):
    expected_revision: int = Field(ge=1)


class AssistantScheduleCreateInput(ContractModel):
    conversation_id: UUID
    instruction: str = Field(min_length=1, max_length=100_000)
    operation_mode: OperationMode
    trigger_kind: AssistantScheduleTriggerKind
    trigger_rule: dict[str, JsonValue]
    timezone: str = Field(min_length=1, max_length=255)
    next_fire_at: datetime
    profile_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_selection: ModelSelectionSnapshotInput | None = None
    idempotency_key: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_one_model_source(self) -> AssistantScheduleCreateInput:
        if (self.profile_id is None) == (self.model_selection is None):
            raise ValueError("provide exactly one of profile_id or model_selection")
        return self


class AssistantScheduleUpdateInput(AssistantScheduleRevisionInput):
    instruction: str = Field(min_length=1, max_length=100_000)
    operation_mode: OperationMode
    trigger_kind: AssistantScheduleTriggerKind
    trigger_rule: dict[str, JsonValue]
    timezone: str = Field(min_length=1, max_length=255)
    next_fire_at: datetime


class AssistantScheduleListInput(ContractModel):
    conversation_id: UUID | None = None
    statuses: frozenset[AssistantScheduleStatus] | None = None
    limit: int = Field(default=100, ge=1, le=500)


class AssistantScheduleRunNowInput(AssistantScheduleRevisionInput):
    idempotency_key: str = Field(min_length=1, max_length=512)


class AssistantScheduleModel(ContractModel):
    id: UUID
    conversation_id: UUID
    task_id: UUID | None
    project_id: UUID | None
    workspace_id: UUID
    version_id: UUID | None
    instruction: str
    operation_mode: OperationMode
    trigger_kind: AssistantScheduleTriggerKind
    trigger_rule: dict[str, JsonValue]
    timezone: str
    next_fire_at: datetime
    execution_target: ExecutionTarget
    profile_id: str | None
    model_selection: ModelSelectionSnapshotModel | None
    permission_profile: PermissionProfile
    timeline_sequence: int = Field(ge=1)
    status: AssistantScheduleStatus
    active_revision: int = Field(ge=1)
    consecutive_failures: int = Field(ge=0)
    attention_code: str | None
    created_at: datetime
    updated_at: datetime
    last_fire_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None


class AssistantSchedulePageModel(ContractModel):
    items: tuple[AssistantScheduleModel, ...]


class AssistantScheduleOccurrenceModel(ContractModel):
    id: UUID
    schedule_id: UUID
    schedule_revision: int = Field(ge=1)
    scheduled_for: datetime
    status: AssistantOccurrenceStatus
    coalesced_count: int = Field(ge=0)
    turn_id: UUID | None
    workflow_run_id: UUID | None
    public_error: str | None
    created_at: datetime
    dispatched_at: datetime | None
    completed_at: datetime | None


__all__ = [
    "AssistantBackgroundTaskListInput",
    "AssistantBackgroundTaskModel",
    "AssistantBackgroundTaskPageModel",
    "AssistantScheduleCreateInput",
    "AssistantScheduleIdInput",
    "AssistantScheduleListInput",
    "AssistantScheduleModel",
    "AssistantScheduleOccurrenceModel",
    "AssistantSchedulePageModel",
    "AssistantScheduleRevisionInput",
    "AssistantScheduleRunNowInput",
    "AssistantScheduleUpdateInput",
]
