from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.assistant.tools import model_tools
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.settings import StaticSandboxHealthProvider
from fairy_core.commanding.types import PermissionProfile
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.transports.stdio import build_local_service


def test_execution_settings_are_revision_fenced_and_tenant_scoped(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    first = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    second = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")

    with first() as unit_of_work:
        default = unit_of_work.execution_settings.get()
        assert default.profile is PermissionProfile.STANDARD
        assert default.capability_overrides == {}
        assert default.revision == 0
        assert default.updated_at.tzinfo is UTC
        changed = unit_of_work.execution_settings.update(
            profile=PermissionProfile.AUTONOMOUS,
            capability_overrides={"run.sandboxed": True},
            expected_revision=0,
            idempotency_key="settings:tenant-a:autonomous",
        )
        unit_of_work.commit()

    assert changed.profile is PermissionProfile.AUTONOMOUS
    assert changed.capability_overrides == {"run.sandboxed": True}
    assert changed.revision == 1

    with second() as unit_of_work:
        isolated = unit_of_work.execution_settings.get()
        assert isolated.profile is PermissionProfile.STANDARD
        assert isolated.revision == 0

    with first() as unit_of_work, pytest.raises(VersionConflictError):
        unit_of_work.execution_settings.update(
            profile=PermissionProfile.OBSERVE,
            capability_overrides={},
            expected_revision=0,
            idempotency_key="settings:tenant-a:stale",
        )

    with first() as unit_of_work:
        replayed = unit_of_work.execution_settings.update(
            profile=PermissionProfile.AUTONOMOUS,
            capability_overrides={"run.sandboxed": True},
            expected_revision=0,
            idempotency_key="settings:tenant-a:autonomous",
        )
        with pytest.raises(IdempotencyConflictError):
            unit_of_work.execution_settings.update(
                profile=PermissionProfile.OBSERVE,
                capability_overrides={},
                expected_revision=1,
                idempotency_key="settings:tenant-a:autonomous",
            )

    assert replayed == changed

    engine.dispose()


def test_execution_settings_update_rolls_back_with_the_unit_of_work(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "rollback.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")

    with factory() as unit_of_work:
        unit_of_work.execution_settings.update(
            profile=PermissionProfile.OBSERVE,
            capability_overrides={"web.search": False},
            expected_revision=0,
            idempotency_key="settings:rollback",
        )

    with factory() as unit_of_work:
        persisted = unit_of_work.execution_settings.get()

    assert persisted.profile is PermissionProfile.STANDARD
    assert persisted.capability_overrides == {}
    assert persisted.revision == 0
    engine.dispose()


def test_capability_manifest_uses_persisted_policy_and_core_health(tmp_path: Path) -> None:
    service = build_local_service(
        tmp_path,
        sandbox_health_provider=StaticSandboxHealthProvider({"local": True}),
    )
    try:
        original = service.invoke("permissions.get", {})
        assert original["profile"] == "standard"
        assert original["revision"] == 0
        assert service.invoke("capabilities.get", {})["operations"]["run.sandboxed"] is False

        changed = service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {"run.sandboxed": True, "web.search": False},
                "expected_revision": 0,
                "idempotency_key": "settings:local:autonomous",
            },
        )
        manifest = service.invoke("capabilities.get", {})

        assert changed["revision"] == 1
        assert manifest["profile"] == "autonomous"
        assert manifest["sandbox_healthy"] is True
        assert manifest["operations"]["run.sandboxed"] is True
        assert manifest["operations"]["web.search"] is False

        with pytest.raises(ValueError, match="unknown capability"):
            service.invoke(
                "permissions.update",
                {
                    "profile": "autonomous",
                    "capability_overrides": {"shell.execute": True},
                    "expected_revision": 1,
                    "idempotency_key": "settings:local:unknown",
                },
            )
        with pytest.raises(ValidationError):
            service.invoke(
                "capabilities.get",
                {"profile": "autonomous", "sandbox_healthy": True},
            )
    finally:
        service.close()


def test_local_execution_settings_survive_service_restart(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "observe",
                "capability_overrides": {"web.search": False},
                "expected_revision": 0,
                "idempotency_key": "settings:local:observe",
            },
        )
    finally:
        service.close()

    reopened = build_local_service(tmp_path)
    try:
        assert reopened.invoke("permissions.get", {}) == {
            "profile": "observe",
            "capability_overrides": {"web.search": False},
            "revision": 1,
            "updated_at": reopened.invoke("permissions.get", {})["updated_at"],
        }
    finally:
        reopened.close()


def test_model_tool_manifest_uses_the_same_effective_policy() -> None:
    registry = build_default_registry()

    standard = {
        item.name
        for item in model_tools(
            registry,
            profile=PermissionProfile.STANDARD,
            sandbox_healthy=False,
            overrides={},
        )
    }
    autonomous = {
        item.name
        for item in model_tools(
            registry,
            profile=PermissionProfile.AUTONOMOUS,
            sandbox_healthy=True,
            overrides={"web.search": False},
        )
    }

    assert "direct_answer" in standard
    assert "run.sandboxed" not in standard
    assert "run.sandboxed" in autonomous
    assert "web.search" not in autonomous
