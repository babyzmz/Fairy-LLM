"""Add immutable Knowledge revisions, snapshots, and Harness manifests.

Revision ID: 20260721_0039
Revises: 20260720_0038
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0039"
down_revision: str | Sequence[str] | None = "20260720_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "core_knowledge_sources",
    "core_knowledge_collections",
    "core_knowledge_items",
    "core_knowledge_revisions",
    "core_knowledge_sync_runs",
    "core_knowledge_snapshots",
    "core_knowledge_snapshot_items",
    "core_harness_context_manifests",
)


def upgrade() -> None:
    for table_name in ("core_tasks", "core_assistant_turns"):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("knowledge_snapshot_id", sa.String(36)))
            batch.add_column(sa.Column("knowledge_snapshot_hash", sa.String(64)))
            batch.add_column(sa.Column("harness_manifest_id", sa.String(36)))
            batch.add_column(sa.Column("harness_manifest_hash", sa.String(64)))
            knowledge_binding = (
                "(knowledge_snapshot_id IS NULL AND knowledge_snapshot_hash IS NULL) OR "
                "(knowledge_snapshot_id IS NOT NULL AND length(knowledge_snapshot_hash) = 64 "
                "AND knowledge_snapshot_hash = lower(knowledge_snapshot_hash))"
            )
            harness_binding = (
                "(harness_manifest_id IS NULL AND harness_manifest_hash IS NULL) OR "
                "(harness_manifest_id IS NOT NULL AND length(harness_manifest_hash) = 64 "
                "AND harness_manifest_hash = lower(harness_manifest_hash))"
            )
            batch.create_check_constraint(
                f"ck_{table_name}_knowledge_snapshot_binding",
                knowledge_binding,
            )
            batch.create_check_constraint(
                f"ck_{table_name}_harness_manifest_binding",
                harness_binding,
            )

    op.create_table(
        "core_knowledge_sources",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("device_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("display_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("sync_cursor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_sources"),
        sa.CheckConstraint(
            "revision >= 1 AND sync_cursor >= 0", name="ck_knowledge_sources_revisions"
        ),
    )
    op.create_table(
        "core_knowledge_collections",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("read_scope", sa.String(32), nullable=False),
        sa.Column("allowed_directories", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("managed_directory", sa.String(1024), nullable=False),
        sa.Column("scope_kind", sa.String(64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_collections"),
        sa.UniqueConstraint("tenant_id", "source_id", name="uq_knowledge_collections_source"),
        sa.CheckConstraint(
            "read_scope IN ('selected_directories', 'whole_vault')",
            name="ck_knowledge_collections_read_scope",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_knowledge_collections_revision"),
    )
    op.create_table(
        "core_knowledge_items",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        sa.Column("current_revision", sa.BigInteger(), nullable=False),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_items"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "relative_path",
            name="uq_knowledge_items_source_path",
        ),
        sa.CheckConstraint("current_revision >= 0", name="ck_knowledge_items_revision"),
    )
    op.create_table(
        "core_knowledge_revisions",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("frontmatter", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("source_cursor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_revisions"),
        sa.UniqueConstraint(
            "tenant_id", "item_id", "revision", name="uq_knowledge_revisions_item_revision"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND source_cursor >= 1",
            name="ck_knowledge_revisions_counters",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_knowledge_revisions_content_hash",
        ),
        sa.CheckConstraint(
            "length(revision_hash) = 64 AND revision_hash = lower(revision_hash)",
            name="ck_knowledge_revisions_revision_hash",
        ),
    )
    op.create_table(
        "core_knowledge_sync_runs",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expected_source_revision", sa.BigInteger(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_cursor", sa.BigInteger(), nullable=False),
        sa.Column("scanned_count", sa.BigInteger(), nullable=False),
        sa.Column("changed_count", sa.BigInteger(), nullable=False),
        sa.Column("deleted_count", sa.BigInteger(), nullable=False),
        sa.Column("failed_count", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("cancellation_revision", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_sync_runs"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_knowledge_sync_runs_request",
        ),
        sa.CheckConstraint(
            "expected_source_revision >= 1 AND source_cursor >= 0 AND scanned_count >= 0 "
            "AND changed_count >= 0 AND deleted_count >= 0 AND failed_count >= 0 "
            "AND lease_fence >= 0 AND attempts >= 0 AND cancellation_revision >= 0",
            name="ck_knowledge_sync_runs_counts",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')",
            name="ck_knowledge_sync_runs_status",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_knowledge_sync_runs_lease_pair",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_knowledge_sync_runs_fingerprint",
        ),
    )
    op.create_table(
        "core_knowledge_snapshots",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36)),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("source_cursor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("degraded_reason", sa.String(128)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_knowledge_snapshots"),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_knowledge_snapshots_task"),
        sa.UniqueConstraint(
            "tenant_id", "request_fingerprint", name="uq_knowledge_snapshots_request"
        ),
        sa.CheckConstraint("source_cursor >= 0", name="ck_knowledge_snapshots_cursor"),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash)",
            name="ck_knowledge_snapshots_hash",
        ),
    )
    op.create_table(
        "core_knowledge_snapshot_items",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "snapshot_id", "ordinal", name="pk_core_knowledge_snapshot_items"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_knowledge_snapshot_items_ordinal"),
    )
    op.create_table(
        "core_harness_context_manifests",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("workspace_version_id", sa.String(36)),
        sa.Column("memory_snapshot_id", sa.String(36), nullable=False),
        sa.Column("memory_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("knowledge_snapshot_id", sa.String(36), nullable=False),
        sa.Column("knowledge_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("tool_registry_generation", sa.BigInteger(), nullable=False),
        sa.Column("tool_registry_digest", sa.String(64), nullable=False),
        sa.Column("tool_definitions", sa.JSON(), nullable=False),
        sa.Column("skill_package_digests", sa.JSON(), nullable=False),
        sa.Column("mcp_capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_selection", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_harness_context_manifests"),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_harness_context_manifests_task"),
        sa.CheckConstraint("tool_registry_generation >= 0", name="ck_harness_registry_generation"),
    )
    op.create_index(
        "ix_knowledge_sources_project_status",
        "core_knowledge_sources",
        ["tenant_id", "project_id", "status"],
    )
    op.create_index(
        "ix_knowledge_items_project_current",
        "core_knowledge_items",
        ["tenant_id", "project_id", "tombstoned_at"],
    )
    op.create_index(
        "ix_knowledge_revisions_project_cursor",
        "core_knowledge_revisions",
        ["tenant_id", "project_id", "source_cursor"],
    )
    op.create_index(
        "ix_knowledge_sync_runs_claim",
        "core_knowledge_sync_runs",
        ["tenant_id", "status", "lease_until", "started_at"],
    )
    for table_name in TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(TABLES):
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table_name}" ON "{table_name}"')
        )
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
        op.drop_table(table_name)
    for table_name in ("core_assistant_turns", "core_tasks"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(
                f"ck_{table_name}_harness_manifest_binding",
                type_="check",
            )
            batch.drop_constraint(
                f"ck_{table_name}_knowledge_snapshot_binding",
                type_="check",
            )
            for column in (
                "harness_manifest_hash",
                "harness_manifest_id",
                "knowledge_snapshot_hash",
                "knowledge_snapshot_id",
            ):
                batch.drop_column(column)


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
