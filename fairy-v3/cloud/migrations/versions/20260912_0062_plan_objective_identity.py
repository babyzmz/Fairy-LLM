"""Preserve file plans between dependent objectives.

Revision ID: 20260912_0062
Revises: 20260909_0061
"""

import sqlalchemy as sa
from alembic import op
from fairy_core.storage.plan_objective_migration import (
    CHECK,
    COLUMN,
    CONDITION,
    FIELDS,
    TABLE,
    UNIQUE,
)

revision = "20260912_0062"
down_revision = "20260909_0061"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(TABLE, sa.Column(COLUMN, sa.BigInteger(), nullable=False, server_default="0"))
    op.drop_constraint(UNIQUE, TABLE, type_="unique")
    op.create_unique_constraint(UNIQUE, TABLE, FIELDS)
    op.create_check_constraint(CHECK, TABLE, CONDITION)


def downgrade():
    # Fail rather than mistake RLS-filtered history for an empty database.
    # This setting does not grant BYPASSRLS or disable table policies.
    op.execute(sa.text("SET LOCAL row_security = off"))
    op.execute(
        sa.text(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE {COLUMN} <> 0) THEN "
            "RAISE EXCEPTION 'Objective file history requires a matching recovery backup'; "
            "END IF; END $$;"
        )
    )
    op.drop_constraint(UNIQUE, TABLE, type_="unique")
    op.create_unique_constraint(UNIQUE, TABLE, FIELDS[:-1])
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
