"""Bind cloud execution network access to a Core-owned purpose."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0015"
down_revision: str | Sequence[str] | None = "20260711_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS = "execution_jobs"
NETWORK_CONSTRAINT = "ck_execution_jobs_network_policy"


def upgrade() -> None:
    op.add_column(
        JOBS,
        sa.Column(
            "purpose",
            sa.String(32),
            nullable=False,
            server_default="raw",
        ),
    )
    op.drop_constraint(NETWORK_CONSTRAINT, JOBS, type_="check")
    op.create_check_constraint(
        NETWORK_CONSTRAINT,
        JOBS,
        "(network_policy = 'none') OR "
        "(network_policy = 'public' AND purpose = 'dependency')",
    )
    op.create_check_constraint(
        "ck_execution_jobs_purpose",
        JOBS,
        "purpose IN ('raw','dependency','review')",
    )
    op.alter_column(JOBS, "purpose", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_execution_jobs_purpose", JOBS, type_="check")
    op.drop_constraint(NETWORK_CONSTRAINT, JOBS, type_="check")
    op.create_check_constraint(
        NETWORK_CONSTRAINT,
        JOBS,
        "network_policy = 'none'",
    )
    op.drop_column(JOBS, "purpose")
