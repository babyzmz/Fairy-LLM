import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Annotated, Any
from uuid import UUID

from fairy_core.application.service import (
    CoreMethodNotFoundError,
    CoreResponseValidationError,
    CoreService,
)
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.contracts.models import (
    ApprovalDecisionInput,
    ApprovalListInput,
    ApprovalPageModel,
    ArtifactListInput,
    ArtifactModel,
    ArtifactPageModel,
    AssistantTurnCancelInput,
    AssistantTurnCreateInput,
    AssistantTurnModel,
    AssistantTurnRetryInput,
    AssistantTurnRunInput,
    CapabilityManifestModel,
    ChangesetModel,
    ChangesetProposal,
    CheckpointModel,
    ConversationCreate,
    ConversationListInput,
    ConversationModel,
    ConversationPageModel,
    DocumentContextModel,
    DocumentDeleteInput,
    DocumentImportInput,
    DocumentListInput,
    DocumentPageModel,
    DocumentSearchInput,
    DocumentSearchPageModel,
    ErrorCode,
    ExecutionSettingsModel,
    ExecutionSettingsUpdateInput,
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
    PreviewModel,
    PreviewResolutionModel,
    PreviewResolveInput,
    PreviewStartInput,
    PreviewStopInput,
    ProjectContextModel,
    ProjectCreate,
    ProjectImport,
    ProjectListInput,
    ProjectModel,
    ProjectPageModel,
    ProviderHealthInput,
    ProviderHealthPageModel,
    ProviderProfilePageModel,
    RuntimeHealthInput,
    RuntimeHealthModel,
    RuntimeModel,
    TaskContextModel,
    TaskCreate,
    TaskListInput,
    TaskModel,
    TaskPageModel,
    VersionAcceptInput,
    VersionListInput,
    VersionModel,
    VersionPageModel,
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.domain.errors import DomainError, IdempotencyConflictError, VersionConflictError
from fairy_core.memory.models import MemoryNamespace
from fairy_core.system_actions.models import SystemActionExecution, SystemActionRequest
from fairy_core.workspace.worker_transport import WorkerRpcError
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import Lifespan

from fairy_cloud.auth import AuthenticationError, DenyAllAuthenticator
from fairy_cloud.auth.models import Authenticator, RequestIdentity
from fairy_cloud.storage.objects import (
    ImmutableObjectConflict,
    ObjectIntegrityError,
    S3ObjectStore,
)
from fairy_cloud.sync.contracts import (
    SyncEventBatch,
    SyncProjectRegistration,
    VersionManifestInput,
)
from fairy_cloud.sync.models import SyncedEvent
from fairy_cloud.sync.ports import SyncStore

_MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
EVENT_POLL_SECONDS = 0.025
PUBLIC_ERROR_STATUS = {
    ErrorCode.PATH_OUT_OF_SCOPE.value: 403,
    ErrorCode.PATH_IDENTITY_CHANGED.value: 409,
    ErrorCode.SCOPE_MISMATCH.value: 409,
    ErrorCode.APPROVAL_REQUIRED.value: 409,
    ErrorCode.SANDBOX_UNAVAILABLE.value: 503,
    ErrorCode.VERSION_CONFLICT.value: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT.value: 409,
    ErrorCode.SECRET_EGRESS_BLOCKED.value: 403,
    ErrorCode.CAPABILITY_NOT_AVAILABLE.value: 503,
    ErrorCode.WORKER_INTERRUPTED.value: 503,
    ErrorCode.MEMORY_SCOPE_VIOLATION.value: 409,
    ErrorCode.MEMORY_CONFLICT.value: 409,
    ErrorCode.MEMORY_INJECTION_BLOCKED.value: 403,
    ErrorCode.MEMORY_SECRET_BLOCKED.value: 403,
    ErrorCode.MEMORY_PROJECTION_STALE.value: 503,
    ErrorCode.MEMORY_SNAPSHOT_TOO_LARGE.value: 413,
    ErrorCode.MEMORY_FORGOTTEN.value: 410,
    ErrorCode.DOCUMENT_PROJECTION_STALE.value: 503,
    ErrorCode.DOCUMENT_INTEGRITY_FAILED.value: 409,
    "INVALID_STATE_TRANSITION": 409,
}


def create_cloud_app(
    service: CoreService,
    *,
    authenticator: Authenticator | None = None,
    sync_store: SyncStore | None = None,
    object_store: S3ObjectStore | None = None,
    max_snapshot_bytes: int = _MAX_SNAPSHOT_BYTES,
    event_poll_seconds: float = EVENT_POLL_SECONDS,
    readiness: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    service_resolver: Callable[[RequestIdentity], CoreService] | None = None,
) -> FastAPI:
    if not 0 < event_poll_seconds <= 0.1:
        raise ValueError("event_poll_seconds must be within the 100 ms delivery budget")
    app = FastAPI(title="Fairy Cloud API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(
        _request: Request,
        error: IdempotencyConflictError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": str(error),
                }
            },
        )

    protected = APIRouter(prefix="/v1")
    token_authenticator = authenticator or DenyAllAuthenticator()
    bearer = HTTPBearer(auto_error=False)
    request_identity: ContextVar[RequestIdentity | None] = ContextVar(
        "fairy_cloud_request_identity",
        default=None,
    )

    async def require_identity(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer),
        ],
        device_id: Annotated[str | None, Header(alias="X-Fairy-Device-ID")] = None,
    ) -> AsyncIterator[None]:
        authorization = None
        if credentials is not None:
            authorization = f"{credentials.scheme} {credentials.credentials}"
        try:
            identity = await token_authenticator.authenticate(
                authorization=authorization,
                device_id=device_id,
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=401,
                detail={"code": error.code, "message": str(error)},
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        request.state.identity = identity
        context_token = request_identity.set(identity)
        try:
            yield
        finally:
            request_identity.reset(context_token)

    protected.dependencies.append(Depends(require_identity))

    def active_service() -> CoreService:
        active_identity = request_identity.get()
        return (
            service_resolver(active_identity)
            if service_resolver is not None and active_identity is not None
            else service
        )

    def invoke(method: str, params: dict[str, Any]) -> Any:
        try:
            return active_service().invoke(method, params)
        except Exception as error:
            raise _core_http_exception(error) from error

    async def invoke_async(method: str, params: dict[str, Any]) -> Any:
        selected = active_service()
        try:
            return await run_in_threadpool(selected.invoke, method, params)
        except Exception as error:
            raise _core_http_exception(error) from error

    def identity_for(request: Request) -> RequestIdentity:
        identity = getattr(request.state, "identity", None)
        if not isinstance(identity, RequestIdentity):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED"})
        return identity

    def configured_sync_store() -> SyncStore:
        if sync_store is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "SYNC_UNAVAILABLE", "message": "cloud sync is unavailable"},
            )
        return sync_store

    def configured_object_store() -> S3ObjectStore:
        if object_store is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "SYNC_UNAVAILABLE", "message": "object storage is unavailable"},
            )
        return object_store

    def require_idempotency_match(body_key: str, header_key: str) -> None:
        if body_key != header_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SCOPE_MISMATCH",
                    "message": "Idempotency-Key does not match Core params",
                },
            )

    @app.get("/v1/health", operation_id="health", response_model=HealthModel)
    def health() -> dict[str, Any]:
        return invoke("health", {})

    @app.get("/v1/ready", operation_id="cloud.ready")
    async def ready() -> dict[str, Any]:
        if readiness is None:
            return {"status": "ready"}
        return await readiness()

    @protected.get(
        "/projects",
        operation_id="projects.list",
        response_model=ProjectPageModel,
    )
    def list_projects(request: Annotated[ProjectListInput, Query()]) -> dict[str, Any]:
        return invoke("projects.list", request.model_dump(mode="json", exclude_none=True))

    @protected.post(
        "/projects",
        operation_id="projects.create",
        response_model=ProjectContextModel,
    )
    async def create_project(body: ProjectCreate, request: Request) -> dict[str, Any]:
        result = await invoke_async("projects.create", body.model_dump(mode="json"))
        if sync_store is not None:
            identity = identity_for(request)
            await sync_store.register_project(
                project_id=str(result["project"]["id"]),
                user_id=identity.user_id,
                active_version_id=result["project"].get("active_version_id"),
            )
        return result

    @protected.post(
        "/projects/import",
        operation_id="projects.import",
        response_model=ProjectContextModel,
    )
    async def import_project(body: ProjectImport, request: Request) -> dict[str, Any]:
        result = await invoke_async("projects.import", body.model_dump(mode="json"))
        if sync_store is not None:
            identity = identity_for(request)
            await sync_store.register_project(
                project_id=str(result["project"]["id"]),
                user_id=identity.user_id,
                active_version_id=result["project"].get("active_version_id"),
            )
        return result

    @protected.get(
        "/projects/{project_id}",
        operation_id="projects.get",
        response_model=ProjectModel,
    )
    def get_project(project_id: UUID) -> dict[str, Any]:
        return invoke("projects.get", {"project_id": str(project_id)})

    @protected.get(
        "/conversations",
        operation_id="conversations.list",
        response_model=ConversationPageModel,
    )
    def list_conversations(
        request: Annotated[ConversationListInput, Query()],
    ) -> dict[str, Any]:
        return invoke(
            "conversations.list",
            request.model_dump(mode="json", exclude_none=True),
        )

    @protected.post(
        "/conversations",
        operation_id="conversations.create",
        response_model=ConversationModel,
    )
    def create_conversation(request: ConversationCreate) -> dict[str, Any]:
        return invoke("conversations.create", request.model_dump(mode="json"))

    @protected.get(
        "/conversations/{conversation_id}",
        operation_id="conversations.get",
        response_model=ConversationModel,
    )
    def get_conversation(conversation_id: UUID) -> dict[str, Any]:
        return invoke("conversations.get", {"conversation_id": str(conversation_id)})

    @protected.get(
        "/messages",
        operation_id="messages.list",
        response_model=MessagePageModel,
    )
    def list_messages(request: Annotated[MessageListInput, Query()]) -> dict[str, Any]:
        return invoke(
            "messages.list",
            request.model_dump(mode="json", exclude_none=True),
        )

    @protected.post(
        "/documents/import",
        operation_id="documents.import",
        response_model=DocumentContextModel,
    )
    async def import_document(
        request: DocumentImportInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return await invoke_async("documents.import", request.model_dump(mode="json"))

    @protected.get(
        "/documents",
        operation_id="documents.list",
        response_model=DocumentPageModel,
    )
    def list_documents(
        request: Annotated[DocumentListInput, Query()],
    ) -> dict[str, Any]:
        return invoke("documents.list", request.model_dump(mode="json"))

    @protected.get(
        "/documents/{document_id}",
        operation_id="documents.get",
        response_model=DocumentContextModel,
    )
    def get_document(document_id: UUID, task_id: UUID) -> dict[str, Any]:
        return invoke(
            "documents.get",
            {"task_id": str(task_id), "document_id": str(document_id)},
        )

    @protected.post(
        "/documents/search",
        operation_id="documents.search",
        response_model=DocumentSearchPageModel,
    )
    def search_documents(request: DocumentSearchInput) -> dict[str, Any]:
        return invoke("documents.search", request.model_dump(mode="json"))

    @protected.post(
        "/documents/{document_id}/delete",
        operation_id="documents.delete",
        response_model=DocumentContextModel,
    )
    async def delete_document(
        document_id: UUID,
        request: DocumentDeleteInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        if request.document_id != document_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "document id mismatch"},
            )
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return await invoke_async("documents.delete", request.model_dump(mode="json"))

    @protected.post(
        "/assistant/turns",
        operation_id="assistant.turns.create",
        response_model=AssistantTurnModel,
    )
    def create_assistant_turn(
        request: AssistantTurnCreateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return invoke("assistant.turns.create", request.model_dump(mode="json"))

    @protected.get(
        "/assistant/turns/{turn_id}",
        operation_id="assistant.turns.get",
        response_model=AssistantTurnModel,
    )
    def get_assistant_turn(turn_id: UUID) -> dict[str, Any]:
        return invoke("assistant.turns.get", {"turn_id": str(turn_id)})

    @protected.post(
        "/assistant/turns/{turn_id}/cancel",
        operation_id="assistant.turns.cancel",
        response_model=AssistantTurnModel,
    )
    def cancel_assistant_turn(
        turn_id: UUID,
        request: AssistantTurnCancelInput,
    ) -> dict[str, Any]:
        if request.turn_id != turn_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "turn id mismatch"},
            )
        return invoke("assistant.turns.cancel", request.model_dump(mode="json"))

    @protected.post(
        "/assistant/turns/{turn_id}/run",
        operation_id="assistant.turns.run",
        response_model=AssistantTurnModel,
    )
    async def run_assistant_turn(
        turn_id: UUID,
        request: AssistantTurnRunInput,
    ) -> dict[str, Any]:
        if request.turn_id != turn_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "turn id mismatch"},
            )
        return await invoke_async(
            "assistant.turns.run",
            request.model_dump(mode="json"),
        )

    @protected.post(
        "/assistant/turns/{turn_id}/retry",
        operation_id="assistant.turns.retry",
        response_model=AssistantTurnModel,
    )
    def retry_assistant_turn(
        turn_id: UUID,
        request: AssistantTurnRetryInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        if request.turn_id != turn_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "turn id mismatch"},
            )
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return invoke("assistant.turns.retry", request.model_dump(mode="json"))

    @protected.get(
        "/tasks",
        operation_id="tasks.list",
        response_model=TaskPageModel,
    )
    def list_tasks(request: Annotated[TaskListInput, Query()]) -> dict[str, Any]:
        return invoke("tasks.list", request.model_dump(mode="json", exclude_none=True))

    @protected.post("/tasks", operation_id="tasks.create", response_model=TaskContextModel)
    def create_task(request: TaskCreate) -> dict[str, Any]:
        return invoke("tasks.create", request.model_dump(mode="json"))

    @protected.get("/tasks/{task_id}", operation_id="tasks.get", response_model=TaskModel)
    def get_task(task_id: UUID) -> dict[str, Any]:
        return invoke("tasks.get", {"task_id": str(task_id)})

    @protected.get(
        "/approvals",
        operation_id="approvals.list",
        response_model=ApprovalPageModel,
    )
    def list_approvals(request: Annotated[ApprovalListInput, Query()]) -> dict[str, Any]:
        return invoke("approvals.list", request.model_dump(mode="json", exclude_none=True))

    @protected.post(
        "/changesets",
        operation_id="changesets.propose",
        response_model=PendingChangesetModel,
    )
    def propose_changeset(request: ChangesetProposal) -> dict[str, Any]:
        return invoke("changesets.propose", request.model_dump(mode="json"))

    @protected.post(
        "/approvals/{approval_id}/decision",
        operation_id="approvals.decide",
        response_model=ChangesetModel,
    )
    def decide_approval(
        approval_id: UUID,
        request: ApprovalDecisionInput,
    ) -> dict[str, Any]:
        if request.approval_id != approval_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "approval id mismatch"},
            )
        return invoke("approvals.decide", request.model_dump(mode="json"))

    @protected.post(
        "/tasks/{task_id}/review",
        operation_id="tasks.review",
        response_model=CheckpointModel,
    )
    def review_task(task_id: UUID) -> dict[str, Any]:
        return invoke("tasks.review", {"task_id": str(task_id)})

    @protected.post(
        "/tasks/{task_id}/accept-version",
        operation_id="versions.accept",
        response_model=ProjectModel,
    )
    def accept_version(task_id: UUID, request: VersionAcceptInput) -> dict[str, Any]:
        if request.task_id != task_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "task id mismatch"},
            )
        return invoke("versions.accept", request.model_dump(mode="json"))

    @protected.delete(
        "/tasks/{task_id}/version",
        operation_id="versions.discard",
        response_model=TaskModel,
    )
    def discard_version(task_id: UUID) -> dict[str, Any]:
        return invoke("versions.discard", {"task_id": str(task_id)})

    @protected.get(
        "/versions",
        operation_id="versions.list",
        response_model=VersionPageModel,
    )
    def list_versions(request: Annotated[VersionListInput, Query()]) -> dict[str, Any]:
        return invoke("versions.list", request.model_dump(mode="json", exclude_none=True))

    @protected.get(
        "/versions/{version_id}",
        operation_id="versions.get",
        response_model=VersionModel,
    )
    def get_version(version_id: UUID) -> dict[str, Any]:
        return invoke("versions.get", {"version_id": str(version_id)})

    @protected.get(
        "/runtimes/health",
        operation_id="runtimes.health",
        response_model=RuntimeHealthModel,
    )
    def runtime_health(request: Annotated[RuntimeHealthInput, Query()]) -> dict[str, Any]:
        return invoke("runtimes.health", request.model_dump(mode="json"))

    @protected.get(
        "/runtimes/{runtime_id}",
        operation_id="runtimes.get",
        response_model=RuntimeModel,
    )
    def get_runtime(runtime_id: UUID) -> dict[str, Any]:
        return invoke("runtimes.get", {"runtime_id": str(runtime_id)})

    @protected.post(
        "/previews/start",
        operation_id="previews.start",
        response_model=PreviewContextModel,
    )
    def start_preview(
        request: PreviewStartInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
    ) -> dict[str, Any]:
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return invoke("previews.start", request.model_dump(mode="json"))

    @protected.get(
        "/previews/resolve",
        operation_id="previews.resolve",
        response_model=PreviewResolutionModel,
    )
    def resolve_preview(
        request: Annotated[PreviewResolveInput, Query()],
    ) -> dict[str, Any] | None:
        return invoke(
            "previews.resolve",
            request.model_dump(mode="json", exclude_none=True),
        )

    @protected.get(
        "/previews/{preview_id}",
        operation_id="previews.get",
        response_model=PreviewContextModel,
    )
    def get_preview(preview_id: UUID) -> dict[str, Any]:
        return invoke("previews.get", {"preview_id": str(preview_id)})

    @protected.post(
        "/previews/{preview_id}/stop",
        operation_id="previews.stop",
        response_model=PreviewModel,
    )
    def stop_preview(
        preview_id: UUID,
        request: PreviewStopInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
    ) -> dict[str, Any]:
        if request.preview_id != preview_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "preview id mismatch"},
            )
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return invoke("previews.stop", request.model_dump(mode="json"))

    @protected.get(
        "/artifacts",
        operation_id="artifacts.list",
        response_model=ArtifactPageModel,
    )
    def list_artifacts(request: Annotated[ArtifactListInput, Query()]) -> dict[str, Any]:
        return invoke("artifacts.list", request.model_dump(mode="json"))

    @protected.get(
        "/artifacts/{artifact_id}",
        operation_id="artifacts.read",
        response_model=ArtifactModel,
    )
    def read_artifact(artifact_id: UUID) -> dict[str, Any]:
        return invoke("artifacts.read", {"artifact_id": str(artifact_id)})

    @protected.get(
        "/capabilities",
        operation_id="capabilities.get",
        response_model=CapabilityManifestModel,
    )
    def capabilities() -> dict[str, Any]:
        return invoke("capabilities.get", {})

    @protected.get(
        "/permissions",
        operation_id="permissions.get",
        response_model=ExecutionSettingsModel,
    )
    def get_permissions() -> dict[str, Any]:
        return invoke("permissions.get", {})

    @protected.put(
        "/permissions",
        operation_id="permissions.update",
        response_model=ExecutionSettingsModel,
    )
    def update_permissions(
        request: ExecutionSettingsUpdateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        require_idempotency_match(request.idempotency_key, idempotency_key)
        return invoke("permissions.update", request.model_dump(mode="json"))

    @protected.get(
        "/providers",
        operation_id="providers.list",
        response_model=ProviderProfilePageModel,
    )
    def list_providers() -> dict[str, Any]:
        return invoke("providers.list", {})

    @protected.get(
        "/providers/health",
        operation_id="providers.health",
        response_model=ProviderHealthPageModel,
    )
    def provider_health(
        request: Annotated[ProviderHealthInput, Query()],
    ) -> dict[str, Any]:
        return invoke(
            "providers.health",
            request.model_dump(mode="json", exclude_none=True),
        )

    @protected.post(
        "/system/actions",
        operation_id="system.actions.execute",
        response_model=SystemActionExecution,
    )
    def execute_system_action(request: SystemActionRequest) -> dict[str, Any]:
        return invoke("system.actions.execute", request.model_dump(mode="json"))

    @protected.post(
        "/voice/transcriptions",
        operation_id="voice.transcribe",
        response_model=VoiceTranscriptModel,
    )
    async def transcribe_voice(request: VoiceTranscribeInput) -> dict[str, Any]:
        return await invoke_async("voice.transcribe", request.model_dump(mode="json"))

    @protected.post(
        "/voice/speech",
        operation_id="voice.synthesize",
        response_model=VoiceAudioModel,
    )
    async def synthesize_voice(request: VoiceSynthesizeInput) -> dict[str, Any]:
        return await invoke_async("voice.synthesize", request.model_dump(mode="json"))

    @protected.post(
        "/memory/observations",
        operation_id="memory.observations.create",
        response_model=MemoryObservationModel,
    )
    def create_memory_observation(request: MemoryObserveInput) -> dict[str, Any]:
        return invoke("memory.observations.create", request.model_dump(mode="json"))

    @protected.get(
        "/memory/observations",
        operation_id="memory.observations.list",
        response_model=MemoryObservationPageModel,
    )
    def list_memory_observations(
        task_id: UUID,
        namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        request = MemoryObservationQuery(task_id=task_id, namespace=namespace)
        return invoke("memory.observations.list", request.model_dump(mode="json"))

    @protected.post(
        "/memory/claims/promote",
        operation_id="memory.claims.promote",
        response_model=MemoryClaimContextModel,
    )
    def promote_memory_claim(request: MemoryClaimPromoteInput) -> dict[str, Any]:
        return invoke("memory.claims.promote", request.model_dump(mode="json"))

    @protected.get(
        "/memory/claims",
        operation_id="memory.claims.list",
        response_model=MemoryClaimPageModel,
    )
    def list_memory_claims(
        task_id: UUID,
        namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        request = MemoryClaimQuery(task_id=task_id, namespace=namespace)
        return invoke("memory.claims.list", request.model_dump(mode="json"))

    @protected.get(
        "/memory/claims/{claim_id}",
        operation_id="memory.claims.get",
        response_model=MemoryClaimContextModel,
    )
    def get_memory_claim(claim_id: UUID, task_id: UUID) -> dict[str, Any]:
        request = MemoryClaimGetInput(task_id=task_id, claim_id=claim_id)
        return invoke("memory.claims.get", request.model_dump(mode="json"))

    @protected.post(
        "/memory/claims/{claim_id}/supersede",
        operation_id="memory.claims.supersede",
        response_model=MemoryClaimContextModel,
    )
    def supersede_memory_claim(
        claim_id: UUID,
        request: MemoryClaimSupersedeInput,
    ) -> dict[str, Any]:
        if request.claim_id != claim_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "claim id mismatch"},
            )
        return invoke("memory.claims.supersede", request.model_dump(mode="json"))

    @protected.post(
        "/memory/claims/{claim_id}/resolve-conflict",
        operation_id="memory.claims.resolve_conflict",
        response_model=MemoryClaimContextModel,
    )
    def resolve_memory_conflict(
        claim_id: UUID,
        request: MemoryClaimResolveInput,
    ) -> dict[str, Any]:
        if request.claim_id != claim_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "claim id mismatch"},
            )
        return invoke(
            "memory.claims.resolve_conflict",
            request.model_dump(mode="json"),
        )

    @protected.post(
        "/memory/forget",
        operation_id="memory.forget",
        response_model=MemoryTombstoneModel,
    )
    def forget_memory(request: MemoryForgetInput) -> dict[str, Any]:
        return invoke("memory.forget", request.model_dump(mode="json"))

    @protected.get(
        "/memory/search",
        operation_id="memory.search",
        response_model=MemorySearchPageModel,
    )
    def search_memory(
        request: Annotated[MemorySearchInput, Query()],
    ) -> dict[str, Any]:
        return invoke("memory.search", request.model_dump(mode="json"))

    @protected.get(
        "/memory/snapshots/{snapshot_id}",
        operation_id="memory.snapshots.get",
        response_model=MemorySnapshotModel,
    )
    def get_memory_snapshot(
        snapshot_id: UUID,
        request: Annotated[MemoryProjectionHealthInput, Query()],
    ) -> dict[str, Any]:
        payload = MemorySnapshotGetInput(
            task_id=request.task_id,
            snapshot_id=snapshot_id,
        )
        return invoke("memory.snapshots.get", payload.model_dump(mode="json"))

    @protected.get(
        "/memory/projection/health",
        operation_id="memory.projection.health",
        response_model=MemoryProjectionHealthModel,
    )
    def get_memory_projection_health(
        request: Annotated[MemoryProjectionHealthInput, Query()],
    ) -> dict[str, Any]:
        return invoke("memory.projection.health", request.model_dump(mode="json"))

    @protected.post("/sync/projects", operation_id="sync.projects.register")
    async def register_synced_project(
        body: SyncProjectRegistration,
        request: Request,
    ) -> dict[str, Any]:
        identity = identity_for(request)
        state = await configured_sync_store().register_project(
            project_id=str(body.project_id),
            user_id=identity.user_id,
            active_version_id=(
                str(body.active_version_id) if body.active_version_id is not None else None
            ),
        )
        return {
            "project_id": state.project_id,
            "revision": state.revision,
            "active_version_id": state.active_version_id,
        }

    @protected.post("/sync/events", operation_id="sync.events.upload")
    async def upload_sync_events(body: SyncEventBatch, request: Request) -> dict[str, Any]:
        identity = identity_for(request)
        store = configured_sync_store()
        accepted: list[dict[str, Any]] = []
        for event in body.items:
            cursor = await store.append_event(
                event_id=str(event.id),
                run_id=str(event.run_id) if event.run_id is not None else None,
                user_id=identity.user_id,
                device_id=identity.device_id,
                project_id=str(event.project_id) if event.project_id is not None else None,
                conversation_id=str(event.conversation_id),
                task_id=str(event.task_id),
                version_id=str(event.version_id) if event.version_id is not None else None,
                task_sequence=event.task_sequence,
                schema_version=event.schema_version,
                event_type=event.event_type,
                visibility=event.visibility.value,
                message=event.message,
                payload=event.model_dump(mode="json"),
            )
            accepted.append({"event_id": str(event.id), "cursor": cursor})
        return {
            "accepted": accepted,
            "next_cursor": max(item["cursor"] for item in accepted),
        }

    @protected.put(
        "/sync/projects/{project_id}/versions/{version_id}/snapshot",
        operation_id="sync.versions.uploadSnapshot",
    )
    async def upload_version_snapshot(
        project_id: UUID,
        version_id: UUID,
        request: Request,
    ) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/zstd":
            raise HTTPException(
                status_code=415,
                detail={"code": "INVALID_PARAMS", "message": "snapshot must be application/zstd"},
            )
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > max_snapshot_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "PAYLOAD_TOO_LARGE", "message": "snapshot exceeds limit"},
                )
        identity = identity_for(request)
        try:
            location = await asyncio.to_thread(
                configured_object_store().put_version_snapshot,
                user_id=identity.user_id,
                project_id=str(project_id),
                version_id=str(version_id),
                payload=bytes(payload),
            )
        except ImmutableObjectConflict as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "VERSION_CONFLICT", "message": str(error)},
            ) from error
        return {
            "bucket": location.bucket,
            "key": location.key,
            "sha256": location.sha256,
            "size": location.size,
        }

    @protected.post(
        "/sync/projects/{project_id}/versions/{version_id}/promote",
        operation_id="sync.versions.promote",
    )
    async def promote_synced_version(
        project_id: UUID,
        version_id: UUID,
        body: VersionManifestInput,
        request: Request,
    ) -> dict[str, Any]:
        identity = identity_for(request)
        expected_key = (
            f"users/{identity.user_id}/projects/{project_id}/versions/{version_id}/snapshot.zst"
        )
        if body.snapshot_key != expected_key:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "snapshot key is out of scope"},
            )
        try:
            await asyncio.to_thread(
                configured_object_store().verify_version_snapshot,
                user_id=identity.user_id,
                project_id=str(project_id),
                version_id=str(version_id),
                expected_sha256=body.snapshot_sha256,
                expected_size=body.snapshot_size,
            )
            state = await configured_sync_store().promote_version(
                user_id=identity.user_id,
                project_id=str(project_id),
                version_id=str(version_id),
                expected_revision=body.expected_revision,
                manifest=body.model_dump(mode="json"),
                decision_event_id=str(body.decision_event_id),
                device_id=identity.device_id,
                conversation_id=str(body.conversation_id),
                task_id=str(body.task_id),
                task_sequence=body.task_sequence,
            )
        except ObjectIntegrityError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": str(error)},
            ) from error
        except VersionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "VERSION_CONFLICT", "message": str(error)},
            ) from error
        return {
            "project_id": state.project_id,
            "active_version_id": state.active_version_id,
            "revision": state.revision,
        }

    @protected.get(
        "/events",
        response_class=EventSourceResponse,
        operation_id="events.subscribe",
    )
    async def events(
        request: Request,
        cursor: Annotated[int, Query(ge=0)] = 0,
        follow: Annotated[bool, Query()] = True,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        current = cursor
        if last_event_id is not None:
            try:
                current = max(current, int(last_event_id))
            except ValueError as error:
                raise HTTPException(
                    status_code=400,
                    detail="Last-Event-ID must be an integer",
                ) from error
        while True:
            if sync_store is None:
                batch = await invoke_async("events.subscribe", {"cursor": current})
                items = batch["items"]
            else:
                identity = identity_for(request)
                synced_events = await sync_store.events_after(
                    user_id=identity.user_id,
                    cursor=current,
                )
                items = [_synced_event_json(event) for event in synced_events]
            for item in items:
                current = int(item["cursor"])
                yield ServerSentEvent(
                    data=item,
                    event=str(item["event_type"]),
                    id=str(current),
                    retry=1_000,
                )
            if not follow or await request.is_disconnected():
                break
            if not items:
                yield ServerSentEvent(comment="keepalive")
            await asyncio.sleep(event_poll_seconds)

    operation_ids = {
        route.operation_id
        for route in (*app.routes, *protected.routes)
        if getattr(route, "operation_id", None) is not None
    }
    missing_methods = set(CORE_METHODS).difference(operation_ids)
    if missing_methods:
        raise RuntimeError(f"FastAPI routes are missing Core methods: {sorted(missing_methods)}")
    app.include_router(protected)
    return app


def _synced_event_json(event: SyncedEvent) -> dict[str, Any]:
    item = dict(event.payload)
    item.update(
        {
            "id": event.event_id,
            "cursor": event.cursor,
            "run_id": event.run_id,
            "project_id": event.project_id,
            "conversation_id": event.conversation_id,
            "task_id": event.task_id,
            "version_id": event.version_id,
            "task_sequence": event.task_sequence,
            "event_type": event.event_type,
            "visibility": event.visibility,
            "message": event.message,
            "schema_version": event.schema_version,
        }
    )
    item.setdefault("created_at", event.created_at.isoformat())
    return item


def _core_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, ValidationError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_PARAMS",
                "message": "Invalid params",
                "details": error.errors(include_url=False),
            },
        )
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(error)},
        )
    if isinstance(error, DomainError):
        error_code = str(getattr(error, "code", "DOMAIN_ERROR"))
        status_code = PUBLIC_ERROR_STATUS.get(error_code, 400)
        return HTTPException(
            status_code=status_code,
            detail={"code": error_code, "message": str(error)},
        )
    if isinstance(error, WorkerRpcError):
        return HTTPException(
            status_code=503 if error.error_code == "WORKER_INTERRUPTED" else 400,
            detail={"code": error.error_code, "message": str(error)},
        )
    if isinstance(error, CoreMethodNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "METHOD_NOT_FOUND", "message": str(error)},
        )
    if isinstance(error, ValueError):
        return HTTPException(
            status_code=422,
            detail={"code": "INVALID_PARAMS", "message": str(error)},
        )
    if isinstance(error, CoreResponseValidationError):
        return HTTPException(
            status_code=500,
            detail={"code": "CORE_ERROR", "message": str(error)},
        )
    return HTTPException(
        status_code=500,
        detail={"code": "CORE_ERROR", "message": "Core request failed"},
    )
