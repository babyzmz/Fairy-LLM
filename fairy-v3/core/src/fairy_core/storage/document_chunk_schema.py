from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.storage.types import UTCDateTime


def build_document_chunks_table(
    *, metadata, tenant_id_column, id_column, document_revisions, id_length
):
    ID_LENGTH = id_length
    return Table(
        "core_document_chunks",
        metadata,
        tenant_id_column(),
        id_column(),
        Column("fts_rowid", BigInteger, nullable=False),
        Column("document_id", String(ID_LENGTH), nullable=False),
        Column("revision", Integer, nullable=False),
        Column("revision_hash", String(64), nullable=False),
        Column("ordinal", Integer, nullable=False),
        Column("section_ordinal", Integer, nullable=False),
        Column("locator", JSON, nullable=False),
        Column("normalized_text", Text, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("token_count", Integer, nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_document_chunks"),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "revision",
            "ordinal",
            name="uq_core_document_chunks_revision_ordinal",
        ),
        CheckConstraint("fts_rowid > 0", name="ck_core_document_chunks_fts_rowid"),
        CheckConstraint("revision > 0", name="ck_core_document_chunks_revision"),
        CheckConstraint("ordinal >= 0", name="ck_core_document_chunks_ordinal"),
        CheckConstraint(
            "section_ordinal >= 0",
            name="ck_core_document_chunks_section_ordinal",
        ),
        CheckConstraint(
            "token_count BETWEEN 1 AND 20000",
            name="ck_core_document_chunks_token_count",
        ),
        CheckConstraint(
            "length(revision_hash) = 64 AND revision_hash = lower(revision_hash)",
            name="ck_core_document_chunks_revision_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_document_chunks_content_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "revision"],
            [
                document_revisions.c.tenant_id,
                document_revisions.c.document_id,
                document_revisions.c.revision,
            ],
            name="fk_core_document_chunks_revision",
            ondelete="CASCADE",
        ),
    )
