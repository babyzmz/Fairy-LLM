"""Add durable media generation jobs.

Revision ID: 20260715_0031
Revises: 20260715_0030
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0031"
down_revision: str | Sequence[str] | None = "20260715_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "core_media_generation_jobs"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36)),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36)),
        sa.Column("command_run_id", sa.String(36), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("endpoint_kind", sa.String(32), nullable=False),
        sa.Column("output_path", sa.String(1024), nullable=False),
        sa.Column("request_spec", sa.JSON(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider_job_id", sa.String(255)),
        sa.Column("progress", sa.BigInteger(), nullable=False),
        sa.Column("usage_cost", sa.String(64)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_media_generation_jobs"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_media_generation_jobs_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "artifact_id",
            name="uq_core_media_generation_jobs_artifact",
        ),
        sa.CheckConstraint(
            "kind IN ('image', 'music', 'video')",
            name="ck_core_media_generation_jobs_kind",
        ),
        sa.CheckConstraint(
            "endpoint_kind IN ('images', 'audio', 'videos')",
            name="ck_core_media_generation_jobs_endpoint",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'generating', 'pending', 'in_progress', "
            "'completed', 'failed', 'cancelled', 'interrupted')",
            name="ck_core_media_generation_jobs_status",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_core_media_generation_jobs_progress",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_core_media_generation_jobs_revision"),
        sa.CheckConstraint(
            "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
            name="ck_core_media_generation_jobs_scope_digest",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_core_media_generation_jobs_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_media_generation_jobs_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["core_workspaces.tenant_id", "core_workspaces.id"],
            name="fk_core_media_generation_jobs_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_media_generation_jobs_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_media_generation_jobs_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_media_generation_jobs_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "turn_id", "conversation_id", "task_id"],
            [
                "core_assistant_turns.tenant_id",
                "core_assistant_turns.id",
                "core_assistant_turns.conversation_id",
                "core_assistant_turns.task_id",
            ],
            name="fk_core_media_generation_jobs_turn_scope",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_media_generation_jobs_recovery",
        "core_media_generation_jobs",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_core_media_generation_jobs_task",
        "core_media_generation_jobs",
        ["tenant_id", "task_id", "created_at"],
    )
    op.create_index(
        "ix_core_media_generation_jobs_provider",
        TABLE_NAME,
        ["tenant_id", "provider_job_id"],
    )
    _enable_rls()


def downgrade() -> None:
    op.execute(
        sa.text(
            f'DROP POLICY IF EXISTS "tenant_isolation_core_media_generation_jobs" ON "{TABLE_NAME}"'
        )
    )
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_media_generation_jobs_provider",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_core_media_generation_jobs_task",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_core_media_generation_jobs_recovery",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)


def _enable_rls() -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            'CREATE POLICY "tenant_isolation_core_media_generation_jobs" '
            f'ON "{TABLE_NAME}" USING ({predicate}) WITH CHECK ({predicate})'
        )
    )
