from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.companion.foreground_window import ForegroundWindow, get_foreground_window
from app.companion.game_event_detector import WhitelistConfig, detect_game, load_whitelist
from app.companion.observer import CompanionObserver, get_companion_observer
from app.companion.quip_pool import QuipCategory


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScreenWatcherSnapshot:
    active_game: str | None
    last_window_title: str
    last_process_name: str
    last_polled_at: float


WindowProbe = Callable[[], ForegroundWindow | None]


class PassiveScreenWatcher:
    def __init__(
        self,
        *,
        observer: CompanionObserver | None = None,
        whitelist: WhitelistConfig | None = None,
        window_probe: WindowProbe | None = None,
    ) -> None:
        self._observer = observer or get_companion_observer()
        self._whitelist = whitelist or load_whitelist()
        self._probe = window_probe or get_foreground_window
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active_game: str | None = None
        self._last_window: ForegroundWindow | None = None
        self._last_polled_at: float = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="passive-screen-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def snapshot(self) -> ScreenWatcherSnapshot:
        with self._lock:
            window = self._last_window
            return ScreenWatcherSnapshot(
                active_game=self._active_game,
                last_window_title=window.window_title if window else "",
                last_process_name=window.process_name if window else "",
                last_polled_at=self._last_polled_at,
            )

    def poll_once(self) -> ScreenWatcherSnapshot:
        window = self._safe_probe()
        detected = detect_game(window, self._whitelist.matchers)
        with self._lock:
            previous = self._active_game
            self._last_window = window
            self._last_polled_at = time.time()
            self._active_game = detected
        if detected != previous:
            self._emit_transition(previous, detected)
        return self.snapshot()

    def _run(self) -> None:
        interval = self._whitelist.poll_interval_seconds
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("passive_screen_watcher_poll_failed")
            self._stop_event.wait(interval)

    def _safe_probe(self) -> ForegroundWindow | None:
        try:
            return self._probe()
        except Exception:
            logger.exception("passive_screen_watcher_probe_failed")
            return None

    def _emit_transition(self, previous: str | None, current: str | None) -> None:
        if previous is None and current is not None:
            self._observer.observe_game_event(QuipCategory.GAME_ENTER, source="screen_watcher")
            logger.info("passive_screen_watcher_game_enter game=%s", current)
            return
        if previous is not None and current is None:
            self._observer.observe_game_event(QuipCategory.GAME_EXIT, source="screen_watcher")
            logger.info("passive_screen_watcher_game_exit previous=%s", previous)
            return
        if previous is not None and current is not None and previous != current:
            self._observer.observe_game_event(QuipCategory.GAME_ENTER, source="screen_watcher")
            logger.info("passive_screen_watcher_game_switch previous=%s current=%s", previous, current)


_singleton: PassiveScreenWatcher | None = None
_singleton_lock = threading.Lock()


def get_passive_screen_watcher() -> PassiveScreenWatcher:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PassiveScreenWatcher()
        return _singleton
