from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, Field

from fairy_core.contracts.models import (
    ApprovalDecisionInput,
    ApprovalListInput,
    ApprovalPageModel,
    CapabilityManifestModel,
    CapabilityRequest,
    ChangesetModel,
    ChangesetProposal,
    CheckpointModel,
    ContractModel,
    ConversationCreate,
    ConversationIdInput,
    ConversationListInput,
    ConversationModel,
    ConversationPageModel,
    EventEnvelopeModel,
    HealthModel,
    MemoryClaimContextModel,
    MemoryClaimGetInput,
    MemoryClaimPageModel,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationModel,
    MemoryObservationPageModel,
    MemoryObservationQuery,
    MemoryObserveInput,
    MemoryProjectionHealthInput,
    MemoryProjectionHealthModel,
    MemorySearchInput,
    MemorySearchPageModel,
    MemorySnapshotGetInput,
    MemorySnapshotModel,
    MemoryTombstoneModel,
    PendingChangesetModel,
    ProjectContextModel,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    ProjectModel,
    ProjectPageModel,
    TaskContextModel,
    TaskCreate,
    TaskIdInput,
    TaskListInput,
    TaskModel,
    TaskPageModel,
    VersionAcceptInput,
    VersionIdInput,
    VersionListInput,
    VersionModel,
    VersionPageModel,
)


class EmptyInput(ContractModel):
    pass


class EventSubscribeInput(ContractModel):
    cursor: int = Field(default=0, ge=0)


class EventPageModel(ContractModel):
    items: tuple[EventEnvelopeModel, ...]
    next_cursor: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class CoreMethod:
    name: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]


CORE_METHODS: Mapping[str, CoreMethod] = MappingProxyType(
    {
        "approvals.decide": CoreMethod(
            "approvals.decide",
            ApprovalDecisionInput,
            ChangesetModel,
        ),
        "approvals.list": CoreMethod(
            "approvals.list",
            ApprovalListInput,
            ApprovalPageModel,
        ),
        "capabilities.get": CoreMethod(
            "capabilities.get",
            CapabilityRequest,
            CapabilityManifestModel,
        ),
        "changesets.propose": CoreMethod(
            "changesets.propose",
            ChangesetProposal,
            PendingChangesetModel,
        ),
        "conversations.create": CoreMethod(
            "conversations.create",
            ConversationCreate,
            ConversationModel,
        ),
        "conversations.get": CoreMethod(
            "conversations.get",
            ConversationIdInput,
            ConversationModel,
        ),
        "conversations.list": CoreMethod(
            "conversations.list",
            ConversationListInput,
            ConversationPageModel,
        ),
        "events.subscribe": CoreMethod(
            "events.subscribe",
            EventSubscribeInput,
            EventPageModel,
        ),
        "health": CoreMethod("health", EmptyInput, HealthModel),
        "memory.claims.get": CoreMethod(
            "memory.claims.get",
            MemoryClaimGetInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.list": CoreMethod(
            "memory.claims.list",
            MemoryClaimQuery,
            MemoryClaimPageModel,
        ),
        "memory.claims.promote": CoreMethod(
            "memory.claims.promote",
            MemoryClaimPromoteInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.resolve_conflict": CoreMethod(
            "memory.claims.resolve_conflict",
            MemoryClaimResolveInput,
            MemoryClaimContextModel,
        ),
        "memory.claims.supersede": CoreMethod(
            "memory.claims.supersede",
            MemoryClaimSupersedeInput,
            MemoryClaimContextModel,
        ),
        "memory.forget": CoreMethod(
            "memory.forget",
            MemoryForgetInput,
            MemoryTombstoneModel,
        ),
        "memory.observations.create": CoreMethod(
            "memory.observations.create",
            MemoryObserveInput,
            MemoryObservationModel,
        ),
        "memory.observations.list": CoreMethod(
            "memory.observations.list",
            MemoryObservationQuery,
            MemoryObservationPageModel,
        ),
        "memory.projection.health": CoreMethod(
            "memory.projection.health",
            MemoryProjectionHealthInput,
            MemoryProjectionHealthModel,
        ),
        "memory.search": CoreMethod(
            "memory.search",
            MemorySearchInput,
            MemorySearchPageModel,
        ),
        "memory.snapshots.get": CoreMethod(
            "memory.snapshots.get",
            MemorySnapshotGetInput,
            MemorySnapshotModel,
        ),
        "projects.create": CoreMethod(
            "projects.create",
            ProjectCreate,
            ProjectContextModel,
        ),
        "projects.get": CoreMethod("projects.get", ProjectIdInput, ProjectModel),
        "projects.import": CoreMethod(
            "projects.import",
            ProjectImport,
            ProjectContextModel,
        ),
        "projects.list": CoreMethod(
            "projects.list",
            ProjectListInput,
            ProjectPageModel,
        ),
        "tasks.create": CoreMethod("tasks.create", TaskCreate, TaskContextModel),
        "tasks.get": CoreMethod("tasks.get", TaskIdInput, TaskModel),
        "tasks.list": CoreMethod("tasks.list", TaskListInput, TaskPageModel),
        "tasks.review": CoreMethod("tasks.review", TaskIdInput, CheckpointModel),
        "versions.accept": CoreMethod(
            "versions.accept",
            VersionAcceptInput,
            ProjectModel,
        ),
        "versions.discard": CoreMethod("versions.discard", TaskIdInput, TaskModel),
        "versions.get": CoreMethod("versions.get", VersionIdInput, VersionModel),
        "versions.list": CoreMethod(
            "versions.list",
            VersionListInput,
            VersionPageModel,
        ),
    }
)


__all__ = [
    "CORE_METHODS",
    "CoreMethod",
    "EmptyInput",
    "EventPageModel",
    "EventSubscribeInput",
]
