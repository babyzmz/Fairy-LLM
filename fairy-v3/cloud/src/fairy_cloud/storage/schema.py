from __future__ import annotations

from fairy_core.commanding.schema import domain_events
from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.schema import projects as core_projects
from fairy_core.storage.schema import research_evidence as core_research_evidence
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)

cloud_metadata = MetaData()

core_tenants = Table(
    "core_tenants",
    cloud_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("subject_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", name="pk_core_tenants"),
    UniqueConstraint("subject_id", name="uq_core_tenants_subject"),
)

outbox = Table(
    "outbox",
    cloud_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("event_id", String(36), nullable=False),
    Column("topic", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("published_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("lease_owner", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("lease_fence", BigInteger, nullable=False, server_default="0"),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_outbox"),
    UniqueConstraint("tenant_id", "event_id", name="uq_outbox_tenant_event"),
    ForeignKeyConstraint(
        ["tenant_id", "event_id"],
        [domain_events.c.tenant_id, domain_events.c.event_id],
        name="fk_outbox_event",
        ondelete="CASCADE",
    ),
)

version_candidates = Table(
    "version_candidates",
    cloud_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("project_id", String(36), nullable=False),
    Column("version_id", String(36), nullable=False),
    Column("base_revision", BigInteger, nullable=False),
    Column("state", String(32), nullable=False, server_default="candidate"),
    Column("manifest", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_version_candidates"),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "version_id",
        name="uq_version_candidate_tenant_project_version",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [core_projects.c.tenant_id, core_projects.c.id],
        name="fk_version_candidates_project",
        ondelete="CASCADE",
    ),
)

worker_leases = Table(
    "worker_leases",
    cloud_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("resource_type", String(64), primary_key=True),
    Column("resource_id", String(128), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON, nullable=False, server_default=text("'{}'::json")),
    PrimaryKeyConstraint(
        "tenant_id",
        "resource_type",
        "resource_id",
        name="pk_worker_leases",
    ),
)

Index(
    "ix_outbox_claim_global",
    outbox.c.published_at,
    outbox.c.available_at,
    outbox.c.lease_expires_at,
    outbox.c.id,
    postgresql_where=outbox.c.published_at.is_(None),
)
Index(
    "ix_version_candidates_tenant_project_state",
    version_candidates.c.tenant_id,
    version_candidates.c.project_id,
    version_candidates.c.state,
)

__all__ = [
    "cloud_metadata",
    "core_projects",
    "core_research_evidence",
    "core_tenants",
    "domain_events",
    "outbox",
    "version_candidates",
    "worker_leases",
]
