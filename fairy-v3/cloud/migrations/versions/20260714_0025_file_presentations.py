"""Add durable file presentations and renderer packs.

Revision ID: 20260714_0025
Revises: 20260713_0024
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0025"
down_revision: str | Sequence[str] | None = "20260713_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_file_render_jobs",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("file_set_id", sa.String(36), nullable=False),
        sa.Column("source_path", sa.String(4096), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("requested_mode", sa.String(64), nullable=False),
        sa.Column("renderer_pack_id", sa.String(128)),
        sa.Column("renderer_pack_version", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("public_summary", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_file_render_jobs"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            "cache_key",
            name="uq_core_file_render_jobs_cache",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_core_file_render_jobs_progress"),
        sa.CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_file_render_jobs_source_hash",
        ),
        sa.CheckConstraint(
            "length(cache_key) = 64 AND cache_key = lower(cache_key)",
            name="ck_core_file_render_jobs_cache_key",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name="fk_core_file_render_jobs_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_file_render_jobs_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_file_render_jobs_status", "core_file_render_jobs", ["tenant_id", "status"]
    )
    _enable_rls("core_file_render_jobs")
    op.create_table(
        "core_file_presentations",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("file_set_id", sa.String(36), nullable=False),
        sa.Column("source_path", sa.String(4096), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("renderer", sa.String(128), nullable=False),
        sa.Column("fidelity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_file_presentations"),
        sa.UniqueConstraint("tenant_id", "job_id", name="uq_core_file_presentations_job"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["core_file_render_jobs.tenant_id", "core_file_render_jobs.id"],
            name="fk_core_file_presentations_job",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_file_presentations_version",
        "core_file_presentations",
        ["tenant_id", "workspace_id", "version_id"],
    )
    _enable_rls("core_file_presentations")
    op.create_table(
        "core_derived_assets",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("presentation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(4096), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_derived_assets"),
        sa.UniqueConstraint(
            "tenant_id",
            "presentation_id",
            "role",
            "content_hash",
            name="uq_core_derived_assets_role_hash",
        ),
        sa.CheckConstraint("byte_length >= 0", name="ck_core_derived_assets_size"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "presentation_id"],
            ["core_file_presentations.tenant_id", "core_file_presentations.id"],
            name="fk_core_derived_assets_presentation",
            ondelete="CASCADE",
        ),
    )
    _enable_rls("core_derived_assets")
    op.create_table(
        "core_renderer_packs",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("install_path", sa.String(4096), nullable=False),
        sa.Column("health", sa.String(32), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", "version", name="pk_core_renderer_packs"),
        sa.CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash = lower(payload_hash)",
            name="ck_core_renderer_packs_payload_hash",
        ),
    )
    _enable_rls("core_renderer_packs")


def downgrade() -> None:
    for table in (
        "core_renderer_packs",
        "core_derived_assets",
        "core_file_presentations",
        "core_file_render_jobs",
    ):
        _disable_rls(table)
    op.drop_table("core_renderer_packs")
    op.drop_table("core_derived_assets")
    op.drop_index("ix_core_file_presentations_version", table_name="core_file_presentations")
    op.drop_table("core_file_presentations")
    op.drop_index("ix_core_file_render_jobs_status", table_name="core_file_render_jobs")
    op.drop_table("core_file_render_jobs")


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
