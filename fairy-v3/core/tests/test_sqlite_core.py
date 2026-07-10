from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from fairy_core.commanding import SqlAlchemyCommandLedger
from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.schema import command_metadata
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage import SqliteStateStore
from fairy_core.storage.schema import state_metadata
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from fairy_core.storage.sqlite_engine import create_sqlite_engine


def _scope(tmp_path: Path, name: str) -> ScopeContract:
    project_id, conversation_id, task_id, base_id, target_id = [new_id() for _ in range(5)]
    project_root = tmp_path / name
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project_id,
        conversation_id=conversation_id,
        task_id=task_id,
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base_id,
        target_version_id=target_id,
        project_root=project_root,
        allowed_write_paths=(project_root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("current_conversation_draft",),
    )


def test_split_database_import_preserves_every_existing_tenant(tmp_path: Path) -> None:
    source_path = tmp_path / "state.db"
    source_engine = create_sqlite_engine(source_path)
    state_metadata.create_all(source_engine)
    project_a = Project.create(name="Tenant A", residency=ProjectResidency.LOCAL_ONLY)
    project_b = Project.create(name="Tenant B", residency=ProjectResidency.SYNCED)
    state_a = SqlAlchemyStateStore(source_engine, tenant_id="tenant-a")
    state_b = SqlAlchemyStateStore(source_engine, tenant_id="tenant-b")
    state_a.save_project(project_a)
    state_b.save_project(project_b)
    source_engine.dispose()

    destination = create_sqlite_core_engine(
        tmp_path / "core.db",
        legacy_state_path=source_path,
    )
    migrated_a = SqlAlchemyStateStore(destination, tenant_id="tenant-a")
    migrated_b = SqlAlchemyStateStore(destination, tenant_id="tenant-b")

    assert migrated_a.get_project(project_a.id) == project_a
    assert migrated_b.get_project(project_b.id) == project_b
    destination.dispose()


def test_split_ledger_import_preserves_every_existing_tenant(tmp_path: Path) -> None:
    source_path = tmp_path / "ledger.db"
    source_engine = create_sqlite_engine(source_path)
    command_metadata.create_all(source_engine)
    ledger_a = SqlAlchemyCommandLedger(source_engine, tenant_id="tenant-a")
    ledger_b = SqlAlchemyCommandLedger(source_engine, tenant_id="tenant-b")
    run_a = ledger_a.create_run(
        command_name="project.read",
        actor="agent-a",
        scope=_scope(tmp_path, "tenant-a"),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="tenant-a-command",
    )
    run_b = ledger_b.create_run(
        command_name="project.read",
        actor="agent-b",
        scope=_scope(tmp_path, "tenant-b"),
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key="tenant-b-command",
    )
    source_engine.dispose()

    destination = create_sqlite_core_engine(
        tmp_path / "core.db",
        legacy_ledger_path=source_path,
    )

    assert SqlAlchemyCommandLedger(destination, tenant_id="tenant-a").get_run(run_a.id) == run_a
    assert SqlAlchemyCommandLedger(destination, tenant_id="tenant-b").get_run(run_b.id) == run_b
    destination.dispose()


def test_split_import_rejects_an_active_legacy_writer(tmp_path: Path) -> None:
    source_path = tmp_path / "state.db"
    project = Project.create(name="Legacy", residency=ProjectResidency.LOCAL_ONLY)
    state = SqliteStateStore(source_path)
    state.save_project(project)
    state.close()

    with closing(sqlite3.connect(source_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="legacy database is still in use"):
            create_sqlite_core_engine(
                tmp_path / "core.db",
                legacy_state_path=source_path,
            )
        writer.rollback()

    destination = create_sqlite_core_engine(
        tmp_path / "core.db",
        legacy_state_path=source_path,
    )
    assert SqlAlchemyStateStore(destination, tenant_id="local").get_project(project.id) == project
    destination.dispose()


def test_split_import_rejects_a_legacy_database_that_reappears(tmp_path: Path) -> None:
    source_path = tmp_path / "state.db"
    first_project = Project.create(name="First", residency=ProjectResidency.LOCAL_ONLY)
    state = SqliteStateStore(source_path)
    state.save_project(first_project)
    state.close()
    destination = create_sqlite_core_engine(
        tmp_path / "core.db",
        legacy_state_path=source_path,
    )
    destination.dispose()

    late_project = Project.create(name="Late", residency=ProjectResidency.LOCAL_ONLY)
    late_state = SqliteStateStore(source_path)
    late_state.save_project(late_project)
    late_state.close()

    with pytest.raises(RuntimeError, match="reappeared after migration"):
        create_sqlite_core_engine(
            tmp_path / "core.db",
            legacy_state_path=source_path,
        )
