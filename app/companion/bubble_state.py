from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass

from app.companion.observer import CompanionObserver, QuipEvent, get_companion_observer


BUBBLE_SHOW_SECONDS = 10.0
FADE_WINDOW_SECONDS = 3.0
PET_BURST_SECONDS = 2.5


@dataclass(slots=True)
class BubbleSnapshot:
    quip: str
    category: str
    source: str
    quip_started_at: float
    fade_at: float
    expires_at: float
    pet_started_at: float | None
    pet_expires_at: float | None
    muted: bool
    now: float


class BubbleState:
    def __init__(self, *, observer: CompanionObserver | None = None) -> None:
        self._observer = observer or get_companion_observer()
        self._lock = threading.Lock()
        self._current: QuipEvent | None = None
        self._quip_started_at: float = 0.0
        self._pet_started_at: float | None = None
        self._observer_unsub = self._observer.subscribe(self._on_quip)

    def _on_quip(self, event: QuipEvent) -> None:
        with self._lock:
            self._current = event
            self._quip_started_at = time.monotonic()

    def trigger_pet(self) -> float:
        with self._lock:
            self._pet_started_at = time.monotonic()
            return self._pet_started_at

    def set_muted(self, muted: bool) -> None:
        self._observer.set_muted(muted)
        if muted:
            with self._lock:
                self._current = None
                self._quip_started_at = 0.0

    def snapshot(self) -> BubbleSnapshot | None:
        now_monotonic = time.monotonic()
        with self._lock:
            current = self._current
            quip_started = self._quip_started_at
            pet_started = self._pet_started_at
            muted = self._observer.muted

        if current is None:
            if pet_started is None and not muted:
                return None
            return BubbleSnapshot(
                quip="",
                category="",
                source="",
                quip_started_at=0.0,
                fade_at=0.0,
                expires_at=0.0,
                pet_started_at=self._monotonic_to_wall(pet_started, now_monotonic) if pet_started else None,
                pet_expires_at=self._monotonic_to_wall(pet_started + PET_BURST_SECONDS, now_monotonic) if pet_started else None,
                muted=muted,
                now=time.time(),
            )

        age = now_monotonic - quip_started
        if age >= BUBBLE_SHOW_SECONDS:
            with self._lock:
                if self._current is current:
                    self._current = None
                    self._quip_started_at = 0.0
            return None

        wall_now = time.time()
        return BubbleSnapshot(
            quip=current.text,
            category=current.category.value,
            source=current.source,
            quip_started_at=self._monotonic_to_wall(quip_started, now_monotonic),
            fade_at=self._monotonic_to_wall(quip_started + (BUBBLE_SHOW_SECONDS - FADE_WINDOW_SECONDS), now_monotonic),
            expires_at=self._monotonic_to_wall(quip_started + BUBBLE_SHOW_SECONDS, now_monotonic),
            pet_started_at=self._monotonic_to_wall(pet_started, now_monotonic) if pet_started else None,
            pet_expires_at=self._monotonic_to_wall(pet_started + PET_BURST_SECONDS, now_monotonic) if pet_started else None,
            muted=muted,
            now=wall_now,
        )

    def snapshot_dict(self) -> dict | None:
        snap = self.snapshot()
        return asdict(snap) if snap else None

    @staticmethod
    def _monotonic_to_wall(monotonic_value: float, monotonic_now: float) -> float:
        return time.time() + (monotonic_value - monotonic_now)

    def shutdown(self) -> None:
        self._observer_unsub()


_singleton: BubbleState | None = None
_singleton_lock = threading.Lock()


def get_bubble_state() -> BubbleState:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BubbleState()
        return _singleton
