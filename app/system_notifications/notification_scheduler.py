from __future__ import annotations

import time


class NotificationScheduler:
    def __init__(self, *, interval_seconds: int = 60) -> None:
        self.interval_seconds = max(15, int(interval_seconds))
        self._last_run = 0.0

    def update_interval(self, interval_seconds: int) -> None:
        self.interval_seconds = max(15, int(interval_seconds))

    def should_run(self, *, force: bool = False) -> bool:
        if force:
            return True
        return (time.monotonic() - self._last_run) >= self.interval_seconds

    def mark_run(self) -> None:
        self._last_run = time.monotonic()
