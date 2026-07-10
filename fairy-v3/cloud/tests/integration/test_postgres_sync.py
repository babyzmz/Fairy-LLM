from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.storage.postgres import (
    LeaseUnavailableError,
    PostgresSyncStore,
    ProjectRevisionState,
    tenant_id_for_user,
)
from fairy_cloud.storage.schema import (
    core_projects,
    core_tenants,
    domain_events,
    outbox,
    version_candidates,
    worker_leases,
)

POSTGRES_DSN = os.environ.get("FAIRY_TEST_POSTGRES_DSN")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_DSN, reason="FAIRY_TEST_POSTGRES_DSN is not set"),
]


def test_postgres_sync_conflict_outbox_and_fencing() -> None:
    assert POSTGRES_DSN is not None
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    old_dsn = os.environ.get("FAIRY_POSTGRES_DSN")
    os.environ["FAIRY_POSTGRES_DSN"] = POSTGRES_DSN
    try:
        command.upgrade(config, "head")
        asyncio.run(_run_scenario(POSTGRES_DSN))
    finally:
        if old_dsn is None:
            os.environ.pop("FAIRY_POSTGRES_DSN", None)
        else:
            os.environ["FAIRY_POSTGRES_DSN"] = old_dsn


async def _run_scenario(dsn: str) -> None:
    suffix = uuid4().hex
    user_id = f"user-{suffix}"
    project_id = f"project-{suffix}"
    event_id = str(uuid4())
    run_id = str(uuid4())
    decision_event_id = str(uuid4())
    resource_id = f"task-{suffix}"
    engine = create_async_engine(dsn)
    store = PostgresSyncStore(engine)
    tenant_id = tenant_id_for_user(user_id)
    try:
        await store.register_project(project_id=project_id, user_id=user_id)
        cursor = await store.append_event(
            event_id=event_id,
            run_id=run_id,
            user_id=user_id,
            device_id="device-a",
            project_id=project_id,
            conversation_id=f"conversation-{suffix}",
            task_id=resource_id,
            task_sequence=1,
            schema_version=1,
            event_type="task.created",
            payload={"task_id": resource_id},
        )
        duplicate_cursor = await store.append_event(
            event_id=event_id,
            run_id=run_id,
            user_id=user_id,
            device_id="device-a",
            project_id=project_id,
            conversation_id=f"conversation-{suffix}",
            task_id=resource_id,
            task_sequence=1,
            schema_version=1,
            event_type="task.created",
            payload={"task_id": resource_id},
        )
        assert duplicate_cursor == cursor
        with pytest.raises(IdempotencyConflictError):
            await store.append_event(
                event_id=event_id,
                run_id=run_id,
                user_id=user_id,
                device_id="device-a",
                project_id=project_id,
                conversation_id=f"conversation-{suffix}",
                task_id=resource_id,
                task_sequence=1,
                schema_version=1,
                event_type="task.created",
                payload={"task_id": "changed"},
            )
        with pytest.raises(IdempotencyConflictError, match="task sequence"):
            await store.append_event(
                event_id=str(uuid4()),
                run_id=run_id,
                user_id=user_id,
                device_id="device-a",
                project_id=project_id,
                conversation_id=f"conversation-{suffix}",
                task_id=resource_id,
                task_sequence=1,
                schema_version=1,
                event_type="task.updated",
                payload={"task_id": resource_id},
            )
        synced_events = await store.events_after(user_id=user_id, cursor=0)
        assert [event.event_id for event in synced_events] == [event_id]
        assert synced_events[0].run_id == run_id

        promoted = await store.promote_version(
            user_id=user_id,
            project_id=project_id,
            version_id=f"version-a-{suffix}",
            expected_revision=0,
            manifest={"snapshot": "a"},
            decision_event_id=decision_event_id,
            device_id="device-a",
            conversation_id=f"conversation-{suffix}",
            task_id=resource_id,
            task_sequence=1,
        )
        assert promoted.revision == 1
        replayed = await store.promote_version(
            user_id=user_id,
            project_id=project_id,
            version_id=f"version-a-{suffix}",
            expected_revision=0,
            manifest={"snapshot": "a"},
            decision_event_id=decision_event_id,
            device_id="device-a",
            conversation_id=f"conversation-{suffix}",
            task_id=resource_id,
            task_sequence=1,
        )
        assert replayed.revision == 1
        candidate_id = f"version-b-{suffix}"
        with pytest.raises(VersionConflictError):
            await store.promote_version(
                user_id=user_id,
                project_id=project_id,
                version_id=candidate_id,
                expected_revision=0,
                manifest={"snapshot": "b"},
            )
        async with engine.connect() as connection:
            candidates = (
                (
                    await connection.execute(
                        select(version_candidates.c.version_id).where(
                            version_candidates.c.project_id == project_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert candidates == [candidate_id]

        concurrent = await asyncio.gather(
            store.promote_version(
                user_id=user_id,
                project_id=project_id,
                version_id=f"version-c-{suffix}",
                expected_revision=1,
                manifest={"snapshot": "c"},
            ),
            store.promote_version(
                user_id=user_id,
                project_id=project_id,
                version_id=f"version-d-{suffix}",
                expected_revision=1,
                manifest={"snapshot": "d"},
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ProjectRevisionState) for result in concurrent) == 1
        assert sum(isinstance(result, VersionConflictError) for result in concurrent) == 1

        lease = await store.acquire_worker_lease(
            tenant_id=tenant_id,
            resource_type="task",
            resource_id=resource_id,
            owner_id="worker-a",
        )
        with pytest.raises(LeaseUnavailableError):
            await store.acquire_worker_lease(
                tenant_id=tenant_id,
                resource_type="task",
                resource_id=resource_id,
                owner_id="worker-b",
            )
        claimed = await store.claim_outbox(owner_id="publisher-a")
        current = next(item for item in claimed if item.event_id == event_id)
        claimed_again = await store.claim_outbox(owner_id="publisher-b")
        assert event_id not in {item.event_id for item in claimed_again}
        stale_item = replace(current, lease_fence=current.lease_fence - 1)
        assert (
            await store.mark_outbox_published(
                owner_id="publisher-a",
                items=[stale_item],
            )
            == 0
        )
        assert (
            await store.mark_outbox_published(
                owner_id="publisher-a",
                items=[current],
            )
            == 1
        )

        assert await store.release_worker_lease(lease)
        next_lease = await store.acquire_worker_lease(
            tenant_id=tenant_id,
            resource_type="task",
            resource_id=resource_id,
            owner_id="worker-b",
        )
        assert next_lease.fence > lease.fence
        assert not await store.release_worker_lease(lease)
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(outbox).where(outbox.c.tenant_id == tenant_id))
            await connection.execute(
                delete(domain_events).where(domain_events.c.tenant_id == tenant_id)
            )
            await connection.execute(
                delete(version_candidates).where(
                    version_candidates.c.tenant_id == tenant_id,
                    version_candidates.c.project_id == project_id,
                )
            )
            await connection.execute(
                delete(worker_leases).where(
                    worker_leases.c.tenant_id == tenant_id,
                    worker_leases.c.resource_id == resource_id,
                )
            )
            await connection.execute(
                delete(core_projects).where(
                    core_projects.c.tenant_id == tenant_id,
                    core_projects.c.id == project_id,
                )
            )
            await connection.execute(
                delete(core_tenants).where(core_tenants.c.tenant_id == tenant_id)
            )
        await engine.dispose()
