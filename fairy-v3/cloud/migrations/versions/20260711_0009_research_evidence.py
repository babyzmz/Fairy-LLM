"""Persist tenant-scoped public research Evidence records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0009"
down_revision: str | Sequence[str] | None = "20260711_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
TABLE_NAME = "core_research_evidence"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False),
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("artifact_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("redirect_chain", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "id",
            name="pk_core_research_evidence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "ordinal",
            name="uq_core_research_evidence_artifact_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "canonical_url",
            name="uq_core_research_evidence_artifact_url",
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_core_research_evidence_ordinal",
        ),
        sa.CheckConstraint(
            "byte_length >= 0",
            name="ck_core_research_evidence_byte_length",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_research_evidence_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["core_artifacts.tenant_id", "core_artifacts.id"],
            name="fk_core_research_evidence_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_research_evidence_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_research_evidence_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_research_evidence_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_research_evidence_version",
        ),
    )
    op.create_index(
        "ix_core_research_evidence_tenant_artifact",
        TABLE_NAME,
        ["tenant_id", "artifact_id", "ordinal"],
    )
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    policy_name = f"tenant_isolation_{TABLE_NAME}"
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy_name}" ON "{TABLE_NAME}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )


def downgrade() -> None:
    policy_name = f"tenant_isolation_{TABLE_NAME}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{TABLE_NAME}"'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_research_evidence_tenant_artifact",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
