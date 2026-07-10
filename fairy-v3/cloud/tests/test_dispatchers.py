from __future__ import annotations

from pathlib import Path

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.dispatchers import TenantDispatcherRegistry


def test_tenant_dispatchers_are_cached_and_use_opaque_paths(tmp_path: Path) -> None:
    built_paths: list[Path] = []

    def build(path: Path):
        built_paths.append(path)
        return object()

    registry = TenantDispatcherRegistry(root=tmp_path, builder=build)
    user_a = RequestIdentity("auth0|alice@example.test", "device-a", frozenset())
    user_b = RequestIdentity("user-b", "device-b", frozenset())

    dispatcher_a = registry.for_identity(user_a)

    assert registry.for_identity(user_a) is dispatcher_a
    assert registry.for_identity(user_b) is not dispatcher_a
    assert len(built_paths) == 2
    assert all(path.parent == tmp_path / "tenants" for path in built_paths)
    assert all(len(path.name) == 64 for path in built_paths)
    assert all("alice" not in str(path) for path in built_paths)
