from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any, cast
from uuid import UUID
from weakref import finalize

from pydantic import BaseModel, ValidationError

from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import (
    PreviewResolveRequest,
    PreviewStartRequest,
    PreviewStopRequest,
    RuntimeApplication,
)
from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.assistant.tools import ToolExecutor
from fairy_core.commanding import EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.contracts.methods import CORE_METHODS, EventSubscribeInput
from fairy_core.contracts.models import (
    ApprovalDecisionInput,
    ApprovalListInput,
    ArtifactIdInput,
    ArtifactListInput,
    AssistantTurnCancelInput,
    AssistantTurnCreateInput,
    AssistantTurnIdInput,
    AssistantTurnRetryInput,
    AssistantTurnRunInput,
    CapabilityRequest,
    ChangesetProposal,
    ConversationCreate,
    ConversationIdInput,
    ConversationListInput,
    DocumentDeleteInput,
    DocumentIdInput,
    DocumentImportInput,
    DocumentListInput,
    DocumentSearchInput,
    MemoryClaimGetInput,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationQuery,
    MemoryObserveInput,
    MemoryProjectionHealthInput,
    MemorySearchInput,
    MemorySnapshotGetInput,
    MessageListInput,
    PreviewIdInput,
    PreviewResolveInput,
    PreviewStartInput,
    PreviewStopInput,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    ProviderHealthInput,
    RuntimeHealthInput,
    RuntimeIdInput,
    TaskCreate,
    TaskIdInput,
    TaskListInput,
    VersionAcceptInput,
    VersionIdInput,
    VersionListInput,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
)
from fairy_core.documents.application import DocumentApplication, DocumentToolExecutor
from fairy_core.documents.ports import DocumentBlobStore, DocumentParser
from fairy_core.domain.errors import (
    CapabilityUnavailableError,
    InvalidTransitionError,
    MemoryScopeViolationError,
)
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.perception import ImageAttachment, ImageAttachmentStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCapability, ProviderRegistry
from fairy_core.research.application import ResearchApplication, ResearchToolExecutor
from fairy_core.research.ports import FetchPort
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.voice.application import VoiceApplication
from fairy_core.voice.registry import VoiceRegistry


