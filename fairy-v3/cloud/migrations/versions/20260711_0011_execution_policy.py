"""Add tenant-scoped Core execution policy settings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0011"
down_revision: str | Sequence[str] | None = "20260711_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS_TABLE = "core_execution_settings"
UPDATES_TABLE = "core_execution_setting_updates"
TABLES = (SETTINGS_TABLE, UPDATES_TABLE)


def upgrade() -> None:
    op.create_table(
        SETTINGS_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("capability_overrides", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_core_execution_settings"),
        sa.CheckConstraint(
            "profile IN ('observe', 'standard', 'autonomous')",
            name="ck_core_execution_settings_profile",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_core_execution_settings_revision",
        ),
    )
    op.create_table(
        UPDATES_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("capability_overrides", sa.JSON(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_execution_setting_updates",
        ),
        sa.CheckConstraint(
            "profile IN ('observe', 'standard', 'autonomous')",
            name="ck_core_execution_setting_updates_profile",
        ),
        sa.CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_execution_setting_updates_revision",
        ),
    )
    for table_name in TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
        op.drop_table(table_name)


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
