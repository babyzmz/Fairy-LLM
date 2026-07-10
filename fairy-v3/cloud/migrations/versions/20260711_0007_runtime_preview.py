"""Persist tenant-scoped Runtime, Preview, and Artifact lifecycle state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0007"
down_revision: str | Sequence[str] | None = "20260711_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
_RLS_TABLES = (
    "core_runtime_sessions",
    "core_preview_sessions",
    "core_artifacts",
)


def upgrade() -> None:
    _create_runtime_sessions()
    _create_preview_sessions()
    _create_artifacts()
    _enable_rls()


def _create_runtime_sessions() -> None:
    op.create_table(
        "core_runtime_sessions",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("project_root", sa.String(4096), nullable=False),
        sa.Column("execution_target", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("executor", sa.String(128), nullable=False),
        sa.Column("executor_handle", sa.String(512)),
        sa.Column("port", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("health", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_runtime_sessions"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_runtime_sessions_tenant_idempotency",
        ),
        sa.CheckConstraint(
            "(executor_handle IS NULL AND port IS NULL) OR "
            "(executor_handle IS NOT NULL AND port BETWEEN 1 AND 65535)",
            name="ck_core_runtime_sessions_handle_port",
        ),
        sa.CheckConstraint(
            "execution_target IN ('local', 'cloud')",
            name="ck_core_runtime_sessions_execution_target",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'starting', 'running', 'stopping', 'stopped', "
            "'failed', 'interrupted')",
            name="ck_core_runtime_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_runtime_sessions_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_runtime_sessions_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_runtime_sessions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_runtime_sessions_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_runtime_sessions_tenant_task",
        "core_runtime_sessions",
        ["tenant_id", "task_id", "created_at"],
    )


def _create_preview_sessions() -> None:
    op.create_table(
        "core_preview_sessions",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("runtime_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("project_root", sa.String(4096), nullable=False),
        sa.Column("execution_target", sa.String(32), nullable=False),
        sa.Column("url", sa.String(4096)),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("health", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_preview_sessions"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_preview_sessions_tenant_idempotency",
        ),
        sa.CheckConstraint(
            "(status IN ('ready', 'stopping') AND url IS NOT NULL) OR "
            "(status IN ('created', 'starting', 'stopped', 'failed') AND url IS NULL) OR "
            "status = 'interrupted'",
            name="ck_core_preview_sessions_active_url",
        ),
        sa.CheckConstraint(
            "execution_target IN ('local', 'cloud')",
            name="ck_core_preview_sessions_execution_target",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'starting', 'ready', 'stopping', 'stopped', "
            "'failed', 'interrupted')",
            name="ck_core_preview_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_preview_sessions_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_preview_sessions_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_preview_sessions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_preview_sessions_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "runtime_id"],
            ["core_runtime_sessions.tenant_id", "core_runtime_sessions.id"],
            name="fk_core_preview_sessions_runtime",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_preview_sessions_tenant_conversation",
        "core_preview_sessions",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "uq_core_preview_sessions_active_task",
        "core_preview_sessions",
        ["tenant_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('created', 'starting', 'ready', 'stopping')"),
    )


def _create_artifacts() -> None:
    op.create_table(
        "core_artifacts",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("storage_location", sa.String(4096), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_artifacts"),
        sa.CheckConstraint("byte_length >= 0", name="ck_core_artifacts_byte_length"),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_artifacts_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_artifacts_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_artifacts_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_artifacts_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_artifacts_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_artifacts_tenant_task",
        "core_artifacts",
        ["tenant_id", "task_id", "created_at"],
    )


def _enable_rls() -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    for table_name in _RLS_TABLES:
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY "{policy_name}" ON "{table_name}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_core_artifacts_tenant_task", table_name="core_artifacts")
    op.drop_table("core_artifacts")
    op.drop_index(
        "uq_core_preview_sessions_active_task",
        table_name="core_preview_sessions",
        postgresql_where=sa.text("status IN ('created', 'starting', 'ready', 'stopping')"),
    )
    op.drop_index(
        "ix_core_preview_sessions_tenant_conversation",
        table_name="core_preview_sessions",
    )
    op.drop_table("core_preview_sessions")
    op.drop_index(
        "ix_core_runtime_sessions_tenant_task",
        table_name="core_runtime_sessions",
    )
    op.drop_table("core_runtime_sessions")


def _tenant_column() -> sa.Column[str]:
    return sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(ID_LENGTH), nullable=False)


def _created_at_column() -> sa.Column[datetime]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated_at_column() -> sa.Column[datetime]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
