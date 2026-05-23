from __future__ import annotations

import random
import unittest

from app.companion.foreground_window import ForegroundWindow
from app.companion.game_event_detector import GameMatcher, WhitelistConfig, detect_game
from app.companion.observer import CompanionObserver, QuipEvent
from app.companion.passive_screen_watcher import PassiveScreenWatcher
from app.companion.quip_pool import QuipCategory


def _window(process: str, title: str) -> ForegroundWindow:
    return ForegroundWindow(process_name=process, window_title=title, exe_path=process, process_id=1)


class GameMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matchers = (
            GameMatcher(name="原神", process_names=("genshinimpact.exe",), title_keywords=("原神",)),
            GameMatcher(name="Elden Ring", process_names=("eldenring.exe",), title_keywords=("ELDEN RING",)),
        )

    def test_detects_by_process(self) -> None:
        win = _window("genshinimpact.exe", "")
        self.assertEqual(detect_game(win, self.matchers), "原神")

    def test_detects_by_title(self) -> None:
        win = _window("game.exe", "ELDEN RING")
        self.assertEqual(detect_game(win, self.matchers), "Elden Ring")

    def test_no_match_returns_none(self) -> None:
        win = _window("notepad.exe", "Untitled - Notepad")
        self.assertIsNone(detect_game(win, self.matchers))

    def test_none_window_returns_none(self) -> None:
        self.assertIsNone(detect_game(None, self.matchers))


class ScreenWatcherTransitionTests(unittest.TestCase):
    def _watcher(self, probes: list[ForegroundWindow | None]) -> tuple[PassiveScreenWatcher, list[QuipEvent]]:
        events: list[QuipEvent] = []
        observer = CompanionObserver(emit_probability=1.0, cooldown_seconds=0.0, rng=random.Random(0))
        observer.subscribe(events.append)
        whitelist = WhitelistConfig(
            matchers=(
                GameMatcher(name="原神", process_names=("genshinimpact.exe",), title_keywords=("原神",)),
                GameMatcher(name="Elden Ring", process_names=("eldenring.exe",), title_keywords=("ELDEN RING",)),
            ),
            poll_interval_seconds=5.0,
        )
        probe_iter = iter(probes)

        def probe() -> ForegroundWindow | None:
            return next(probe_iter)

        watcher = PassiveScreenWatcher(observer=observer, whitelist=whitelist, window_probe=probe)
        return watcher, events

    def test_enter_emits_game_enter(self) -> None:
        watcher, events = self._watcher([None, _window("genshinimpact.exe", "原神")])
        watcher.poll_once()
        watcher.poll_once()
        categories = [event.category for event in events]
        self.assertIn(QuipCategory.GAME_ENTER, categories)

    def test_exit_emits_game_exit(self) -> None:
        watcher, events = self._watcher(
            [
                _window("genshinimpact.exe", "原神"),
                _window("notepad.exe", "Untitled - Notepad"),
            ]
        )
        watcher.poll_once()
        watcher.poll_once()
        categories = [event.category for event in events]
        self.assertIn(QuipCategory.GAME_EXIT, categories)

    def test_switch_emits_game_enter_again(self) -> None:
        watcher, events = self._watcher(
            [
                _window("genshinimpact.exe", "原神"),
                _window("eldenring.exe", "ELDEN RING"),
            ]
        )
        watcher.poll_once()
        events.clear()
        watcher.poll_once()
        categories = [event.category for event in events]
        self.assertIn(QuipCategory.GAME_ENTER, categories)


if __name__ == "__main__":
    unittest.main()
