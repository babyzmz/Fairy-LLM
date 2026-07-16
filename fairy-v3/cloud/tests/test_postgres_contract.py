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
        "execution_jobs",
        "execution_workers",
        "runtime_leases",
        "runtime_routes",
        "runtime_workers",
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
        "core_assistant_turns",
        "core_assistant_message_sequences",
        "core_assistant_messages",
        "core_assistant_tool_invocations",
        "core_task_workspaces",
        "core_project_indexes",
        "core_research_evidence",
        "core_documents",
        "core_document_revisions",
        "core_document_chunks",
        "command_runs",
        "domain_events",
        "event_ledgers",
        "outbox",
        "task_event_sequences",
        "version_candidates",
        "worker_leases",
        "execution_jobs",
        "execution_workers",
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
    assert "purpose" in cloud_metadata.tables["execution_jobs"].c
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
        "pk_core_assistant_turns",
        "pk_core_assistant_message_sequences",
        "pk_core_assistant_messages",
        "pk_core_assistant_tool_invocations",
        "pk_core_task_workspaces",
        "pk_core_project_indexes",
        "pk_core_research_evidence",
        "pk_core_documents",
        "pk_core_document_revisions",
        "pk_core_document_chunks",
        "pk_command_runs",
        "pk_event_ledgers",
        "pk_task_event_sequences",
        "pk_domain_events",
        "pk_outbox",
        "pk_version_candidates",
        "pk_worker_leases",
        "pk_execution_jobs",
        "pk_execution_workers",
        "fk_execution_jobs_command_run",
        "uq_execution_jobs_tenant_command_run",
        "fk_outbox_event",
        "fk_version_candidates_project",
        "fk_core_conversations_project",
        "fk_core_versions_project",
        "fk_core_tasks_conversation",
        "fk_core_changesets_task",
        "fk_core_approvals_changeset",
        "fk_core_approvals_tool_invocation",
        "fk_core_task_workspaces_task",
        "fk_core_task_workspaces_version",
        "fk_core_project_indexes_version",
        "fk_core_checkpoints_version",
        "uq_domain_events_tenant_event",
        "uq_event_ledgers_id",
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
        "uq_core_assistant_turns_tenant_idempotency",
        "uq_core_assistant_messages_conversation_sequence",
        "uq_core_assistant_tool_invocations_turn_arguments",
        "uq_core_assistant_tool_invocations_turn_provider_call",
        "uq_core_approvals_tenant_command_run",
        "uq_core_approvals_tenant_tool_invocation",
        "uq_core_research_evidence_artifact_url",
        "uq_core_documents_tenant_idempotency",
        "uq_core_document_chunks_revision_ordinal",
        "ck_memory_snapshots_status",
        "ck_execution_jobs_command_identity",
        "ck_execution_jobs_active_lease",
        "ck_execution_jobs_result_evidence",
        "ck_execution_jobs_result_status",
        "ck_execution_jobs_network_policy",
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
        "ix_core_research_evidence_tenant_artifact",
        "uq_core_document_chunks_fts_rowid",
        "ix_core_document_chunks_tenant_document",
        "ix_core_task_workspaces_tenant_version",
        "ix_core_project_indexes_tenant_project",
        "ix_execution_jobs_claim",
        "ix_execution_jobs_tenant_task",
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
