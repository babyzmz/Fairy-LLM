from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from fairy_core.storage.schema import ID_LENGTH, TENANT_ID_LENGTH, UTCDateTime

command_metadata = MetaData()

command_runs = Table(
    "command_runs",
    command_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("command_name", String(128), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("scope", JSON, nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("input", JSON, nullable=False),
    Column("risk_level", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("lease_owner", String(128)),
    Column("lease_until", UTCDateTime()),
    Column("lease_fence", BigInteger, nullable=False, server_default="0"),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_command_runs"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_command_runs_tenant_idempotency",
    ),
)

task_event_sequences = Table(
    "task_event_sequences",
    command_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("task_id", String(ID_LENGTH), primary_key=True),
    Column("last_sequence", BigInteger, nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "task_id",
        name="pk_task_event_sequences",
    ),
)

_CURSOR_TYPE = BigInteger().with_variant(Integer, "sqlite")

domain_events = Table(
    "domain_events",
    command_metadata,
    Column("cursor", _CURSOR_TYPE, Identity(), primary_key=True, autoincrement=True),
    Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
    Column("event_id", String(ID_LENGTH), nullable=False),
    Column("run_id", String(ID_LENGTH)),
    Column("user_id", String(128), nullable=False),
    Column("device_id", String(128), nullable=False),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH)),
    Column("task_id", String(ID_LENGTH)),
    Column("version_id", String(ID_LENGTH)),
    Column("task_sequence", BigInteger),
    Column("schema_version", Integer, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("message", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("cursor", name="pk_domain_events"),
    UniqueConstraint("tenant_id", "event_id", name="uq_domain_events_tenant_event"),
    UniqueConstraint(
        "tenant_id",
        "task_id",
        "task_sequence",
        name="uq_domain_events_tenant_task_sequence",
    ),
)

Index(
    "ix_command_runs_tenant_status",
    command_runs.c.tenant_id,
    command_runs.c.status,
    command_runs.c.created_at,
)
Index("ix_domain_events_tenant_cursor", domain_events.c.tenant_id, domain_events.c.cursor)
Index("ix_domain_events_tenant_project", domain_events.c.tenant_id, domain_events.c.project_id)
