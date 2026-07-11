from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, Field

from fairy_core.contracts.models import (
    ApprovalDecisionInput,
    ApprovalListInput,
    ApprovalPageModel,
    ArtifactIdInput,
    ArtifactListInput,
    ArtifactModel,
    ArtifactPageModel,
    AssistantTurnCancelInput,
    AssistantTurnCreateInput,
    AssistantTurnIdInput,
    AssistantTurnModel,
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
    MessageListInput,
    MessagePageModel,
    PendingChangesetModel,
    PreviewContextModel,
    PreviewIdInput,
    PreviewModel,
    PreviewResolutionModel,
    PreviewResolveInput,
    PreviewStartInput,
    PreviewStopInput,
    ProjectContextModel,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    ProjectModel,
    ProjectPageModel,
    ProviderHealthInput,
    ProviderHealthPageModel,
    ProviderProfilePageModel,
    RuntimeHealthInput,
    RuntimeHealthModel,
    RuntimeIdInput,
    RuntimeModel,
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
        "artifacts.list": CoreMethod(
            "artifacts.list",
            ArtifactListInput,
            ArtifactPageModel,
        ),
        "artifacts.read": CoreMethod(
            "artifacts.read",
            ArtifactIdInput,
            ArtifactModel,
        ),
        "assistant.turns.cancel": CoreMethod(
            "assistant.turns.cancel",
            AssistantTurnCancelInput,
            AssistantTurnModel,
        ),
        "assistant.turns.create": CoreMethod(
            "assistant.turns.create",
            AssistantTurnCreateInput,
            AssistantTurnModel,
        ),
        "assistant.turns.get": CoreMethod(
            "assistant.turns.get",
            AssistantTurnIdInput,
            AssistantTurnModel,
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
        "messages.list": CoreMethod(
            "messages.list",
            MessageListInput,
            MessagePageModel,
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
        "previews.get": CoreMethod(
            "previews.get",
            PreviewIdInput,
            PreviewContextModel,
        ),
        "previews.resolve": CoreMethod(
            "previews.resolve",
            PreviewResolveInput,
            PreviewResolutionModel,
        ),
        "previews.start": CoreMethod(
            "previews.start",
            PreviewStartInput,
            PreviewContextModel,
        ),
        "previews.stop": CoreMethod(
            "previews.stop",
            PreviewStopInput,
            PreviewModel,
        ),
        "providers.health": CoreMethod(
            "providers.health",
            ProviderHealthInput,
            ProviderHealthPageModel,
        ),
        "providers.list": CoreMethod(
            "providers.list",
            EmptyInput,
            ProviderProfilePageModel,
        ),
        "runtimes.get": CoreMethod(
            "runtimes.get",
            RuntimeIdInput,
            RuntimeModel,
        ),
        "runtimes.health": CoreMethod(
            "runtimes.health",
            RuntimeHealthInput,
            RuntimeHealthModel,
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
