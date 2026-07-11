from __future__ import annotations

from fairy_core.commanding.schema import command_runs, domain_events
from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.schema import document_chunks as core_document_chunks
from fairy_core.storage.schema import document_revisions as core_document_revisions
from fairy_core.storage.schema import documents as core_documents
from fairy_core.storage.schema import projects as core_projects
from fairy_core.storage.schema import research_evidence as core_research_evidence
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
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

execution_jobs = Table(
    "execution_jobs",
    cloud_metadata,
    Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
    Column("job_id", String(36), primary_key=True),
    Column("command_run_id", String(36), nullable=False),
    Column("project_id", String(36)),
    Column("conversation_id", String(36), nullable=False),
    Column("task_id", String(36), nullable=False),
    Column("version_id", String(36)),
    Column("scope_digest", String(64), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("workspace_generation", BigInteger, nullable=False),
    Column("request_lease_fence", BigInteger, nullable=False),
    Column("argv", JSON, nullable=False),
    Column("cwd", String(4096), nullable=False),
    Column("environment", JSON, nullable=False),
    Column("timeout_seconds", Integer, nullable=False),
    Column("output_limit_bytes", Integer, nullable=False),
    Column("network_policy", String(16), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("workspace_archive", LargeBinary, nullable=False),
    Column("archive_sha256", String(64), nullable=False),
    Column("archive_byte_length", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, server_default=text("false")),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("lease_owner", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("lease_fence", BigInteger, nullable=False, server_default="0"),
    Column("spawned_at", DateTime(timezone=True)),
    Column("result_status", String(32)),
    Column("executor", String(128)),
    Column("executor_version", String(64)),
    Column("exit_code", Integer),
    Column("stdout", LargeBinary),
    Column("stderr", LargeBinary),
    Column("stdout_sha256", String(64)),
    Column("stderr_sha256", String(64)),
    Column("output_truncated", Boolean),
    Column("started_at", DateTime(timezone=True)),
    Column("result_recorded_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("tenant_id", "job_id", name="pk_execution_jobs"),
    UniqueConstraint(
        "tenant_id",
        "command_run_id",
        name="uq_execution_jobs_tenant_command_run",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "command_run_id"],
        [command_runs.c.tenant_id, command_runs.c.id],
        name="fk_execution_jobs_command_run",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "status IN ('queued','claimed','running','result_recorded','succeeded',"
        "'failed','timed_out','cancelled','interrupted')",
        name="ck_execution_jobs_status",
    ),
    CheckConstraint(
        "(project_id IS NULL AND version_id IS NULL) OR "
        "(project_id IS NOT NULL AND version_id IS NOT NULL)",
        name="ck_execution_jobs_project_version",
    ),
    CheckConstraint(
        "workspace_generation > 0 AND request_lease_fence > 0 AND lease_fence >= 0",
        name="ck_execution_jobs_fences",
    ),
    CheckConstraint(
        "job_id = command_run_id",
        name="ck_execution_jobs_command_identity",
    ),
    CheckConstraint(
        "timeout_seconds BETWEEN 1 AND 900 AND output_limit_bytes BETWEEN 1024 AND 1048576",
        name="ck_execution_jobs_resources",
    ),
    CheckConstraint(
        "archive_byte_length > 0 AND archive_byte_length <= 134217728 AND "
        "archive_byte_length = octet_length(workspace_archive)",
        name="ck_execution_jobs_archive_size",
    ),
    CheckConstraint(
        "scope_digest ~ '^[0-9a-f]{64}$' AND "
        "request_fingerprint ~ '^[0-9a-f]{64}$' AND "
        "archive_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_execution_jobs_hashes",
    ),
    CheckConstraint(
        "(network_policy = 'none') OR (network_policy = 'public' AND purpose = 'dependency')",
        name="ck_execution_jobs_network_policy",
    ),
    CheckConstraint(
        "purpose IN ('raw','dependency','review')",
        name="ck_execution_jobs_purpose",
    ),
    CheckConstraint(
        "(status IN ('claimed','running','result_recorded')) = "
        "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_fence > 0)",
        name="ck_execution_jobs_active_lease",
    ),
    CheckConstraint(
        "(result_status IS NULL AND executor IS NULL AND executor_version IS NULL "
        "AND exit_code IS NULL AND stdout IS NULL AND stderr IS NULL "
        "AND stdout_sha256 IS NULL AND stderr_sha256 IS NULL "
        "AND output_truncated IS NULL AND started_at IS NULL "
        "AND result_recorded_at IS NULL) OR "
        "(result_status IN ('completed','failed','timed_out','cancelled') "
        "AND executor = 'cloud_oci_worker' AND executor_version = '1.0.0' "
        "AND stdout IS NOT NULL AND stderr IS NOT NULL "
        "AND stdout_sha256 ~ '^[0-9a-f]{64}$' "
        "AND stderr_sha256 ~ '^[0-9a-f]{64}$' "
        "AND output_truncated IS NOT NULL AND started_at IS NOT NULL "
        "AND result_recorded_at IS NOT NULL AND finished_at IS NOT NULL "
        "AND octet_length(stdout) + octet_length(stderr) <= output_limit_bytes)",
        name="ck_execution_jobs_result_evidence",
    ),
    CheckConstraint(
        "(status IN ('queued','claimed','running','interrupted') "
        "AND result_status IS NULL) OR "
        "(status = 'result_recorded' AND result_status IS NOT NULL) OR "
        "(status = 'succeeded' AND result_status = 'completed') OR "
        "(status = 'failed' AND result_status = 'failed') OR "
        "(status = 'timed_out' AND result_status = 'timed_out') OR "
        "(status = 'cancelled' AND (result_status IS NULL OR result_status = 'cancelled'))",
        name="ck_execution_jobs_result_status",
    ),
)

execution_workers = Table(
    "execution_workers",
    cloud_metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("executor", String(128), nullable=False),
    Column("executor_version", String(64), nullable=False),
    Column("attestation_digest", String(64), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("owner_id", name="pk_execution_workers"),
    CheckConstraint(
        "attestation_digest ~ '^[0-9a-f]{64}$'",
        name="ck_execution_workers_attestation_digest",
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
Index(
    "ix_execution_jobs_claim",
    execution_jobs.c.status,
    execution_jobs.c.lease_expires_at,
    execution_jobs.c.created_at,
    postgresql_where=execution_jobs.c.status.in_(("queued", "claimed", "result_recorded")),
)
Index(
    "ix_execution_jobs_tenant_task",
    execution_jobs.c.tenant_id,
    execution_jobs.c.task_id,
    execution_jobs.c.created_at,
)

__all__ = [
    "cloud_metadata",
    "core_document_chunks",
    "core_document_revisions",
    "core_documents",
    "core_projects",
    "core_research_evidence",
    "core_tenants",
    "domain_events",
    "execution_jobs",
    "execution_workers",
    "outbox",
    "version_candidates",
    "worker_leases",
]
