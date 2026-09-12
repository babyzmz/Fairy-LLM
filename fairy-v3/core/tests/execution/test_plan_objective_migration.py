from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.plan_objective_migration import CHECK, COLUMN, FIELDS, TABLE, UNIQUE
from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction
from fairy_core.transports.stdio import build_local_service
from tests.execution.test_plan_generations import _task


def test_populated_previous_schema_upgrades_and_reopens_without_losing_steps(tmp_path):
    service = build_local_service(tmp_path)
    service._workflow_scheduler.close()
    try:
        task = _task(service, "objective-migration")
        before = service.invoke(
            "execution_plans.create",
            {
                "task_id": task["id"],
                "files": [{"path": "README.md", "purpose": "Notes", "batch": 1}],
            },
        )
    finally:
        service.close()
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint(UNIQUE, type_="unique")
            batch.drop_constraint(CHECK, type_="check")
            batch.drop_column(COLUMN)
            batch.create_unique_constraint(UNIQUE, FIELDS[:-1])
    engine.dispose()
    from uuid import UUID

    for _ in range(2):
        engine = create_sqlite_core_engine(tmp_path / "core.db")
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")() as unit:
                restored = unit.state.get_execution_plan(UUID(before["plan"]["id"]))
                assert restored.workflow_objective_index == 0
                assert len(unit.state.task_steps_for_plan(restored.id)) == len(before["steps"])
                assert not unit._connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            uniques = {
                item["name"]: item["column_names"]
                for item in inspect(engine).get_unique_constraints(TABLE)
            }
            assert uniques[UNIQUE] == FIELDS
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")() as unit:
                assert unit.state.get_execution_plan(restored.id) is None
        finally:
            engine.dispose()
