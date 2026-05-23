from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class DensityGovernor:
    window_seconds: float = 180.0
    soft_cap: int = 5
    hard_cap: int = 9
    energy_drain_per_emit: float = 0.12
    energy_recovery_per_second: float = 0.02
    energy_floor: float = 0.05
    _emissions: deque[float] = field(default_factory=deque)
    _energy: float = 1.0
    _energy_updated_at: float = field(default_factory=time.monotonic)

    def gate(self, base_probability: float) -> float:
        density_factor = self._density_factor()
        if density_factor <= 0.0:
            return 0.0
        energy = self._current_energy()
        return base_probability * density_factor * energy

    def record_emission(self, *, at: float | None = None) -> None:
        now = at if at is not None else time.monotonic()
        self._emissions.append(now)
        self._prune(now)
        current = self._compute_energy_at(now)
        drained = max(self.energy_floor, current - self.energy_drain_per_emit)
        self._energy = drained
        self._energy_updated_at = now

    def recent_count(self) -> int:
        self._prune(time.monotonic())
        return len(self._emissions)

    def current_energy(self) -> float:
        return self._current_energy()

    def reset(self) -> None:
        self._emissions.clear()
        self._energy = 1.0
        self._energy_updated_at = time.monotonic()

    def _density_factor(self) -> float:
        self._prune(time.monotonic())
        count = len(self._emissions)
        if count >= self.hard_cap:
            return 0.0
        if count < self.soft_cap:
            return 1.0
        span = max(1, self.hard_cap - self.soft_cap)
        decay = 1.0 - (count - self.soft_cap) / span
        return 0.1 + 0.5 * decay

    def _current_energy(self) -> float:
        return self._compute_energy_at(time.monotonic())

    def _compute_energy_at(self, now: float) -> float:
        elapsed = max(0.0, now - self._energy_updated_at)
        recovered = self._energy + elapsed * self.energy_recovery_per_second
        return max(self.energy_floor, min(1.0, recovered))

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._emissions and self._emissions[0] < cutoff:
            self._emissions.popleft()
