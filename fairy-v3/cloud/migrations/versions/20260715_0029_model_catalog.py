"""Add model catalog cache and device selection state.

Revision ID: 20260715_0029
Revises: 20260714_0028
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0029"
down_revision: str | Sequence[str] | None = "20260714_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATALOG_TABLE = "core_model_catalogs"
SELECTION_TABLE = "core_model_selections"
UPDATE_TABLE = "core_model_selection_updates"
TABLES = (CATALOG_TABLE, SELECTION_TABLE, UPDATE_TABLE)


def upgrade() -> None:
    op.create_table(
        CATALOG_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("provider_kind", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("credential_status", sa.String(32), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_core_model_catalogs"),
        sa.CheckConstraint(
            "provider_kind = 'openrouter'",
            name="ck_core_model_catalogs_provider_kind",
        ),
        sa.CheckConstraint(
            "credential_status IN ('configured', 'invalid', 'unavailable')",
            name="ck_core_model_catalogs_credential_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_core_model_catalogs_revision"),
        sa.CheckConstraint(
            "expires_at > fetched_at",
            name="ck_core_model_catalogs_expiry",
        ),
    )
    op.create_table(
        SELECTION_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=True),
        sa.Column("allow_free_fallback", sa.Boolean(), nullable=False),
        sa.Column("zero_data_retention", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_core_model_selections"),
        sa.CheckConstraint(
            "mode IN ('auto', 'manual')",
            name="ck_core_model_selections_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'auto' AND model_id IS NULL) OR (mode = 'manual' AND model_id IS NOT NULL)",
            name="ck_core_model_selections_model",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_core_model_selections_revision"),
    )
    op.create_table(
        UPDATE_TABLE,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=True),
        sa.Column("allow_free_fallback", sa.Boolean(), nullable=False),
        sa.Column("zero_data_retention", sa.Boolean(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_revision", sa.BigInteger(), nullable=False),
        sa.Column("result_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_model_selection_updates",
        ),
        sa.CheckConstraint(
            "mode IN ('auto', 'manual')",
            name="ck_core_model_selection_updates_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'auto' AND model_id IS NULL) OR (mode = 'manual' AND model_id IS NOT NULL)",
            name="ck_core_model_selection_updates_model",
        ),
        sa.CheckConstraint(
            "expected_revision >= 0 AND result_revision = expected_revision + 1",
            name="ck_core_model_selection_updates_revision",
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
