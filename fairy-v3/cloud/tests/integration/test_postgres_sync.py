from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fairy_core.domain.errors import VersionConflictError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.storage.postgres import LeaseUnavailableError, PostgresSyncStore
from fairy_cloud.storage.schema import (
    cloud_projects,
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
    resource_id = f"task-{suffix}"
    engine = create_async_engine(dsn)
    store = PostgresSyncStore(engine)
    try:
        await store.register_project(project_id=project_id, user_id=user_id)
        cursor = await store.append_event(
            event_id=event_id,
            user_id=user_id,
            device_id="device-a",
            project_id=project_id,
            schema_version=1,
            event_type="task.created",
            payload={"task_id": resource_id},
        )
        duplicate_cursor = await store.append_event(
            event_id=event_id,
            user_id=user_id,
            device_id="device-a",
            project_id=project_id,
            schema_version=1,
            event_type="task.created",
            payload={"task_id": resource_id},
        )
        assert duplicate_cursor == cursor
        synced_events = await store.events_after(user_id=user_id, cursor=0)
        assert [event.event_id for event in synced_events] == [event_id]

        promoted = await store.promote_version(
            user_id=user_id,
            project_id=project_id,
            version_id=f"version-a-{suffix}",
            expected_revision=0,
            manifest={"snapshot": "a"},
        )
        assert promoted.revision == 1
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

        lease = await store.acquire_worker_lease(
            resource_type="task",
            resource_id=resource_id,
            owner_id="worker-a",
        )
        with pytest.raises(LeaseUnavailableError):
            await store.acquire_worker_lease(
                resource_type="task",
                resource_id=resource_id,
                owner_id="worker-b",
            )
        claimed = await store.claim_outbox(owner_id="publisher-a")
        current = next(item for item in claimed if item.event_id == event_id)
        claimed_again = await store.claim_outbox(owner_id="publisher-b")
        assert event_id not in {item.event_id for item in claimed_again}
        assert (
            await store.mark_outbox_published(
                owner_id="publisher-a",
                item_ids=[current.id],
            )
            == 1
        )

        assert await store.release_worker_lease(lease)
        next_lease = await store.acquire_worker_lease(
            resource_type="task",
            resource_id=resource_id,
            owner_id="worker-b",
        )
        assert next_lease.fence > lease.fence
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(outbox).where(outbox.c.event_id == event_id))
            await connection.execute(
                delete(domain_events).where(domain_events.c.event_id == event_id)
            )
            await connection.execute(
                delete(version_candidates).where(version_candidates.c.project_id == project_id)
            )
            await connection.execute(
                delete(worker_leases).where(worker_leases.c.resource_id == resource_id)
            )
            await connection.execute(
                delete(cloud_projects).where(cloud_projects.c.project_id == project_id)
            )
        await engine.dispose()
