"""Add generated asset sets and immutable variants.

Revision ID: 20260714_0027
Revises: 20260714_0026
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0027"
down_revision: str | Sequence[str] | None = "20260714_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_asset_sets",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("generation_parameters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_asset_sets"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_core_asset_sets_idempotency",
        ),
        sa.CheckConstraint(
            "length(input_digest) = 64 AND input_digest = lower(input_digest)",
            name="ck_core_asset_sets_input_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name="fk_core_asset_sets_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_asset_sets_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_asset_sets_version",
        "core_asset_sets",
        ["tenant_id", "workspace_id", "version_id"],
    )
    _enable_rls("core_asset_sets")
    op.create_table(
        "core_asset_variants",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("asset_set_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.String(4096), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "asset_set_id", "ordinal", name="pk_core_asset_variants"
        ),
        sa.UniqueConstraint(
            "tenant_id", "asset_set_id", "path", name="uq_core_asset_variants_path"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_core_asset_variants_ordinal"),
        sa.CheckConstraint("byte_length >= 0", name="ck_core_asset_variants_size"),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_asset_variants_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_set_id"],
            ["core_asset_sets.tenant_id", "core_asset_sets.id"],
            name="fk_core_asset_variants_asset_set",
            ondelete="CASCADE",
        ),
    )
    _enable_rls("core_asset_variants")


def downgrade() -> None:
    for table in ("core_asset_variants", "core_asset_sets"):
        _disable_rls(table)
        op.drop_table(table)


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


def _disable_rls(table_name: str) -> None:
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
