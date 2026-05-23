from __future__ import annotations

import random
import time
import unittest

from app.companion.observer import CompanionObserver, QuipEvent
from app.companion.quip_pool import QuipCategory, classify_text


class QuipClassifyTests(unittest.TestCase):
    def test_low_hp_keywords(self) -> None:
        self.assertEqual(classify_text("血量危险了，赶紧回防"), QuipCategory.LOW_HP)

    def test_boss_keywords(self) -> None:
        self.assertEqual(classify_text("BOSS 出现"), QuipCategory.BOSS)

    def test_strategy_keywords(self) -> None:
        self.assertEqual(classify_text("这是一个 build 配装攻略"), QuipCategory.STRATEGY)

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(classify_text("今天天气真好"))


class ObserverTests(unittest.TestCase):
    def _build(self, *, probability: float = 1.0, cooldown: float = 0.0) -> CompanionObserver:
        return CompanionObserver(emit_probability=probability, cooldown_seconds=cooldown, rng=random.Random(7))

    def test_subscribers_receive_quip(self) -> None:
        observer = self._build()
        events: list[QuipEvent] = []
        observer.subscribe(events.append)
        emitted = observer.observe_assistant_text("BOSS 来了")
        self.assertIsNotNone(emitted)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, QuipCategory.BOSS)

    def test_cooldown_blocks_second_emit(self) -> None:
        observer = self._build(cooldown=10.0)
        observer.observe_assistant_text("BOSS 来了")
        second = observer.observe_assistant_text("再来一波 BOSS")
        self.assertIsNone(second)

    def test_muted_blocks_emit(self) -> None:
        observer = self._build()
        observer.set_muted(True)
        self.assertIsNone(observer.observe_assistant_text("BOSS"))

    def test_game_event_force_emits(self) -> None:
        observer = self._build(probability=0.0, cooldown=0.0)
        event = observer.observe_game_event(QuipCategory.GAME_ENTER)
        self.assertIsNotNone(event)
        self.assertEqual(event.category, QuipCategory.GAME_ENTER)

    def test_unsubscribe_stops_dispatch(self) -> None:
        observer = self._build()
        received: list[QuipEvent] = []
        unsubscribe = observer.subscribe(received.append)
        observer.observe_assistant_text("BOSS")
        unsubscribe()
        observer.observe_assistant_text("BOSS")
        self.assertEqual(len(received), 1)

    def test_probability_zero_skips(self) -> None:
        observer = self._build(probability=0.0)
        self.assertIsNone(observer.observe_assistant_text("BOSS"))


if __name__ == "__main__":
    unittest.main()
