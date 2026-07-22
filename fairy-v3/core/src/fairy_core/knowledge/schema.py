from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36
knowledge_metadata = MetaData()


def _tenant_id() -> Column[str]:
    return Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True)


knowledge_sources = Table(
    "core_knowledge_sources",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("device_id", String(255), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("display_path", String(1024), nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("sync_cursor", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_sources"),
    CheckConstraint("revision >= 1 AND sync_cursor >= 0", name="ck_knowledge_sources_revisions"),
)

knowledge_collections = Table(
    "core_knowledge_collections",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("source_id", String(ID_LENGTH), nullable=False),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("read_scope", String(32), nullable=False),
    Column("allowed_directories", JSON, nullable=False),
    Column("filters", JSON, nullable=False),
    Column("managed_directory", String(1024), nullable=False),
    Column("scope_kind", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_collections"),
    UniqueConstraint("tenant_id", "source_id", name="uq_knowledge_collections_source"),
    CheckConstraint(
        "read_scope IN ('selected_directories', 'whole_vault')",
        name="ck_knowledge_collections_read_scope",
    ),
    CheckConstraint("revision >= 1", name="ck_knowledge_collections_revision"),
)

knowledge_items = Table(
    "core_knowledge_items",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("source_id", String(ID_LENGTH), nullable=False),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("relative_path", String(1024), nullable=False),
    Column("current_revision_id", String(ID_LENGTH)),
    Column("current_revision", BigInteger, nullable=False),
    Column("tombstoned_at", UTCDateTime()),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_items"),
    UniqueConstraint(
        "tenant_id", "source_id", "relative_path", name="uq_knowledge_items_source_path"
    ),
    CheckConstraint("current_revision >= 0", name="ck_knowledge_items_revision"),
)

knowledge_revisions = Table(
    "core_knowledge_revisions",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("item_id", String(ID_LENGTH), nullable=False),
    Column("source_id", String(ID_LENGTH), nullable=False),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("relative_path", String(1024), nullable=False),
    Column("title", String(200), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("revision_hash", String(64), nullable=False),
    Column("links", JSON, nullable=False),
    Column("frontmatter", JSON, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("source_cursor", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_revisions"),
    UniqueConstraint(
        "tenant_id", "item_id", "revision", name="uq_knowledge_revisions_item_revision"
    ),
    CheckConstraint("revision >= 1 AND source_cursor >= 1", name="ck_knowledge_revisions_counters"),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_knowledge_revisions_content_hash",
    ),
    CheckConstraint(
        "length(revision_hash) = 64 AND revision_hash = lower(revision_hash)",
        name="ck_knowledge_revisions_revision_hash",
    ),
)

knowledge_sync_runs = Table(
    "core_knowledge_sync_runs",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("source_id", String(ID_LENGTH), nullable=False),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("status", String(32), nullable=False),
    Column("expected_source_revision", BigInteger, nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("source_cursor", BigInteger, nullable=False),
    Column("scanned_count", BigInteger, nullable=False),
    Column("changed_count", BigInteger, nullable=False),
    Column("deleted_count", BigInteger, nullable=False),
    Column("failed_count", BigInteger, nullable=False),
    Column("error_code", String(128)),
    Column("lease_owner", String(255)),
    Column("lease_until", UTCDateTime()),
    Column("lease_fence", BigInteger, nullable=False),
    Column("attempts", BigInteger, nullable=False),
    Column("cancellation_revision", BigInteger, nullable=False),
    Column("started_at", UTCDateTime(), nullable=False),
    Column("completed_at", UTCDateTime()),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_sync_runs"),
    UniqueConstraint(
        "tenant_id",
        "request_fingerprint",
        name="uq_knowledge_sync_runs_request",
    ),
    CheckConstraint(
        "expected_source_revision >= 1 AND source_cursor >= 0 AND scanned_count >= 0 "
        "AND changed_count >= 0 AND deleted_count >= 0 AND failed_count >= 0 "
        "AND lease_fence >= 0 AND attempts >= 0 AND cancellation_revision >= 0",
        name="ck_knowledge_sync_runs_counts",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')",
        name="ck_knowledge_sync_runs_status",
    ),
    CheckConstraint(
        "(lease_owner IS NULL AND lease_until IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
        name="ck_knowledge_sync_runs_lease_pair",
    ),
    CheckConstraint(
        "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
        name="ck_knowledge_sync_runs_fingerprint",
    ),
)

knowledge_snapshots = Table(
    "core_knowledge_snapshots",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("source_cursor", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("degraded_reason", String(128)),
    Column("content_hash", String(64), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_snapshots"),
    UniqueConstraint("tenant_id", "task_id", name="uq_knowledge_snapshots_task"),
    UniqueConstraint("tenant_id", "request_fingerprint", name="uq_knowledge_snapshots_request"),
    CheckConstraint("source_cursor >= 0", name="ck_knowledge_snapshots_cursor"),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_knowledge_snapshots_hash",
    ),
)

knowledge_snapshot_items = Table(
    "core_knowledge_snapshot_items",
    knowledge_metadata,
    _tenant_id(),
    Column("snapshot_id", String(ID_LENGTH), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("item_id", String(ID_LENGTH), nullable=False),
    Column("revision_id", String(ID_LENGTH), nullable=False),
    Column("source_id", String(ID_LENGTH), nullable=False),
    Column("relative_path", String(1024), nullable=False),
    Column("title", String(200), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("revision_hash", String(64), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id", "snapshot_id", "ordinal", name="pk_core_knowledge_snapshot_items"
    ),
    CheckConstraint("ordinal >= 0", name="ck_knowledge_snapshot_items_ordinal"),
)

harness_context_manifests = Table(
    "core_harness_context_manifests",
    knowledge_metadata,
    _tenant_id(),
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("workspace_version_id", String(ID_LENGTH)),
    Column("memory_snapshot_id", String(ID_LENGTH), nullable=False),
    Column("memory_snapshot_hash", String(64), nullable=False),
    Column("knowledge_snapshot_id", String(ID_LENGTH), nullable=False),
    Column("knowledge_snapshot_hash", String(64), nullable=False),
    Column("tool_registry_generation", BigInteger, nullable=False),
    Column("tool_registry_digest", String(64), nullable=False),
    Column("tool_definitions", JSON, nullable=False),
    Column("skill_package_digests", JSON, nullable=False),
    Column("mcp_capability_snapshot", JSON, nullable=False),
    Column("model_selection", JSON, nullable=False),
    Column("budget", JSON, nullable=False),
    Column("persona_version", String(64), nullable=False, server_default="legacy"),
    Column("persona_digest", String(64), nullable=False, server_default="0" * 64),
    Column("persona_instruction", Text, nullable=False, server_default=""),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_harness_context_manifests"),
    UniqueConstraint("tenant_id", "task_id", name="uq_harness_context_manifests_task"),
    CheckConstraint("tool_registry_generation >= 0", name="ck_harness_registry_generation"),
)

Index(
    "ix_knowledge_sources_project_status",
    knowledge_sources.c.tenant_id,
    knowledge_sources.c.project_id,
    knowledge_sources.c.status,
)
Index(
    "ix_knowledge_items_project_current",
    knowledge_items.c.tenant_id,
    knowledge_items.c.project_id,
    knowledge_items.c.tombstoned_at,
)
Index(
    "ix_knowledge_revisions_project_cursor",
    knowledge_revisions.c.tenant_id,
    knowledge_revisions.c.project_id,
    knowledge_revisions.c.source_cursor,
)
Index(
    "ix_knowledge_sync_runs_claim",
    knowledge_sync_runs.c.tenant_id,
    knowledge_sync_runs.c.status,
    knowledge_sync_runs.c.lease_until,
    knowledge_sync_runs.c.started_at,
)


__all__ = [
    "harness_context_manifests",
    "knowledge_collections",
    "knowledge_items",
    "knowledge_metadata",
    "knowledge_revisions",
    "knowledge_snapshot_items",
    "knowledge_snapshots",
    "knowledge_sources",
    "knowledge_sync_runs",
]
