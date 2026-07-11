from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from fairy_capabilities.composition import build_provider_registry, build_tool_executor
from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.service import CoreService
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy.engine import Engine

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.storage.postgres import tenant_id_for_user

RuntimeBuilder = Callable[[str, Path, Engine], CoreService]
_SYSTEM_TENANT_ID = "system"


def build_postgres_core_service(
    tenant_id: str,
    workspace_root: Path,
    engine: Engine,
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
    return CoreService(
        application,
        unit_of_work_factory=unit_of_work_factory,
        registry=registry,
        provider_registry=build_provider_registry(),
        tool_executor=build_tool_executor(),
        runtime_application=runtime_application,
    )


class TenantRuntimeRegistry:
    """Caches tenant-bound Core services over one shared PostgreSQL Engine."""

    def __init__(
        self,
        *,
        root: Path,
        engine: Engine,
        builder: RuntimeBuilder = build_postgres_core_service,
    ) -> None:
        self._root = root
        self._engine = engine
        self._builder = builder
        self._services: dict[str, CoreService] = {}
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
        for service in services:
            service.close()

    def _service_for_tenant(self, tenant_id: str, path: Path) -> CoreService:
        with self._lock:
            service = self._services.get(tenant_id)
            if service is None:
                service = self._builder(tenant_id, path, self._engine)
                self._services[tenant_id] = service
            return service


__all__ = ["TenantRuntimeRegistry", "build_postgres_core_service"]
