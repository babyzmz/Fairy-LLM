from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Column, inspect

from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction

TABLE = "core_execution_plans"
COLUMN = "workflow_objective_index"
UNIQUE = "uq_core_execution_plans_workflow_revision"
FIELDS = ["tenant_id", "workflow_run_id", "workflow_plan_revision", COLUMN]
CHECK = "ck_core_execution_plans_objective"
CONDITION = f"{COLUMN} >= 0 AND {COLUMN} < 16 AND ({COLUMN} = 0 OR workflow_run_id IS NOT NULL)"


def migrate_plan_objectives(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        if COLUMN in {item["name"] for item in inspector.get_columns(TABLE)}:
            uniques = {
                item["name"]: item["column_names"]
                for item in inspector.get_unique_constraints(TABLE)
            }
            checks = {item["name"] for item in inspector.get_check_constraints(TABLE)}
            if uniques.get(UNIQUE) != FIELDS or CHECK not in checks:
                raise RuntimeError("File plan has an incomplete objective migration")
            return
    # Existing plans keep objective zero: a creation index cannot be reconstructed
    # from a later tool replay. Only new, owned completion certificates consume it.
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(TABLE, recreate="always") as batch:
            batch.add_column(Column(COLUMN, BigInteger, nullable=False, server_default="0"))
            batch.drop_constraint(UNIQUE, type_="unique")
            batch.create_unique_constraint(UNIQUE, FIELDS)
            batch.create_check_constraint(CHECK, CONDITION)
