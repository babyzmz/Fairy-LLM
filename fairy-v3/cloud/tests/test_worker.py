from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fairy_cloud.storage.postgres import OutboxItem
from fairy_cloud.workers.outbox import DomainEventRouter, OutboxWorker


class FakeOutboxStore:
    def __init__(self) -> None:
        expires = datetime.now(UTC) + timedelta(seconds=30)
        self.items = [
            OutboxItem(
                "tenant", 1, "event-1", "domain.events", {"ok": True}, 1, "worker", expires, 1
            ),
            OutboxItem("tenant", 2, "event-2", "task.run", {"ok": False}, 1, "worker", expires, 1),
        ]
        self.published: list[int] = []

    async def claim_outbox(self, *, owner_id: str, batch_size: int, lease_seconds: int):
        del owner_id, batch_size, lease_seconds
        return self.items

    async def mark_outbox_published(self, *, owner_id: str, items: list[OutboxItem]) -> int:
        del owner_id
        self.published.extend(item.id for item in items)
        return len(items)


def test_outbox_worker_module_entrypoint_has_no_eager_import_warning() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            (
                "import asyncio, runpy; "
                "asyncio.run=lambda coro: coro.close(); "
                "runpy.run_module('fairy_cloud.workers.outbox', run_name='__main__')"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_worker_acknowledges_only_successful_outbox_items() -> None:
    store = FakeOutboxStore()
    handled: list[str] = []

    async def handle_event(item: OutboxItem) -> None:
        assert item.payload["ok"] is True
        handled.append("event")

    async def fail_task(_item: OutboxItem) -> None:
        raise RuntimeError("worker interrupted")

    worker = OutboxWorker(
        store=store,
        owner_id="worker-a",
        handlers={"domain.events": handle_event, "task.run": fail_task},
    )

    result = await worker.run_once()

    assert result.claimed == 2
    assert result.published == 1
    assert result.failed == 1
    assert handled == ["event"]
    assert store.published == [1]


@pytest.mark.asyncio
async def test_domain_event_router_validates_identity_and_routes_with_delivery_fence() -> None:
    expires = datetime.now(UTC) + timedelta(seconds=30)
    item = OutboxItem(
        tenant_id="tenant-a",
        id=7,
        event_id="019f5059-5a8d-755c-a88c-a09edb078a04",
        topic="domain.events",
        payload={
            "tenant_id": "tenant-a",
            "cursor": 9,
            "event_id": "019f5059-5a8d-755c-a88c-a09edb078a04",
            "run_id": "019f5059-5a8d-755c-a88c-a09edb078a05",
            "user_id": "user-a",
            "device_id": "device-a",
            "project_id": None,
            "conversation_id": "019f5059-5a8d-755c-a88c-a09edb078a06",
            "task_id": "019f5059-5a8d-755c-a88c-a09edb078a07",
            "version_id": None,
            "task_sequence": 3,
            "schema_version": 1,
            "event_type": "memory.observation.accepted",
            "visibility": "user",
            "message": "Memory observation accepted",
            "payload": {"observation_id": "019f5059-5a8d-755c-a88c-a09edb078a08"},
            "created_at": "2026-07-11T00:00:00Z",
        },
        attempts=2,
        lease_owner="worker-a",
        lease_expires_at=expires,
        lease_fence=4,
    )
    handled: list[tuple[str, str, int, int]] = []

    async def handle(delivery) -> None:
        handled.append(
            (
                str(delivery.envelope.id),
                delivery.tenant_id,
                delivery.attempt,
                delivery.lease_fence,
            )
        )

    router = DomainEventRouter({"memory.observation.accepted": handle})

    await router(item)

    assert handled == [(item.event_id, "tenant-a", 2, 4)]

    forged = replace(item, payload={**item.payload, "tenant_id": "tenant-b"})
    with pytest.raises(ValueError, match="tenant"):
        await router(forged)
