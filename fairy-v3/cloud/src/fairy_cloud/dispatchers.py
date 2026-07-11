from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from fairy_capabilities.composition import (
    build_capability_bundle,
    build_provider_registry,
    build_voice_registry,
)
from fairy_capabilities.documents import CompositeDocumentParser
from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.service import CoreService
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.mcp.application import McpApplication
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.runtime.evidence_store import FileRuntimeEvidenceStore
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor
from fairy_core.sandbox.tools import ExecutorSandboxHealthProvider
from fairy_core.skills.registry import SkillRegistry
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy.engine import Engine

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.execution.executor import CloudSandboxExecutor
from fairy_cloud.execution.repository import ExecutionJobRepository
from fairy_cloud.mcp import CloudMcpConnector
from fairy_cloud.runtime.executor import CloudRuntimeExecutor
from fairy_cloud.runtime.repository import CloudRuntimeRepository
from fairy_cloud.runtime.review import CloudRuntimeReviewer
from fairy_cloud.runtime.tokens import CloudPreviewSigner
from fairy_cloud.storage.objects import S3ObjectStore
from fairy_cloud.storage.postgres import tenant_id_for_user

RuntimeBuilder = Callable[[str, Path, Engine], CoreService]
MonotonicClock = Callable[[], float]
_SYSTEM_TENANT_ID = "system"


@dataclass(frozen=True, slots=True)
class CloudSandboxComponents:
    executor: CloudSandboxExecutor
    health: ExecutorSandboxHealthProvider


def build_cloud_sandbox(engine: Engine, *, tenant_id: str) -> CloudSandboxComponents:
    executor = CloudSandboxExecutor(store=ExecutionJobRepository(engine, tenant_id=tenant_id))
    return CloudSandboxComponents(
        executor=executor,
        health=ExecutorSandboxHealthProvider(
            executor,
            execution_target="cloud",
            expected_executor="cloud_oci_worker",
            expected_version="1.0.0",
        ),
    )


def build_postgres_core_service(
    tenant_id: str,
    workspace_root: Path,
    engine: Engine,
    *,
    object_store: S3ObjectStore | None = None,
    preview_base_url: str | None = None,
    preview_signer: CloudPreviewSigner | None = None,
    runtime_gateway_key: str | None = None,
    mcp_credentials: Mapping[str, str] | None = None,
    mcp_allowed_hosts: tuple[str, ...] = (),
) -> CoreService:
    registry = build_default_registry()
    sandbox = build_cloud_sandbox(engine, tenant_id=tenant_id)
    execution_policy = ExecutionPolicyResolver(sandbox.health)
    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    application = CoreApplication(
        unit_of_work_factory=unit_of_work_factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(workspace_root),
        registry=registry,
        policy=PolicyEngine(registry),
        execution_policy=execution_policy,
    )
    mcp_application = McpApplication(
        unit_of_work_factory=unit_of_work_factory,
        registry=registry,
        connector=CloudMcpConnector(
            mcp_credentials or {},
            allowed_hosts=mcp_allowed_hosts,
        ),
        scope_resolver=application.scope_for_task,
        execution_policy=execution_policy,
        execution_target="cloud",
    )
    runtime_executor = (
        CloudRuntimeExecutor(
            tenant_id=tenant_id,
            store=CloudRuntimeRepository(engine, tenant_id=tenant_id),
            preview_base_url=preview_base_url,
            signer=preview_signer,
        )
        if preview_base_url is not None
        and preview_signer is not None
        and runtime_gateway_key is not None
        else UnavailableRuntimeExecutor(
            executor="cloud_oci_runtime",
            diagnostic="Cloud OCI Runtime is not configured",
        )
    )
    runtime_application = RuntimeApplication(
        unit_of_work_factory=unit_of_work_factory,
        executor=runtime_executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=application.scope_for_task,
        execution_policy=execution_policy,
    )
    providers = build_provider_registry()
    try:
        voice = build_voice_registry()
    except BaseException:
        providers.close()
        raise
    try:
        capabilities = build_capability_bundle()
    except BaseException:
        voice.close()
        providers.close()
        raise
    try:
        return CoreService(
            application,
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            provider_registry=providers,
            voice_registry=voice,
            image_attachment_store=ImageAttachmentStore(workspace_root / "perception"),
            tool_executor=capabilities.executor,
            research_fetch_port=capabilities.web.fetch_port,
            document_parser=(CompositeDocumentParser() if object_store is not None else None),
            document_blob_store=(
                object_store.document_blob_store(tenant_id) if object_store is not None else None
            ),
            runtime_application=runtime_application,
            runtime_reviewer=(
                CloudRuntimeReviewer(
                    gateway_base_url="http://runtime:8082",
                    gateway_key=runtime_gateway_key,
                )
                if preview_base_url is not None
                and preview_signer is not None
                and runtime_gateway_key is not None
                else None
            ),
            runtime_evidence_store=(
                (
                    object_store.runtime_evidence_store(tenant_id)
                    if object_store is not None
                    else FileRuntimeEvidenceStore(workspace_root / "runtime-evidence")
                )
                if preview_base_url is not None
                and preview_signer is not None
                and runtime_gateway_key is not None
                else None
            ),
            sandbox_executor=sandbox.executor,
            sandbox_health_provider=sandbox.health,
            skill_registry=SkillRegistry(registry),
            mcp_application=mcp_application,
            default_execution_target="cloud",
        )
    except BaseException:
        capabilities.executor.close()
        voice.close()
        providers.close()
        raise


