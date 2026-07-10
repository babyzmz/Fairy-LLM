from __future__ import annotations

import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from fairy_core.commanding import SqlAlchemyCommandLedger, SqliteCommandLedger
from fairy_core.commanding.models import EventVisibility
from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage import SqlAlchemyStateStore, SqliteStateStore
from fairy_core.transports.stdio import build_local_dispatcher, process_stream


def test_stdio_processes_one_jsonrpc_response_per_input_line(tmp_path: Path) -> None:
    dispatcher = build_local_dispatcher(tmp_path)
    assert (tmp_path / "core.db").is_file()
    assert not (tmp_path / "state.db").exists()
    assert not (tmp_path / "ledger.db").exists()
    source = io.StringIO(
        "\n".join(
            (
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}}),
                "{invalid-json",
                "",
            )
        )
    )
    destination = io.StringIO()

    process_stream(dispatcher, source, destination)

    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["status"] == "ok"
    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == -32700
    assert len(responses) == 2
    dispatcher.close()


def test_stdio_imports_split_state_and_ledger_databases_once(tmp_path: Path) -> None:
    project = Project.create(name="Legacy project", residency=ProjectResidency.LOCAL_ONLY)
    state = SqliteStateStore(tmp_path / "state.db")
    state.save_project(project)
    state.close()

    conversation_id = new_id()
    task_id = new_id()
    base_version_id = new_id()
    target_version_id = new_id()
    project_root = tmp_path / "workspaces" / "legacy"
    scope = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation_id,
        task_id=task_id,
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base_version_id,
        target_version_id=target_version_id,
        project_root=project_root,
        allowed_write_paths=(project_root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="project_safe",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("current_conversation_draft",),
    )
    old_ledger = SqliteCommandLedger(tmp_path / "ledger.db")
    run = old_ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={"query": "entrypoints"},
        risk_level=RiskLevel.LOW,
        idempotency_key="legacy-command",
    )
    old_ledger.append_event(
        run_id=run.id,
        event_type="command.queued",
        visibility=EventVisibility.USER,
        message="Queued before database unification",
        payload={"stage": "queued"},
    )
    old_ledger.close()
    with closing(sqlite3.connect(tmp_path / "ledger.db")) as connection:
        connection.execute("UPDATE domain_events SET cursor = cursor + 6")
        connection.commit()

    first_dispatcher = build_local_dispatcher(tmp_path)
    first_dispatcher.close()
    second_dispatcher = build_local_dispatcher(tmp_path)
    second_dispatcher.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    migrated_state = SqlAlchemyStateStore(engine, tenant_id="local")
    migrated_ledger = SqlAlchemyCommandLedger(engine, tenant_id="local")
    events = [event for event in migrated_ledger.events_after(cursor=0) if event.run_id == run.id]
    appended = migrated_ledger.append_event(
        run_id=run.id,
        event_type="command.resumed",
        visibility=EventVisibility.USER,
        message="Resumed after database unification",
        payload={"stage": "resumed"},
    )

    assert migrated_state.get_project(project.id) == project
    assert migrated_ledger.get_run(run.id) == run
    assert [event.event_type for event in events] == ["command.created", "command.queued"]
    assert [event.cursor for event in events] == [7, 8]
    assert appended.cursor == 9
    assert (tmp_path / "state.db.fairy-v3-migrated").is_file()
    assert (tmp_path / "ledger.db.fairy-v3-migrated").is_file()
    migrated_state.close()
    migrated_ledger.close()
    engine.dispose()


def test_stdio_holds_an_exclusive_data_directory_lock(tmp_path: Path) -> None:
    first = build_local_dispatcher(tmp_path)

    with pytest.raises(RuntimeError, match="data directory is already in use"):
        build_local_dispatcher(tmp_path)

    first.close()
    restarted = build_local_dispatcher(tmp_path)
    restarted.close()
