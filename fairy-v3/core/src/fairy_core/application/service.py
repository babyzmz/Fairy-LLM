from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast
from uuid import UUID
from weakref import finalize

from pydantic import BaseModel, ValidationError

from fairy_core.application.ambient_dialogue_service import AmbientDialogueService
from fairy_core.application.approval_service import CoreApprovalServiceMixin
from fairy_core.application.assistant_cancellation import AssistantCancellationMixin
from fairy_core.application.cancellation_cleanup import CancellationCleanupQueue
from fairy_core.application.core import CoreApplication
from fairy_core.application.execution_helpers import (
    execution_plan_requests_preview,
)
from fairy_core.application.extension_service import ExtensionService
from fairy_core.application.history_service import history_service_handlers
from fairy_core.application.knowledge_service import knowledge_service_handlers
from fairy_core.application.model_catalog_service import ModelCatalogService
from fairy_core.application.planning_service import planning_service_handlers
from fairy_core.application.presentation_service import presentation_service_handlers
from fairy_core.application.realtime_service import RealtimeService
from fairy_core.application.recovery import close_resources, recover_interrupted_work
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.runtime_contracts import PreviewStartRequest, PreviewStopRequest
from fairy_core.application.runtime_review import RuntimeReviewApplication
from fairy_core.application.runtime_service import runtime_service_handlers
from fairy_core.application.service_endpoints import CoreServiceEndpointsMixin
from fairy_core.application.workspace_service import WorkspaceService
from fairy_core.assistant import workflow_adapter as assistant_workflow
from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.domain_commands import AssistantDomainCommands
from fairy_core.assistant.engine_version import DEFAULT_ASSISTANT_ENGINE_VERSION
from fairy_core.assistant.image_inputs import build_image_attachments
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.message_ingress import AssistantMessageIngress
from fairy_core.assistant.presentation import AssistantPresentationService
from fairy_core.assistant.schedule_service import AssistantScheduleService
from fairy_core.assistant.tools import ToolExecutor
from fairy_core.assistant.trace_models import TraceStepKind, TraceStepStatus
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.assistant.trace_service import TurnTraceService
from fairy_core.assistant.turn_scheduler import AssistantTurnScheduler
from fairy_core.assistant.turn_selection import resolve_turn_model_source
from fairy_core.browser import BrowserService, BrowserToolExecutor, browser_service_handlers
from fairy_core.browser.activity import BrowserTaskActivityProbe
from fairy_core.commanding.local_device_bus import LocalDeviceCommandBus
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import (
    ExecutionPolicyResolver,
    SandboxHealthProvider,
)
from fairy_core.contracts.knowledge import KnowledgeSyncRunInput, KnowledgeSyncStartInput
from fairy_core.contracts.method_primitives import EmptyInput
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.contracts.models import (
    AssistantTurnCreateInput,
    AssistantTurnIdInput,
    ProviderHealthInput,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
)
from fairy_core.contracts.obsidian import (
    ObsidianSourceCreateInput,
    ObsidianSourceIdInput,
    ObsidianSourceListInput,
    ObsidianSourceSyncInput,
    ObsidianVaultItemReadInput,
)
from fairy_core.contracts.voice_sessions import VoiceSessionIdInput, VoiceSessionStartInput
from fairy_core.documents.application import DocumentApplication, DocumentToolExecutor
from fairy_core.documents.ports import DocumentBlobStore, DocumentParser
from fairy_core.domain.errors import ProjectBusyError
from fairy_core.domain.execution import (
    ArtifactType,
    PreviewStatus,
    RuntimeStatus,
)
from fairy_core.domain.models import TaskStatus
from fairy_core.execution.application import (
    ProjectExecutionApplication,
    ProjectExecutionToolExecutor,
)
from fairy_core.execution.plans import TaskStep, TaskStepKind, TaskStepStatus
from fairy_core.knowledge.application import ProjectKnowledgeApplication
from fairy_core.knowledge.scheduler import KnowledgeSyncScheduler
from fairy_core.knowledge.sync import ObsidianKnowledgeSync
from fairy_core.knowledge.tools import KnowledgeToolExecutor
from fairy_core.mcp.application import McpApplication
from fairy_core.mcp.tools import McpToolExecutor
from fairy_core.media.composition import build_media_composition
from fairy_core.media.ports import MediaProvider
from fairy_core.media.staging import MediaStagingStore
from fairy_core.media.tools import MediaToolExecutor
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.retention import MemoryRetentionCoordinator
from fairy_core.memory.tools import MemoryToolExecutor
from fairy_core.model_catalog.ports import ModelCatalogSource
from fairy_core.obsidian import ObsidianConnector
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.persona import (
    AmbientDialogueGenerator,
    FairyDialogueDirector,
    load_default_dialogue_catalog,
    load_default_persona_authority,
)
from fairy_core.presentation.packs import RendererPackInstaller
from fairy_core.providers import ProviderRegistry
from fairy_core.realtime.tools import RealtimeAssistanceToolExecutor
from fairy_core.research.application import ResearchApplication, ResearchToolExecutor
from fairy_core.research.ports import FetchPort
from fairy_core.runtime.pool_scheduler import PreviewIdleScheduler
from fairy_core.runtime.review import RuntimeEvidenceStore, RuntimeReviewer
from fairy_core.runtime.templates import (
    RuntimeAdapter,
    RuntimeTemplateError,
    select_runtime_template,
)
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
from fairy_core.workflow.scheduler import WorkflowAdapterRegistry, WorkflowScheduler
from fairy_core.workspace.ports import WorkspaceProvisioner
from fairy_core.workspace.tools import ProjectToolExecutor

