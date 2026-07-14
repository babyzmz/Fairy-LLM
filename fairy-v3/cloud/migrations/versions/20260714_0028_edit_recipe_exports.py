"""Add recoverable Edit Recipe export state.

Revision ID: 20260714_0028
Revises: 20260714_0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0028"
down_revision: str | Sequence[str] | None = "20260714_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_edit_recipes",
        sa.Column("apply_idempotency_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "core_edit_recipes",
        sa.Column("applied_version_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "core_edit_recipes",
        sa.Column("output_hash", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_core_edit_recipes_apply_idempotency",
        "core_edit_recipes",
        ["tenant_id", "apply_idempotency_key"],
    )
    op.create_check_constraint(
        "ck_core_edit_recipes_output_hash",
        "core_edit_recipes",
        "output_hash IS NULL OR (length(output_hash) = 64 AND output_hash = lower(output_hash))",
    )
    op.create_foreign_key(
        "fk_core_edit_recipes_applied_version",
        "core_edit_recipes",
        "core_versions",
        ["tenant_id", "applied_version_id"],
        ["tenant_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_core_edit_recipes_applied_version",
        "core_edit_recipes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_core_edit_recipes_output_hash",
        "core_edit_recipes",
        type_="check",
    )
    op.drop_constraint(
        "uq_core_edit_recipes_apply_idempotency",
        "core_edit_recipes",
        type_="unique",
    )
    op.drop_column("core_edit_recipes", "output_hash")
    op.drop_column("core_edit_recipes", "applied_version_id")
    op.drop_column("core_edit_recipes", "apply_idempotency_key")
