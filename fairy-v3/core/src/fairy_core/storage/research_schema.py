from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.storage.types import UTCDateTime


def build_research_evidence_table(
    *,
    metadata: MetaData,
    tenant_id_column: Callable[[], Column[str]],
    id_column: Callable[[], Column[str]],
    artifacts: Table,
    projects: Table,
    conversations: Table,
    tasks: Table,
    versions: Table,
    id_length: int,
) -> Table:
    return Table(
        "core_research_evidence",
        metadata,
        tenant_id_column(),
        id_column(),
        Column("artifact_id", String(id_length), nullable=False),
        Column("project_id", String(id_length)),
        Column("conversation_id", String(id_length), nullable=False),
        Column("task_id", String(id_length), nullable=False),
        Column("version_id", String(id_length)),
        Column("ordinal", Integer, nullable=False),
        Column("source_url", String(2048), nullable=False),
        Column("canonical_url", String(2048), nullable=False),
        Column("redirect_chain", JSON, nullable=False),
        Column("title", String(1000), nullable=False),
        Column("media_type", String(255), nullable=False),
        Column("byte_length", BigInteger, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("excerpt", Text, nullable=False),
        Column("fetched_at", UTCDateTime(), nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_research_evidence"),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "ordinal",
            name="uq_core_research_evidence_artifact_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "canonical_url",
            name="uq_core_research_evidence_artifact_url",
        ),
        CheckConstraint("ordinal > 0", name="ck_core_research_evidence_ordinal"),
        CheckConstraint(
            "byte_length >= 0",
            name="ck_core_research_evidence_byte_length",
        ),
        CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_core_research_evidence_content_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            [artifacts.c.tenant_id, artifacts.c.id],
            name="fk_core_research_evidence_artifact",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [projects.c.tenant_id, projects.c.id],
            name="fk_core_research_evidence_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_research_evidence_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_research_evidence_task",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_research_evidence_version",
        ),
    )


__all__ = ["build_research_evidence_table"]
