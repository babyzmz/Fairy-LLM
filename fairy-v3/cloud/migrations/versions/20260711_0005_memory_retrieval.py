"""Add immutable memory Snapshots and rebuildable lexical projections."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0005"
down_revision: str | Sequence[str] | None = "20260710_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
_RLS_TABLES = (
    "memory_snapshots",
    "memory_snapshot_items",
    "memory_search_documents",
    "memory_access_log",
    "memory_projection_checkpoints",
)


def upgrade() -> None:
    op.add_column(
        "core_tasks",
        sa.Column("memory_snapshot_id", sa.String(ID_LENGTH)),
    )
    op.add_column(
        "core_tasks",
        sa.Column("memory_snapshot_hash", sa.String(64)),
    )
    op.create_check_constraint(
        "ck_core_tasks_memory_snapshot_binding",
        "core_tasks",
        "(memory_snapshot_id IS NULL AND memory_snapshot_hash IS NULL) OR "
        "(memory_snapshot_id IS NOT NULL AND memory_snapshot_hash IS NOT NULL)",
    )

    op.create_table(
        "memory_snapshots",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("base_version_id", sa.String(ID_LENGTH)),
        sa.Column("target_version_id", sa.String(ID_LENGTH)),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("source_watermark_cursor", sa.BigInteger(), nullable=False),
        sa.Column("projection_generation", sa.BigInteger(), nullable=False),
        sa.Column("projection_watermark_cursor", sa.BigInteger(), nullable=False),
        sa.Column("projection_state", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("degraded_reason", sa.String(128)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_snapshots"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            name="uq_memory_snapshots_tenant_task",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_memory_snapshots_tenant_request",
        ),
        sa.CheckConstraint(
            "snapshot_version = 1",
            name="ck_memory_snapshots_version",
        ),
        sa.CheckConstraint(
            "source_watermark_cursor >= 0 AND projection_generation >= 1 AND "
            "projection_watermark_cursor >= 0",
            name="ck_memory_snapshots_watermarks",
        ),
        sa.CheckConstraint(
            "token_count >= 0 AND token_count <= 3000",
            name="ck_memory_snapshots_token_count",
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND projection_state = 'ready' AND "
            "degraded_reason IS NULL AND "
            "projection_watermark_cursor >= source_watermark_cursor) OR "
            "(status = 'degraded' AND projection_state <> 'ready' AND "
            "degraded_reason IS NOT NULL)",
            name="ck_memory_snapshots_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_memory_snapshots_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_memory_snapshots_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_memory_snapshots_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "base_version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_memory_snapshots_base_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_memory_snapshots_target_version",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_memory_snapshots_tenant_scope",
        "memory_snapshots",
        ["tenant_id", "project_id", "conversation_id", "created_at"],
    )

    op.create_table(
        "memory_snapshot_items",
        _tenant_column(),
        sa.Column("snapshot_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_revision", sa.BigInteger()),
        sa.Column("namespace", sa.String(32)),
        sa.Column("selection_reason", sa.String(32), nullable=False),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("rendered_text_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "snapshot_id",
            "ordinal",
            name="pk_memory_snapshot_items",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_memory_snapshot_items_ordinal"),
        sa.CheckConstraint(
            "token_count >= 1",
            name="ck_memory_snapshot_items_token_count",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["memory_snapshots.tenant_id", "memory_snapshots.id"],
            name="fk_memory_snapshot_items_snapshot",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "memory_search_documents",
        _tenant_column(),
        _id_column(),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("namespace", sa.String(32)),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH)),
        sa.Column("task_id", sa.String(ID_LENGTH)),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_cursor", sa.BigInteger(), nullable=False),
        sa.Column("projection_generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(normalized_text, ''))",
                persisted=True,
            ),
        ),
        _updated_at_column(),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_memory_search_documents",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "projection_generation",
            "source_kind",
            "source_id",
            "source_revision",
            name="uq_memory_search_documents_tenant_source",
        ),
        sa.CheckConstraint(
            "source_revision >= 0",
            name="ck_memory_search_documents_revision",
        ),
        sa.CheckConstraint(
            "source_cursor >= 1 AND projection_generation >= 1",
            name="ck_memory_search_documents_watermarks",
        ),
        *_scope_foreign_keys("memory_search_documents"),
    )
    op.create_index(
        "ix_memory_search_documents_tenant_scope",
        "memory_search_documents",
        [
            "tenant_id",
            "projection_generation",
            "project_id",
            "conversation_id",
            "namespace",
        ],
    )
    op.create_index(
        "ix_memory_search_documents_vector",
        "memory_search_documents",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "memory_access_log",
        _tenant_column(),
        _id_column(),
        sa.Column("snapshot_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_revision", sa.BigInteger()),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("rejection_reason", sa.String(128)),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_access_log"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_memory_access_log_latency"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["memory_snapshots.tenant_id", "memory_snapshots.id"],
            name="fk_memory_access_log_snapshot",
            ondelete="CASCADE",
        ),
        *_scope_foreign_keys("memory_access_log"),
    )
    op.create_index(
        "ix_memory_access_log_tenant_snapshot",
        "memory_access_log",
        ["tenant_id", "snapshot_id", "created_at"],
    )

    op.create_table(
        "memory_projection_checkpoints",
        _tenant_column(),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("source_watermark_cursor", sa.BigInteger(), nullable=False),
        sa.Column("projected_watermark_cursor", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        _updated_at_column(),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "generation",
            name="pk_memory_projection_checkpoints",
        ),
        sa.CheckConstraint(
            "generation >= 1 AND source_watermark_cursor >= 0 AND "
            "projected_watermark_cursor >= 0 AND retry_count >= 0",
            name="ck_memory_projection_checkpoints_bounds",
        ),
    )

    for table_name in _RLS_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        _disable_rls(table_name)

    op.drop_table("memory_projection_checkpoints")
    op.drop_index(
        "ix_memory_access_log_tenant_snapshot",
        table_name="memory_access_log",
    )
    op.drop_table("memory_access_log")
    op.drop_index(
        "ix_memory_search_documents_vector",
        table_name="memory_search_documents",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_memory_search_documents_tenant_scope",
        table_name="memory_search_documents",
    )
    op.drop_table("memory_search_documents")
    op.drop_table("memory_snapshot_items")
    op.drop_index(
        "ix_memory_snapshots_tenant_scope",
        table_name="memory_snapshots",
    )
    op.drop_table("memory_snapshots")

    op.drop_constraint(
        "ck_core_tasks_memory_snapshot_binding",
        "core_tasks",
        type_="check",
    )
    op.drop_column("core_tasks", "memory_snapshot_hash")
    op.drop_column("core_tasks", "memory_snapshot_id")


def _tenant_column() -> sa.Column[str]:
    return sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(ID_LENGTH), nullable=False)


def _created_at_column() -> sa.Column[object]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated_at_column() -> sa.Column[object]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)


def _scope_foreign_keys(prefix: str) -> tuple[sa.ForeignKeyConstraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name=f"fk_{prefix}_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name=f"fk_{prefix}_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name=f"fk_{prefix}_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name=f"fk_{prefix}_version",
            ondelete="CASCADE",
        ),
    )


def _enable_rls(table_name: str) -> None:
    policy_name = f"tenant_isolation_{table_name}"
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
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
