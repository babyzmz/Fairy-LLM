from __future__ import annotations

from threading import Event
from uuid import UUID

from fairy_core.runtime.pool_scheduler import PreviewIdleScheduler


class _RuntimeProbe:
    def __init__(self) -> None:
        self.called = Event()

    def reap_idle_previews(self) -> tuple[UUID, ...]:
        self.called.set()
        return ()


def test_preview_idle_scheduler_wakes_and_closes() -> None:
    runtime = _RuntimeProbe()
    scheduler = PreviewIdleScheduler(runtime, sweep_interval_seconds=60)

    scheduler.wake()

    assert runtime.called.wait(1)
    scheduler.close()
    scheduler.close()
