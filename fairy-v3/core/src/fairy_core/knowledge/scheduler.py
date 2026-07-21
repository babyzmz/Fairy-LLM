from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread
from uuid import UUID

from fairy_core.commanding.models import EventVisibility
from fairy_core.contracts.knowledge import KnowledgeSyncStartInput
from fairy_core.domain.ids import new_id
from fairy_core.knowledge.models import KnowledgeSyncRun, KnowledgeSyncStatus
from fairy_core.knowledge.sync import ObsidianKnowledgeSync
from fairy_core.knowledge.work_queue import (
    KNOWLEDGE_SYNC_LEASE_DURATION,
    KnowledgeSyncClaim,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError

logger = logging.getLogger(__name__)

_TERMINAL = {
    KnowledgeSyncStatus.COMPLETED,
    KnowledgeSyncStatus.FAILED,
    KnowledgeSyncStatus.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class _ActiveSync:
    claim: KnowledgeSyncClaim
    cancellation: CancellationToken


class KnowledgeSyncScheduler:
    def __init__(
        self,
        *,
        application: ObsidianKnowledgeSync,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        max_workers: int = 1,
        lease_duration: timedelta = KNOWLEDGE_SYNC_LEASE_DURATION,
        heartbeat_interval: float = 5.0,
        poll_interval: float = 0.1,
        wait_timeout: float = 300.0,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if min(heartbeat_interval, poll_interval, wait_timeout) <= 0:
            raise ValueError("Knowledge Sync worker intervals must be positive")
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._max_workers = max_workers
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._wait_timeout = wait_timeout
        self._owner_id = f"knowledge-worker:{new_id()}"
        self._active: dict[UUID, _ActiveSync] = {}
        self._lock = RLock()
        self._wake = Event()
        self._changed = Event()
        self._closed = False
        self._last_heartbeat = time.monotonic()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fairy-knowledge",
        )
        self._coordinator = Thread(
            target=self._coordinate,
            name="fairy-knowledge-coordinator",
            daemon=True,
        )
        self._coordinator.start()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for active in self._active.values():
                active.cancellation.interrupt()
        self._wake.set()
        self._changed.set()
        self._coordinator.join()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def start(self, request: KnowledgeSyncStartInput) -> KnowledgeSyncRun:
        with self._lock:
            if self._closed:
                raise RuntimeError("Core service is closing")
        run = self._application.enqueue(request)
        if run.status not in _TERMINAL:
            self._wake.set()
        return run

    def run(self, request: KnowledgeSyncStartInput) -> KnowledgeSyncRun:
        run = self.start(request)
        deadline = time.monotonic() + self._wait_timeout
        while run.status not in _TERMINAL:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Knowledge Sync did not settle before its wait deadline")
            self._changed.wait(min(self._poll_interval, remaining))
            self._changed.clear()
            run = self.get(run.id)
        return run

    def get(self, run_id: UUID) -> KnowledgeSyncRun:
        return self._application.get(run_id)

    def cancel(self, run_id: UUID) -> KnowledgeSyncRun:
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                active.cancellation.cancel()
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.knowledge.cancel_sync_run(run_id)
            payload = {
                "run_id": str(run.id),
                "source_id": str(run.source_id),
                "status": run.status.value,
            }
            if (
                run.status is KnowledgeSyncStatus.CANCELLED
                and not unit_of_work.commands.has_domain_event(
                    event_type="knowledge.sync.cancelled",
                    project_id=run.project_id,
                    conversation_id=None,
                    payload=payload,
                )
            ):
                unit_of_work.commands.append_domain_event(
                    event_type="knowledge.sync.cancelled",
                    visibility=EventVisibility.USER,
                    message="Knowledge sync cancelled",
                    payload=payload,
                    actor="user",
                    project_id=run.project_id,
                )
            unit_of_work.commit()
        self._changed.set()
        self._wake.set()
        return run

    def _coordinate(self) -> None:
        while True:
            self._wake.wait(self._poll_interval)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
            try:
                self._heartbeat_if_due()
                self._claim_available()
            except Exception:
                logger.exception("Knowledge Sync coordinator iteration failed")

    def _heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < self._heartbeat_interval:
            return
        self._last_heartbeat = now
        with self._lock:
            active_items = tuple(self._active.values())
        for active in active_items:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    renewed = unit_of_work.knowledge.renew_sync_run(
                        active.claim,
                        lease_until=self._new_lease_until(),
                    )
                    if renewed:
                        unit_of_work.commit()
            except Exception:
                logger.exception(
                    "Knowledge Sync %s lease renewal failed",
                    active.claim.run_id,
                )
                renewed = False
            if not renewed:
                active.cancellation.interrupt()

    def _claim_available(self) -> None:
        while True:
            with self._lock:
                if self._closed or len(self._active) >= self._max_workers:
                    return
            with self._unit_of_work_factory() as unit_of_work:
                claim = unit_of_work.knowledge.claim_next_sync_run(
                    worker_id=self._owner_id,
                    lease_until=self._new_lease_until(),
                )
                if claim is not None:
                    unit_of_work.commit()
            if claim is None:
                return
            active = _ActiveSync(claim=claim, cancellation=CancellationToken())
            with self._lock:
                if claim.run_id in self._active:
                    duplicate = True
                else:
                    duplicate = False
                    self._active[claim.run_id] = active
            if duplicate:
                self._abandon(claim)
                continue
            self._executor.submit(self._execute, active)

    def _execute(self, active: _ActiveSync) -> None:
        try:
            self._application.execute(active.claim, active.cancellation)
        except ProviderCancelledError:
            if active.cancellation.is_interrupted:
                self._abandon(active.claim)
        except Exception:
            logger.exception("Knowledge Sync %s failed", active.claim.run_id)
        finally:
            with self._lock:
                current = self._active.get(active.claim.run_id)
                if current is active:
                    self._active.pop(active.claim.run_id, None)
            self._changed.set()
            self._wake.set()

    def _abandon(self, claim: KnowledgeSyncClaim) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            abandoned = unit_of_work.knowledge.abandon_sync_run(claim)
            if abandoned:
                unit_of_work.commit()

    def _new_lease_until(self) -> datetime:
        return datetime.now(UTC) + self._lease_duration


__all__ = ["KnowledgeSyncScheduler"]
