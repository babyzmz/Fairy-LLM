from dataclasses import replace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.exc import IntegrityError

from fairy_core.assistant.tool_revision import tool_command_key
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.schema import workflow_nodes
from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction
from tests.assistant.test_tool_revision_identity import _seed


def test_identical_tool_arguments_have_distinct_objective_identity_after_reopen(tmp_path):
    path = tmp_path / "core.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-a")
    try:
        turn, first, _ = _seed(factory, revised=False)
        second = replace(first, id=uuid4(), sequence=2, workflow_objective_index=1)
        with factory() as unit:
            unit.assistant.save_tool_invocation(second)
            unit.commit()
        assert first.argument_hash == second.argument_hash
        assert tool_command_key(first) != tool_command_key(second)
        for invalid in (
            replace(second, id=uuid4(), sequence=3),
            replace(second, id=uuid4(), sequence=3, workflow_objective_index=16),
            replace(second, id=uuid4(), sequence=3, workflow_run_id=None),
        ):
            with factory() as unit, pytest.raises(IntegrityError):
                unit.assistant.save_tool_invocation(invalid)
        other_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-b")
        other, _, _ = _seed(other_factory, revised=False)
        assert other.id != turn.id
    finally:
        engine.dispose()
    for _ in range(2):
        engine = create_sqlite_core_engine(path)
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-a")() as unit:
                assert unit.assistant.list_tool_invocations(turn.id) == (first, second)
                assert unit.assistant.get_turn(other.id) is None
        finally:
            engine.dispose()


@pytest.mark.parametrize("damage", ["ambiguous", "foreign_scope", "partial"])
def test_tool_objective_migration_refuses_ambiguous_or_partial_history(tmp_path, damage):
    path = tmp_path / "broken.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-broken")
    try:
        turn, first, _ = _seed(factory, revised=False)
        _legacy_objective_schema(engine)
        with engine.begin() as connection:
            row = (
                connection.execute(
                    select(workflow_nodes).where(
                        workflow_nodes.c.run_id == str(turn.workflow_run_id),
                    )
                )
                .mappings()
                .one()
            )
            if damage == "partial":
                connection.exec_driver_sql(
                    "ALTER TABLE core_assistant_tool_invocations ADD COLUMN "
                    "workflow_objective_index BIGINT NOT NULL DEFAULT 0"
                )
            else:
                payload = {
                    "invocation_id": str(first.id),
                    "turn_id": str(turn.id),
                    "objective_protocol": 1,
                    "objective_index": 1,
                }
                if damage == "foreign_scope":
                    payload["turn_id"] = str(uuid4())
                    connection.execute(
                        update(workflow_nodes)
                        .where(
                            workflow_nodes.c.id == row["id"],
                        )
                        .values(payload=payload)
                    )
                else:
                    connection.execute(
                        insert(workflow_nodes).values(
                            {
                                **row,
                                "id": str(uuid4()),
                                "node_key": "conflicting-objective",
                                "payload": payload,
                            }
                        )
                    )
    finally:
        engine.dispose()
    with pytest.raises(RuntimeError, match="objective"):
        create_sqlite_core_engine(path)
    from fairy_core.storage.sqlite_engine import create_sqlite_engine

    engine = create_sqlite_engine(path)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM core_assistant_tool_invocations"
                ).scalar_one()
                == 1
            )
            columns = {
                item["name"]
                for item in inspect(connection).get_columns(
                    "core_assistant_tool_invocations",
                )
            }
            assert ("workflow_objective_index" in columns) == (damage == "partial")
    finally:
        engine.dispose()


def _legacy_objective_schema(engine):
    table = "core_assistant_tool_invocations"
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(table, recreate="always") as batch:
            for suffix, field in (
                ("arguments", "argument_hash"),
                ("provider_call", "provider_call_id"),
            ):
                name = f"uq_core_assistant_tool_invocations_revision_{suffix}"
                batch.drop_constraint(name, type_="unique")
                batch.create_unique_constraint(
                    name,
                    [
                        "tenant_id",
                        "turn_id",
                        "workflow_plan_revision",
                        field,
                    ],
                )
            batch.drop_constraint("ck_core_assistant_tool_invocations_objective", type_="check")
            batch.drop_column("workflow_objective_index")


@pytest.mark.parametrize(
    "index,checkpoint",
    [
        (None, False),
        (0, False),
        (1, False),
        (15, False),
        (-1, False),
        (16, False),
        ("1", False),
        (True, False),
        (1, True),
        ("1", True),
    ],
)
def test_old_tool_objective_schema_recovers_owned_node_or_rejects_bad_index(
    tmp_path,
    index,
    checkpoint,
):
    path = tmp_path / "old.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-migration")
    try:
        turn, first, _ = _seed(factory, revised=False)
        if index is not None:
            with engine.begin() as connection:
                connection.execute(
                    update(workflow_nodes)
                    .where(
                        workflow_nodes.c.run_id == str(turn.workflow_run_id),
                    )
                    .values(
                        payload={
                            "invocation_id": str(first.id),
                            "turn_id": str(turn.id),
                            "objective_protocol": 1,
                            "objective_index": index,
                        }
                    )
                )
        if checkpoint:
            payload = {
                "invocation_id": str(first.id),
                "turn_id": str(turn.id),
                "objective_protocol": 1,
                "objective_index": index,
            }
            with engine.begin() as connection:
                connection.execute(
                    update(workflow_nodes)
                    .where(
                        workflow_nodes.c.run_id == str(turn.workflow_run_id),
                    )
                    .values(
                        kind="assistant.step.model",
                        payload={"turn_id": str(turn.id)},
                        result={
                            "type": "tools",
                            "nodes": [{"kind": "assistant.step.tool", "payload": payload}],
                        },
                    )
                )
        _legacy_objective_schema(engine)
    finally:
        engine.dispose()
    if index is not None and (type(index) is not int or not 0 <= index < 16):
        with pytest.raises(RuntimeError, match="objective provenance"):
            create_sqlite_core_engine(path)
        return
    for _ in range(2):
        engine = create_sqlite_core_engine(path)
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="objective-migration")() as unit:
                item = unit.assistant.get_tool_invocation(first.id)
                assert item.workflow_objective_index == (index or 0)
                assert item.argument_hash == first.argument_hash
            with engine.connect() as connection:
                assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        finally:
            engine.dispose()
