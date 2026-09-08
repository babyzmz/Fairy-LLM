from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from uuid import UUID

from fairy_core.contracts.browser import BrowserSessionModel, BrowserSessionStatus
from fairy_core.workspace.worker_transport import WorkerRpcError

ActivityProbe = Callable[[tuple[UUID, ...]], frozenset[UUID]]
_RESIDENT = {BrowserSessionStatus.STARTING, BrowserSessionStatus.ACTIVE}


def resource_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._resources.operation():
            return method(self, *args, **kwargs)

    return guarded


class BrowserResources:
    """Own resident pages, not tasks. All task activity comes from Core authority."""

    def __init__(
        self,
        *,
        sessions: Callable[[], tuple[BrowserSessionModel, ...]],
        suspend: Callable[[UUID], None],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._sessions = sessions
        self._suspend = suspend
        self._clock = clock
        self._lock = RLock()
        self._wake = Event()
        self._stop = Event()
        self._last_used: dict[UUID, float] = {}
        self._activity: ActivityProbe = lambda ids: frozenset(ids)
        self._maintenance_enabled = False
        self._thread: Thread | None = None

    @contextmanager
    def operation(self) -> Iterator[None]:
        with self._lock:
            if self._stop.is_set():
                raise WorkerRpcError("Browser resources are shutting down")
            yield

    def configure_activity(self, probe: ActivityProbe, *, maintenance: bool) -> None:
        with self._lock:
            self._activity = probe
            self._maintenance_enabled = maintenance
            self._start_if_needed()

    def touch(self, session_id: UUID) -> None:
        with self._lock:
            self._last_used[session_id] = self._clock()
            self._start_if_needed()
            self._wake.set()

    def ensure_capacity(
        self, *, sessions: int = 0, tabs: int = 0, protect: UUID | None = None
    ) -> None:
        with self.operation():
            resident = self._resident()
            if self._fits(resident, sessions, tabs):
                return
            pinned = self._pinned(resident)
            for candidate in sorted(resident, key=lambda item: self._last_used.get(item.id, 0)):
                if candidate.id == protect or candidate.task_id in pinned:
                    continue
                try:
                    self._suspend(candidate.id)
                except Exception as error:
                    raise WorkerRpcError(
                        "Browser capacity cannot be released; close the idle session and retry.",
                        error_code="BROWSER_CAPACITY_EXCEEDED",
                    ) from error
                if self._fits(self._resident(), sessions, tabs):
                    return
            raise WorkerRpcError(
                "Browser capacity is occupied by active tasks; wait or close an idle session.",
                error_code="BROWSER_CAPACITY_EXCEEDED",
            )

    def reap_idle(self) -> None:
        with self.operation():
            resident = self._resident()
            now = self._clock()
            expired = [item for item in resident if now - self._last_used.get(item.id, now) >= 300]
            if not expired:
                return
            pinned = self._pinned(resident)
            for session in expired:
                if session.task_id not in pinned:
                    self._suspend(session.id)

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def close(self) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=35)

    def _resident(self) -> tuple[BrowserSessionModel, ...]:
        resident = tuple(item for item in self._sessions() if item.status in _RESIDENT)
        ids = {item.id for item in resident}
        self._last_used = {key: value for key, value in self._last_used.items() if key in ids}
        return resident

    def _pinned(self, resident: tuple[BrowserSessionModel, ...]) -> frozenset[UUID]:
        ids = tuple({item.task_id for item in resident if item.task_id is not None})
        if not ids:
            return frozenset()
        try:
            return self._activity(ids)
        except Exception:
            # Missing/offline authority must never be interpreted as no active task.
            return frozenset(ids)

    @staticmethod
    def _fits(resident: tuple[BrowserSessionModel, ...], sessions: int, tabs: int) -> bool:
        return (
            len(resident) + sessions <= 4
            and sum(max(1, len(item.tabs)) for item in resident) + tabs <= 12
        )

    def _start_if_needed(self) -> None:
        if (
            self._maintenance_enabled
            and self._thread is None
            and self._resident()
            and not self._stop.is_set()
        ):
            self._thread = Thread(
                target=self._maintain, name="fairy-browser-resources", daemon=True
            )
            self._thread.start()

    def _maintain(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                resident = self._resident()
                now = self._clock()
                due = min(
                    (self._last_used.get(item.id, now) + 300 - now for item in resident),
                    default=None,
                )
                delay = None if due is None else max(5.0, due)
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.reap_idle()
            except Exception:
                # Resource release failure leaves the session resident for retry.
                self._stop.wait(5)
