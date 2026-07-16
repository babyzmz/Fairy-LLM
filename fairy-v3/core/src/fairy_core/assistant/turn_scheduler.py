from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from uuid import UUID

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import AssistantTurn, AssistantTurnStatus
from fairy_core.providers import CancellationToken

logger = logging.getLogger(__name__)


class AssistantTurnScheduler:
    def __init__(
        self,
        *,
        application: AssistantApplication,
        ledger: AssistantLedgerApplication,
        max_workers: int = 4,
    ) -> None:
        self._application = application
        self._ledger = ledger
        self._active: dict[UUID, CancellationToken] = {}
        self._restart_requests: set[UUID] = set()
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fairy-assistant",
        )
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for cancellation in self._active.values():
                cancellation.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._active.clear()
            self._restart_requests.clear()

    def cancel(self, turn_id: UUID) -> bool:
        with self._lock:
            cancellation = self._active.get(turn_id)
            if cancellation is None:
                return False
            cancellation.cancel()
            return True

    def run(self, turn_id: UUID) -> AssistantTurn:
        cancellation = CancellationToken()
        with self._lock:
            if turn_id in self._active:
                raise ValueError("Assistant Turn is already running")
            self._active[turn_id] = cancellation
        try:
            return self._application.run_turn(turn_id, cancellation)
        finally:
            self._finish(turn_id, cancellation)

    def start(
        self,
        turn_id: UUID,
        *,
        restart_if_running: bool = False,
    ) -> AssistantTurn:
        turn = self._ledger.get_turn(turn_id)
        if turn.status in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.CANCELLED,
            AssistantTurnStatus.FAILED,
        }:
            return turn
        cancellation = CancellationToken()
        with self._lock:
            if self._closed:
                raise RuntimeError("Core service is closing")
            if turn_id in self._active:
                if restart_if_running:
                    self._restart_requests.add(turn_id)
                return self._ledger.get_turn(turn_id)
            self._active[turn_id] = cancellation
            try:
                self._executor.submit(self._run_in_background, turn_id, cancellation)
            except Exception:
                self._active.pop(turn_id, None)
                raise
        return self._ledger.get_turn(turn_id)

    def _run_in_background(
        self,
        turn_id: UUID,
        cancellation: CancellationToken,
    ) -> None:
        try:
            self._application.run_turn(turn_id, cancellation)
        except Exception:
            logger.exception("Assistant Turn %s failed in the background Runner", turn_id)
        finally:
            self._finish(turn_id, cancellation)

    def _finish(self, turn_id: UUID, cancellation: CancellationToken) -> None:
        restart = False
        with self._lock:
            if self._active.get(turn_id) is cancellation:
                self._active.pop(turn_id, None)
            if turn_id in self._restart_requests:
                self._restart_requests.discard(turn_id)
                restart = not self._closed
        if restart:
            try:
                self.start(turn_id)
            except Exception:
                logger.exception(
                    "Assistant Turn %s could not resume after its prior Runner exited",
                    turn_id,
                )


__all__ = ["AssistantTurnScheduler"]
