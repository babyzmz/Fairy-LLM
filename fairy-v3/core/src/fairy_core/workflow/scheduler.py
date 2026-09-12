from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
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
    WorkflowEdge,
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
    next_nodes: tuple[WorkflowNode, ...] = ()
    next_edges: tuple[WorkflowEdge, ...] = ()

    def __post_init__(self) -> None:
        if self.available_at is not None and (self.next_nodes or self.next_edges):
            raise ValueError("Deferred Workflow nodes cannot append continuations")


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


class WorkflowWaitingForInput(RuntimeError):
    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        super().__init__("Workflow is waiting for user input")
        self.result = dict(result or {})


class WorkflowCancelled(RuntimeError):
    pass


class WorkflowPaused(RuntimeError):
    pass


class WorkflowAdapterRegistry:
    def __init__(self, adapters: Mapping[str, WorkflowNodeAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {})
        self._failure_handlers = {}

    def register_failure_handler(self, owner_kind, engine_version, handler):
        key = (owner_kind, engine_version)
        if key in self._failure_handlers or not callable(handler):
            raise ValueError("Workflow failure handler is invalid or already registered")
        self._failure_handlers[key] = handler

    def settle_failed_run(self, unit, run):
        if run.status is not WorkflowRunStatus.FAILED:
            return
        handler = self._failure_handlers.get((run.owner_kind, run.engine_version))
        if handler is not None:
            handler(unit, run)

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

    def reconciliation_phases(self) -> dict[str, str]:
        phases = {}
        for kind, adapter in self._adapters.items():
            policy = getattr(adapter, "paused_reconciliation_phase", None)
            phase = policy(kind) if callable(policy) else None
            if isinstance(phase, str) and phase:
                phases[kind] = phase
        return phases


@dataclass(frozen=True, slots=True)
class _ActiveNode:
    claim: WorkflowAttemptClaim
    cancellation: CancellationToken
    kind: str
    parent_run_id: UUID | None
    node: WorkflowNode | None = None


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
        self._cancelled_idle: dict[UUID, Future[None]] = {}
        self._resume_after_boundary: set[UUID] = set()
        self._lock = RLock()
        self._wake = Event()
        self._changed = Event()
        signal = getattr(unit_of_work_factory, "workflow_signal", None)
        self._unsubscribe = (
            (signal.subscribe(self._wake), signal.subscribe(self._changed))
            if signal is not None
            else ()
        )
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

    def diagnostics(self):
        with self._lock:
            return {
                "active_nodes": len(self._active),
                "worker_limit": self._max_workers,
                "active_runs": len({item.claim.run_id for item in self._active.values()}),
                "started": self._started,
                "closing": self._closed,
            }

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
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._started:
            self._coordinator.join()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            remaining = tuple(self._active.values())
            self._active.clear()
            self._resume_after_boundary.clear()
        for item in remaining:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    if unit_of_work.workflows.abandon(item.claim):
                        self._adapters.settle_failed_run(
                            unit_of_work,
                            unit_of_work.workflows.get_run(item.claim.run_id),
                        )
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
                run = unit_of_work.workflows.get_run(run_id)
            if run is None:
                raise KeyError(f"Workflow Run not found: {run_id}")
            if run.status in {
                WorkflowRunStatus.COMPLETED,
                WorkflowRunStatus.CANCELLED,
                WorkflowRunStatus.FAILED,
                WorkflowRunStatus.PAUSED,
                WorkflowRunStatus.WAITING_FOR_APPROVAL,
                WorkflowRunStatus.WAITING_FOR_INPUT,
            }:
                with self._unit_of_work_factory() as unit_of_work:
                    snapshot = unit_of_work.workflows.get(run_id)
                if snapshot is not None and snapshot.run.status is run.status:
                    return snapshot
                continue
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

    def resume_after_boundary(self, run_id: UUID) -> None:
        """Resume a Run once an in-flight node publishes its waiting state.

        Approval decisions can race the node that is persisting
        ``waiting_for_approval``. Keeping this short-lived coordinator intent
        prevents the approved Run from being stranded at that boundary. Crash
        recovery remains authoritative because the approval decision itself is
        durable.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("Workflow Scheduler is closing")
            self._resume_after_boundary.add(run_id)
        self._resume_after_boundary_if_ready(run_id)
        self._wake.set()

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

    def cancelled_run_idle(self, run_id: UUID) -> Future[None]:
        """Confirm local adapter return, separately from the durable cancel request."""
        with self._unit_of_work_factory() as unit:
            run = unit.workflows.get_run(run_id)
            if run is None or run.status is not WorkflowRunStatus.CANCELLED:
                raise ValueError("Idle acknowledgement requires a cancelled Workflow")
        with self._lock:
            if any(item.claim.run_id == run_id for item in self._active.values()):
                return self._cancelled_idle.setdefault(run_id, Future())
        finished: Future[None] = Future()
        finished.set_result(None)
        return finished

    def _coordinate(self) -> None:
        idle_delay = self._poll_interval
        delay = idle_delay
        while True:
            notified = self._wake.wait(delay)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
            try:
                self._heartbeat_if_due()
                claimed = self._claim_available()
                idle_delay = (
                    self._poll_interval if claimed or notified else min(5.0, idle_delay * 2)
                )
                with self._unit_of_work_factory() as unit_of_work:
                    due = unit_of_work.workflows.next_wake_delay(
                        now=datetime.now(UTC),
                        maximum=idle_delay,
                        reconciliation_phases=self._adapters.reconciliation_phases(),
                    )
                delay = max(self._poll_interval, due)
                with self._lock:
                    if self._active:
                        delay = min(
                            delay,
                            max(
                                0.0,
                                self._heartbeat_interval
                                - (time.monotonic() - self._last_heartbeat),
                            ),
                        )
            except Exception:
                logger.exception("Workflow coordinator iteration failed")
                idle_delay = min(5.0, idle_delay * 2)
                delay = idle_delay

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
                        on_failed=lambda run: self._adapters.settle_failed_run(unit_of_work, run),
                    )
                    # Renewal also settles expired run budgets, even when this
                    # particular attempt can no longer retain its lease.
                    unit_of_work.commit()
                    node = item.node
                    if renewed and node is None:
                        node = unit_of_work.workflows.get_node(
                            item.claim.run_id,
                            item.claim.node_id,
                        )
                if renewed and node is not None:
                    heartbeat = getattr(self._adapters.require(node.kind), "heartbeat", None)
                    if callable(heartbeat):
                        renewed = bool(heartbeat(node))
            except Exception:
                logger.exception("Workflow node %s heartbeat failed", item.claim.node_id)
                renewed = False
            if not renewed:
                item.cancellation.interrupt()

    def _claim_available(self) -> bool:
        with self._lock:
            capacity = self._max_workers - len(self._active)
            active = tuple(self._active.values())
        if capacity <= 0:
            return False
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
                reconciliation_phases=self._adapters.reconciliation_phases(),
                on_failed=lambda run: self._adapters.settle_failed_run(unit_of_work, run),
            )
            claim_nodes = {}
            for claim in claims:
                run = unit_of_work.workflows.get_run(claim.run_id)
                node = unit_of_work.workflows.get_node(claim.run_id, claim.node_id)
                if run is None or node is None:
                    continue
                claim_nodes[claim.node_id] = (node, run.parent_run_id)
            # claim_ready also performs deadline/expired-lease maintenance.
            # No returned claim does not imply that the transaction was read-only.
            unit_of_work.commit()
        for claim in claims:
            cancellation = CancellationToken()
            claim_node = claim_nodes.get(claim.node_id)
            if claim_node is None:
                self._abandon(claim)
                continue
            node, parent_run_id = claim_node
            active = _ActiveNode(
                claim=claim,
                cancellation=cancellation,
                kind=node.kind,
                parent_run_id=parent_run_id,
                node=node,
            )
            with self._lock:
                if self._closed:
                    cancellation.interrupt()
                    self._abandon(claim)
                    return False
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
        return bool(claims)

    def _execute(self, active: _ActiveNode) -> None:
        claim = active.claim
        settled = False
        adapter = None
        try:
            node = active.node
            if node is None:
                with self._unit_of_work_factory() as unit_of_work:
                    node = unit_of_work.workflows.get_node(claim.run_id, claim.node_id)
            if node is None:
                raise KeyError(f"Workflow Node not found: {claim.node_id}")
            adapter = self._adapters.require(node.kind)
            execute_claimed = getattr(adapter, "execute_claimed", None)
            result = (
                execute_claimed(node, claim, active.cancellation)
                if callable(execute_claimed)
                else adapter.execute(node, active.cancellation)
            )
            active.cancellation.raise_if_cancelled()
            with self._unit_of_work_factory() as unit_of_work:
                if result.available_at is None:
                    snapshot = unit_of_work.workflows.complete(
                        claim,
                        result=result.output,
                        evidence_refs=result.evidence_refs,
                        public_summary=result.public_summary,
                        next_nodes=result.next_nodes,
                        next_edges=result.next_edges,
                    )
                else:
                    snapshot = unit_of_work.workflows.defer(
                        claim,
                        available_at=result.available_at,
                        result=result.output,
                    )
                self._adapters.settle_failed_run(unit_of_work, snapshot.run)
                unit_of_work.commit()
            settled = True
        except WorkflowRetryableError as error:
            if active.cancellation.is_interrupted:
                self._abandon(claim)
                settled = True
            else:
                try:
                    with self._unit_of_work_factory() as unit_of_work:
                        snapshot = unit_of_work.workflows.retry(
                            claim,
                            available_at=datetime.now(UTC)
                            + timedelta(seconds=min(5.0, 0.25 * 2 ** (claim.attempt_number - 1))),
                            error_code=error.error_code,
                        )
                        self._adapters.settle_failed_run(unit_of_work, snapshot.run)
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
        except WorkflowWaitingForInput as waiting:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    unit_of_work.workflows.wait_for_input(claim, result=waiting.result)
                    unit_of_work.commit()
                settled = True
            except Exception:
                logger.exception("Workflow node %s could not wait for user input", claim.node_id)
        except WorkflowCancelled:
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    unit_of_work.workflows.cancel(claim.run_id)
                    unit_of_work.commit()
                settled = True
            except Exception:
                logger.exception("Workflow Run %s could not be cancelled", claim.run_id)
        except WorkflowPaused:
            abandoned = self._abandon(claim, disable_reconciliation=True)
            settled = abandoned
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
                        snapshot = unit_of_work.workflows.fail(claim, error_code=error_code)
                        on_failure = getattr(adapter, "settle_failure_in_unit", None)
                        if callable(on_failure):
                            on_failure(unit_of_work, node, claim, error_code)
                        self._adapters.settle_failed_run(unit_of_work, snapshot.run)
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
        finished = None
        run_idle = False
        try:
            with self._lock:
                current = self._active.get(claim.node_id)
                if current is active:
                    self._active.pop(claim.node_id, None)
                if not any(item.claim.run_id == claim.run_id for item in self._active.values()):
                    run_idle = True
                    finished = self._cancelled_idle.pop(claim.run_id, None)
        finally:
            if finished is not None and not finished.done():
                finished.set_result(None)
            if run_idle:
                self._replan_completed_boundary(active)
            self._resume_after_boundary_if_ready(claim.run_id)
            self._changed.set()
            self._wake.set()

    def _replan_completed_boundary(self, active: _ActiveNode) -> None:
        """Both a yielded model and successfully completed tools are node boundaries."""
        try:
            with self._unit_of_work_factory() as unit:
                run = unit.workflows.get_run(active.claim.run_id)
                if run is None or run.status is not WorkflowRunStatus.PAUSED:
                    return
                node = active.node or unit.workflows.get_node(
                    active.claim.run_id,
                    active.claim.node_id,
                )
            if node is None:
                return
            replan = getattr(self._adapters.require(node.kind), "replan_after_pause", None)
            if callable(replan):
                replan(node)
        except Exception:
            logger.exception(
                "Workflow Run %s could not apply its boundary update",
                active.claim.run_id,
            )

    def _resume_after_boundary_if_ready(self, run_id: UUID) -> None:
        with self._lock:
            if run_id not in self._resume_after_boundary:
                return
            has_active = any(item.claim.run_id == run_id for item in self._active.values())
        try:
            with self._unit_of_work_factory() as unit_of_work:
                run = unit_of_work.workflows.get_run(run_id)
                if run is None:
                    resolved = True
                elif run.status in {
                    WorkflowRunStatus.WAITING_FOR_APPROVAL,
                    WorkflowRunStatus.WAITING_FOR_INPUT,
                    WorkflowRunStatus.PAUSED,
                }:
                    unit_of_work.workflows.resume(run_id)
                    unit_of_work.commit()
                    resolved = True
                else:
                    resolved = (
                        run.status
                        in {
                            WorkflowRunStatus.COMPLETED,
                            WorkflowRunStatus.CANCELLED,
                            WorkflowRunStatus.FAILED,
                        }
                        or not has_active
                    )
        except Exception:
            logger.exception("Workflow Run %s could not resume after its boundary", run_id)
            resolved = False
        if resolved:
            with self._lock:
                self._resume_after_boundary.discard(run_id)

    def _abandon(
        self,
        claim: WorkflowAttemptClaim,
        *,
        disable_reconciliation: bool = False,
    ) -> bool:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                abandoned = unit_of_work.workflows.abandon(
                    claim,
                    disable_reconciliation=disable_reconciliation,
                )
                if abandoned:
                    self._adapters.settle_failed_run(
                        unit_of_work,
                        unit_of_work.workflows.get_run(claim.run_id),
                    )
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
    "WorkflowWaitingForInput",
]
