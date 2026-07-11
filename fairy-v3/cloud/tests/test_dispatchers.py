from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.dispatchers import TenantRuntimeRegistry
from fairy_cloud.settings import to_sync_postgres_dsn


def test_asyncpg_dsn_is_explicitly_converted_for_sync_core() -> None:
    assert (
        to_sync_postgres_dsn("postgresql+asyncpg://fairy_app:secret@postgres:5432/fairy")
        == "postgresql+psycopg://fairy_app:secret@postgres:5432/fairy"
    )


def test_tenant_runtimes_are_cached_and_use_opaque_workspace_paths(tmp_path: Path) -> None:
    built: list[tuple[str, Path, Any]] = []
    engine = object()

    def build(tenant_id: str, path: Path, received_engine: Any):
        built.append((tenant_id, path, received_engine))
        return object()

    registry = TenantRuntimeRegistry(root=tmp_path, engine=engine, builder=build)
    user_a = RequestIdentity("auth0|alice@example.test", "device-a", frozenset())
    user_b = RequestIdentity("user-b", "device-b", frozenset())

    service_a = registry.for_identity(user_a)

    assert registry.for_identity(user_a) is service_a
    assert registry.for_identity(user_b) is not service_a
    assert len(built) == 2
    assert all(path.parent == tmp_path / "tenants" for _, path, _ in built)
    assert all(tenant_id == path.name for tenant_id, path, _ in built)
    assert all(len(path.name) == 64 for _, path, _ in built)
    assert all("alice" not in str(path) for _, path, _ in built)
    assert all(received_engine is engine for _, _, received_engine in built)


def test_runtime_registry_has_no_local_dispatcher_dependency() -> None:
    import fairy_cloud.dispatchers as runtimes

    source = Path(runtimes.__file__).read_text(encoding="utf-8")

    assert "build_local_dispatcher" not in source
    assert "create_sqlite" not in source
    assert "core.db" not in source


def test_system_runtime_cannot_collide_with_an_oidc_subject(tmp_path: Path) -> None:
    built: list[str] = []

    def build(tenant_id: str, _path: Path, _engine: Any):
        built.append(tenant_id)
        return object()

    registry = TenantRuntimeRegistry(root=tmp_path, engine=object(), builder=build)
    system = registry.system_service()
    user = registry.for_identity(
        RequestIdentity("fairy-system", "device", frozenset()),
    )

    assert system is not user
    assert built[0] == "system"
    assert len(built[1]) == 64


def test_tenant_runtime_runs_recovery_on_creation_and_after_interval(tmp_path: Path) -> None:
    now = [10.0]

    class RecoverableService:
        def __init__(self) -> None:
            self.recovery_calls = 0

        def recover_interrupted_work(self) -> dict[str, int]:
            self.recovery_calls += 1
            return {"assistant_turns": 0, "previews": 0}

    service = RecoverableService()
    registry = TenantRuntimeRegistry(
        root=tmp_path,
        engine=object(),
        builder=lambda _tenant, _path, _engine: service,
        recovery_interval_seconds=5,
        clock=lambda: now[0],
    )
    identity = RequestIdentity("recovery-user", "device-a", frozenset())

    assert registry.for_identity(identity) is service
    assert registry.for_identity(identity) is service
    assert service.recovery_calls == 1

    now[0] += 5
    assert registry.for_identity(identity) is service
    assert service.recovery_calls == 2


def test_recovery_for_one_tenant_does_not_block_another_tenant(tmp_path: Path) -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    built = 0

    class RecoverableService:
        def __init__(self, order: int) -> None:
            self.order = order

        def recover_interrupted_work(self) -> dict[str, int]:
            if self.order == 1:
                first_entered.set()
                assert release_first.wait(timeout=5)
            else:
                second_entered.set()
            return {"assistant_turns": 0, "previews": 0}

        def close(self) -> None:
            pass

    def build(_tenant: str, _path: Path, _engine: Any) -> RecoverableService:
        nonlocal built
        built += 1
        return RecoverableService(built)

    registry = TenantRuntimeRegistry(root=tmp_path, engine=object(), builder=build)
    identity_a = RequestIdentity("parallel-a", "device-a", frozenset())
    identity_b = RequestIdentity("parallel-b", "device-b", frozenset())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(registry.for_identity, identity_a)
        assert first_entered.wait(timeout=5)
        second = executor.submit(registry.for_identity, identity_b)
        try:
            assert second_entered.wait(timeout=1)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
