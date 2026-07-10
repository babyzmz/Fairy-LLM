from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fairy_core.domain.errors import VersionConflictError
from sqlalchemy.dialects import postgresql

from fairy_cloud.storage.postgres import (
    PostgresSyncStore,
    ProjectRevisionState,
    build_acquire_worker_lease_statement,
    build_append_event_statement,
    build_claim_outbox_statement,
    build_promote_version_statement,
    cloud_metadata,
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
        "cloud_projects",
        "domain_events",
        "outbox",
        "version_candidates",
        "worker_leases",
    }


def test_active_version_statement_uses_revision_compare_and_swap() -> None:
    sql = str(
        build_promote_version_statement(
            project_id="project-1",
            version_id="version-2",
            expected_revision=7,
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "CLOUD_PROJECTS.REVISION = 7" in sql
    assert "REVISION=(CLOUD_PROJECTS.REVISION+1)" in sql.replace(" ", "")
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
            event_id="event-1",
            user_id="user-1",
            device_id="device-1",
            project_id="project-1",
            schema_version=1,
            event_type="task.created",
            payload={"task_id": "task-1"},
        ).compile(dialect=postgresql.dialect())
    ).upper()

    assert "ON CONFLICT (EVENT_ID) DO NOTHING" in sql
    assert "RETURNING DOMAIN_EVENTS.CURSOR" in sql


def test_worker_lease_uses_expiry_guard_and_fencing_token() -> None:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    sql = str(
        build_acquire_worker_lease_statement(
            resource_type="task",
            resource_id="task-1",
            owner_id="worker-1",
            now=now,
            expires_at=now + timedelta(seconds=30),
            metadata={"region": "local"},
        ).compile(dialect=postgresql.dialect())
    ).upper()

    assert "ON CONFLICT (RESOURCE_TYPE, RESOURCE_ID) DO UPDATE" in sql
    assert "WORKER_LEASES.FENCE +" in sql
    assert "WORKER_LEASES.EXPIRES_AT <=" in sql
    assert "RETURNING WORKER_LEASES.FENCE" in sql
    assert hasattr(PostgresSyncStore, "append_event")
    assert hasattr(PostgresSyncStore, "promote_version")
    assert hasattr(PostgresSyncStore, "claim_outbox")