class CoreMethodNotFoundError(LookupError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Core method not found: {method}")


class CoreResponseValidationError(RuntimeError):
    def __init__(self, method: str, error: ValidationError) -> None:
        self.method = method
        self.validation_error = error
        super().__init__(f"Core method returned an invalid response: {method}")


class CoreService:
    """Transport-independent validation and application facade."""

    def __init__(
        self,
        application: CoreApplication,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        provider_registry: ProviderRegistry | None = None,
        voice_registry: VoiceRegistry | None = None,
        image_attachment_store: ImageAttachmentStore | None = None,
        tool_executor: ToolExecutor | None = None,
        research_fetch_port: FetchPort | None = None,
        document_parser: DocumentParser | None = None,
        document_blob_store: DocumentBlobStore | None = None,
        runtime_application: RuntimeApplication | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._provider_registry = provider_registry or ProviderRegistry()
        self._voice_registry = voice_registry or VoiceRegistry()
        self._voice_application = VoiceApplication(
            unit_of_work_factory=unit_of_work_factory,
            registry=self._voice_registry,
        )
        self._image_attachments = image_attachment_store or ImageAttachmentStore()
        self._turn_cancellations: dict[UUID, CancellationToken] = {}
        self._turn_cancellation_lock = RLock()
        self._runtime_application = runtime_application
        self._memory_application = MemoryApplication(
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            command_policy=PolicyEngine(registry),
            memory_policy=MemoryPolicy(),
            scope_resolver=application.scope_for_task,
        )
        self._assistant_ledger = AssistantLedgerApplication(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
        )
        effective_tool_executor = tool_executor
        if research_fetch_port is not None:
            effective_tool_executor = ResearchToolExecutor(
                application=ResearchApplication(
                    unit_of_work_factory=unit_of_work_factory,
                    scope_resolver=application.scope_for_task,
                    fetch_port=research_fetch_port,
                ),
                delegate=tool_executor,
            )
        if (document_parser is None) != (document_blob_store is None):
            raise ValueError("document parser and blob store must be configured together")
        self._document_application = None
        if document_parser is not None and document_blob_store is not None:
            self._document_application = DocumentApplication(
                unit_of_work_factory=unit_of_work_factory,
                registry=registry,
                command_policy=PolicyEngine(registry),
                scope_resolver=application.scope_for_task,
                parser=document_parser,
                blob_store=document_blob_store,
            )
            effective_tool_executor = DocumentToolExecutor(
                application=self._document_application,
                delegate=effective_tool_executor,
            )
        self._tool_executor = effective_tool_executor
        self._assistant_application = AssistantApplication(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
            registry=registry,
            providers=self._provider_registry,
            image_attachments=self._image_attachments,
            tool_executor=effective_tool_executor,
        )
        self._finalizer = finalize(self, on_close) if on_close is not None else None
        self._handlers: Mapping[str, Callable[[BaseModel], Any]] = {
            "approvals.decide": self._decide_approval,
            "approvals.list": self._list_approvals,
            "artifacts.list": self._list_artifacts,
            "artifacts.read": self._read_artifact,
            "assistant.turns.cancel": self._cancel_assistant_turn,
            "assistant.turns.create": self._create_assistant_turn,
            "assistant.turns.get": self._get_assistant_turn,
            "assistant.turns.retry": self._retry_assistant_turn,
            "assistant.turns.run": self._run_assistant_turn,
            "capabilities.get": self._get_capabilities,
            "changesets.propose": self._propose_changeset,
            "conversations.create": self._create_conversation,
            "conversations.get": self._get_conversation,
            "conversations.list": self._list_conversations,
            "documents.delete": self._delete_document,
            "documents.get": self._get_document,
            "documents.import": self._import_document,
            "documents.list": self._list_documents,
            "documents.search": self._search_documents,
            "events.subscribe": self._subscribe_events,
            "health": self._health,
            "memory.claims.get": self._get_memory_claim,
            "memory.claims.list": self._list_memory_claims,
            "memory.claims.promote": self._promote_memory_claim,
            "memory.claims.resolve_conflict": self._resolve_memory_conflict,
            "memory.claims.supersede": self._supersede_memory_claim,
            "memory.forget": self._forget_memory,
            "memory.observations.create": self._observe_memory,
            "memory.observations.list": self._list_memory_observations,
            "memory.projection.health": self._memory_projection_health,
            "memory.search": self._search_memory,
            "memory.snapshots.get": self._get_memory_snapshot,
            "messages.list": self._list_messages,
            "projects.create": self._create_project,
            "projects.get": self._get_project,
            "projects.import": self._import_project,
            "projects.list": self._list_projects,
            "previews.get": self._get_preview,
            "previews.resolve": self._resolve_preview,
            "previews.start": self._start_preview,
            "previews.stop": self._stop_preview,
            "providers.health": self._provider_health,
            "providers.list": self._list_providers,
            "runtimes.get": self._get_runtime,
            "runtimes.health": self._runtime_health,
            "tasks.create": self._create_task,
            "tasks.get": self._get_task,
            "tasks.list": self._list_tasks,
            "tasks.review": self._review_task,
            "versions.accept": self._accept_version,
            "versions.discard": self._discard_version,
            "versions.get": self._get_version,
            "versions.list": self._list_versions,
            "voice.synthesize": self._synthesize_voice,
            "voice.transcribe": self._transcribe_voice,
        }
        if self._handlers.keys() != CORE_METHODS.keys():
            raise RuntimeError("Core service handlers do not match the public method catalog")

    def close(self) -> None:
        with self._turn_cancellation_lock:
            for cancellation in self._turn_cancellations.values():
                cancellation.cancel()
            self._turn_cancellations.clear()
        self._image_attachments.close()
        self._provider_registry.close()
        self._voice_registry.close()
        if self._tool_executor is not None:
            close_tool_executor = getattr(self._tool_executor, "close", None)
            if callable(close_tool_executor):
                close_tool_executor()
        if self._finalizer is not None:
            self._finalizer()

    def invoke(self, method: str, params: Mapping[str, Any]) -> Any:
        definition = CORE_METHODS.get(method)
        if definition is None:
            raise CoreMethodNotFoundError(method)
        request = definition.request_model.model_validate(dict(params))
        result = self._handlers[method](request)
        try:
            response = definition.response_model.model_validate(result)
        except ValidationError as error:
            raise CoreResponseValidationError(method, error) from error
        return response.model_dump(mode="json")

    @staticmethod
    def _health(_request: BaseModel) -> dict[str, str]:
        return {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v1",
        }

    def _list_providers(self, _request: BaseModel) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": profile.id,
                    "display_name": profile.display_name,
                    "kind": profile.kind,
                    "base_url": profile.base_url,
                    "model_id": profile.model_id,
                    "capabilities": tuple(
                        sorted(
                            profile.capabilities,
                            key=lambda capability: capability.value,
                        )
                    ),
                    "fallback_profile_id": profile.fallback_profile_id,
                    "timeout_seconds": profile.timeout_seconds,
                    "enabled": profile.enabled,
                    "credential_required": profile.credential_required,
                    "credential_configured": profile.credential_configured,
                }
                for profile in self._provider_registry.list_public()
            ]
        }

    def _provider_health(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(ProviderHealthInput, request)
        return {"items": self._provider_registry.health(validated.profile_id)}

    def _transcribe_voice(self, request: BaseModel) -> Any:
        return self._voice_application.transcribe(cast(VoiceTranscribeInput, request))

    def _synthesize_voice(self, request: BaseModel) -> Any:
        return self._voice_application.synthesize(cast(VoiceSynthesizeInput, request))

    def _create_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectCreate, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
        )

    def _create_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnCreateInput, request)
        attachments = self._build_image_attachments(validated)
        try:
            if attachments:
                profile = self._provider_registry.profile(validated.profile_id)
                if ProviderCapability.VISION not in profile.capabilities:
                    raise CapabilityUnavailableError(
                        f"provider profile {profile.id!r} lacks vision capability"
                    )
                self._image_attachments.prepare(attachments)
            turn = self._assistant_ledger.create_turn(
                task_id=validated.task_id,
                profile_id=validated.profile_id,
                idempotency_key=validated.idempotency_key,
            )
            self._image_attachments.register(turn.id, attachments)
            return turn
        except BaseException:
            for attachment in attachments:
                attachment.zero()
            raise

    @staticmethod
    def _build_image_attachments(
        request: AssistantTurnCreateInput,
    ) -> tuple[ImageAttachment, ...]:
        attachments: list[ImageAttachment] = []
        try:
            for value in request.image_attachments:
                try:
                    png = base64.b64decode(value.png_base64, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise ValueError("screen attachment must use canonical base64") from error
                attachments.append(
                    ImageAttachment.create(
                        task_id=request.task_id,
                        media_type=value.media_type,
                        png=png,
                        content_hash=value.content_hash,
                        width=value.width,
                        height=value.height,
                        source_label=value.source_label,
                        captured_at_ms=value.captured_at_ms,
                        persistence=value.persistence,
                    )
                )
            return tuple(attachments)
        except BaseException:
            for attachment in attachments:
                attachment.zero()
            raise

    def _get_assistant_turn(self, request: BaseModel) -> Any:
        return self._assistant_ledger.get_turn(cast(AssistantTurnIdInput, request).turn_id)

    def _cancel_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnCancelInput, request)
        with self._turn_cancellation_lock:
            cancellation = self._turn_cancellations.get(validated.turn_id)
            was_running = cancellation is not None
            if cancellation is not None:
                cancellation.cancel()
        try:
            cancelled = self._assistant_ledger.cancel_turn(
                turn_id=validated.turn_id,
                expected_cancellation_revision=validated.expected_cancellation_revision,
            )
            self._image_attachments.release(validated.turn_id)
            return cancelled
        except InvalidTransitionError:
            if not was_running:
                raise
            persisted = self._assistant_ledger.get_turn(validated.turn_id)
            if persisted.status is not AssistantTurnStatus.CANCELLED:
                raise
            self._image_attachments.release(validated.turn_id)
            return persisted

    def _run_assistant_turn(self, request: BaseModel) -> Any:
        turn_id = cast(AssistantTurnRunInput, request).turn_id
        cancellation = CancellationToken()
        with self._turn_cancellation_lock:
            if turn_id in self._turn_cancellations:
                raise ValueError("Assistant Turn is already running")
            self._turn_cancellations[turn_id] = cancellation
        try:
            return self._assistant_application.run_turn(turn_id, cancellation)
        finally:
            with self._turn_cancellation_lock:
                self._turn_cancellations.pop(turn_id, None)

    def _retry_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnRetryInput, request)
        return self._assistant_ledger.retry_turn(
            turn_id=validated.turn_id,
            idempotency_key=validated.idempotency_key,
        )

    def _list_messages(self, request: BaseModel) -> Any:
        validated = cast(MessageListInput, request)
        return self._assistant_ledger.list_messages(
            conversation_id=validated.conversation_id,
            limit=validated.limit,
            cursor=validated.cursor,
        )

    def _import_document(self, request: BaseModel) -> Any:
        return self._documents().import_document(cast(DocumentImportInput, request))

    def _get_document(self, request: BaseModel) -> Any:
        return self._documents().get_document(cast(DocumentIdInput, request))

    def _list_documents(self, request: BaseModel) -> Any:
        return self._documents().list_documents(cast(DocumentListInput, request))

    def _search_documents(self, request: BaseModel) -> Any:
        return self._documents().search_documents(cast(DocumentSearchInput, request))

    def _delete_document(self, request: BaseModel) -> Any:
        return self._documents().delete_document(cast(DocumentDeleteInput, request))

    def _documents(self) -> DocumentApplication:
        if self._document_application is None:
            raise RuntimeError("Managed document capability is unavailable")
        return self._document_application

    def _observe_memory(self, request: BaseModel) -> Any:
        return self._memory_application.observe(cast(MemoryObserveInput, request))

    def _list_memory_observations(self, request: BaseModel) -> dict[str, Any]:
        return {
            "items": self._memory_application.list_observations(
                cast(MemoryObservationQuery, request)
            )
        }

    def _promote_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.promote_claim(cast(MemoryClaimPromoteInput, request))

    def _get_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.get_claim(cast(MemoryClaimGetInput, request))

    def _list_memory_claims(self, request: BaseModel) -> dict[str, Any]:
        return {"items": self._memory_application.list_claims(cast(MemoryClaimQuery, request))}

    def _supersede_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.supersede_claim(cast(MemoryClaimSupersedeInput, request))

    def _resolve_memory_conflict(self, request: BaseModel) -> Any:
        return self._memory_application.resolve_conflict(cast(MemoryClaimResolveInput, request))

    def _forget_memory(self, request: BaseModel) -> Any:
        return self._memory_application.forget(cast(MemoryForgetInput, request))

    def _search_memory(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MemorySearchInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            scope = self._application.scope_for_task(unit_of_work.state, task)
            hits = unit_of_work.memory_search.search(
                scope=scope,
                query=validated.query,
                generation=1,
                limit=validated.limit,
            )
        return {"items": hits}

    def _get_memory_snapshot(self, request: BaseModel) -> Any:
        validated = cast(MemorySnapshotGetInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            if task.memory_snapshot_id != validated.snapshot_id:
                raise MemoryScopeViolationError("Snapshot is not bound to the requested Task")
            snapshot = unit_of_work.snapshots.get(
                validated.snapshot_id,
                task_id=validated.task_id,
            )
            if snapshot is None:
                raise MemoryScopeViolationError("Task-bound Snapshot is unavailable")
            return snapshot

    def _memory_projection_health(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MemoryProjectionHealthInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            self._application.scope_for_task(unit_of_work.state, task)
            source_watermark_cursor = unit_of_work.commands.current_cursor()
            health = unit_of_work.memory_search.health(
                generation=1,
                source_watermark_cursor=source_watermark_cursor,
            )
        return {
            "generation": health.generation,
            "state": health.state,
            "source_watermark_cursor": health.source_watermark_cursor,
            "projected_watermark_cursor": health.projected_watermark_cursor,
            "lag": max(
                0,
                health.source_watermark_cursor - health.projected_watermark_cursor,
            ),
            "last_error_code": health.last_error_code,
            "updated_at": health.updated_at,
        }

    def _import_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectImport, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
            source=validated.source_path,
        )

    def _get_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectIdInput, request)
        return self._application.get_project(validated.project_id)

    def _list_projects(self, request: BaseModel) -> Any:
        validated = cast(ProjectListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_projects(
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _create_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationCreate, request)
        return self._application.create_conversation(
            project_id=validated.project_id,
            workspace_type=validated.workspace_type,
        )

    def _get_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationIdInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            conversation = unit_of_work.state.get_conversation(validated.conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {validated.conversation_id}")
        return conversation

    def _list_conversations(self, request: BaseModel) -> Any:
        validated = cast(ConversationListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_conversations(
                project_id=validated.project_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _create_task(self, request: BaseModel) -> Any:
        return self._application.create_task(cast(TaskCreate, request))

    def _get_task(self, request: BaseModel) -> Any:
        return self._application.get_task(cast(TaskIdInput, request).task_id)

    def _list_tasks(self, request: BaseModel) -> Any:
        validated = cast(TaskListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_tasks(
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _review_task(self, request: BaseModel) -> Any:
        return self._application.review_task(cast(TaskIdInput, request).task_id)

    def _propose_changeset(self, request: BaseModel) -> Any:
        return self._application.propose_changeset(cast(ChangesetProposal, request))

    def _decide_approval(self, request: BaseModel) -> Any:
        validated = cast(ApprovalDecisionInput, request)
        return self._application.decide_approval(
            approval_id=validated.approval_id,
            approved=validated.approved,
            decided_by=validated.decided_by,
        )

    def _get_version(self, request: BaseModel) -> Any:
        return self._application.get_version(cast(VersionIdInput, request).version_id)

    def _list_versions(self, request: BaseModel) -> Any:
        validated = cast(VersionListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_versions(
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                task_id=validated.task_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _list_approvals(self, request: BaseModel) -> Any:
        validated = cast(ApprovalListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_approvals(
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                task_id=validated.task_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _get_runtime(self, request: BaseModel) -> Any:
        validated = cast(RuntimeIdInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            runtime = unit_of_work.state.get_runtime(validated.runtime_id)
        if runtime is None:
            raise KeyError(f"Runtime not found: {validated.runtime_id}")
        return runtime

    def _runtime_health(self, request: BaseModel) -> Any:
        validated = cast(RuntimeHealthInput, request)
        return self._runtime().runtime_health(validated.task_id)

    def _start_preview(self, request: BaseModel) -> Any:
        validated = cast(PreviewStartInput, request)
        return self._runtime().start_preview(
            PreviewStartRequest(
                task_id=validated.task_id,
                idempotency_key=validated.idempotency_key,
            )
        )

    def _get_preview(self, request: BaseModel) -> Any:
        validated = cast(PreviewIdInput, request)
        return self._runtime().get_preview(validated.preview_id)

    def _resolve_preview(self, request: BaseModel) -> Any:
        validated = cast(PreviewResolveInput, request)
        return self._runtime().resolve_preview(
            PreviewResolveRequest(
                conversation_id=validated.conversation_id,
                preview_id=validated.preview_id,
            )
        )

    def _stop_preview(self, request: BaseModel) -> Any:
        validated = cast(PreviewStopInput, request)
        return self._runtime().stop_preview(
            PreviewStopRequest(
                preview_id=validated.preview_id,
                idempotency_key=validated.idempotency_key,
            )
        )

    def _list_artifacts(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(ArtifactListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            items = unit_of_work.state.artifacts_for_task(task.id)
        return {"items": items}

    def _read_artifact(self, request: BaseModel) -> Any:
        validated = cast(ArtifactIdInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            artifact = unit_of_work.state.get_artifact(validated.artifact_id)
        if artifact is None:
            raise KeyError(f"Artifact not found: {validated.artifact_id}")
        return artifact

    def _runtime(self) -> RuntimeApplication:
        if self._runtime_application is None:
            raise RuntimeExecutorError(
                "Runtime execution is unavailable",
                error_code="SANDBOX_UNAVAILABLE",
            )
        return self._runtime_application

    def _accept_version(self, request: BaseModel) -> Any:
        validated = cast(VersionAcceptInput, request)
        return self._application.accept_task_version(
            task_id=validated.task_id,
            expected_project_revision=validated.expected_project_revision,
            user_confirmed=validated.user_confirmed,
        )

    def _discard_version(self, request: BaseModel) -> Any:
        return self._application.discard_task_version(cast(TaskIdInput, request).task_id)

    def _get_capabilities(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(CapabilityRequest, request)
        return {
            "profile": validated.profile,
            "operations": self._registry.capability_manifest(
                profile=validated.profile,
                sandbox_healthy=validated.sandbox_healthy,
                overrides=validated.overrides,
            ),
            "sandbox_healthy": validated.sandbox_healthy,
            "command_metadata": self._registry.frontend_metadata(),
            "schema_version": 1,
        }

    def _subscribe_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventSubscribeInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            events = unit_of_work.commands.events_after(
                cursor=validated.cursor,
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
            )
        return {
            "items": events,
            "next_cursor": events[-1].cursor if events else validated.cursor,
        }


__all__ = ["CoreMethodNotFoundError", "CoreResponseValidationError", "CoreService"]
