from __future__ import annotations

import logging
from threading import Event, RLock, Thread
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class PreviewIdleApplication(Protocol):
    def reap_idle_previews(self) -> tuple[UUID, ...]: ...


class PreviewIdleScheduler:
    def __init__(
        self,
        application: PreviewIdleApplication,
        *,
        sweep_interval_seconds: float = 30.0,
    ) -> None:
        if sweep_interval_seconds <= 0:
            raise ValueError("Preview sweep interval must be positive")
        self._application = application
        self._sweep_interval_seconds = sweep_interval_seconds
        self._wake = Event()
        self._lock = RLock()
        self._closed = False
        self._thread = Thread(
            target=self._run,
            name="fairy-preview-idle",
            daemon=True,
        )
        self._thread.start()

    def wake(self) -> None:
        with self._lock:
            if self._closed:
                return
        self._wake.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake.set()
        self._thread.join()

    def _run(self) -> None:
        while True:
            self._wake.wait(self._sweep_interval_seconds)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
            try:
                self._application.reap_idle_previews()
            except Exception:
                logger.exception("Preview idle sweep failed")


__all__ = ["PreviewIdleScheduler"]
