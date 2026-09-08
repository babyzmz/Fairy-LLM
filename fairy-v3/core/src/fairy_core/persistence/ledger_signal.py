from __future__ import annotations

from threading import Condition


class LedgerCommitSignal:
    """Process-local hint to reread the durable ledger, never the event authority."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._version = 0

    @property
    def version(self) -> int:
        with self._condition:
            return self._version

    def notify(self) -> None:
        with self._condition:
            self._version += 1
            self._condition.notify_all()

    def wait(self, after: int, timeout: float = 5.0) -> int:
        with self._condition:
            self._condition.wait_for(lambda: self._version != after, timeout=timeout)
            return self._version