class TenantRuntimeRegistry:
    """Caches tenant-bound Core services over one shared PostgreSQL Engine."""

    def __init__(
        self,
        *,
        root: Path,
        engine: Engine,
        builder: RuntimeBuilder = build_postgres_core_service,
        object_store: S3ObjectStore | None = None,
        preview_base_url: str | None = None,
        preview_signer: CloudPreviewSigner | None = None,
        runtime_gateway_key: str | None = None,
        mcp_credentials: Mapping[str, str] | None = None,
        mcp_allowed_hosts: tuple[str, ...] = (),
        recovery_interval_seconds: float = 5.0,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be positive")
        self._root = root
        self._engine = engine
        self._builder = builder
        self._object_store = object_store
        self._preview_base_url = preview_base_url
        self._preview_signer = preview_signer
        self._runtime_gateway_key = runtime_gateway_key
        self._mcp_credentials = dict(mcp_credentials or {})
        self._mcp_allowed_hosts = tuple(mcp_allowed_hosts)
        self._recovery_interval_seconds = recovery_interval_seconds
        self._clock = clock
        self._services: dict[str, CoreService] = {}
        self._last_recovery: dict[str, float] = {}
        self._recovery_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    def system_service(self) -> CoreService:
        return self._service_for_tenant(_SYSTEM_TENANT_ID, self._root / "system")

    def for_identity(self, identity: RequestIdentity) -> CoreService:
        tenant_id = tenant_id_for_user(identity.user_id)
        return self._service_for_tenant(
            tenant_id,
            self._root / "tenants" / tenant_id,
        )

    def close(self) -> None:
        with self._lock:
            services = tuple(self._services.values())
            self._services.clear()
            self._last_recovery.clear()
            self._recovery_locks.clear()
        for service in services:
            service.close()

    def _service_for_tenant(self, tenant_id: str, path: Path) -> CoreService:
        with self._lock:
            service = self._services.get(tenant_id)
            if service is None:
                if self._builder is build_postgres_core_service:
                    service = build_postgres_core_service(
                        tenant_id,
                        path,
                        self._engine,
                        object_store=self._object_store,
                        preview_base_url=self._preview_base_url,
                        preview_signer=self._preview_signer,
                        runtime_gateway_key=self._runtime_gateway_key,
                        mcp_credentials=self._mcp_credentials,
                        mcp_allowed_hosts=self._mcp_allowed_hosts,
                    )
                else:
                    service = self._builder(tenant_id, path, self._engine)
                self._services[tenant_id] = service
            recovery_lock = self._recovery_locks.setdefault(tenant_id, threading.Lock())

        with recovery_lock:
            now = self._clock()
            with self._lock:
                last_recovery = self._last_recovery.get(tenant_id)
            recovery_due = (
                last_recovery is None or now - last_recovery >= self._recovery_interval_seconds
            )
            recover = getattr(service, "recover_interrupted_work", None)
            if recovery_due and callable(recover):
                recover()
                with self._lock:
                    self._last_recovery[tenant_id] = now
        return service


__all__ = [
    "CloudSandboxComponents",
    "TenantRuntimeRegistry",
    "build_cloud_sandbox",
    "build_postgres_core_service",
]
