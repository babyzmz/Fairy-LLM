from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)

cloud_metadata = MetaData()

cloud_projects = Table(
    "cloud_projects",
    cloud_metadata,
    Column("project_id", String(36), primary_key=True),
    Column("user_id", String(128), nullable=False),
    Column("revision", BigInteger, nullable=False, server_default="0"),
    Column("active_version_id", String(36)),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

domain_events = Table(
    "domain_events",
    cloud_metadata,
    Column("cursor", BigInteger, Identity(), primary_key=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("user_id", String(128), nullable=False),
    Column("device_id", String(128), nullable=False),
    Column("project_id", String(36)),
    Column("conversation_id", String(36)),
    Column("task_id", String(36)),
    Column("version_id", String(36)),
    Column("task_sequence", BigInteger),
    Column("schema_version", Integer, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

outbox = Table(
    "outbox",
    cloud_metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("topic", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("published_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("lease_owner", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
)

version_candidates = Table(
    "version_candidates",
    cloud_metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("project_id", String(36), nullable=False),
    Column("version_id", String(36), nullable=False),
    Column("base_revision", BigInteger, nullable=False),
    Column("state", String(32), nullable=False, server_default="candidate"),
    Column("manifest", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("project_id", "version_id", name="uq_version_candidate"),
)

worker_leases = Table(
    "worker_leases",
    cloud_metadata,
    Column("resource_type", String(64), primary_key=True),
    Column("resource_id", String(128), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON, nullable=False, server_default="{}"),
)

Index("ix_cloud_projects_user_id", cloud_projects.c.user_id)
Index("ix_domain_events_project_id", domain_events.c.project_id)
Index("ix_domain_events_user_cursor", domain_events.c.user_id, domain_events.c.cursor)
Index("ix_domain_events_task_sequence", domain_events.c.task_id, domain_events.c.task_sequence)
Index(
    "ix_outbox_claim",
    outbox.c.published_at,
    outbox.c.available_at,
    outbox.c.lease_expires_at,
)
Index(
    "ix_version_candidates_project_state",
    version_candidates.c.project_id,
    version_candidates.c.state,
)
