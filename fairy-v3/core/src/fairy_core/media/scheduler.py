from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread
from uuid import UUID

from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.domain.errors import InvalidTransitionError, WorkerFenceError
from fairy_core.domain.ids import new_id
from fairy_core.media.application import MediaApplication, MediaGenerationResult
from fairy_core.media.models import MediaGenerationKind
from fairy_core.media.work_queue import (
    MEDIA_COMMAND_LEASE_DURATION,
    MEDIA_WORK_LEASE_DURATION,
    MediaWorkClaim,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderCancelledError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ActiveWork:
    claim: MediaWorkClaim
    cancellation: CancellationToken
    owned_command: CommandRun | None = None


class _CommandBusy(RuntimeError):
    pass


class MediaScheduler:
    def __init__(
        self,
        *,
        application: MediaApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        max_workers: int = 2,
        lease_duration: timedelta = MEDIA_WORK_LEASE_DURATION,
        heartbeat_interval: float = 5.0,
        poll_interval: float = 0.05,
        video_poll_interval: float = 1.0,
        wait_timeout: float = 900.0,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if lease_duration <= MEDIA_COMMAND_LEASE_DURATION:
            raise ValueError("Media work lease must outlive its Command lease")
        if min(heartbeat_interval, poll_interval, video_poll_interval, wait_timeout) <= 0:
            raise ValueError("Media worker intervals must be positive")
        if heartbeat_interval >= MEDIA_COMMAND_LEASE_DURATION.total_seconds():
            raise ValueError("heartbeat_interval must be shorter than Command leases")
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._max_workers = max_workers
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._video_poll_interval = video_poll_interval
        self._wait_timeout = wait_timeout
        self._owner_id = f"media-worker:{new_id()}"
        self._active: dict[UUID, _ActiveWork] = {}
        self._lock = RLock()
        self._wake = Event()
        self._changed = Event()
        self._closed = False
        self._last_heartbeat = time.monotonic()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fairy-media",
        )
        self._coordinator = Thread(
            target=self._coordinate,
            name="fairy-media-coordinator",
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
        with self._lock:
            self._active.clear()

    def enqueue(self, job_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.state.enqueue_media_work(job_id)
            unit_of_work.commit()
        self._wake.set()

    def run(
        self,
        job_id: UUID,
        *,
        cancellation: CancellationToken,
        return_when_video_active: bool = False,
    ) -> MediaGenerationResult:
        self.enqueue(job_id)
        deadline = time.monotonic() + self._wait_timeout
        while True:
            result = self._application.get_result(
                job_id,
                include_active_video=return_when_video_active,
            )
            if result is not None:
                return result
            try:
                cancellation.raise_if_cancelled()
            except ProviderCancelledError:
                if not cancellation.is_interrupted:
                    self.cancel(job_id)
                raise
            with self._lock:
                if self._closed:
                    raise ProviderCancelledError("Media Worker is closing")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Media generation did not settle before its wait deadline")
            self._changed.wait(min(0.05, remaining))
            self._changed.clear()

    def cancel(self, job_id: UUID) -> bool:
        with self._lock:
            active = self._active.get(job_id)
            if active is not None:
                active.cancellation.cancel()
        self._application.cancel_work(job_id)
        with self._unit_of_work_factory() as unit_of_work:
            cancelled = unit_of_work.state.cancel_media_work(job_id)
            if cancelled:
                unit_of_work.commit()
        self._changed.set()
        return active is not None or cancelled

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
                logger.exception("Media coordinator iteration failed")

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
                    renewed = unit_of_work.state.renew_media_work(
                        active.claim,
                        lease_until=self._new_lease_until(),
                    )
                    if renewed and active.owned_command is not None:
                        command = active.owned_command
                        renewed = unit_of_work.commands.renew(
                            command.id,
                            lease_owner=command.lease_owner or "",
                            lease_fence=command.lease_fence,
                            lease_until=self._new_command_lease_until(),
                        )
                    unit_of_work.commit()
            except Exception:
                logger.exception("Media Job %s lease heartbeat failed", active.claim.job_id)
                renewed = False
            if not renewed:
                active.cancellation.interrupt()

    def _claim_available_work(self) -> None:
        while True:
            with self._lock:
                if self._closed or len(self._active) >= self._max_workers:
                    return
            with self._unit_of_work_factory() as unit_of_work:
                claim = unit_of_work.state.claim_next_media_work(
                    worker_id=self._owner_id,
                    lease_until=self._new_lease_until(),
                )
                if claim is not None:
                    unit_of_work.commit()
            if claim is None:
                return
            with self._lock:
                if claim.job_id in self._active:
                    with self._unit_of_work_factory() as unit_of_work:
                        unit_of_work.state.abandon_media_work(claim)
                        unit_of_work.commit()
                    return
                active = _ActiveWork(claim=claim, cancellation=CancellationToken())
                self._active[claim.job_id] = active
                try:
                    self._executor.submit(self._execute_claim, active)
                except Exception:
                    self._active.pop(claim.job_id, None)
                    with self._unit_of_work_factory() as unit_of_work:
                        unit_of_work.state.abandon_media_work(claim)
                        unit_of_work.commit()
                    raise

    def _execute_claim(self, active: _ActiveWork) -> None:
        result: MediaGenerationResult | None = None
        error_code: str | None = None
        reschedule_at: datetime | None = None
        abandon = False
        abandon_command = False
        try:
            active.owned_command = self._claim_user_command(active.claim.job_id)
            result = self._application.execute_job(
                active.claim.job_id,
                cancellation=active.cancellation,
                attempt_number=active.claim.attempts,
            )
            if result.job.is_terminal:
                reschedule_at = None
            elif result.job.kind is MediaGenerationKind.VIDEO:
                reschedule_at = datetime.now(UTC) + timedelta(seconds=self._video_poll_interval)
            else:
                raise RuntimeError("Non-video Media Job remained active after execution")
            self._settle_owned_command(active.owned_command, result=result, error=None)
        except _CommandBusy:
            reschedule_at = datetime.now(UTC) + timedelta(seconds=self._poll_interval)
        except ProviderCancelledError as error:
            if active.cancellation.is_interrupted:
                abandon = True
            else:
                error_code = "MEDIA_CANCELLED"
                self._settle_owned_command(active.owned_command, result=None, error=error)
        except Exception as error:
            error_code = _safe_error_code(error)
            result = self._application.get_result(
                active.claim.job_id,
                include_active_video=False,
                raise_terminal_error=False,
            )
            if result is not None:
                self._settle_owned_command(active.owned_command, result=None, error=error)
                reschedule_at = None
            elif active.claim.attempts >= 3:
                self._application.fail_work(
                    active.claim.job_id,
                    error_code=error_code,
                )
                self._settle_owned_command(active.owned_command, result=None, error=error)
                reschedule_at = None
            else:
                abandon_command = active.owned_command is not None
                reschedule_at = datetime.now(UTC) + timedelta(
                    seconds=min(5.0, 0.25 * (2 ** (active.claim.attempts - 1)))
                )
            logger.exception("Media Job %s failed in the durable Worker", active.claim.job_id)
        finally:
            self._finish(active)
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    if abandon:
                        unit_of_work.state.abandon_media_work(active.claim)
                        self._abandon_owned_command(unit_of_work, active.owned_command)
                    else:
                        if abandon_command:
                            self._abandon_owned_command(unit_of_work, active.owned_command)
                        unit_of_work.state.settle_media_work(
                            active.claim,
                            available_at=reschedule_at,
                            error_code=error_code,
                        )
                    unit_of_work.commit()
            except Exception:
                logger.exception(
                    "Media Job %s work claim could not be settled", active.claim.job_id
                )
            self._changed.set()
            self._wake.set()

    def _claim_user_command(self, job_id: UUID) -> CommandRun | None:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.state.get_media_job(job_id)
            if job is None:
                raise KeyError(f"Media generation job not found: {job_id}")
            run = unit_of_work.commands.get_run(job.command_run_id)
            if run is None:
                raise RuntimeError("Media Job lost its CommandRun")
            if run.actor == "assistant" or run.status is CommandStatus.SUCCEEDED:
                return None
            if run.status not in {CommandStatus.QUEUED, CommandStatus.RUNNING}:
                raise RuntimeError(f"Media Command cannot run from {run.status.value}")
            try:
                claimed = unit_of_work.commands.claim(
                    run.id,
                    worker_id=self._owner_id,
                    lease_until=self._new_command_lease_until(),
                )
            except (InvalidTransitionError, WorkerFenceError) as error:
                raise _CommandBusy from error
            unit_of_work.commit()
        return claimed

    def _settle_owned_command(
        self,
        command: CommandRun | None,
        *,
        result: MediaGenerationResult | None,
        error: BaseException | None,
    ) -> None:
        if command is None:
            return
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(command.id)
            if persisted is None or persisted.status is not CommandStatus.RUNNING:
                return
            bus = CommandBus(
                registry=self._registry,
                policy=PolicyEngine(self._registry),
                ledger=unit_of_work.commands,
            )
            if error is None and result is not None:
                bus.complete(
                    persisted.id,
                    output={
                        "job_id": str(result.job.id),
                        "status": result.job.status.value,
                        "artifact_id": (
                            str(result.artifact.id) if result.artifact is not None else None
                        ),
                    },
                    lease_owner=persisted.lease_owner,
                    lease_fence=persisted.lease_fence,
                )
            else:
                bus.fail(
                    persisted.id,
                    error_code=_safe_error_code(error or RuntimeError("Media failed")),
                    lease_owner=persisted.lease_owner,
                    lease_fence=persisted.lease_fence,
                )
            unit_of_work.commit()

    @staticmethod
    def _abandon_owned_command(unit_of_work, command: CommandRun | None) -> None:
        if command is None or command.lease_owner is None:
            return
        unit_of_work.commands.abandon(
            command.id,
            lease_owner=command.lease_owner,
            lease_fence=command.lease_fence,
        )

    def _finish(self, active: _ActiveWork) -> None:
        with self._lock:
            current = self._active.get(active.claim.job_id)
            if current is active:
                self._active.pop(active.claim.job_id, None)

    def _new_lease_until(self) -> datetime:
        return datetime.now(UTC) + self._lease_duration

    @staticmethod
    def _new_command_lease_until() -> datetime:
        return datetime.now(UTC) + MEDIA_COMMAND_LEASE_DURATION


def _safe_error_code(error: BaseException) -> str:
    value = getattr(error, "error_code", None)
    if isinstance(value, str) and value.strip():
        return value.strip()[:128]
    if isinstance(error, TimeoutError):
        return "MEDIA_TIMEOUT"
    return "MEDIA_FAILED"


__all__ = ["MediaScheduler"]
