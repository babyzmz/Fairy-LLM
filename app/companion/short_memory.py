from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass

from app.companion.scene import Scene


@dataclass(slots=True)
class ShortMemoryEntry:
    scene: Scene
    key: str
    at: float


def _key_for(scene: Scene, game_name: str | None) -> str:
    suffix = game_name or "unknown"
    return f"{scene.value}:{suffix}"


class ShortMemory:
    def __init__(
        self,
        *,
        capacity: int = 50,
        repeat_threshold: int = 3,
        window_seconds: float = 30 * 60.0,
    ) -> None:
        self._capacity = capacity
        self._repeat_threshold = repeat_threshold
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._ring: deque[ShortMemoryEntry] = deque(maxlen=capacity)

    def record(self, scene: Scene, game_name: str | None) -> int:
        key = _key_for(scene, game_name)
        now = time.monotonic()
        with self._lock:
            self._ring.append(ShortMemoryEntry(scene=scene, key=key, at=now))
            return self._count_locked(key, now)

    def repetition_count(self, scene: Scene, game_name: str | None) -> int:
        key = _key_for(scene, game_name)
        with self._lock:
            return self._count_locked(key, time.monotonic())

    def is_repeating(self, scene: Scene, game_name: str | None) -> bool:
        return self.repetition_count(scene, game_name) >= self._repeat_threshold

    def clear(self) -> None:
        with self._lock:
            self._ring.clear()

    def _count_locked(self, key: str, now: float) -> int:
        cutoff = now - self._window_seconds
        return sum(1 for entry in self._ring if entry.key == key and entry.at >= cutoff)

    def snapshot(self) -> Counter[str]:
        with self._lock:
            return Counter(entry.key for entry in self._ring)


_singleton: ShortMemory | None = None
_singleton_lock = threading.Lock()


def get_short_memory() -> ShortMemory:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ShortMemory()
        return _singleton
