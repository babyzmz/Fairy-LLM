from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.assistant.evidence import EvidenceSourceKind
from fairy_core.assistant.trace_models import (
    TraceStepKind,
    TraceStepStatus,
    TraceVisibility,
)
from fairy_core.contracts.models import ContractModel
from fairy_core.providers import ModelExecutionRole


class TraceStepModel(ContractModel):
    id: UUID
    trace_id: UUID
    turn_id: UUID
    sequence: int = Field(ge=1)
    parent_step_id: UUID | None
    caused_by_step_id: UUID | None
    kind: TraceStepKind
    status: TraceStepStatus
    public_summary: str = Field(min_length=1, max_length=512)
    public_detail: str | None = Field(default=None, min_length=1, max_length=4_000)
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_role: ModelExecutionRole | None
    provider_attempt_id: UUID | None
    command_run_id: UUID | None
    artifact_refs: tuple[UUID, ...] = Field(max_length=256)
    visibility: TraceVisibility
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_complete_model_identity(self) -> TraceStepModel:
        if (self.model_id is None) != (self.model_role is None):
            raise ValueError("Trace Step model identity is incomplete")
        return self


class EvidenceSourceModel(ContractModel):
    id: UUID
    source_kind: EvidenceSourceKind
    public_label: str = Field(min_length=1, max_length=240)
    tool_name: str = Field(min_length=1, max_length=255)
    workspace_id: UUID | None = None
    version_id: UUID | None = None
    relative_path: str | None = Field(default=None, max_length=1_024)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    safe_url: str | None = Field(default=None, max_length=2_048)
    observed_at: datetime
    expires_at: datetime | None
    truncated: bool


class TurnTraceModel(ContractModel):
    id: UUID
    turn_id: UUID
    conversation_id: UUID
    task_id: UUID
    legacy: bool
    last_sequence: int = Field(ge=0)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    steps: tuple[TraceStepModel, ...]
    evidence_sources: tuple[EvidenceSourceModel, ...] = Field(max_length=32)


__all__ = ["EvidenceSourceModel", "TraceStepModel", "TurnTraceModel"]
