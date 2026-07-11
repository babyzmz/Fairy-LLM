from __future__ import annotations

import threading
import time
from collections.abc import Callable
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
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy.engine import Engine

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.storage.objects import S3ObjectStore
from fairy_cloud.storage.postgres import tenant_id_for_user

RuntimeBuilder = Callable[[str, Path, Engine], CoreService]
MonotonicClock = Callable[[], float]
_SYSTEM_TENANT_ID = "system"


def build_postgres_core_service(
    tenant_id: str,
    workspace_root: Path,
    engine: Engine,
    *,
    object_store: S3ObjectStore | None = None,
) -> CoreService:
    registry = build_default_registry()
    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    application = CoreApplication(
        unit_of_work_factory=unit_of_work_factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(workspace_root),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    runtime_application = RuntimeApplication(
        unit_of_work_factory=unit_of_work_factory,
        executor=UnavailableRuntimeExecutor(
            executor="cloud_oci_worker",
            diagnostic="Cloud OCI Runtime is not configured",
        ),
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=application.scope_for_task,
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
        recovery_interval_seconds: float = 5.0,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be positive")
        self._root = root
        self._engine = engine
        self._builder = builder
        self._object_store = object_store
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


__all__ = ["TenantRuntimeRegistry", "build_postgres_core_service"]
