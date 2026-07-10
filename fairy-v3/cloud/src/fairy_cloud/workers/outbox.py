from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.settings import CloudSettings
from fairy_cloud.storage.postgres import OutboxItem, PostgresSyncStore

logger = logging.getLogger(__name__)
OutboxHandler = Callable[[dict[str, Any]], Awaitable[None]]


class OutboxStore(Protocol):
    async def claim_outbox(
        self,
        *,
        owner_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> list[OutboxItem]: ...

    async def mark_outbox_published(
        self,
        *,
        owner_id: str,
        items: list[OutboxItem],
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class WorkerCycle:
    claimed: int
    published: int
    failed: int


class OutboxWorker:
    def __init__(
        self,
        *,
        store: OutboxStore,
        owner_id: str,
        handlers: Mapping[str, OutboxHandler],
        batch_size: int = 100,
        lease_seconds: int = 30,
    ) -> None:
        self._store = store
        self._owner_id = owner_id
        self._handlers = dict(handlers)
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds

    async def run_once(self) -> WorkerCycle:
        items = await self._store.claim_outbox(
            owner_id=self._owner_id,
            batch_size=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        successful: list[OutboxItem] = []
        failed = 0
        for item in items:
            handler = self._handlers.get(item.topic)
            if handler is None:
                failed += 1
                logger.error("No outbox handler registered for topic %s", item.topic)
                continue
            try:
                await handler(item.payload)
            except Exception:
                failed += 1
                logger.exception("Outbox item %s failed", item.id)
            else:
                successful.append(item)
        published = await self._store.mark_outbox_published(
            owner_id=self._owner_id,
            items=successful,
        )
        return WorkerCycle(claimed=len(items), published=published, failed=failed)

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        poll_seconds: float,
        heartbeat_path: Path,
    ) -> None:
        while not stop.is_set():
            await self.run_once()
            heartbeat_path.touch()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


async def _validate_durable_event(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("event_id"), str) or not isinstance(payload.get("cursor"), int):
        raise ValueError("domain event outbox payload is malformed")


async def _run() -> None:
    settings = CloudSettings()
    engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
    store = PostgresSyncStore(engine)
    worker = OutboxWorker(
        store=store,
        owner_id=settings.worker_owner_id,
        handlers={"domain.events": _validate_durable_event},
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop.set)
    try:
        await worker.run_forever(
            stop=stop,
            poll_seconds=settings.worker_poll_seconds,
            heartbeat_path=settings.worker_heartbeat_path,
        )
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()


__all__ = [
    "OutboxHandler",
    "OutboxStore",
    "OutboxWorker",
    "WorkerCycle",
    "main",
]
