from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import UUID
from weakref import finalize

from pydantic import BaseModel, ValidationError

from fairy_core.application.core import CoreApplication
from fairy_core.application.extension_service import ExtensionService
from fairy_core.application.model_catalog_service import ModelCatalogService
from fairy_core.application.planning_service import planning_service_handlers
from fairy_core.application.presentation_service import presentation_service_handlers
from fairy_core.application.recovery import close_resources, recover_interrupted_work
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.runtime_review import RuntimeReviewApplication
from fairy_core.application.runtime_service import runtime_service_handlers
from fairy_core.application.workspace_service import WorkspaceService
from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.image_inputs import build_image_attachments
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.tools import ToolExecutor
from fairy_core.assistant.trace_service import TurnTraceService
from fairy_core.assistant.turn_scheduler import AssistantTurnScheduler
from fairy_core.assistant.turn_selection import resolve_turn_model_source
from fairy_core.commanding import EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import (
    ExecutionPolicyResolver,
    SandboxHealthProvider,
)
from fairy_core.contracts.approvals import ApprovalDecisionInput, ApprovalListInput
from fairy_core.contracts.history import (
    ConversationDeleteInput,
    ConversationMoveToProjectInput,
    ConversationUpdateInput,
    TaskArchiveInput,
    TaskMetadataUpdateInput,
)
from fairy_core.contracts.media import MediaJobListInput
from fairy_core.contracts.methods import CORE_METHODS, EventListInput, EventSubscribeInput
from fairy_core.contracts.models import (
    ArtifactIdInput,
    ArtifactListInput,
    AssistantTurnCancelInput,
    AssistantTurnCreateInput,
    AssistantTurnIdInput,
    AssistantTurnRetryInput,
    AssistantTurnRunInput,
    AssistantTurnStartInput,
    ChangesetProposal,
    ConversationCreate,
    ConversationIdInput,
    ConversationListInput,
    DocumentDeleteInput,
    DocumentIdInput,
    DocumentImportInput,
    DocumentListInput,
    DocumentSearchInput,
    ExecutionSettingsUpdateInput,
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
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    ProjectListInput,
    ProviderHealthInput,
    TaskCreate,
    TaskIdInput,
    TaskListInput,
    VersionAcceptInput,
    VersionIdInput,
    VersionListInput,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
)
from fairy_core.contracts.voice_sessions import VoiceSessionIdInput, VoiceSessionStartInput
from fairy_core.contracts.workspaces import WorkspaceFileMutateInput
from fairy_core.documents.application import DocumentApplication, DocumentToolExecutor
from fairy_core.documents.ports import DocumentBlobStore, DocumentParser
from fairy_core.domain.errors import (
    InvalidTransitionError,
    MemoryScopeViolationError,
)
from fairy_core.domain.execution import Approval, ApprovalDecision
from fairy_core.execution.application import (
    ProjectExecutionApplication,
    ProjectExecutionToolExecutor,
)
from fairy_core.mcp.application import McpApplication
from fairy_core.mcp.tools import McpToolExecutor
from fairy_core.media.composition import build_media_composition
from fairy_core.media.ports import MediaProvider
from fairy_core.media.service import media_job_model
from fairy_core.media.staging import MediaStagingStore
from fairy_core.media.tools import MediaToolExecutor
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.model_catalog.ports import ModelCatalogSource
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.packs import RendererPackInstaller
from fairy_core.providers import ProviderRegistry
from fairy_core.research.application import ResearchApplication, ResearchToolExecutor
from fairy_core.research.ports import FetchPort
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.review import RuntimeEvidenceStore, RuntimeReviewer
from fairy_core.sandbox.archive import WorkspaceArchiveBuilder
from fairy_core.sandbox.ports import SandboxExecutor
from fairy_core.sandbox.tools import SandboxToolExecutor
from fairy_core.skills.manager import SkillManager
from fairy_core.skills.registry import SkillRegistry
from fairy_core.skills.tools import SkillToolExecutor
from fairy_core.system_actions.application import (
    SystemActionApplication,
    SystemActionToolExecutor,
    SystemActionUnavailableError,
    SystemActionWorker,
)
from fairy_core.system_actions.models import SystemActionRequest
from fairy_core.voice.application import VoiceApplication
from fairy_core.voice.registry import VoiceRegistry
from fairy_core.workspace.ports import WorkspaceProvisioner
from fairy_core.workspace.tools import ProjectToolExecutor


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
        runtime_reviewer: RuntimeReviewer | None = None,
        runtime_evidence_store: RuntimeEvidenceStore | None = None,
        system_action_worker: SystemActionWorker | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        sandbox_health_provider: SandboxHealthProvider | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_manager: SkillManager | None = None,
        mcp_application: McpApplication | None = None,
        renderer_pack_installer: RendererPackInstaller | None = None,
        model_catalog_source: ModelCatalogSource | None = None,
        media_provider: MediaProvider | None = None,
        media_staging_store: MediaStagingStore | None = None,
        workspace_provisioner: WorkspaceProvisioner | None = None,
        default_execution_target: str = "local",
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if default_execution_target not in {"local", "cloud"}:
            raise ValueError("default_execution_target must be local or cloud")
        self._application = application
        self._workspace_service = WorkspaceService(application.workspace_access)
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._skill_registry = skill_registry or SkillRegistry(registry)
        self._skill_manager = skill_manager
        self._mcp_application = mcp_application
        self._provider_registry = provider_registry or ProviderRegistry()
        self._model_catalog_service = ModelCatalogService(
            unit_of_work_factory=unit_of_work_factory,
            provider_registry=self._provider_registry,
            source=model_catalog_source,
        )
        self._voice_registry = voice_registry or VoiceRegistry()
        self._voice_application = VoiceApplication(
            unit_of_work_factory=unit_of_work_factory,
            registry=self._voice_registry,
        )
        self._image_attachments = image_attachment_store or ImageAttachmentStore()
        self._runtime_application = runtime_application
        self._default_execution_target = default_execution_target
        self._execution_policy = ExecutionPolicyResolver(sandbox_health_provider)
        self._extension_service = ExtensionService(
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            execution_policy=self._execution_policy,
            default_execution_target=default_execution_target,
            skill_registry=self._skill_registry,
            skill_manager=self._skill_manager,
            mcp_application=self._mcp_application,
        )
        if (runtime_reviewer is None) != (runtime_evidence_store is None):
            raise ValueError("Runtime reviewer and evidence store must be configured together")
        self._runtime_review_application = (
            RuntimeReviewApplication(
                unit_of_work_factory=unit_of_work_factory,
                reviewer=runtime_reviewer,
                evidence_store=runtime_evidence_store,
                registry=registry,
                policy=PolicyEngine(registry),
                scope_resolver=application.scope_for_task,
                execution_policy=self._execution_policy,
            )
            if runtime_reviewer is not None and runtime_evidence_store is not None
            else None
        )
        self._project_execution_application = (
            ProjectExecutionApplication(
                unit_of_work_factory=unit_of_work_factory,
                sandbox_executor=sandbox_executor,
                registry=registry,
                policy=PolicyEngine(registry),
                execution_policy=self._execution_policy,
                scope_resolver=application.scope_for_task,
                planning=application.execution_planning,
            )
            if sandbox_executor is not None
            else None
        )
        self._system_action_application = (
            SystemActionApplication(
                unit_of_work_factory=unit_of_work_factory,
                registry=registry,
                command_policy=PolicyEngine(registry),
                execution_policy=self._execution_policy,
                scope_resolver=application.scope_for_task,
                worker=system_action_worker,
            )
            if system_action_worker is not None
            else None
        )
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
        self._turn_trace_service = TurnTraceService(unit_of_work_factory)
        self._execution_planning = application.execution_planning
        media = build_media_composition(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
            registry=registry,
            execution_policy=self._execution_policy,
            provider=media_provider,
            staging=media_staging_store,
            workspaces=workspace_provisioner,
        )
        self._media_provider = media.provider
        self._media_application = media.application
        self._media_service = media.service
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
        if system_action_worker is not None:
            effective_tool_executor = SystemActionToolExecutor(
                worker=system_action_worker,
                delegate=effective_tool_executor,
            )
        effective_tool_executor = ProjectToolExecutor(
            application=application,
            unit_of_work_factory=unit_of_work_factory,
            delegate=effective_tool_executor,
        )
        if self._project_execution_application is not None:
            effective_tool_executor = ProjectExecutionToolExecutor(
                application=self._project_execution_application,
                delegate=effective_tool_executor,
            )
        if sandbox_executor is not None:
            effective_tool_executor = SandboxToolExecutor(
                executor=sandbox_executor,
                archive_builder=WorkspaceArchiveBuilder(unit_of_work_factory),
                execution_target=default_execution_target,
                delegate=effective_tool_executor,
            )
        effective_tool_executor = SkillToolExecutor(
            skills=self._skill_registry,
            unit_of_work_factory=unit_of_work_factory,
            delegate=effective_tool_executor,
        )
        if self._mcp_application is not None:
            effective_tool_executor = McpToolExecutor(
                application=self._mcp_application,
                unit_of_work_factory=unit_of_work_factory,
                delegate=effective_tool_executor,
            )
        if self._media_application is not None:
            effective_tool_executor = MediaToolExecutor(
                application=self._media_application,
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
            execution_policy=self._execution_policy,
        )
        self._assistant_scheduler = AssistantTurnScheduler(
            application=self._assistant_application,
            ledger=self._assistant_ledger,
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
            "assistant.turns.start": self._start_assistant_turn,
            **self._turn_trace_service.handlers,
            "capabilities.get": self._get_capabilities,
            "changesets.propose": self._propose_changeset,
            "conversations.create": self._create_conversation,
            "conversations.delete": self._delete_conversation,
            "conversations.get": self._get_conversation,
            "conversations.list": self._list_conversations,
            "conversations.move_to_project": self._move_conversation_to_project,
            "conversations.update": self._update_conversation,
            "documents.delete": self._delete_document,
            "documents.get": self._get_document,
            "documents.import": self._import_document,
            "documents.list": self._list_documents,
            "documents.search": self._search_documents,
            "events.subscribe": self._subscribe_events,
            "events.list": self._list_events,
            "events.state": self._event_stream_state,
            **self._extension_service.handlers,
            **planning_service_handlers(self._execution_planning),
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
            **self._media_service.handlers,
            "media.jobs.list": self._list_media_jobs,
            "messages.list": self._list_messages,
            **self._model_catalog_service.handlers,
            "projects.create": self._create_project,
            "projects.get": self._get_project,
            "projects.import": self._import_project,
            "projects.list": self._list_projects,
            **runtime_service_handlers(self._runtime, self._unit_of_work_factory),
            "permissions.get": self._get_permissions,
            "permissions.update": self._update_permissions,
            "providers.health": self._provider_health,
            "providers.list": self._list_providers,
            "system.actions.execute": self._execute_system_action,
            "tasks.archive": self._archive_task,
            "tasks.create": self._create_task,
            "tasks.get": self._get_task,
            "tasks.list": self._list_tasks,
            "tasks.review": self._review_task,
            "tasks.update_metadata": self._update_task_metadata,
            "versions.accept": self._accept_version,
            "versions.discard": self._discard_version,
            "versions.get": self._get_version,
            "versions.list": self._list_versions,
            "workspaces.files.list": self._workspace_service.list_files,
            "workspaces.files.mutate": self._mutate_workspace_files,
            "workspaces.files.read": self._workspace_service.read_file,
            "files.open_stream": self._workspace_service.open_stream,
            "files.probe": self._workspace_service.probe_file,
            **presentation_service_handlers(
                unit_of_work_factory=unit_of_work_factory,
                workspaces=application.workspace_access,
                renderer_pack_installer=renderer_pack_installer,
                mutate_workspace_files=application.workspace_mutations.mutate,
            ),
            "file_sets.resolve": self._workspace_service.resolve_file_set,
            "file_sets.get": self._workspace_service.get_file_set,
            "workspaces.export": self._workspace_service.export,
            "workspaces.get": self._workspace_service.get,
            "voice.synthesize": self._synthesize_voice,
            "voice.sessions.cancel": self._cancel_voice_session,
            "voice.sessions.get": self._get_voice_session,
            "voice.sessions.start": self._start_voice_session,
            "voice.transcribe": self._transcribe_voice,
        }
        if self._handlers.keys() != CORE_METHODS.keys():
            raise RuntimeError("Core service handlers do not match the public method catalog")

    def close(self) -> None:
        self._assistant_scheduler.close()
        close_resources(
            self._image_attachments,
            self._model_catalog_service,
            self._provider_registry,
            self._voice_registry,
            self._tool_executor,
            self._media_provider,
        )
        if self._finalizer is not None:
            self._finalizer()

    def recover_interrupted_work(
        self,
        *,
        verify_running_previews: bool = False,
    ) -> dict[str, int]:
        result = recover_interrupted_work(
            assistant=self._assistant_ledger,
            runtime=self._runtime_application,
            media=self._media_application,
            verify_running_previews=verify_running_previews,
        )
        for turn_id in self._assistant_ledger.resumable_waiting_turn_ids():
            self._assistant_scheduler.start(turn_id)
        return result

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

    def _start_voice_session(self, request: BaseModel) -> Any:
        return self._voice_application.start_session(cast(VoiceSessionStartInput, request))

    def _get_voice_session(self, request: BaseModel) -> Any:
        return self._voice_application.get_session(cast(VoiceSessionIdInput, request))

    def _cancel_voice_session(self, request: BaseModel) -> Any:
        return self._voice_application.cancel_session(cast(VoiceSessionIdInput, request))

    def _execute_system_action(self, request: BaseModel) -> Any:
        if self._system_action_application is None:
            raise SystemActionUnavailableError("Typed system actions are unavailable")
        return self._system_action_application.execute(cast(SystemActionRequest, request))

    def _create_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectCreate, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
        )

    def _create_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnCreateInput, request)
        attachments = build_image_attachments(
            validated.task_id,
            validated.image_attachments,
        )
        try:
            requested = validated.model_selection
            profile_id, model_selection = resolve_turn_model_source(
                requested_mode=requested.mode if requested is not None else None,
                requested_model_id=requested.model_id if requested is not None else None,
                requested_revision=requested.revision if requested is not None else None,
                legacy_profile_id=validated.profile_id,
                has_attachments=bool(attachments),
                unit_of_work_factory=self._unit_of_work_factory,
                providers=self._provider_registry,
            )
            if attachments:
                self._image_attachments.prepare(attachments)
            turn = self._assistant_ledger.create_turn(
                task_id=validated.task_id,
                profile_id=profile_id,
                idempotency_key=validated.idempotency_key,
                model_selection=model_selection,
            )
            self._image_attachments.register(turn.id, attachments)
            return turn
        except BaseException:
            for attachment in attachments:
                attachment.zero()
            raise

    def _get_assistant_turn(self, request: BaseModel) -> Any:
        return self._assistant_ledger.get_turn(cast(AssistantTurnIdInput, request).turn_id)

    def _cancel_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnCancelInput, request)
        was_running = self._assistant_scheduler.cancel(validated.turn_id)
        if was_running:
            self._cancel_running_tool_command(validated.turn_id)
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

    def _cancel_running_tool_command(self, turn_id: UUID) -> None:
        cancel_command = getattr(self._tool_executor, "cancel_command", None)
        if not callable(cancel_command):
            return
        with self._unit_of_work_factory() as unit_of_work:
            runs = tuple(
                run
                for invocation in unit_of_work.assistant.list_tool_invocations(turn_id)
                if invocation.status is ToolInvocationStatus.RUNNING
                and invocation.command_run_id is not None
                if (run := unit_of_work.commands.get_run(invocation.command_run_id)) is not None
            )
        for run in runs:
            try:
                cancel_command(run)
            except Exception:
                continue

    def _run_assistant_turn(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        turn_id = cast(AssistantTurnRunInput, request).turn_id
        return self._assistant_scheduler.run(turn_id)

    def _start_assistant_turn(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        turn_id = cast(AssistantTurnStartInput, request).turn_id
        return self._assistant_scheduler.start(turn_id)

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

    def _update_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationUpdateInput, request)
        return self._application.history.update_conversation(
            conversation_id=validated.conversation_id,
            title=validated.title,
            pinned=validated.pinned,
            expected_revision=validated.expected_revision,
        )

    def _delete_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationDeleteInput, request)
        return self._application.history.delete_conversation(
            conversation_id=validated.conversation_id,
            expected_revision=validated.expected_revision,
            user_confirmed=validated.user_confirmed,
        )

    def _list_conversations(self, request: BaseModel) -> Any:
        validated = cast(ConversationListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_conversations(
                project_id=validated.project_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _move_conversation_to_project(self, request: BaseModel) -> Any:
        validated = cast(ConversationMoveToProjectInput, request)
        return self._application.history.move_to_project(
            conversation_id=validated.conversation_id,
            target_project_id=validated.target_project_id,
            expected_revision=validated.expected_revision,
            user_confirmed=validated.user_confirmed,
            idempotency_key=validated.idempotency_key,
        )

    def _create_task(self, request: BaseModel) -> Any:
        return self._application.create_task(cast(TaskCreate, request))

    def _archive_task(self, request: BaseModel) -> Any:
        validated = cast(TaskArchiveInput, request)
        return self._application.history.archive_task(
            task_id=validated.task_id,
            expected_revision=validated.expected_revision,
        )

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

    def _update_task_metadata(self, request: BaseModel) -> Any:
        validated = cast(TaskMetadataUpdateInput, request)
        return self._application.history.update_task(
            task_id=validated.task_id,
            display_title=validated.display_title,
            pinned=validated.pinned,
            expected_revision=validated.expected_revision,
        )

    def _review_task(self, request: BaseModel) -> Any:
        task_id = cast(TaskIdInput, request).task_id
        if self._project_execution_application is not None:
            self._project_execution_application.run_review_suite(task_id)
        if self._runtime_review_application is not None:
            with self._unit_of_work_factory() as unit_of_work:
                preview = unit_of_work.state.preview_for_task(
                    task_id,
                    include_terminal=True,
                )
            if preview is not None and preview.status.value == "ready":
                self._runtime_review_application.review_task(task_id)
        return self._application.review_task(task_id)

    def _propose_changeset(self, request: BaseModel) -> Any:
        return self._application.propose_changeset(cast(ChangesetProposal, request))

    def _mutate_workspace_files(self, request: BaseModel) -> Any:
        context = self._application.workspace_mutations.mutate(
            cast(WorkspaceFileMutateInput, request)
        )
        return {
            "workspace": context.workspace,
            "conversation": context.conversation,
            "task": context.task,
            "target_version": context.target_version,
            "changeset": context.changeset,
            "approval": context.approval,
        }

    def _assistant_turn_id_for_approval(self, approval: Approval) -> UUID | None:
        if approval.changeset_id is not None:
            return None
        with self._unit_of_work_factory() as unit_of_work:
            if approval.tool_invocation_id is not None:
                invocation = unit_of_work.assistant.get_tool_invocation(approval.tool_invocation_id)
                if (
                    invocation is None
                    or invocation.command_run_id != approval.command_run_id
                    or invocation.task_id != approval.task_id
                ):
                    raise InvalidTransitionError(
                        "approval does not match its Assistant Tool Invocation"
                    )
                turn = unit_of_work.assistant.get_turn(invocation.turn_id)
                if turn is None or turn.task_id != approval.task_id:
                    raise InvalidTransitionError(
                        "approval Tool Invocation does not match its Assistant Turn"
                    )
                return turn.id

            command = unit_of_work.commands.get_run(approval.command_run_id)
            if command is None:
                raise KeyError(f"command run not found: {approval.command_run_id}")
            if command.command_name != "model.generate.expensive":
                return None
            raw_turn_id = command.input_payload.get("turn_id")
            try:
                turn_id = UUID(str(raw_turn_id))
            except (TypeError, ValueError) as error:
                raise InvalidTransitionError(
                    "model budget approval has no valid Assistant Turn"
                ) from error
            turn = unit_of_work.assistant.get_turn(turn_id)
            if (
                turn is None
                or turn.task_id != approval.task_id
                or turn.budget_approval_run_id != command.id
            ):
                raise InvalidTransitionError(
                    "model budget approval does not match its Assistant Turn"
                )
            return turn.id

    def _decide_approval(self, request: BaseModel) -> Any:
        validated = cast(ApprovalDecisionInput, request)
        approval = self._application.get_approval(validated.approval_id)
        was_pending = approval.decision is ApprovalDecision.PENDING
        assistant_turn_id = self._assistant_turn_id_for_approval(approval)
        changeset = None
        if approval.changeset_id is not None:
            changeset = self._application.decide_approval(
                approval_id=validated.approval_id,
                approved=validated.approved,
                decided_by="user",
            )
        else:
            self._application.record_approval_decision(
                approval_id=validated.approval_id,
                approved=validated.approved,
                decided_by="user",
            )
        decided = self._application.get_approval(validated.approval_id)
        if assistant_turn_id is not None:
            self._assistant_scheduler.start(
                assistant_turn_id,
                restart_if_running=was_pending,
            )
        return {
            "approval": decided,
            "changeset": changeset,
        }

    def _get_version(self, request: BaseModel) -> Any:
        return self._application.get_version(cast(VersionIdInput, request).version_id)

    def _list_versions(self, request: BaseModel) -> Any:
        validated = cast(VersionListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_versions(
                workspace_id=validated.workspace_id,
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

    def _list_artifacts(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(ArtifactListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            items = unit_of_work.state.artifacts_for_task(task.id)
        return {"items": items}

    def _list_media_jobs(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MediaJobListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            items = [media_job_model(job) for job in unit_of_work.state.list_media_jobs(task.id)]
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

    def _get_capabilities(self, _request: BaseModel) -> dict[str, Any]:
        self._extension_service.refresh_registry()
        with self._unit_of_work_factory() as unit_of_work:
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=self._default_execution_target,
            )
        operations = self._registry.capability_manifest(
            profile=policy.profile,
            sandbox_healthy=policy.sandbox_healthy,
            overrides=dict(policy.capability_overrides),
        )
        return {
            "profile": policy.profile,
            "operations": operations,
            "sandbox_healthy": policy.sandbox_healthy,
            "command_metadata": self._registry.frontend_metadata(),
            "slash_commands": self._registry.slash_command_metadata(operations),
            "schema_version": 3,
        }

    def _get_permissions(self, _request: BaseModel) -> Any:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.execution_settings.get()

    def _update_permissions(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        validated = cast(ExecutionSettingsUpdateInput, request)
        unknown = sorted(
            name for name in validated.capability_overrides if self._registry.get(name) is None
        )
        if unknown:
            raise ValueError(f"unknown capability override: {', '.join(unknown)}")
        with self._unit_of_work_factory() as unit_of_work:
            changed = unit_of_work.execution_settings.update(
                profile=validated.profile,
                capability_overrides=validated.capability_overrides,
                expected_revision=validated.expected_revision,
                idempotency_key=validated.idempotency_key,
            )
            unit_of_work.commit()
        return changed

    def _subscribe_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventSubscribeInput, request)
        return self._event_page(cursor=validated.cursor, limit=500)

    def _list_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventListInput, request)
        return self._event_page(cursor=validated.cursor, limit=validated.limit)

    def _event_page(self, *, cursor: int, limit: int) -> dict[str, Any]:
        with self._unit_of_work_factory() as unit_of_work:
            events = unit_of_work.commands.events_after(
                cursor=cursor,
                limit=limit,
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
            )
        return {
            "items": events,
            "next_cursor": events[-1].cursor if events else cursor,
        }

    def _event_stream_state(self, _request: BaseModel) -> Any:
        with self._unit_of_work_factory() as unit_of_work:
            state = unit_of_work.commands.stream_state(
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER}
            )
            unit_of_work.commit()
        return state


__all__ = ["CoreMethodNotFoundError", "CoreResponseValidationError", "CoreService"]
