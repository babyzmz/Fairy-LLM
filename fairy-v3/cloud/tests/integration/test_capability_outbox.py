from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.dispatchers import build_postgres_core_service
from fairy_cloud.storage.postgres import PostgresSyncStore
from fairy_cloud.workers.outbox import DeliveredDomainEvent, DomainEventRouter, OutboxWorker

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_capability_event_is_atomically_delivered_once_with_identity_and_fence(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"capability-outbox-{uuid4().hex}")
    core_engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    worker_engine = create_async_engine(postgres_test_context.admin_dsn, pool_pre_ping=True)
    service = build_postgres_core_service(tenant_id, tmp_path / tenant_id, core_engine)
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        task = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Remember the release preference",
                "operation_mode": "answer",
                "execution_target": "cloud",
                "idempotency_key": "capability-outbox:task",
            },
        )["task"]
        observation = service.invoke(
            "memory.observations.create",
            {
                "task_id": task["id"],
                "content": "Prefer concise release notes.",
                "idempotency_key": "capability-outbox:memory",
            },
        )

        with core_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            row = connection.execute(
                text(
                    """
                    SELECT events.event_id, outbox.id, outbox.published_at
                    FROM domain_events AS events
                    JOIN outbox
                      ON outbox.tenant_id = events.tenant_id
                     AND outbox.event_id = events.event_id
                    WHERE events.tenant_id = :tenant_id
                      AND events.event_type = 'memory.observation.accepted'
                      AND events.payload ->> 'observation_id' = :observation_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "observation_id": observation["id"],
                },
            ).one()
        assert row.published_at is None

        delivered: list[DeliveredDomainEvent] = []

        async def capture(event: DeliveredDomainEvent) -> None:
            delivered.append(event)

        worker = OutboxWorker(
            store=PostgresSyncStore(worker_engine),
            owner_id="capability-worker-a",
            handlers={"domain.events": DomainEventRouter({"memory.observation.accepted": capture})},
        )

        first = await worker.run_once()
        second = await worker.run_once()

        assert first.failed == 0
        assert first.published == first.claimed
        assert second.claimed == 0
        assert len(delivered) == 1
        assert delivered[0].tenant_id == tenant_id
        assert delivered[0].event_id == row.event_id
        assert delivered[0].outbox_id == row.id
        assert delivered[0].attempt == 1
        assert delivered[0].lease_fence == 1
        assert delivered[0].envelope.payload["observation_id"] == observation["id"]
    finally:
        service.close()
        await worker_engine.dispose()
        core_engine.dispose()
