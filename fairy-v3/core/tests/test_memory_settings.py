from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.transports.stdio import build_local_service


def test_memory_settings_are_revision_fenced_and_tenant_scoped(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    first = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    second = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")

    with first() as unit_of_work:
        defaults = unit_of_work.memory_settings.get()
        assert defaults.enabled is True
        assert defaults.retention_days == 365
        assert defaults.export_to_obsidian is False
        assert defaults.sync_normalized_content is False
        assert defaults.revision == 0
        assert defaults.updated_at.tzinfo is UTC
        changed = unit_of_work.memory_settings.update(
            enabled=True,
            retention_days=730,
            export_to_obsidian=True,
            sync_normalized_content=False,
            expected_revision=0,
            idempotency_key="memory-settings:tenant-a:1",
        )
        unit_of_work.commit()

    with second() as unit_of_work:
        assert unit_of_work.memory_settings.get().revision == 0

    with first() as unit_of_work:
        replayed = unit_of_work.memory_settings.update(
            enabled=True,
            retention_days=730,
            export_to_obsidian=True,
            sync_normalized_content=False,
            expected_revision=0,
            idempotency_key="memory-settings:tenant-a:1",
        )
        assert replayed == changed
        with pytest.raises(IdempotencyConflictError):
            unit_of_work.memory_settings.update(
                enabled=False,
                retention_days=30,
                export_to_obsidian=False,
                sync_normalized_content=False,
                expected_revision=1,
                idempotency_key="memory-settings:tenant-a:1",
            )

    with first() as unit_of_work, pytest.raises(VersionConflictError):
        unit_of_work.memory_settings.update(
            enabled=False,
            retention_days=30,
            export_to_obsidian=False,
            sync_normalized_content=False,
            expected_revision=0,
            idempotency_key="memory-settings:tenant-a:stale",
        )
    engine.dispose()


def test_memory_settings_rpc_persists_core_authority(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        defaults = service.invoke("memory.settings.get", {})
        assert defaults["enabled"] is True
        assert defaults["revision"] == 0
        changed = service.invoke(
            "memory.settings.update",
            {
                "enabled": False,
                "retention_days": 90,
                "export_to_obsidian": False,
                "sync_normalized_content": False,
                "expected_revision": 0,
                "idempotency_key": "memory-settings:rpc:1",
            },
        )
        assert changed["enabled"] is False
        assert changed["retention_days"] == 90
        assert changed["revision"] == 1
        assert service.invoke("memory.settings.get", {}) == changed
    finally:
        service.close()
