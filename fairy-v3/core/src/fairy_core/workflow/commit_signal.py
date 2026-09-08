from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock


class WorkflowCommitSignal:
    """Tenant-local wake hints; durable rows remain the scheduling authority."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: set[Event] = set()

    def subscribe(self, wake: Event) -> Callable[[], None]:
        with self._lock:
            self._subscribers.add(wake)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.discard(wake)

        return unsubscribe

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def notify(self) -> None:
        # Only Event.set is allowed here: no subscriber code, database work or queues.
        with self._lock:
            for wake in self._subscribers:
                wake.set()
