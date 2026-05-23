from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.companion.scene import HOMECOMING_GAP_SECONDS


logger = logging.getLogger(__name__)


DEFAULT_PERSISTENT_PATH = Path("data/companion_memory.json")


@dataclass(slots=True)
class PersistentMemoryState:
    games_played: dict[str, int] = field(default_factory=dict)
    last_session_ended_at: float = 0.0
    last_known_game: str | None = None
    consecutive_victories: int = 0
    consecutive_defeats: int = 0
    total_quips_emitted: int = 0


class PersistentMemory:
    def __init__(self, *, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_PERSISTENT_PATH
        self._lock = threading.Lock()
        self._state = PersistentMemoryState()
        self._loaded = False

    def load(self) -> PersistentMemoryState:
        with self._lock:
            if self._loaded:
                return self._snapshot_locked()
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    self._state = PersistentMemoryState(
                        games_played={str(k): int(v) for k, v in (raw.get("games_played") or {}).items()},
                        last_session_ended_at=float(raw.get("last_session_ended_at") or 0.0),
                        last_known_game=raw.get("last_known_game"),
                        consecutive_victories=int(raw.get("consecutive_victories") or 0),
                        consecutive_defeats=int(raw.get("consecutive_defeats") or 0),
                        total_quips_emitted=int(raw.get("total_quips_emitted") or 0),
                    )
                except Exception:
                    logger.exception("persistent_memory_load_failed path=%s", self._path)
            self._loaded = True
            return self._snapshot_locked()

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(self._state)
            try:
                self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                logger.exception("persistent_memory_save_failed path=%s", self._path)

    def note_session_end(self) -> None:
        with self._lock:
            self._state.last_session_ended_at = time.time()
        self.save()

    def record_game_played(self, game_name: str) -> int:
        if not game_name:
            return 0
        with self._lock:
            count = int(self._state.games_played.get(game_name, 0)) + 1
            self._state.games_played[game_name] = count
            self._state.last_known_game = game_name
            return count

    def record_victory(self) -> int:
        with self._lock:
            self._state.consecutive_defeats = 0
            self._state.consecutive_victories += 1
            return self._state.consecutive_victories

    def record_defeat(self) -> int:
        with self._lock:
            self._state.consecutive_victories = 0
            self._state.consecutive_defeats += 1
            return self._state.consecutive_defeats

    def record_quip(self) -> None:
        with self._lock:
            self._state.total_quips_emitted += 1

    def games_played(self, game_name: str) -> int:
        with self._lock:
            return int(self._state.games_played.get(game_name, 0))

    def homecoming_gap(self) -> float:
        with self._lock:
            last = self._state.last_session_ended_at
        if last <= 0:
            return 0.0
        return max(0.0, time.time() - last)

    def should_emit_homecoming(self) -> tuple[bool, float]:
        gap = self.homecoming_gap()
        return (gap >= HOMECOMING_GAP_SECONDS, gap)

    def snapshot(self) -> PersistentMemoryState:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> PersistentMemoryState:
        return PersistentMemoryState(
            games_played=dict(self._state.games_played),
            last_session_ended_at=self._state.last_session_ended_at,
            last_known_game=self._state.last_known_game,
            consecutive_victories=self._state.consecutive_victories,
            consecutive_defeats=self._state.consecutive_defeats,
            total_quips_emitted=self._state.total_quips_emitted,
        )


_singleton: PersistentMemory | None = None
_singleton_lock = threading.Lock()


def get_persistent_memory() -> PersistentMemory:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PersistentMemory()
        return _singleton
