from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fairy_core.contracts.models import EventEnvelopeModel
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.settings import CloudSettings
from fairy_cloud.storage.postgres import OutboxItem, PostgresSyncStore

logger = logging.getLogger(__name__)
OutboxHandler = Callable[[OutboxItem], Awaitable[None]]


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


@dataclass(frozen=True, slots=True)
class DeliveredDomainEvent:
    tenant_id: str
    outbox_id: int
    event_id: str
    attempt: int
    lease_fence: int
    envelope: EventEnvelopeModel


DomainEventHandler = Callable[[DeliveredDomainEvent], Awaitable[None]]


class DomainEventRouter:
    def __init__(self, handlers: Mapping[str, DomainEventHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    async def __call__(self, item: OutboxItem) -> None:
        if item.topic != "domain.events":
            raise ValueError("domain event router received the wrong topic")
        payload = item.payload
        if payload.get("tenant_id") != item.tenant_id:
            raise ValueError("domain event tenant does not match the outbox item")
        if payload.get("event_id") != item.event_id:
            raise ValueError("domain event identity does not match the outbox item")
        envelope = EventEnvelopeModel.model_validate(
            {
                "id": payload.get("event_id"),
                "cursor": payload.get("cursor"),
                "run_id": payload.get("run_id"),
                "project_id": payload.get("project_id"),
                "conversation_id": payload.get("conversation_id"),
                "task_id": payload.get("task_id"),
                "version_id": payload.get("version_id"),
                "task_sequence": payload.get("task_sequence"),
                "schema_version": payload.get("schema_version"),
                "event_type": payload.get("event_type"),
                "visibility": payload.get("visibility"),
                "message": payload.get("message"),
                "payload": payload.get("payload"),
                "created_at": payload.get("created_at"),
            }
        )
        if envelope.schema_version != 1:
            raise ValueError("unsupported domain event schema version")
        handler = self._handlers.get(envelope.event_type)
        if handler is None:
            return
        await handler(
            DeliveredDomainEvent(
                tenant_id=item.tenant_id,
                outbox_id=item.id,
                event_id=item.event_id,
                attempt=item.attempts,
                lease_fence=item.lease_fence,
                envelope=envelope,
            )
        )


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
                await handler(item)
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


async def _run() -> None:
    settings = CloudSettings()
    engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
    store = PostgresSyncStore(engine)
    worker = OutboxWorker(
        store=store,
        owner_id=settings.worker_owner_id,
        handlers={"domain.events": DomainEventRouter()},
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
    "DeliveredDomainEvent",
    "DomainEventHandler",
    "DomainEventRouter",
    "OutboxHandler",
    "OutboxStore",
    "OutboxWorker",
    "WorkerCycle",
    "main",
]
