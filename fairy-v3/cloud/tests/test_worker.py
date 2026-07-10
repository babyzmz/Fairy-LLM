from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from fairy_cloud.storage.postgres import OutboxItem
from fairy_cloud.worker import OutboxWorker as CompatibilityOutboxWorker
from fairy_cloud.workers.outbox import OutboxWorker


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


def test_legacy_worker_module_is_a_compatibility_reexport() -> None:
    assert CompatibilityOutboxWorker is OutboxWorker


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

    async def handle_event(payload: dict) -> None:
        assert payload["ok"] is True
        handled.append("event")

    async def fail_task(_payload: dict) -> None:
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
