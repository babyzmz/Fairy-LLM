from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore, Condition

from fairy_core.domain.errors import CommandRejectedError


class CancellationCleanupQueue:
    """A bounded, Core-owned stop-signal lane, never a business-work scheduler."""

    def __init__(self, *, capacity: int = 32) -> None:
        if capacity < 1:
            raise ValueError("cancellation capacity must be positive")
        self._slots = BoundedSemaphore(capacity)
        self._lock = Condition()
        self._reservations = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="core-stop-cleanup")

    @contextmanager
    def reserve(self) -> Iterator[Callable[[Callable[[], None]], None]]:
        with self._lock:
            if self._closed or not self._slots.acquire(blocking=False):
                raise CommandRejectedError(
                    "Stop queue is unavailable; cancellation was not accepted",
                    code="RPC_CAPACITY_EXCEEDED",
                )
            self._reservations += 1
        submitted = False

        def submit(cleanup: Callable[[], None]) -> None:
            nonlocal submitted
            if submitted:
                raise RuntimeError("stop reservation already submitted")
            with self._lock:
                future = self._executor.submit(cleanup)
                submitted = True
            future.add_done_callback(lambda _future: self._slots.release())

        try:
            yield submit
        finally:
            if not submitted:
                self._slots.release()
            with self._lock:
                self._reservations -= 1
                self._lock.notify_all()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.wait_for(lambda: self._reservations == 0)
        # Accepted reservations finish before this owner closes its domain executors.
        self._executor.shutdown(wait=True)
