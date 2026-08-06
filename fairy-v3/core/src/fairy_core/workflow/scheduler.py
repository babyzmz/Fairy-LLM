from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread
from typing import Any, Protocol
from uuid import UUID

from fairy_core.domain.ids import new_id
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError
from fairy_core.workflow.models import (
    WorkflowAttemptClaim,
    WorkflowNode,
    WorkflowRunStatus,
    WorkflowSnapshot,
)

logger = logging.getLogger(__name__)

WORKFLOW_LEASE_DURATION = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class WorkflowNodeResult:
    output: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    public_summary: str | None = None
    available_at: datetime | None = None


class WorkflowNodeAdapter(Protocol):
    def execute(
        self,
        node: WorkflowNode,
        cancellation: CancellationToken,
    ) -> WorkflowNodeResult: ...


class WorkflowRetryableError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "WORKFLOW_RETRYABLE") -> None:
        super().__init__(message)
        self.error_code = error_code


class WorkflowWaitingForApproval(RuntimeError):
    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        super().__init__("Workflow is waiting for approval")
        self.result = dict(result or {})


class WorkflowCancelled(RuntimeError):
    pass


class WorkflowPaused(RuntimeError):
    pass


class WorkflowAdapterRegistry:
    def __init__(self, adapters: Mapping[str, WorkflowNodeAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def register(self, kind: str, adapter: WorkflowNodeAdapter) -> None:
        normalized = kind.strip()
        if not normalized or normalized in self._adapters:
            raise ValueError("Workflow node kind is invalid or already registered")
        self._adapters[normalized] = adapter

    def require(self, kind: str) -> WorkflowNodeAdapter:
        try:
            return self._adapters[kind]
        except KeyError as error:
            raise RuntimeError(f"Workflow adapter is unavailable: {kind}") from error

    def child_waiting_kinds(self) -> frozenset[str]:
        return frozenset(
            kind
            for kind, adapter in self._adapters.items()
            if bool(getattr(adapter, "may_wait_for_child_workflow", False))
        )


@dataclass(frozen=True, slots=True)
class _ActiveNode:
    claim: WorkflowAttemptClaim
    cancellation: CancellationToken
    kind: str
    parent_run_id: UUID | None


class WorkflowScheduler:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        adapters: WorkflowAdapterRegistry,
        max_workers: int = 4,
        lease_duration: timedelta = WORKFLOW_LEASE_DURATION,
        heartbeat_interval: float = 5.0,
        poll_interval: float = 0.1,
        autostart: bool = True,
    ) -> None:
        if max_workers < 1:
            raise ValueError("Workflow max_workers must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("Workflow lease duration must be positive")
        if min(heartbeat_interval, poll_interval) <= 0:
            raise ValueError("Workflow worker intervals must be positive")
        if heartbeat_interval >= lease_duration.total_seconds():
            raise ValueError("Workflow heartbeat must be shorter than its lease")
        self._unit_of_work_factory = unit_of_work_factory
        self._adapters = adapters
        self._max_workers = max_workers
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._owner_id = f"workflow-worker:{new_id()}"
        self._active: dict[UUID, _ActiveNode] = {}
        self._lock = RLock()
        self._wake = Event()
        self._changed = Event()
        self._closed = False
        self._started = False
        self._last_heartbeat = time.monotonic()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fairy-workflow",
        )
        self._coordinator = Thread(
            target=self._coordinate,
            name="fairy-workflow-coordinator",
            daemon=True,
        )
        if autostart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Workflow Scheduler is closing")
            if self._started:
                return
            self._started = True
            self._coordinator.start()
        self._wake.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = tuple(self._active.values())
            for item in active:
                try:
                    with self._unit_of_work_factory() as unit_of_work:
                        unit_of_work.workflows.request_pause(item.claim.run_id)
                        unit_of_work.commit()
                except Exception:
                    logger.exception(
                        "Workflow Run %s could not be paused during shutdown",
                        item.claim.run_id,
                    )
                item.cancellation.interrupt()
        self._wake.set()
        self._changed.set()
        if self._started:
            self._coordinator.join()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            remaining = tuple(self._active.values())
            self._active.clear()
        for item in remaining:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    if unit_of_work.workflows.abandon(item.claim):
                        unit_of_work.commit()
            except Exception:
                logger.exception("Workflow node %s could not be abandoned", item.claim.node_id)

    def wake(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Workflow Scheduler is closing")
        self._wake.set()

    def wait(self, run_id: UUID, *, timeout: float = 30.0) -> WorkflowSnapshot:
        if timeout <= 0:
            raise ValueError("Workflow wait timeout must be positive")
        deadline = time.monotonic() + timeout
        while True:
            with self._unit_of_work_factory() as unit_of_work:
                snapshot = unit_of_work.workflows.get(run_id)
            if snapshot is None:
                raise KeyError(f"Workflow Run not found: {run_id}")
            if snapshot.run.status in {
                WorkflowRunStatus.COMPLETED,
                WorkflowRunStatus.CANCELLED,
                WorkflowRunStatus.FAILED,
                WorkflowRunStatus.PAUSED,
                WorkflowRunStatus.WAITING_FOR_APPROVAL,
            }:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Workflow did not settle before its wait deadline")
            self._changed.wait(min(self._poll_interval, remaining))
            self._changed.clear()

    def pause(self, run_id: UUID) -> WorkflowSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            snapshot = unit_of_work.workflows.request_pause(run_id)
            unit_of_work.commit()
        self._wake.set()
        return snapshot

    def resume(self, run_id: UUID) -> WorkflowSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            snapshot = unit_of_work.workflows.resume(run_id)
            unit_of_work.commit()
        self._wake.set()
        return snapshot

    def cancel(self, run_id: UUID) -> WorkflowSnapshot:
        with self._lock:
            active = tuple(item for item in self._active.values() if item.claim.run_id == run_id)
            for item in active:
                item.cancellation.cancel()
        with self._unit_of_work_factory() as unit_of_work:
            snapshot = unit_of_work.workflows.cancel(run_id)
            unit_of_work.commit()
        self._wake.set()
        self._changed.set()
        return snapshot

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
                logger.exception("Workflow coordinator iteration failed")

    def _heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < self._heartbeat_interval:
            return
        self._last_heartbeat = now
        with self._lock:
            active = tuple(self._active.values())
        for item in active:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    renewed = unit_of_work.workflows.renew(
                        item.claim,
                        lease_until=self._new_lease_until(),
                    )
                    if renewed:
                        unit_of_work.commit()
                        snapshot = unit_of_work.workflows.get(item.claim.run_id)
                    else:
                        snapshot = None
                if renewed and snapshot is not None:
                    node = next(value for value in snapshot.nodes if value.id == item.claim.node_id)
                    heartbeat = getattr(self._adapters.require(node.kind), "heartbeat", None)
                    if callable(heartbeat):
                        renewed = bool(heartbeat(node))
            except Exception:
                logger.exception("Workflow node %s heartbeat failed", item.claim.node_id)
                renewed = False
            if not renewed:
                item.cancellation.interrupt()

    def _claim_available(self) -> None:
        with self._lock:
            capacity = self._max_workers - len(self._active)
            active = tuple(self._active.values())
        if capacity <= 0:
            return
        blocking_parent_kinds = (
            self._adapters.child_waiting_kinds() if self._max_workers > 1 else frozenset()
        )
        reserve_child_slot = any(
            item.parent_run_id is None and item.kind in blocking_parent_kinds for item in active
        )
        with self._unit_of_work_factory() as unit_of_work:
            claims = unit_of_work.workflows.claim_ready(
                worker_id=self._owner_id,
                lease_until=self._new_lease_until(),
                limit=capacity,
                blocking_parent_kinds=blocking_parent_kinds,
                reserve_child_slot=reserve_child_slot,
            )
            claim_nodes = {}
            for claim in claims:
                snapshot = unit_of_work.workflows.get(claim.run_id)
                if snapshot is None:
                    continue
                node = next(value for value in snapshot.nodes if value.id == claim.node_id)
                claim_nodes[claim.node_id] = (node.kind, snapshot.run.parent_run_id)
            if claims:
                unit_of_work.commit()
        for claim in claims:
            cancellation = CancellationToken()
            claim_node = claim_nodes.get(claim.node_id)
            if claim_node is None:
                self._abandon(claim)
                continue
            kind, parent_run_id = claim_node
            active = _ActiveNode(
                claim=claim,
                cancellation=cancellation,
                kind=kind,
                parent_run_id=parent_run_id,
            )
            with self._lock:
                if self._closed:
                    cancellation.interrupt()
                    self._abandon(claim)
                    return
                if claim.node_id in self._active:
                    self._abandon(claim)
                    continue
                self._active[claim.node_id] = active
            try:
                self._executor.submit(self._execute, active)
            except Exception:
                with self._lock:
                    self._active.pop(claim.node_id, None)
                self._abandon(claim)
                raise

    def _execute(self, active: _ActiveNode) -> None:
        claim = active.claim
        settled = False
        try:
            with self._unit_of_work_factory() as unit_of_work:
                snapshot = unit_of_work.workflows.get(claim.run_id)
            if snapshot is None:
                raise KeyError(f"Workflow Run not found: {claim.run_id}")
            node = next(node for node in snapshot.nodes if node.id == claim.node_id)
            result = self._adapters.require(node.kind).execute(node, active.cancellation)
            active.cancellation.raise_if_cancelled()
            with self._unit_of_work_factory() as unit_of_work:
                if result.available_at is None:
                    unit_of_work.workflows.complete(
                        claim,
                        result=result.output,
                        evidence_refs=result.evidence_refs,
                        public_summary=result.public_summary,
                    )
                else:
                    unit_of_work.workflows.defer(
                        claim,
                        available_at=result.available_at,
                        result=result.output,
                    )
                unit_of_work.commit()
            settled = True
        except WorkflowRetryableError as error:
            if active.cancellation.is_interrupted:
                self._abandon(claim)
                settled = True
            else:
                try:
                    with self._unit_of_work_factory() as unit_of_work:
                        unit_of_work.workflows.retry(
                            claim,
                            available_at=datetime.now(UTC)
                            + timedelta(seconds=min(5.0, 0.25 * 2 ** (claim.attempt_number - 1))),
                            error_code=error.error_code,
                        )
                        unit_of_work.commit()
                    settled = True
                except Exception:
                    logger.exception("Workflow node %s retry could not settle", claim.node_id)
        except WorkflowWaitingForApproval as waiting:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    unit_of_work.workflows.wait_for_approval(claim, result=waiting.result)
                    unit_of_work.commit()
                settled = True
            except Exception:
                logger.exception("Workflow node %s could not wait for approval", claim.node_id)
        except WorkflowCancelled:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    unit_of_work.workflows.cancel(claim.run_id)
                    unit_of_work.commit()
                settled = True
            except Exception:
                logger.exception("Workflow Run %s could not be cancelled", claim.run_id)
        except WorkflowPaused:
            abandoned = self._abandon(claim)
            settled = abandoned
            if abandoned:
                replan = getattr(self._adapters.require(node.kind), "replan_after_pause", None)
                if callable(replan) and replan(node):
                    self._wake.set()
        except ProviderCancelledError:
            if active.cancellation.is_interrupted:
                self._abandon(claim)
                settled = True
            elif active.cancellation.is_cancelled:
                settled = True
        except Exception as error:
            if active.cancellation.is_interrupted:
                self._abandon(claim)
                settled = True
            else:
                error_code = str(getattr(error, "error_code", "WORKFLOW_NODE_FAILED"))[:128]
                try:
                    with self._unit_of_work_factory() as unit_of_work:
                        unit_of_work.workflows.fail(claim, error_code=error_code)
                        unit_of_work.commit()
                    settled = True
                except Exception:
                    logger.exception("Workflow node %s failure could not settle", claim.node_id)
                logger.exception("Workflow node %s execution failed", claim.node_id)
        finally:
            if not settled:
                self._abandon(claim)
            self._finish_active(active)

    def _finish_active(self, active: _ActiveNode) -> None:
        claim = active.claim
        try:
            with self._lock:
                current = self._active.get(claim.node_id)
                if current is active:
                    self._active.pop(claim.node_id, None)
        finally:
            self._changed.set()
            self._wake.set()

    def _abandon(self, claim: WorkflowAttemptClaim) -> bool:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                abandoned = unit_of_work.workflows.abandon(claim)
                if abandoned:
                    unit_of_work.commit()
                return abandoned
        except Exception:
            logger.exception("Workflow node %s could not be abandoned", claim.node_id)
            return False

    def _new_lease_until(self) -> datetime:
        return datetime.now(UTC) + self._lease_duration


__all__ = [
    "WORKFLOW_LEASE_DURATION",
    "WorkflowAdapterRegistry",
    "WorkflowCancelled",
    "WorkflowNodeAdapter",
    "WorkflowNodeResult",
    "WorkflowPaused",
    "WorkflowRetryableError",
    "WorkflowScheduler",
    "WorkflowWaitingForApproval",
]
