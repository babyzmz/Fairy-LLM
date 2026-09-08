from dataclasses import replace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from fairy_core.assistant.models import ToolInvocation, ToolInvocationStatus
from fairy_core.assistant.tool_revision import tool_command_key
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction
from fairy_core.storage.tool_revision_migration import TABLE
from fairy_core.workflow.models import (
    WorkflowNode,
    WorkflowPlanReason,
    WorkflowRun,
    WorkflowTriggerKind,
)
from tests.assistant.test_repository_contract import _seed_task, _turn


def _seed(factory, *, revised=True, same_arguments=True, in_flight=False):
    task, scope = _seed_task(factory, label="tool-revision")
    turn = _turn(task, scope, key="tool-revision")
    run = WorkflowRun.create(
        owner_kind="assistant_turn",
        owner_id=str(turn.id),
        execution_target=ExecutionTarget.LOCAL,
        trigger_kind=WorkflowTriggerKind.USER_TURN,
        idempotency_key="tool-workflow",
        task_id=task.id,
        conversation_id=task.conversation_id,
        engine_version=4,
    )
    turn.workflow_run_id = run.id
    turn.execution_engine_version = 4

    def invocation(revision):
        return ToolInvocation.create(
            turn=turn,
            model_round=revision,
            sequence=revision,
            provider_call_id="call-1" if same_arguments else f"call-{revision}",
            tool_name="info.time",
            scope_digest=turn.scope_digest,
            arguments={"timezone": "UTC" if same_arguments or revision == 1 else "Asia/Shanghai"},
            workflow_run_id=run.id,
            workflow_plan_revision=revision,
        )

    def node(item):
        return WorkflowNode.create(
            run_id=run.id,
            plan_revision=item.workflow_plan_revision,
            node_key=f"tool-{item.sequence}",
            kind="assistant.step.tool",
            payload={"invocation_id": str(item.id)},
            public_summary="Read current time",
        )

    first, second = invocation(1), invocation(2) if revised else None
    with factory() as unit:
        unit.workflows.create(run, nodes=(node(first),), edges=())
        unit.assistant.save_turn(turn)
        unit.assistant.save_tool_invocation(first)
        if second is not None:
            unit.workflows.append_plan(
                run.id,
                expected_revision=1,
                reason=WorkflowPlanReason.STEERING,
                instruction_id=None,
                nodes=(
                    replace(node(second), kind="test.undispatched", payload={})
                    if in_flight
                    else node(second),
                ),
                edges=(),
            )
            unit.assistant.save_tool_invocation(second)
        unit.commit()
    return turn, first, second


def _legacy_schema(engine):
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint("uq_core_assistant_tool_invocations_revision_provider_call",
                                  type_="unique")
            batch.create_unique_constraint("uq_core_assistant_tool_invocations_turn_provider_call",
                                           ["tenant_id", "turn_id", "provider_call_id"])
            batch.drop_constraint(
                "fk_core_assistant_tool_invocations_workflow_revision", type_="foreignkey"
            )
            batch.drop_constraint(
                "ck_core_assistant_tool_invocations_workflow_binding", type_="check"
            )
            batch.drop_constraint(
                "uq_core_assistant_tool_invocations_revision_arguments", type_="unique"
            )
            batch.drop_column("workflow_run_id")
            batch.drop_column("workflow_plan_revision")
            batch.create_unique_constraint(
                "uq_core_assistant_tool_invocations_turn_arguments",
                ["tenant_id", "turn_id", "argument_hash"],
            )


def test_tool_arguments_are_unique_per_revision_and_isolated_after_reopen(tmp_path):
    path = tmp_path / "core.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    try:
        turn, first, second = _seed(factory)
        assert first.argument_hash == second.argument_hash
        assert tool_command_key(first) == f"assistant:{turn.id}:tool:{first.argument_hash}"
        assert tool_command_key(first) != tool_command_key(second)
        for item in (
            replace(second, id=uuid4(), sequence=3, provider_call_id="duplicate"),
            replace(
                second,
                id=uuid4(),
                sequence=3,
                provider_call_id="bad-fk",
                workflow_plan_revision=999,
            ),
        ):
            with factory() as unit, pytest.raises(IntegrityError):
                unit.assistant.save_tool_invocation(item)
        other_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")
        other_turn, other, _ = _seed(other_factory, revised=False)
        assert other.turn_id != turn.id and other.argument_hash == first.argument_hash
    finally:
        engine.dispose()
    for _ in range(2):
        engine = create_sqlite_core_engine(path)
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")() as unit:
                assert unit.assistant.list_tool_invocations(turn.id) == (first, second)
                assert not unit.assistant.list_tool_invocations(other_turn.id)
                assert unit.assistant.get_tool_invocation(other.id) is None
                assert not unit._connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-b")() as unit:
                assert unit.assistant.list_tool_invocations(other_turn.id) == (other,)
                assert unit.assistant.get_tool_invocation(first.id) is None
        finally:
            engine.dispose()


@pytest.mark.parametrize("in_flight", [False, True])
def test_populated_upgrade_backfills_real_node_revisions_and_preserves_hashes(tmp_path, in_flight):
    path = tmp_path / "core.db"
    engine = create_sqlite_core_engine(path)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
        turn, first, second = _seed(factory, same_arguments=False, in_flight=in_flight)
        _legacy_schema(engine)
    finally:
        engine.dispose()
    for _ in range(2):
        engine = create_sqlite_core_engine(path)
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")() as unit:
                assert unit.assistant.list_tool_invocations(turn.id) == (first, second)
                assert not unit._connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                assert unit._connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            uniques = {item["name"] for item in inspect(engine).get_unique_constraints(TABLE)}
            assert "uq_core_assistant_tool_invocations_revision_arguments" in uniques
        finally:
            engine.dispose()


def test_partial_migration_and_ambiguous_history_fail_without_erasing_rows(tmp_path):
    path = tmp_path / "core.db"
    engine = create_sqlite_core_engine(path)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
        _, _, second = _seed(factory, same_arguments=False, in_flight=True)
        with factory() as unit:
            second.status = ToolInvocationStatus.COMPLETED
            unit.assistant.update_tool_invocation(
                second, expected_status=ToolInvocationStatus.CREATED
            )
            unit.commit()
        _legacy_schema(engine)
        with pytest.raises(RuntimeError, match="ambiguous Workflow provenance"):
            create_sqlite_core_engine(path)
        with engine.connect() as connection:
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar_one() == 2
            assert "workflow_run_id" not in {
                c["name"] for c in inspect(connection).get_columns(TABLE)
            }
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"ALTER TABLE {TABLE} ADD COLUMN workflow_run_id VARCHAR(36)"
            )
        with pytest.raises(RuntimeError, match="incomplete Workflow binding"):
            create_sqlite_core_engine(path)
    finally:
        engine.dispose()
