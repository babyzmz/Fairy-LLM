"""Create tenant-scoped canonical Hermes memory tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0003"
down_revision: str | Sequence[str] | None = "20260710_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
_RLS_TABLES = (
    "memory_observations",
    "memory_claims",
    "memory_claim_revisions",
    "memory_tombstones",
)


def upgrade() -> None:
    op.create_table(
        "memory_observations",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("source_event_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_cursor", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("proposed_namespace", sa.String(32), nullable=False),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("scan_result", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_observations"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_memory_observations_tenant_request",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_observations_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_memory_observations_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_memory_observations_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_memory_observations_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_memory_observations_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_event_id"],
            ["domain_events.tenant_id", "domain_events.event_id"],
            name="fk_memory_observations_source_event",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_memory_observations_tenant_scope",
        "memory_observations",
        [
            "tenant_id",
            "proposed_namespace",
            "project_id",
            "conversation_id",
            "status",
        ],
    )

    op.create_table(
        "memory_claims",
        _tenant_column(),
        _id_column(),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH)),
        sa.Column("task_id", sa.String(ID_LENGTH)),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("device_id", sa.String(128)),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("predicate", sa.String(512), nullable=False),
        sa.Column("current_revision", sa.BigInteger(), nullable=False),
        sa.Column("conflict_set_id", sa.String(ID_LENGTH)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_claims"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_memory_claims_tenant_request",
        ),
        sa.CheckConstraint(
            "current_revision >= 0",
            name="ck_memory_claims_revision",
        ),
        sa.CheckConstraint(
            "(namespace = 'project_canonical' AND project_id IS NOT NULL) OR "
            "(namespace = 'conversation_draft' AND conversation_id IS NOT NULL) OR "
            "(namespace = 'user_profile' AND project_id IS NULL AND "
            "conversation_id IS NULL AND task_id IS NULL AND version_id IS NULL AND "
            "device_id IS NULL) OR "
            "(namespace = 'device_local' AND device_id IS NOT NULL) OR "
            "(namespace = 'task_episode' AND task_id IS NOT NULL)",
            name="ck_memory_claims_namespace_scope",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_memory_claims_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_memory_claims_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_memory_claims_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_memory_claims_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_memory_claims_tenant_scope",
        "memory_claims",
        ["tenant_id", "namespace", "project_id", "conversation_id", "status"],
    )

    op.create_table(
        "memory_claim_revisions",
        _tenant_column(),
        sa.Column("claim_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("source_observation_ids", sa.JSON(), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("supersedes_revision", sa.BigInteger()),
        sa.Column("resolved_claim_ids", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "claim_id",
            "revision",
            name="pk_memory_claim_revisions",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "claim_id",
            "request_fingerprint",
            name="uq_memory_claim_revisions_tenant_request",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_memory_claim_revisions_revision",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_claim_revisions_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["memory_claims.tenant_id", "memory_claims.id"],
            name="fk_memory_claim_revisions_claim",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "uq_memory_claim_revisions_current",
        "memory_claim_revisions",
        ["tenant_id", "claim_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_memory_claim_revisions_validity",
        "memory_claim_revisions",
        ["tenant_id", "valid_to"],
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "memory_tombstones",
        _tenant_column(),
        _id_column(),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_tombstones"),
        sa.UniqueConstraint(
            "tenant_id",
            "target_kind",
            "target_id",
            name="uq_memory_tombstones_tenant_target",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_memory_tombstones_tenant_request",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_event_id"],
            ["domain_events.tenant_id", "domain_events.event_id"],
            name="fk_memory_tombstones_source_event",
            ondelete="RESTRICT",
        ),
    )

    for table_name in _RLS_TABLES:
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


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))

    op.drop_table("memory_tombstones")
    op.drop_index(
        "ix_memory_claim_revisions_validity",
        table_name="memory_claim_revisions",
    )
    op.drop_index(
        "uq_memory_claim_revisions_current",
        table_name="memory_claim_revisions",
    )
    op.drop_table("memory_claim_revisions")
    op.drop_index("ix_memory_claims_tenant_scope", table_name="memory_claims")
    op.drop_table("memory_claims")
    op.drop_index(
        "ix_memory_observations_tenant_scope",
        table_name="memory_observations",
    )
    op.drop_table("memory_observations")


def _tenant_column() -> sa.Column[str]:
    return sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(ID_LENGTH), nullable=False)


def _created_at_column() -> sa.Column[object]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated_at_column() -> sa.Column[object]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
