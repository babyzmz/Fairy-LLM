from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class DensityGovernor:
    window_seconds: float = 180.0
    soft_cap: int = 5
    hard_cap: int = 9
    _emissions: deque[float] = field(default_factory=deque)

    def gate(self, base_probability: float) -> float:
        self._prune()
        count = len(self._emissions)
        if count >= self.hard_cap:
            return 0.0
        if count < self.soft_cap:
            return base_probability
        span = max(1, self.hard_cap - self.soft_cap)
        decay = 1.0 - (count - self.soft_cap) / span
        return base_probability * (0.1 + 0.5 * decay)

    def record_emission(self, *, at: float | None = None) -> None:
        self._emissions.append(at if at is not None else time.monotonic())
        self._prune()

    def recent_count(self) -> int:
        self._prune()
        return len(self._emissions)

    def reset(self) -> None:
        self._emissions.clear()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        while self._emissions and self._emissions[0] < cutoff:
            self._emissions.popleft()
