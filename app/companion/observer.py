from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.companion.quip_pool import QuipCategory, classify_text, pick_quip


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QuipEvent:
    text: str
    category: QuipCategory
    emitted_at: float
    source: str


QuipSubscriber = Callable[[QuipEvent], None]


class CompanionObserver:
    def __init__(
        self,
        *,
        emit_probability: float = 0.2,
        cooldown_seconds: float = 8.0,
        rng: random.Random | None = None,
    ) -> None:
        self._emit_probability = emit_probability
        self._cooldown_seconds = cooldown_seconds
        self._rng = rng or random.Random()
        self._last_emit_at: float = 0.0
        self._lock = threading.Lock()
        self._subscribers: list[QuipSubscriber] = []
        self._muted = False

    def subscribe(self, subscriber: QuipSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            self._muted = bool(muted)

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    def observe_assistant_text(self, text: str, *, source: str = "assistant") -> QuipEvent | None:
        category = classify_text(text)
        if category is None:
            category = QuipCategory.GENERAL
        return self._maybe_emit(category, source=source, force=False)

    def observe_game_event(self, category: QuipCategory, *, source: str = "screen_watcher") -> QuipEvent | None:
        return self._maybe_emit(category, source=source, force=True)

    def force_emit(self, category: QuipCategory, *, source: str = "manual") -> QuipEvent:
        event = self._build_event(category, source=source)
        self._dispatch(event)
        return event

    def _maybe_emit(self, category: QuipCategory, *, source: str, force: bool) -> QuipEvent | None:
        now = time.monotonic()
        with self._lock:
            if self._muted:
                return None
            if not force and self._rng.random() > self._emit_probability:
                return None
            if now - self._last_emit_at < self._cooldown_seconds:
                return None
            self._last_emit_at = now
        event = self._build_event(category, source=source)
        self._dispatch(event)
        return event

    def _build_event(self, category: QuipCategory, *, source: str) -> QuipEvent:
        text = pick_quip(category, rng=self._rng)
        return QuipEvent(text=text, category=category, emitted_at=time.time(), source=source)

    def _dispatch(self, event: QuipEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                logger.exception("companion_observer_subscriber_failed")


_singleton: CompanionObserver | None = None
_singleton_lock = threading.Lock()


def get_companion_observer() -> CompanionObserver:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = CompanionObserver()
        return _singleton
