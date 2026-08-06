from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread
from uuid import UUID

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.assistant.work_queue import (
    ASSISTANT_COMMAND_LEASE_DURATION,
    ASSISTANT_TURN_LEASE_DURATION,
    AssistantTurnWorkClaim,
    assistant_command_lease_until,
)
from fairy_core.domain.ids import new_id
from fairy_core.providers import CancellationToken
from fairy_core.workflow.models import WorkflowRunStatus
from fairy_core.workflow.scheduler import WorkflowScheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ActiveWork:
    claim: AssistantTurnWorkClaim
    cancellation: CancellationToken


class AssistantTurnScheduler:
    def __init__(
        self,
        *,
        application: AssistantApplication,
        ledger: AssistantLedgerApplication,
        workflow_scheduler: WorkflowScheduler,
        max_workers: int = 4,
        lease_duration: timedelta = ASSISTANT_TURN_LEASE_DURATION,
        heartbeat_interval: float = 5.0,
        poll_interval: float = 0.1,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if lease_duration <= ASSISTANT_COMMAND_LEASE_DURATION:
            raise ValueError("Turn lease must outlive Assistant Command leases")
        if heartbeat_interval <= 0 or poll_interval <= 0:
            raise ValueError("worker intervals must be positive")
        if heartbeat_interval >= ASSISTANT_COMMAND_LEASE_DURATION.total_seconds():
            raise ValueError("heartbeat_interval must be shorter than Command leases")
        self._application = application
        self._ledger = ledger
        self._workflow_scheduler = workflow_scheduler
        self._max_workers = max_workers
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._owner_id = f"assistant-worker:{new_id()}"
        self._active: dict[UUID, _ActiveWork] = {}
        self._lock = RLock()
        self._wake = Event()
        self._closed = False
        self._last_heartbeat = time.monotonic()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fairy-assistant",
        )
        self._coordinator = Thread(
            target=self._coordinate,
            name="fairy-assistant-coordinator",
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
        self._coordinator.join()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._active.clear()

    def cancel(self, turn_id: UUID) -> bool:
        turn = self._ledger.get_turn(turn_id)
        if turn.workflow_run_id is not None:
            if turn.workflow_summary is None or turn.workflow_summary.status in {
                WorkflowRunStatus.COMPLETED,
                WorkflowRunStatus.CANCELLED,
                WorkflowRunStatus.FAILED,
            }:
                return False
            self._workflow_scheduler.cancel(turn.workflow_run_id)
            return True
        with self._lock:
            active = self._active.get(turn_id)
            if active is not None:
                active.cancellation.cancel()
        cancelled = self._ledger.cancel_turn_work(turn_id)
        self._wake.set()
        return active is not None or cancelled

    def pause(self, turn_id: UUID) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.workflow_run_id is None:
            raise ValueError("Legacy Assistant Turn cannot be paused")
        self._workflow_scheduler.pause(turn.workflow_run_id)
        return self._ledger.get_turn(turn_id)

    def resume(self, turn_id: UUID) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.workflow_run_id is None:
            raise ValueError("Legacy Assistant Turn cannot be resumed")
        self._workflow_scheduler.resume(turn.workflow_run_id)
        return self._ledger.get_turn(turn_id)

    def steer(
        self,
        *,
        turn_id: UUID,
        instruction: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> AssistantTurn:
        turn = self._ledger.steer_turn(
            turn_id=turn_id,
            instruction=instruction,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        self._workflow_scheduler.wake()
        return turn

    def run(self, turn_id: UUID) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.workflow_run_id is not None:
            self._start_workflow(turn)
            deadline = time.monotonic() + 2 * 60 * 60
            snapshot = self._workflow_scheduler.wait(
                turn.workflow_run_id,
                timeout=2 * 60 * 60,
            )
            if snapshot.run.status is not WorkflowRunStatus.CANCELLED:
                return self._ledger.get_turn(turn_id)
            while True:
                turn = self._ledger.get_turn(turn_id)
                if turn.status in _TERMINAL_TURN_STATUSES:
                    return turn
                if time.monotonic() >= deadline:
                    raise TimeoutError("Assistant Turn did not settle with its Workflow")
                time.sleep(self._poll_interval)
        with self._lock:
            if self._closed:
                raise RuntimeError("Core service is closing")
            turn = self._ledger.get_turn(turn_id)
            if turn.status in _TERMINAL_TURN_STATUSES:
                return turn
            if turn_id in self._active:
                raise ValueError("Assistant Turn is already running")
            self._ledger.enqueue_turn_work(turn_id)
            claim = self._ledger.claim_turn_work(
                turn_id,
                worker_id=self._owner_id,
                lease_until=self._new_lease_until(),
            )
            if claim is None:
                raise ValueError("Assistant Turn is already running")
            cancellation = CancellationToken()
            self._active[turn_id] = _ActiveWork(claim, cancellation)
        return self._execute_claim(claim, cancellation, raise_errors=True)

    def start(
        self,
        turn_id: UUID,
        *,
        restart_if_running: bool = False,
    ) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.status in _TERMINAL_TURN_STATUSES:
            return turn
        if turn.workflow_run_id is not None:
            self._start_workflow(turn)
            return self._ledger.get_turn(turn_id)
        with self._lock:
            if self._closed:
                raise RuntimeError("Core service is closing")
        self._ledger.enqueue_turn_work(turn_id, force=restart_if_running)
        self._wake.set()
        return self._ledger.get_turn(turn_id)

    def _start_workflow(self, turn: AssistantTurn) -> None:
        assert turn.workflow_run_id is not None
        summary = turn.workflow_summary
        if summary is None:
            raise RuntimeError("Assistant Workflow summary is unavailable")
        if summary.status in {
            WorkflowRunStatus.WAITING_FOR_APPROVAL,
            WorkflowRunStatus.PAUSED,
        }:
            self._workflow_scheduler.resume(turn.workflow_run_id)
        elif summary.status not in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.FAILED,
        }:
            self._workflow_scheduler.wake()

    def _coordinate(self) -> None:
        while True:
            self._wake.wait(self._poll_interval)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
            try:
                self._heartbeat_if_due()
                self._claim_available_work()
            except Exception:
                logger.exception("Assistant Turn coordinator iteration failed")

    def _heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < self._heartbeat_interval:
            return
        self._last_heartbeat = now
        with self._lock:
            active_items = tuple(self._active.values())
        for active in active_items:
            try:
                renewed = self._ledger.renew_turn_work(
                    active.claim,
                    lease_until=self._new_lease_until(),
                )
                if renewed:
                    renewed = self._ledger.renew_turn_command_leases(
                        active.claim.turn_id,
                        lease_until=assistant_command_lease_until(),
                    )
            except Exception:
                logger.exception(
                    "Assistant Turn %s lease heartbeat failed",
                    active.claim.turn_id,
                )
                renewed = False
            if not renewed:
                active.cancellation.interrupt()

    def _claim_available_work(self) -> None:
        while True:
            with self._lock:
                if self._closed or len(self._active) >= self._max_workers:
                    return
                claim = self._ledger.claim_next_turn_work(
                    worker_id=self._owner_id,
                    lease_until=self._new_lease_until(),
                )
                if claim is None:
                    return
                if claim.turn_id in self._active:
                    self._ledger.abandon_turn_work(claim)
                    return
                cancellation = CancellationToken()
                self._active[claim.turn_id] = _ActiveWork(claim, cancellation)
                try:
                    self._executor.submit(self._execute_claim, claim, cancellation)
                except Exception:
                    self._active.pop(claim.turn_id, None)
                    self._ledger.abandon_turn_work(claim)
                    raise

    def _execute_claim(
        self,
        claim: AssistantTurnWorkClaim,
        cancellation: CancellationToken,
        *,
        raise_errors: bool = False,
    ) -> AssistantTurn:
        error_code: str | None = None
        turn: AssistantTurn | None = None
        try:
            turn = self._application.run_turn(claim.turn_id, cancellation)
            return turn
        except Exception as error:
            if cancellation.is_interrupted:
                turn = self._ledger.get_turn(claim.turn_id)
                if raise_errors:
                    raise
                return turn
            error_code = str(getattr(error, "error_code", "ASSISTANT_INTERNAL_ERROR"))
            if raise_errors:
                raise
            logger.exception(
                "Assistant Turn %s failed in the durable Worker",
                claim.turn_id,
            )
            turn = self._ledger.get_turn(claim.turn_id)
            return turn
        finally:
            self._finish(claim.turn_id, cancellation)
            try:
                unsettled = turn is None or turn.status not in _TERMINAL_TURN_STATUSES
                if unsettled and (cancellation.is_interrupted or error_code is not None):
                    self._ledger.abandon_turn_work(claim)
                else:
                    self._ledger.release_turn_work(claim, error_code=error_code)
            except Exception:
                logger.exception(
                    "Assistant Turn %s work claim could not be settled",
                    claim.turn_id,
                )
            self._wake.set()

    def _finish(self, turn_id: UUID, cancellation: CancellationToken) -> None:
        with self._lock:
            active = self._active.get(turn_id)
            if active is not None and active.cancellation is cancellation:
                self._active.pop(turn_id, None)

    def _new_lease_until(self) -> datetime:
        return datetime.now(UTC) + self._lease_duration


_TERMINAL_TURN_STATUSES = {
    AssistantTurnStatus.COMPLETED,
    AssistantTurnStatus.CANCELLED,
    AssistantTurnStatus.FAILED,
}


__all__ = ["AssistantTurnScheduler"]
