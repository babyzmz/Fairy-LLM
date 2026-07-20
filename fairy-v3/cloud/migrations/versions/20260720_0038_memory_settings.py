"""Add Core-owned revision-fenced memory settings.

Revision ID: 20260720_0038
Revises: 20260720_0037
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0038"
down_revision: str | Sequence[str] | None = "20260720_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS_TABLE = "core_memory_settings"
UPDATES_TABLE = "core_memory_setting_updates"


def upgrade() -> None:
    op.create_table(
        SETTINGS_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.BigInteger(), nullable=False),
        sa.Column("export_to_obsidian", sa.Boolean(), nullable=False),
        sa.Column("sync_normalized_content", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_core_memory_settings"),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_core_memory_settings_retention",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_core_memory_settings_revision"),
    )
    op.create_table(
        UPDATES_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.BigInteger(), nullable=False),
        sa.Column("export_to_obsidian", sa.Boolean(), nullable=False),
        sa.Column("sync_normalized_content", sa.Boolean(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_memory_setting_updates",
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_core_memory_setting_updates_retention",
        ),
        sa.CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_memory_setting_updates_revision",
        ),
    )
    _enable_rls(SETTINGS_TABLE)
    _enable_rls(UPDATES_TABLE)


def downgrade() -> None:
    for table_name in (UPDATES_TABLE, SETTINGS_TABLE):
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table_name}" ON "{table_name}"')
        )
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
        op.drop_table(table_name)


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
