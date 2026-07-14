"""Add annotations, edit recipes, and selection references.

Revision ID: 20260714_0026
Revises: 20260714_0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0026"
down_revision: str | Sequence[str] | None = "20260714_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_annotation_documents",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("file_set_id", sa.String(36), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("annotations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_annotation_documents"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            "file_set_id",
            name="uq_core_annotation_documents_file_set",
        ),
        sa.CheckConstraint("revision > 0", name="ck_core_annotation_documents_revision"),
        sa.CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_annotation_documents_source_hash",
        ),
        *_scope_foreign_keys("annotation_documents"),
    )
    _enable_rls("core_annotation_documents")
    op.create_table(
        "core_edit_recipes",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("file_set_id", sa.String(36), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_edit_recipes"),
        sa.CheckConstraint("revision > 0", name="ck_core_edit_recipes_revision"),
        sa.CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_edit_recipes_source_hash",
        ),
        *_scope_foreign_keys("edit_recipes"),
    )
    _enable_rls("core_edit_recipes")
    op.create_table(
        "core_selection_references",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("file_set_id", sa.String(36), nullable=False),
        sa.Column("source_path", sa.String(4096), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("viewer_kind", sa.String(64), nullable=False),
        sa.Column("locator_kind", sa.String(64), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_selection_references"),
        sa.CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_selection_references_source_hash",
        ),
        *_scope_foreign_keys("selection_references"),
    )
    _enable_rls("core_selection_references")


def downgrade() -> None:
    for table in ("core_selection_references", "core_edit_recipes", "core_annotation_documents"):
        _disable_rls(table)
        op.drop_table(table)


def _scope_foreign_keys(label: str) -> tuple[sa.ForeignKeyConstraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name=f"fk_core_{label}_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name=f"fk_core_{label}_version",
            ondelete="CASCADE",
        ),
    )


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
