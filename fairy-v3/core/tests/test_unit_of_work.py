from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

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
from fairy_core.persistence import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.schema import state_metadata


@pytest.fixture
def engine() -> Engine:
    value = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    state_metadata.create_all(value)
    command_metadata.create_all(value)
    yield value
    value.dispose()


def _scope(tmp_path: Path) -> ScopeContract:
    root = tmp_path / "version"
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=new_id(),
        conversation_id=new_id(),
        task_id=new_id(),
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=new_id(),
        target_version_id=new_id(),
        project_root=root,
        allowed_write_paths=(root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("current_conversation_draft",),
    )


def test_unit_of_work_rolls_back_state_and_command_event_together(
    engine: Engine,
    tmp_path: Path,
) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    project = Project.create(name="Rollback", residency=ProjectResidency.LOCAL_ONLY)

    with pytest.raises(RuntimeError, match="crash"), factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.commands.create_run(
            command_name="project.read",
            actor="agent",
            scope=_scope(tmp_path),
            input_payload={"query": "entrypoints"},
            risk_level=RiskLevel.LOW,
            idempotency_key="rollback-command",
        )
        raise RuntimeError("crash")

    with factory() as unit_of_work:
        assert unit_of_work.state.get_project(project.id) is None
        assert unit_of_work.commands.events_after(cursor=0) == []


def test_unit_of_work_commits_state_and_command_event_together(
    engine: Engine,
    tmp_path: Path,
) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    project = Project.create(name="Commit", residency=ProjectResidency.LOCAL_ONLY)

    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        run = unit_of_work.commands.create_run(
            command_name="project.read",
            actor="agent",
            scope=_scope(tmp_path),
            input_payload={"query": "entrypoints"},
            risk_level=RiskLevel.LOW,
            idempotency_key="commit-command",
        )
        unit_of_work.commit()

    with factory() as unit_of_work:
        assert unit_of_work.state.get_project(project.id) == project
        events = unit_of_work.commands.events_after(cursor=0)
        assert [event.run_id for event in events] == [run.id]
        assert [event.event_type for event in events] == ["command.created"]


def test_unit_of_work_rolls_back_clean_exit_without_commit(engine: Engine) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    project = Project.create(name="No commit", residency=ProjectResidency.LOCAL_ONLY)

    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)

    with factory() as unit_of_work:
        assert unit_of_work.state.get_project(project.id) is None


class _BeginFailureConnection:
    def __init__(self) -> None:
        self.closed = False

    def begin(self) -> None:
        raise RuntimeError("begin failed")

    def close(self) -> None:
        self.closed = True


class _BeginFailureEngine:
    def __init__(self, connection: _BeginFailureConnection) -> None:
        self._connection = connection

    def connect(self) -> _BeginFailureConnection:
        return self._connection


def test_unit_of_work_closes_connection_when_begin_fails() -> None:
    connection = _BeginFailureConnection()
    engine = cast(Engine, _BeginFailureEngine(connection))
    unit_of_work = SqlAlchemyUnitOfWork(engine, tenant_id="tenant-a")

    with pytest.raises(RuntimeError, match="begin failed"):
        unit_of_work.__enter__()

    assert connection.closed is True


def test_unit_of_work_factory_normalizes_tenant_id(engine: Engine) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="  tenant-a  ")

    assert factory.tenant_id == "tenant-a"
    with pytest.raises(ValueError, match="tenant_id"):
        SqlAlchemyUnitOfWorkFactory(engine, tenant_id="   ")
