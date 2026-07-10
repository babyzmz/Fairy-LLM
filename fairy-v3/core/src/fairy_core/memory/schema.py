from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.commanding.schema import domain_events
from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.schema import (
    ID_LENGTH,
    UTCDateTime,
    conversations,
    projects,
    tasks,
    versions,
)

memory_metadata = MetaData()


def _tenant_id() -> Column[str]:
    return Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True)


def _id() -> Column[str]:
    return Column("id", String(ID_LENGTH), primary_key=True)


def _scope_foreign_keys(prefix: str) -> tuple[ForeignKeyConstraint, ...]:
    return (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [projects.c.tenant_id, projects.c.id],
            name=f"fk_{prefix}_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name=f"fk_{prefix}_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name=f"fk_{prefix}_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name=f"fk_{prefix}_version",
            ondelete="CASCADE",
        ),
    )


memory_observations = Table(
    "memory_observations",
    memory_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("scope_digest", String(64), nullable=False),
    Column("source_event_id", String(ID_LENGTH), nullable=False),
    Column("source_cursor", BigInteger, nullable=False),
    Column("source_type", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("proposed_namespace", String(32), nullable=False),
    Column("authority", String(32), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("sensitivity", String(32), nullable=False),
    Column("scan_result", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("content_fingerprint", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_observations"),
    UniqueConstraint(
        "tenant_id",
        "request_fingerprint",
        name="uq_memory_observations_tenant_request",
    ),
    CheckConstraint(
        "confidence >= 0 AND confidence <= 1",
        name="ck_memory_observations_confidence",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_event_id"],
        [domain_events.c.tenant_id, domain_events.c.event_id],
        name="fk_memory_observations_source_event",
        ondelete="RESTRICT",
    ),
    *_scope_foreign_keys("memory_observations"),
)

memory_claims = Table(
    "memory_claims",
    memory_metadata,
    _tenant_id(),
    _id(),
    Column("namespace", String(32), nullable=False),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH)),
    Column("task_id", String(ID_LENGTH)),
    Column("version_id", String(ID_LENGTH)),
    Column("device_id", String(128)),
    Column("subject", String(512), nullable=False),
    Column("predicate", String(512), nullable=False),
    Column("current_revision", BigInteger, nullable=False),
    Column("conflict_set_id", String(ID_LENGTH)),
    Column("status", String(32), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("content_fingerprint", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_claims"),
    UniqueConstraint(
        "tenant_id",
        "request_fingerprint",
        name="uq_memory_claims_tenant_request",
    ),
    CheckConstraint("current_revision >= 0", name="ck_memory_claims_revision"),
    CheckConstraint(
        "(namespace = 'project_canonical' AND project_id IS NOT NULL) OR "
        "(namespace = 'conversation_draft' AND conversation_id IS NOT NULL) OR "
        "(namespace = 'user_profile' AND project_id IS NULL AND "
        "conversation_id IS NULL AND task_id IS NULL AND version_id IS NULL AND "
        "device_id IS NULL) OR "
        "(namespace = 'device_local' AND device_id IS NOT NULL) OR "
        "(namespace = 'task_episode' AND task_id IS NOT NULL)",
        name="ck_memory_claims_namespace_scope",
    ),
    *_scope_foreign_keys("memory_claims"),
)

memory_claim_revisions = Table(
    "memory_claim_revisions",
    memory_metadata,
    _tenant_id(),
    Column("claim_id", String(ID_LENGTH), primary_key=True),
    Column("revision", BigInteger, primary_key=True),
    Column("value", JSON, nullable=False),
    Column("normalized_text", Text, nullable=False),
    Column("source_observation_ids", JSON, nullable=False),
    Column("source_event_ids", JSON, nullable=False),
    Column("authority", String(32), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("valid_from", UTCDateTime()),
    Column("valid_to", UTCDateTime()),
    Column("recorded_at", UTCDateTime(), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("supersedes_revision", BigInteger),
    Column("resolved_claim_ids", JSON, nullable=False),
    Column("is_current", Boolean, nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("content_fingerprint", String(64), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "claim_id",
        "revision",
        name="pk_memory_claim_revisions",
    ),
    UniqueConstraint(
        "tenant_id",
        "claim_id",
        "request_fingerprint",
        name="uq_memory_claim_revisions_tenant_request",
    ),
    CheckConstraint("revision >= 1", name="ck_memory_claim_revisions_revision"),
    CheckConstraint(
        "confidence >= 0 AND confidence <= 1",
        name="ck_memory_claim_revisions_confidence",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "claim_id"],
        [memory_claims.c.tenant_id, memory_claims.c.id],
        name="fk_memory_claim_revisions_claim",
        ondelete="CASCADE",
    ),
)

memory_tombstones = Table(
    "memory_tombstones",
    memory_metadata,
    _tenant_id(),
    _id(),
    Column("target_kind", String(32), nullable=False),
    Column("target_id", String(ID_LENGTH), nullable=False),
    Column("reason", Text, nullable=False),
    Column("actor", String(128), nullable=False),
    Column("source_event_id", String(ID_LENGTH), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("content_fingerprint", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_memory_tombstones"),
    UniqueConstraint(
        "tenant_id",
        "target_kind",
        "target_id",
        name="uq_memory_tombstones_tenant_target",
    ),
    UniqueConstraint(
        "tenant_id",
        "request_fingerprint",
        name="uq_memory_tombstones_tenant_request",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_event_id"],
        [domain_events.c.tenant_id, domain_events.c.event_id],
        name="fk_memory_tombstones_source_event",
        ondelete="RESTRICT",
    ),
)

Index(
    "ix_memory_observations_tenant_scope",
    memory_observations.c.tenant_id,
    memory_observations.c.proposed_namespace,
    memory_observations.c.project_id,
    memory_observations.c.conversation_id,
    memory_observations.c.status,
)
Index(
    "ix_memory_claims_tenant_scope",
    memory_claims.c.tenant_id,
    memory_claims.c.namespace,
    memory_claims.c.project_id,
    memory_claims.c.conversation_id,
    memory_claims.c.status,
)
Index(
    "uq_memory_claim_revisions_current",
    memory_claim_revisions.c.tenant_id,
    memory_claim_revisions.c.claim_id,
    unique=True,
    sqlite_where=memory_claim_revisions.c.is_current.is_(True),
    postgresql_where=memory_claim_revisions.c.is_current.is_(True),
)
Index(
    "ix_memory_claim_revisions_validity",
    memory_claim_revisions.c.tenant_id,
    memory_claim_revisions.c.valid_to,
    postgresql_where=memory_claim_revisions.c.is_current.is_(True),
    sqlite_where=memory_claim_revisions.c.is_current.is_(True),
)


__all__ = [
    "memory_claim_revisions",
    "memory_claims",
    "memory_metadata",
    "memory_observations",
    "memory_tombstones",
]