_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.READY,
        TaskStatus.ACCEPTED,
        TaskStatus.REJECTED,
        TaskStatus.ARCHIVED,
        TaskStatus.FAILED,
    }
)

_STOPPABLE_PREVIEW_STATUSES = frozenset(
    {PreviewStatus.READY, PreviewStatus.STOPPING, PreviewStatus.INTERRUPTED}
)

_ACTIVE_RUNTIME_STATUSES = frozenset(
    {
        RuntimeStatus.CREATED,
        RuntimeStatus.STARTING,
        RuntimeStatus.RUNNING,
        RuntimeStatus.STOPPING,
        RuntimeStatus.INTERRUPTED,
    }
)


class CoreMethodNotFoundError(LookupError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Core method not found: {method}")


class CoreResponseValidationError(RuntimeError):
    def __init__(self, method: str, error: ValidationError) -> None:
        self.method = method
        self.validation_error = error
        super().__init__(f"Core method returned an invalid response: {method}")


class CoreService(AssistantCancellationMixin, CoreServiceEndpointsMixin, CoreApprovalServiceMixin):
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
        obsidian_connector: ObsidianConnector | None = None,
        browser_service: BrowserService | None = None,
        default_execution_target: str = "local",
        assistant_workflow_engine_version: int = DEFAULT_ASSISTANT_ENGINE_VERSION,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if default_execution_target not in {"local", "cloud"}:
            raise ValueError("default_execution_target must be local or cloud")
        self._application = application
        self._workspace_service = WorkspaceService(application.workspace_access)
        self._unit_of_work_factory = unit_of_work_factory
        self.ledger_signal = getattr(unit_of_work_factory, "ledger_signal", None)
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
        self._application.history.configure_attachment_store(self._image_attachments)
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
        self._memory_retention = MemoryRetentionCoordinator(unit_of_work_factory)
        persona_authority = load_default_persona_authority()
        self._ambient_dialogue = AmbientDialogueService(
            FairyDialogueDirector(
                catalog=load_default_dialogue_catalog(),
                persona=persona_authority,
            ),
            AmbientDialogueGenerator(
                providers=self._provider_registry,
                command_bus=LocalDeviceCommandBus(registry),
                persona=persona_authority,
            ),
        )
        self._assistant_ledger = AssistantLedgerApplication(
            workflow_engine_version=assistant_workflow_engine_version,
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
            registry=registry,
            execution_policy=self._execution_policy,
            execution_target=default_execution_target,
        )
        self._turn_trace_service = TurnTraceService(unit_of_work_factory)
        self._turn_trace_runtime = TurnTraceRuntime(unit_of_work_factory)
        self._browser_service = browser_service
        if browser_service is not None:
            browser_service.configure_activity_probe(BrowserTaskActivityProbe(unit_of_work_factory))
        self._execution_planning = application.execution_planning
        self._workflow_adapters = WorkflowAdapterRegistry()
        self._workflow_scheduler = WorkflowScheduler(
            unit_of_work_factory=unit_of_work_factory,
            adapters=self._workflow_adapters,
            autostart=False,
        )
        media = build_media_composition(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
            registry=registry,
            execution_policy=self._execution_policy,
            provider=media_provider,
            staging=media_staging_store,
            workspaces=workspace_provisioner,
            workflow_scheduler=self._workflow_scheduler,
            workflow_adapters=self._workflow_adapters,
        )
        self._media_provider = media.provider
        self._media_application = media.application
        self._media_scheduler = media.scheduler
        self._media_service = media.service
        selected_obsidian = obsidian_connector or ObsidianConnector()
        self._obsidian_knowledge = ObsidianKnowledgeSync(
            connector=selected_obsidian,
            unit_of_work_factory=unit_of_work_factory,
        )
        self._knowledge_sync_scheduler = KnowledgeSyncScheduler(
            application=self._obsidian_knowledge,
            unit_of_work_factory=unit_of_work_factory,
            workflow_scheduler=self._workflow_scheduler,
            adapters=self._workflow_adapters,
        )
        effective_tool_executor = tool_executor
        if self._browser_service is not None:
            effective_tool_executor = BrowserToolExecutor(
                service=self._browser_service,
                delegate=effective_tool_executor,
            )
        effective_tool_executor = KnowledgeToolExecutor(
            unit_of_work_factory=unit_of_work_factory,
            delegate=effective_tool_executor,
        )
        effective_tool_executor = MemoryToolExecutor(
            application=self._memory_application,
            delegate=effective_tool_executor,
        )
        if research_fetch_port is not None:
            effective_tool_executor = ResearchToolExecutor(
                application=ResearchApplication(
                    unit_of_work_factory=unit_of_work_factory,
                    scope_resolver=application.scope_for_task,
                    fetch_port=research_fetch_port,
                ),
                delegate=effective_tool_executor,
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
                scheduler=media.scheduler,
                delegate=effective_tool_executor,
            )
        effective_tool_executor = RealtimeAssistanceToolExecutor(
            unit_of_work_factory=unit_of_work_factory,
            delegate=effective_tool_executor,
        )
        self._tool_executor = effective_tool_executor
        self._cancellation_cleanup = CancellationCleanupQueue()
        self._assistant_application = AssistantApplication(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
            registry=registry,
            providers=self._provider_registry,
            image_attachments=self._image_attachments,
            tool_executor=effective_tool_executor,
            execution_policy=self._execution_policy,
            execution_completion_hook=self._finalize_assistant_execution,
        )
        failure_projection = assistant_workflow.register_assistant_workflow_adapter(
            self._workflow_adapters,
            unit_of_work_factory=unit_of_work_factory,
            application=self._assistant_application,
            ledger=self._assistant_ledger,
        )
        failure_projection.recover(unit_of_work_factory)
        self._workflow_scheduler.start()
        self._assistant_scheduler = AssistantTurnScheduler(
            ledger=self._assistant_ledger,
            workflow_scheduler=self._workflow_scheduler,
        )
        self._assistant_schedule_service = AssistantScheduleService(
            application=application,
            ledger=self._assistant_ledger,
            scheduler=self._assistant_scheduler,
            unit_of_work_factory=unit_of_work_factory,
            providers=self._provider_registry,
        )
        self._realtime_service = RealtimeService(
            unit_of_work_factory,
            persona_authority=persona_authority,
            scratch_conversation_factory=(application.create_scratch_conversation_in_unit_of_work),
            scratch_conversation_cleanup=application.purge_scratch_conversation,
            task_factory=application.create_task,
            turn_factory=self._create_assistant_turn,
            turn_starter=self._assistant_scheduler.start,
            turn_canceller=lambda turn_id, revision: self._cancel_assistant_turn_by_id(
                turn_id,
                expected_cancellation_revision=revision,
            ),
            selection_provider=self._model_catalog_service.selection_preference,
        )
        self._finalizer = finalize(self, on_close) if on_close is not None else None
        self._message_ingress = AssistantMessageIngress(
            application=application,
            scheduler=self._assistant_scheduler,
            providers=self._provider_registry,
            unit_of_work_factory=unit_of_work_factory,
            turn_factory=self._create_assistant_turn,
            turn_canceller=self._cancel_assistant_turn,
        )
        self._domain_commands = AssistantDomainCommands(
            application=application,
            units=unit_of_work_factory,
            commands=lambda: self._get_capabilities(EmptyInput())["slash_commands"],
            cancel=self._cancel_assistant_turn,
        )
        self._assistant_presentation = AssistantPresentationService(unit_of_work_factory)
        self._preview_idle_scheduler = (
            PreviewIdleScheduler(runtime_application) if runtime_application is not None else None
        )
        self._handlers: Mapping[str, Callable[[BaseModel], Any]] = {
            "ambient.dialogue.evaluate": self._ambient_dialogue.evaluate,
            "assistant.messages.submit": self._message_ingress.submit,
            "assistant.messages.cancel": self._message_ingress.cancel,
            "assistant.commands.dispatch": self._domain_commands.dispatch,
            "assistant.conversations.presentation.get": self._assistant_presentation.get,
            "approvals.decide": self._decide_approval,
            "approvals.list": self._list_approvals,
            "artifacts.list": self._list_artifacts,
            "artifacts.read": self._read_artifact,
            **self._assistant_schedule_service.handlers,
            "assistant.turns.cancel": self._cancel_assistant_turn,
            "assistant.turns.create": self._create_assistant_turn,
            "assistant.turns.get": self._get_assistant_turn,
            "assistant.turns.retry": self._retry_assistant_turn,
            "assistant.turns.run": self._run_assistant_turn,
            "assistant.turns.start": self._start_assistant_turn,
            **self._assistant_workflow_handlers(),
            **self._turn_trace_service.handlers,
            "capabilities.get": self._get_capabilities,
            "changesets.propose": self._propose_changeset,
            **history_service_handlers(
                application=application,
                unit_of_work_factory=unit_of_work_factory,
                cancel_project_activity=self._cancel_project_activity,
            ),
            **knowledge_service_handlers(ProjectKnowledgeApplication(unit_of_work_factory)),
            **browser_service_handlers(self._browser_service),
            "obsidian.health.get": lambda _request: selected_obsidian.health(),
            "obsidian.sources.create": lambda request: self._obsidian_knowledge.create_source(
                cast(ObsidianSourceCreateInput, request)
            ),
            "obsidian.sources.items.list": lambda request: selected_obsidian.list_items(
                cast(ObsidianSourceIdInput, request).source_id
            ),
            "obsidian.sources.items.read": lambda request: selected_obsidian.read_item(
                cast(ObsidianVaultItemReadInput, request)
            ),
            "obsidian.sources.list": lambda request: selected_obsidian.list_sources(
                cast(ObsidianSourceListInput, request)
            ),
            "obsidian.sync.start": self._sync_obsidian_compatibility,
            "knowledge.sync.start": lambda request: self._knowledge_sync_scheduler.start(
                cast(KnowledgeSyncStartInput, request)
            ),
            "knowledge.sync.get": lambda request: self._knowledge_sync_scheduler.get(
                cast(KnowledgeSyncRunInput, request).run_id
            ),
            "knowledge.sync.cancel": lambda request: self._knowledge_sync_scheduler.cancel(
                cast(KnowledgeSyncRunInput, request).run_id
            ),
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
            "memory.proposals.accept": self._accept_memory_proposal,
            "memory.proposals.list": self._list_memory_proposals,
            "memory.proposals.reject": self._reject_memory_proposal,
            "memory.search": self._search_memory,
            "memory.settings.get": self._get_memory_settings,
            "memory.settings.update": self._update_memory_settings,
            "memory.snapshots.get": self._get_memory_snapshot,
            **self._media_service.handlers,
            "media.jobs.list": self._list_media_jobs,
            "messages.list": self._list_messages,
            **self._model_catalog_service.handlers,
            **runtime_service_handlers(
                self._runtime,
                self._unit_of_work_factory,
                on_activation=(
                    self._preview_idle_scheduler.wake
                    if self._preview_idle_scheduler is not None
                    else None
                ),
            ),
            "permissions.get": self._get_permissions,
            "permissions.update": self._update_permissions,
            "providers.health": self._provider_health,
            "providers.list": self._list_providers,
            **self._realtime_service.handlers,
            "system.actions.execute": self._execute_system_action,
            "tasks.create": self._create_task,
            "tasks.get": self._get_task,
            "tasks.review": self._review_task,
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
        self._assistant_schedule_service.start()

    def close(self) -> None:
        self._assistant_schedule_service.close()
        self._cancellation_cleanup.close()
        if self._preview_idle_scheduler is not None:
            self._preview_idle_scheduler.close()
        self._knowledge_sync_scheduler.close()
        self._assistant_scheduler.close()
        self._workflow_scheduler.close()
        if self._media_scheduler is not None:
            self._media_scheduler.close()
        close_resources(
            self._browser_service,
            self._image_attachments,
            self._model_catalog_service,
            self._provider_registry,
            self._voice_registry,
            self._tool_executor,
            self._media_provider,
        )
        if self._finalizer is not None:
            self._finalizer()

    def _sync_obsidian_compatibility(
        self,
        request: BaseModel,
    ) -> Any:
        parsed = cast(ObsidianSourceSyncInput, request)
        run = self._knowledge_sync_scheduler.run(
            KnowledgeSyncStartInput(
                source_id=parsed.source_id,
                expected_revision=parsed.expected_revision,
                idempotency_key=(f"obsidian-sync:{parsed.source_id}:{parsed.expected_revision}"),
            )
        )
        return self._obsidian_knowledge.result(run)

    def recover_interrupted_work(
        self,
        *,
        verify_running_previews: bool = False,
    ) -> dict[str, int]:
        self._memory_retention.run_if_due(force=True)
        result = recover_interrupted_work(
            assistant=self._assistant_ledger,
            runtime=self._runtime_application,
            media=self._media_scheduler,
            verify_running_previews=verify_running_previews,
        )
        self._knowledge_sync_scheduler.recover_interrupted()
        for turn_id in assistant_workflow.resumable_assistant_turn_ids(self._assistant_ledger):
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

    def _create_assistant_turn(self, request: BaseModel) -> Any:
        self._memory_retention.run_if_due()
        self._extension_service.refresh_registry()
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

    def _cancel_project_activity(self, project_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            tasks = []
            cursor: str | None = None
            while True:
                page = unit_of_work.state.list_tasks(
                    project_id=project_id,
                    conversation_id=None,
                    limit=100,
                    cursor=cursor,
                )
                tasks.extend(page.items)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            active_tasks = tuple(
                task for task in tasks if task.status not in _TERMINAL_TASK_STATUSES
            )
            previews = tuple(
                preview
                for task in tasks
                if (
                    preview := unit_of_work.state.preview_for_task(
                        task.id,
                        include_terminal=True,
                    )
                )
                is not None
                and preview.status in _STOPPABLE_PREVIEW_STATUSES
            )
            turns = unit_of_work.assistant.nonterminal_turns_for_tasks(
                tuple(task.id for task in tasks)
            )

        for preview in previews:
            self._runtime().stop_preview(
                PreviewStopRequest(
                    preview_id=preview.id,
                    task_id=preview.task_id,
                    idempotency_key=f"project-delete:{project_id}:preview:{preview.id}",
                )
            )
        for turn in turns:
            self._cancel_assistant_turn_by_id(
                turn.id,
                expected_cancellation_revision=turn.cancellation_revision,
                strict_tool_cancellation=True,
            )

        with self._unit_of_work_factory() as unit_of_work:
            changed = False
            for original in active_tasks:
                task = unit_of_work.state.get_task(original.id)
                if task is None or task.status in _TERMINAL_TASK_STATUSES:
                    continue
                if task.status is TaskStatus.CREATED:
                    task.transition_to(TaskStatus.RESOLVING_SCOPE)
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
                changed = True
            if changed:
                unit_of_work.commit()

        with self._unit_of_work_factory() as unit_of_work:
            for task in tasks:
                if any(
                    runtime.status in _ACTIVE_RUNTIME_STATUSES
                    for runtime in unit_of_work.state.runtimes_for_task(task.id)
                ):
                    raise ProjectBusyError("Project Runtime did not stop")

    def _finalize_assistant_execution(self, turn_id: UUID) -> str | None:
        """Advance durable file work through validation and Preview before final prose."""
        from fairy_core.assistant.execution_intent_policy import readonly_intent_issue
        from fairy_core.execution.plan_revisions import obsolete_file_plan

        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                return "The Assistant Turn is unavailable for execution finalization."
            plan = unit_of_work.state.execution_plan_for_task(turn.task_id)
            if plan is None:
                return None
            if (
                plan.status.value not in {"active", "paused"}
                or obsolete_file_plan(unit_of_work, turn.task_id, plan)
                or readonly_intent_issue(
                    unit_of_work.assistant.get_execution_intent(turn.id),
                    self._registry.get("run.sandboxed"),
                )
            ):
                return None
            task = unit_of_work.state.get_task(turn.task_id)
            if task is None or task.target_version_id is None or task.workspace_id is None:
                return "The planned Task no longer has a writable Workspace Version."
            steps = unit_of_work.state.task_steps_for_plan(plan.id)
            implementation = [step for step in steps if step.kind is TaskStepKind.IMPLEMENT]
            if any(
                step.status not in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
                for step in implementation
            ):
                return None
            scope = self._application.scope_for_task(unit_of_work.state, task)
            workspace = unit_of_work.state.get_workspace(task.workspace_id)
            if workspace is None:
                return "The planned Task Workspace is unavailable."
            preview = unit_of_work.state.preview_for_task(task.id, include_terminal=True)
            manifest = dict(plan.manifest)

        try:
            template = select_runtime_template(
                scope.project_root,
                execution_target=scope.execution_target,
            )
        except RuntimeTemplateError as error:
            if execution_plan_requests_preview(manifest):
                return (
                    "The generated files do not form a runnable Preview "
                    f"({error.error_code}). Create the complete planned entrypoint before "
                    "returning a final response."
                )
            for kind in (TaskStepKind.INSTALL, TaskStepKind.TEST):
                step = next(item for item in steps if item.kind is kind)
                if step.status is TaskStepStatus.PENDING:
                    self._application.execution_planning.skip_step(task.id, kind)
            self._application.execution_planning.skip_step(
                task.id,
                TaskStepKind.PREVIEW,
            )
            return self._finish_successful_execution_steps(
                task.id,
                checkpoint=task.project_id is None,
            )

        prerequisite_issue = self._prepare_preview_prerequisites(
            task.id,
            steps=steps,
            adapter=template.adapter,
            validation_commands=manifest.get("validation_commands"),
        )
        if prerequisite_issue is not None:
            return prerequisite_issue

        if preview is not None and preview.status is PreviewStatus.READY:
            if preview.version_id != task.target_version_id:
                return "The ready Preview is bound to a stale Workspace Version."
            self._complete_execution_step(task.id, TaskStepKind.PREVIEW)
            completion_issue = self._finish_successful_execution_steps(
                task.id,
                checkpoint=task.project_id is None,
            )
            if completion_issue is not None:
                return completion_issue
            self._append_preview_verification(turn_id, preview.id)
            return None

        if self._runtime_application is None:
            return "Preview runtime is unavailable (SANDBOX_UNAVAILABLE)."

        self._application.execution_planning.start_step(task.id, TaskStepKind.PREVIEW)
        try:
            context = self._runtime_application.start_preview(
                PreviewStartRequest(
                    task_id=task.id,
                    workspace_id=task.workspace_id,
                    version_id=task.target_version_id,
                    expected_workspace_revision=workspace.revision,
                    idempotency_key=(f"assistant:preview:{task.id}:{task.target_version_id}"),
                )
            )
        except Exception as error:
            error_code = str(
                getattr(
                    error,
                    "error_code",
                    getattr(error, "code", "WORKER_INTERRUPTED"),
                )
            )
            self._application.execution_planning.fail_step(
                task.id,
                TaskStepKind.PREVIEW,
                error_code=error_code,
            )
            return (
                f"Preview validation failed ({error_code}). Do not claim the generated "
                "Workspace is complete."
            )
        if context.preview.status is not PreviewStatus.READY:
            self._application.execution_planning.fail_step(
                task.id,
                TaskStepKind.PREVIEW,
                error_code="WORKER_INTERRUPTED",
            )
            return "Preview did not reach a durable ready state (WORKER_INTERRUPTED)."

        self._application.execution_planning.complete_step(task.id, TaskStepKind.PREVIEW)
        completion_issue = self._finish_successful_execution_steps(
            task.id,
            checkpoint=task.project_id is None,
        )
        if completion_issue is not None:
            return completion_issue
        self._append_preview_verification(turn_id, context.preview.id)
        return None

    def _prepare_preview_prerequisites(
        self,
        task_id: UUID,
        *,
        steps: Sequence[TaskStep],
        adapter: RuntimeAdapter,
        validation_commands: object,
    ) -> str | None:
        by_kind = {step.kind: step for step in steps}
        install = by_kind[TaskStepKind.INSTALL]
        test = by_kind[TaskStepKind.TEST]
        if adapter is RuntimeAdapter.STATIC:
            for kind, step in (
                (TaskStepKind.INSTALL, install),
                (TaskStepKind.TEST, test),
            ):
                if step.status is TaskStepStatus.PENDING:
                    self._application.execution_planning.skip_step(task_id, kind)
                elif step.status not in {
                    TaskStepStatus.COMPLETED,
                    TaskStepStatus.SKIPPED,
                }:
                    return f"The {kind.value} step has not reached a terminal success state."
            return None

        if install.status is not TaskStepStatus.COMPLETED:
            return (
                "Install the locked project dependencies with deps.install before returning "
                "a final response."
            )
        commands = validation_commands if isinstance(validation_commands, list) else []
        if commands and test.status is not TaskStepStatus.COMPLETED:
            return "Run the planned validation suite before starting Preview."
        if not commands and test.status is TaskStepStatus.PENDING:
            self._application.execution_planning.skip_step(task_id, TaskStepKind.TEST)
        return None

    def _finish_successful_execution_steps(
        self,
        task_id: UUID,
        *,
        checkpoint: bool,
    ) -> str | None:
        self._application.execution_planning.skip_step(task_id, TaskStepKind.REPAIR)
        self._complete_execution_step(task_id, TaskStepKind.SUMMARY)
        if checkpoint:
            try:
                self._application.checkpoint_scratch_task(task_id)
            except Exception as error:
                error_code = str(
                    getattr(
                        error,
                        "error_code",
                        getattr(error, "code", "WORKER_INTERRUPTED"),
                    )
                )
                return (
                    f"Workspace checkpoint failed ({error_code}). Do not claim the candidate "
                    "Version is saved or complete."
                )
            self._complete_execution_step(task_id, TaskStepKind.CHECKPOINT)
        return None

    def _complete_execution_step(self, task_id: UUID, kind: TaskStepKind) -> None:
        step = self._application.execution_planning.start_step(task_id, kind)
        if step is not None and step.status is not TaskStepStatus.COMPLETED:
            self._application.execution_planning.complete_step(task_id, kind)

    def _append_preview_verification(self, turn_id: UUID, preview_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                return
            manifest = next(
                (
                    artifact
                    for artifact in reversed(unit_of_work.state.artifacts_for_task(turn.task_id))
                    if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
                    and artifact.metadata.get("preview_id") == str(preview_id)
                ),
                None,
            )
            if manifest is None:
                return
            raw_run_id = manifest.metadata.get("command_run_id")
            try:
                run_id = UUID(str(raw_run_id))
            except (TypeError, ValueError):
                return
            run = unit_of_work.commands.get_run(run_id)
            existing = (
                unit_of_work.assistant.find_trace_step_by_command_run_id(
                    run_id,
                    kind=TraceStepKind.VERIFICATION,
                )
                if run is not None
                else None
            )
        if run is None or existing is not None:
            return
        self._turn_trace_runtime.append_step(
            turn_id=turn_id,
            run=run,
            kind=TraceStepKind.VERIFICATION,
            status=TraceStepStatus.SUCCEEDED,
            public_summary="Preview ready",
            public_detail="Generated files were verified in the bound Workspace Version.",
            artifact_refs=(manifest.id,),
        )


__all__ = ["CoreMethodNotFoundError", "CoreResponseValidationError", "CoreService"]
