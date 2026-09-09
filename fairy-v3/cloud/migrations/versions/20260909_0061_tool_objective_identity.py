"""Keep tool recovery identity distinct between declared objectives.

Revision ID: 20260909_0061
Revises: 20260909_0060
"""

import sqlalchemy as sa
from alembic import op
from fairy_core.storage.tool_objective_migration import (
    CHECK,
    COLUMN,
    TABLE,
    UNIQUE_FIELDS,
    tool_objective_backfill_sql,
)

revision = "20260909_0061"
down_revision = "20260909_0060"
branch_labels = None
depends_on = None


def upgrade():
    invalid, backfill = tool_objective_backfill_sql(postgres=True)
    op.execute(sa.text(
        f"DO $$ BEGIN IF EXISTS ({invalid}) THEN "
        "RAISE EXCEPTION 'Tool Invocation history has invalid objective provenance'; "
        "END IF; END $$;"
    ))
    op.add_column(TABLE, sa.Column(COLUMN, sa.BigInteger(), nullable=False, server_default="0"))
    for suffix, field in UNIQUE_FIELDS:
        name = f"uq_core_assistant_tool_invocations_revision_{suffix}"
        op.drop_constraint(name, TABLE, type_="unique")
        op.create_unique_constraint(name, TABLE, [
            "tenant_id", "turn_id", "workflow_plan_revision", COLUMN, field,
        ])
    op.create_check_constraint(
        CHECK, TABLE, f"{COLUMN} >= 0 AND {COLUMN} < 16 AND "
        f"({COLUMN} = 0 OR workflow_run_id IS NOT NULL)",
    )
    op.execute(sa.text(backfill))


def downgrade():
    op.execute(sa.text(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE {COLUMN} <> 0) THEN "
        "RAISE EXCEPTION 'Objective tool history requires a matching recovery backup'; "
        "END IF; END $$;"
    ))
    for suffix, field in UNIQUE_FIELDS:
        name = f"uq_core_assistant_tool_invocations_revision_{suffix}"
        op.drop_constraint(name, TABLE, type_="unique")
        op.create_unique_constraint(name, TABLE, [
            "tenant_id", "turn_id", "workflow_plan_revision", field,
        ])
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
