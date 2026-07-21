from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator

from fairy_core.contracts.common import ContractModel, JsonValue
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryNamespace,
    MemoryScanResult,
    MemorySensitivity,
    MemorySourceType,
    MemoryTargetKind,
    ObservationStatus,
)


class MemorySettingsUpdateInput(ContractModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=3_650)
    export_to_obsidian: bool
    sync_normalized_content: bool
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)


class MemorySettingsModel(ContractModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=3_650)
    export_to_obsidian: bool
    sync_normalized_content: bool
    revision: int = Field(ge=0)
    updated_at: datetime


class MemoryForgetTargetModel(StrEnum):
    OBSERVATION = "observation"
    CLAIM = "claim"


class MemoryObserveInput(ContractModel):
    task_id: UUID
    content: str = Field(min_length=1, max_length=100_000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class MemorySuggestInput(ContractModel):
    task_id: UUID
    content: str = Field(min_length=1, max_length=10_000)
    proposed_namespace: MemoryNamespace = MemoryNamespace.CONVERSATION_DRAFT
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("proposed_namespace")
    @classmethod
    def validate_proposed_namespace(cls, value: MemoryNamespace) -> MemoryNamespace:
        if value not in {
            MemoryNamespace.PROJECT_CANONICAL,
            MemoryNamespace.CONVERSATION_DRAFT,
            MemoryNamespace.TASK_EPISODE,
        }:
            raise ValueError("model suggestions require a task-scoped namespace")
        return value


class MemoryProposalActionInput(ContractModel):
    task_id: UUID
    observation_id: UUID
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=255)


class MemoryProposalListInput(ContractModel):
    task_id: UUID
    limit: int = Field(default=100, ge=1, le=500)


class MemoryObservationQuery(ContractModel):
    task_id: UUID
    namespace: MemoryNamespace


class MemoryClaimGetInput(ContractModel):
    task_id: UUID
    claim_id: UUID


class MemoryClaimQuery(ContractModel):
    task_id: UUID
    namespace: MemoryNamespace


class MemorySearchInput(ContractModel):
    task_id: UUID
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory search query is required")
        return normalized


class MemorySnapshotGetInput(ContractModel):
    task_id: UUID
    snapshot_id: UUID


class MemoryProjectionHealthInput(ContractModel):
    task_id: UUID


class _MemoryClaimValueInput(ContractModel):
    task_id: UUID
    value: JsonValue
    normalized_text: str = Field(min_length=1, max_length=100_000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("value")
    @classmethod
    def require_strict_json_value(cls, value: JsonValue) -> JsonValue:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("memory Claim value must be valid JSON") from error
        return value


class MemoryClaimPromoteInput(_MemoryClaimValueInput):
    observation_id: UUID
    subject: str = Field(min_length=1, max_length=512)
    predicate: str = Field(min_length=1, max_length=512)


class MemoryClaimSupersedeInput(_MemoryClaimValueInput):
    claim_id: UUID
    expected_revision: int = Field(ge=1)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class MemoryClaimResolveInput(MemoryClaimSupersedeInput):
    resolved_claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class MemoryForgetInput(ContractModel):
    task_id: UUID
    target_kind: MemoryForgetTargetModel
    target_id: UUID
    reason: str = Field(min_length=1, max_length=10_000)
    user_confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=255)


class MemoryObservationModel(ContractModel):
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    source_event_id: UUID
    source_cursor: int = Field(ge=1)
    source_type: MemorySourceType
    content: str
    content_hash: str
    proposed_namespace: MemoryNamespace
    authority: MemoryAuthority
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity
    scan_result: MemoryScanResult
    status: ObservationStatus
    actor: str
    created_at: datetime


class MemoryObservationPageModel(ContractModel):
    items: tuple[MemoryObservationModel, ...]


class MemoryProposalModel(MemoryObservationModel):
    pass


class MemoryProposalPageModel(ContractModel):
    items: tuple[MemoryProposalModel, ...]


class MemoryClaimModel(ContractModel):
    id: UUID
    namespace: MemoryNamespace
    project_id: UUID | None
    conversation_id: UUID | None
    task_id: UUID | None
    version_id: UUID | None
    device_id: str | None
    subject: str
    predicate: str
    current_revision: int = Field(ge=0)
    conflict_set_id: UUID | None
    status: ClaimStatus
    created_at: datetime
    updated_at: datetime


class MemoryClaimRevisionModel(ContractModel):
    claim_id: UUID
    revision: int = Field(ge=1)
    value: JsonValue
    normalized_text: str
    source_observation_ids: tuple[UUID, ...]
    source_event_ids: tuple[UUID, ...]
    authority: MemoryAuthority
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    actor: str
    supersedes_revision: int | None
    resolved_claim_ids: tuple[UUID, ...]


class MemoryClaimContextModel(ContractModel):
    claim: MemoryClaimModel
    current_revision: MemoryClaimRevisionModel


class MemoryClaimPageModel(ContractModel):
    items: tuple[MemoryClaimContextModel, ...]


class MemoryTombstoneModel(ContractModel):
    id: UUID
    target_kind: MemoryTargetKind
    target_id: UUID
    reason: str
    actor: str
    source_event_id: UUID
    created_at: datetime
