"""Add tenant-scoped managed documents and lexical RAG projection."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0010"
down_revision: str | Sequence[str] | None = "20260711_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
TABLES = (
    "core_documents",
    "core_document_revisions",
    "core_document_chunks",
)


def upgrade() -> None:
    op.create_table(
        "core_documents",
        sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False),
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH)),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_location", sa.String(4096), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_documents"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_documents_tenant_idempotency",
        ),
        sa.CheckConstraint("byte_length >= 0", name="ck_core_documents_byte_length"),
        sa.CheckConstraint(
            "current_revision > 0",
            name="ck_core_documents_current_revision",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_documents_content_hash",
        ),
        sa.CheckConstraint(
            "visibility IN ('conversation', 'project')",
            name="ck_core_documents_visibility",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_core_documents_status",
        ),
        sa.CheckConstraint(
            "visibility != 'project' OR project_id IS NOT NULL",
            name="ck_core_documents_project_visibility",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_documents_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_documents_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_documents_source_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_documents_version",
        ),
    )
    op.create_index(
        "ix_core_documents_tenant_conversation",
        "core_documents",
        ["tenant_id", "conversation_id", "status", "created_at"],
    )
    op.create_index(
        "ix_core_documents_tenant_project",
        "core_documents",
        ["tenant_id", "project_id", "status", "created_at"],
    )

    op.create_table(
        "core_document_revisions",
        sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False),
        sa.Column("document_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("parser", sa.String(128), nullable=False),
        sa.Column("parser_version", sa.String(128), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "document_id",
            "revision",
            name="pk_core_document_revisions",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_core_document_revisions_revision",
        ),
        sa.CheckConstraint(
            "byte_length >= 0",
            name="ck_core_document_revisions_byte_length",
        ),
        sa.CheckConstraint(
            "section_count BETWEEN 1 AND 10000",
            name="ck_core_document_revisions_section_count",
        ),
        sa.CheckConstraint(
            "chunk_count BETWEEN 1 AND 100000",
            name="ck_core_document_revisions_chunk_count",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_document_revisions_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["core_documents.tenant_id", "core_documents.id"],
            name="fk_core_document_revisions_document",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_document_revisions_tenant_document",
        "core_document_revisions",
        ["tenant_id", "document_id", "revision"],
    )

    op.create_table(
        "core_document_chunks",
        sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False),
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("fts_rowid", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section_ordinal", sa.Integer(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, coalesce(normalized_text, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_document_chunks"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "revision",
            "ordinal",
            name="uq_core_document_chunks_revision_ordinal",
        ),
        sa.CheckConstraint("fts_rowid > 0", name="ck_core_document_chunks_fts_rowid"),
        sa.CheckConstraint("revision > 0", name="ck_core_document_chunks_revision"),
        sa.CheckConstraint("ordinal >= 0", name="ck_core_document_chunks_ordinal"),
        sa.CheckConstraint(
            "section_ordinal >= 0",
            name="ck_core_document_chunks_section_ordinal",
        ),
        sa.CheckConstraint(
            "token_count BETWEEN 1 AND 20000",
            name="ck_core_document_chunks_token_count",
        ),
        sa.CheckConstraint(
            "length(revision_hash) = 64 AND revision_hash = lower(revision_hash)",
            name="ck_core_document_chunks_revision_hash",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_document_chunks_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id", "revision"],
            [
                "core_document_revisions.tenant_id",
                "core_document_revisions.document_id",
                "core_document_revisions.revision",
            ],
            name="fk_core_document_chunks_revision",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_document_chunks_tenant_document",
        "core_document_chunks",
        ["tenant_id", "document_id", "revision", "ordinal"],
    )
    op.create_index(
        "uq_core_document_chunks_fts_rowid",
        "core_document_chunks",
        ["fts_rowid"],
        unique=True,
    )
    op.create_index(
        "ix_core_document_chunks_search_vector",
        "core_document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    for table_name in TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_core_document_chunks_search_vector",
        table_name="core_document_chunks",
        postgresql_using="gin",
    )
    op.drop_index("uq_core_document_chunks_fts_rowid", table_name="core_document_chunks")
    op.drop_index(
        "ix_core_document_chunks_tenant_document",
        table_name="core_document_chunks",
    )
    op.drop_table("core_document_chunks")
    op.drop_index(
        "ix_core_document_revisions_tenant_document",
        table_name="core_document_revisions",
    )
    op.drop_table("core_document_revisions")
    op.drop_index("ix_core_documents_tenant_project", table_name="core_documents")
    op.drop_index("ix_core_documents_tenant_conversation", table_name="core_documents")
    op.drop_table("core_documents")


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
