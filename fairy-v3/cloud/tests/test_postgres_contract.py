from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fairy_core.commanding.schema import command_metadata
from fairy_core.domain.errors import VersionConflictError
from fairy_core.memory.schema import memory_metadata
from fairy_core.storage.schema import state_metadata
from sqlalchemy.dialects import postgresql

from fairy_cloud.storage.postgres import (
    PostgresSyncStore,
    ProjectRevisionState,
    build_acquire_worker_lease_statement,
    build_append_event_statement,
    build_claim_outbox_statement,
    build_events_after_statement,
    build_promote_version_statement,
    canonical_payload_fingerprint,
    cloud_metadata,
    tenant_id_for_user,
)


def test_outbox_claim_uses_postgres_skip_locked_without_external_broker() -> None:
    sql = str(
        build_claim_outbox_statement(batch_size=25).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 25" in sql
    assert {table.name for table in cloud_metadata.tables.values()} == {
        "core_tenants",
        "outbox",
        "version_candidates",
        "worker_leases",
    }
    canonical_tables = {
        table.name
        for metadata in (state_metadata, command_metadata, memory_metadata, cloud_metadata)
        for table in metadata.tables.values()
    }
    assert {
        "core_projects",
        "core_runtime_sessions",
        "core_preview_sessions",
        "core_artifacts",
        "command_runs",
        "domain_events",
        "outbox",
        "task_event_sequences",
        "version_candidates",
        "worker_leases",
        "memory_observations",
        "memory_claims",
        "memory_claim_revisions",
        "memory_tombstones",
        "memory_snapshots",
        "memory_snapshot_items",
        "memory_search_documents",
        "memory_access_log",
        "memory_projection_checkpoints",
    } <= canonical_tables
    assert {"tenant_id", "lease_fence"} <= {
        column.name for column in cloud_metadata.tables["outbox"].c
    }
    claim_index = next(
        index
        for index in cloud_metadata.tables["outbox"].indexes
        if index.name == "ix_outbox_claim_global"
    )
    assert [column.name for column in claim_index.columns] == [
        "published_at",
        "available_at",
        "lease_expires_at",
        "id",
    ]
    assert "published_at IS NULL" in str(claim_index.dialect_options["postgresql"]["where"])


def test_event_subscription_uses_tenant_as_the_authorization_boundary() -> None:
    sql = str(
        build_events_after_statement(
            tenant_id="tenant-a",
            cursor=7,
            limit=50,
            visibilities=frozenset({"user", "developer"}),
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "DOMAIN_EVENTS.TENANT_ID = 'TENANT-A'" in sql
    assert "DOMAIN_EVENTS.CURSOR > 7" in sql
    assert "DOMAIN_EVENTS.USER_ID =" not in sql


def test_runtime_metadata_matches_canonical_constraint_names() -> None:
    constraint_names = {
        constraint.name
        for metadata in (state_metadata, command_metadata, memory_metadata, cloud_metadata)
        for table in metadata.tables.values()
        for constraint in table.constraints
        if constraint.name is not None
    }

    assert {
        "pk_core_tenants",
        "pk_core_projects",
        "pk_core_conversations",
        "pk_core_versions",
        "pk_core_tasks",
        "pk_core_changesets",
        "pk_core_approvals",
        "pk_core_checkpoints",
        "pk_core_runtime_sessions",
        "pk_core_preview_sessions",
        "pk_core_artifacts",
        "pk_command_runs",
        "pk_task_event_sequences",
        "pk_domain_events",
        "pk_outbox",
        "pk_version_candidates",
        "pk_worker_leases",
        "fk_outbox_event",
        "fk_version_candidates_project",
        "fk_core_conversations_project",
        "fk_core_versions_project",
        "fk_core_tasks_conversation",
        "fk_core_changesets_task",
        "fk_core_approvals_changeset",
        "fk_core_checkpoints_version",
        "uq_domain_events_tenant_event",
        "uq_outbox_tenant_event",
        "uq_version_candidate_tenant_project_version",
        "pk_memory_observations",
        "pk_memory_claims",
        "pk_memory_claim_revisions",
        "pk_memory_tombstones",
        "fk_memory_observations_source_event",
        "fk_memory_claim_revisions_claim",
        "fk_memory_tombstones_source_event",
        "ck_memory_claims_namespace_scope",
        "pk_memory_snapshots",
        "pk_memory_snapshot_items",
        "pk_memory_search_documents",
        "pk_memory_access_log",
        "pk_memory_projection_checkpoints",
        "uq_memory_snapshots_tenant_task",
        "fk_memory_snapshot_items_snapshot",
        "fk_memory_access_log_snapshot",
        "ck_core_tasks_memory_snapshot_binding",
        "ck_core_runtime_sessions_handle_port",
        "ck_core_preview_sessions_active_url",
        "ck_memory_snapshots_status",
    } <= constraint_names
    assert "fk_domain_events_run" not in constraint_names
    index_names = {
        index.name
        for metadata in (state_metadata, command_metadata, memory_metadata, cloud_metadata)
        for table in metadata.tables.values()
        for index in table.indexes
    }
    assert {
        "uq_memory_search_documents_fts_rowid",
        "uq_core_preview_sessions_active_task",
    } <= index_names


def test_active_version_statement_uses_revision_compare_and_swap() -> None:
    sql = str(
        build_promote_version_statement(
            tenant_id="tenant-a",
            project_id="project-1",
            version_id="version-2",
            expected_revision=7,
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "CORE_PROJECTS.TENANT_ID = 'TENANT-A'" in sql
    assert "CORE_PROJECTS.REVISION = 7" in sql
    assert "REVISION=(CORE_PROJECTS.REVISION+1)" in sql.replace(" ", "")
    assert "RETURNING" in sql


def test_stale_device_version_is_retained_as_candidate() -> None:
    state = ProjectRevisionState(
        project_id="project-1",
        revision=3,
        active_version_id="version-active",
    )

    with pytest.raises(VersionConflictError):
        state.promote(version_id="version-device-b", expected_revision=2)

    assert state.active_version_id == "version-active"
    assert state.revision == 3
    assert state.candidate_version_ids == ["version-device-b"]


def test_event_insert_is_idempotent_and_returns_global_cursor() -> None:
    sql = str(
        build_append_event_statement(
            tenant_id="tenant-a",
            event_id="event-1",
            run_id="run-1",
            user_id="user-1",
            device_id="device-1",
            project_id="project-1",
            schema_version=1,
            event_type="task.created",
            payload={"task_id": "task-1"},
        ).compile(dialect=postgresql.dialect())
    ).upper()

    assert "ON CONFLICT DO NOTHING" in sql
    assert "RUN_ID" in sql.partition("VALUES")[0]
    assert "RETURNING DOMAIN_EVENTS.CURSOR" in sql
    assert "DOMAIN_EVENTS.CREATED_AT" in sql.partition("RETURNING")[2]


def test_payload_fingerprint_is_canonical_and_content_sensitive() -> None:
    assert canonical_payload_fingerprint({"b": 2, "a": [1]}) == canonical_payload_fingerprint(
        {"a": [1], "b": 2}
    )
    assert canonical_payload_fingerprint({"a": 1}) != canonical_payload_fingerprint({"a": 2})


def test_tenant_id_preserves_the_exact_oidc_subject() -> None:
    assert tenant_id_for_user("user-a") != tenant_id_for_user(" user-a ")

    with pytest.raises(ValueError, match="must not be empty"):
        tenant_id_for_user("  ")


def test_worker_lease_uses_expiry_guard_and_fencing_token() -> None:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    sql = str(
        build_acquire_worker_lease_statement(
            tenant_id="tenant-a",
            resource_type="task",
            resource_id="task-1",
            owner_id="worker-1",
            now=now,
            expires_at=now + timedelta(seconds=30),
            metadata={"region": "local"},
        ).compile(dialect=postgresql.dialect())
    ).upper()

    assert "ON CONFLICT (TENANT_ID, RESOURCE_TYPE, RESOURCE_ID) DO UPDATE" in sql
    assert "WORKER_LEASES.FENCE +" in sql
    assert "WORKER_LEASES.EXPIRES_AT <=" in sql
    assert "RETURNING WORKER_LEASES.FENCE" in sql
    assert hasattr(PostgresSyncStore, "append_event")
    assert hasattr(PostgresSyncStore, "promote_version")
    assert hasattr(PostgresSyncStore, "claim_outbox")
