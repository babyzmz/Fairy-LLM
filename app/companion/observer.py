from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from app.companion.density_governor import DensityGovernor
from app.companion.quip_pool import QuipCategory, classify_text, pick_quip
from app.companion.scene import GAME_SCENES, Scene
from app.companion.scene_quips import pick_scene_quip, pick_transition_quip
from app.companion.scene_state_machine import (
    SceneStateMachine,
    SceneTransition,
    get_scene_state_machine,
)


logger = logging.getLogger(__name__)


SCENE_TO_CATEGORY: dict[Scene, QuipCategory] = {
    Scene.IDLE: QuipCategory.GENERAL,
    Scene.GAME_WARMING: QuipCategory.GAME_ENTER,
    Scene.GAME_ACTIVE: QuipCategory.GENERAL,
    Scene.COMBAT: QuipCategory.LOW_HP,
    Scene.BOSS: QuipCategory.BOSS,
    Scene.VICTORY_AFTERGLOW: QuipCategory.VICTORY,
    Scene.DEFEAT_REGROUP: QuipCategory.DEFEAT,
    Scene.AFK: QuipCategory.GENERAL,
    Scene.HOMECOMING: QuipCategory.GENERAL,
}


@dataclass(slots=True)
class QuipEvent:
    text: str
    category: QuipCategory
    emitted_at: float
    source: str
    scene: Scene = Scene.IDLE
    repetition: bool = False


QuipSubscriber = Callable[[QuipEvent], None]
RepetitionAdvisor = Callable[[Scene, str | None], bool]


class CompanionObserver:
    def __init__(
        self,
        *,
        emit_probability: float = 0.2,
        cooldown_seconds: float = 8.0,
        rng: random.Random | None = None,
        governor: DensityGovernor | None = None,
        scene_state: SceneStateMachine | None = None,
        repetition_advisor: RepetitionAdvisor | None = None,
    ) -> None:
        self._emit_probability = emit_probability
        self._cooldown_seconds = cooldown_seconds
        self._rng = rng or random.Random()
        self._last_emit_at: float = 0.0
        self._lock = threading.Lock()
        self._subscribers: list[QuipSubscriber] = []
        self._muted = False
        self._governor = governor or DensityGovernor()
        self._scene_state = scene_state or get_scene_state_machine()
        self._repetition_advisor = repetition_advisor

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

    @property
    def scene(self) -> Scene:
        return self._scene_state.scene

    def set_repetition_advisor(self, advisor: RepetitionAdvisor | None) -> None:
        self._repetition_advisor = advisor

    def observe_assistant_text(self, text: str, *, source: str = "assistant") -> QuipEvent | None:
        transition = self._scene_state.observe_assistant_text(text)
        if transition is not None:
            return self._emit_for_transition(transition, source=f"{source}/scene")
        if not text:
            return None
        category = classify_text(text) or QuipCategory.GENERAL
        return self._maybe_emit_scene(source=source, fallback_category=category)

    def observe_game_enter(self, game_name: str, *, source: str = "screen_watcher") -> QuipEvent | None:
        transition = self._scene_state.observe_game_enter(game_name)
        if transition is None:
            return None
        return self._emit_for_transition(transition, source=source, force=True)

    def observe_game_exit(self, *, source: str = "screen_watcher") -> QuipEvent | None:
        transition = self._scene_state.observe_game_exit()
        if transition is None:
            return None
        return self._emit_for_transition(transition, source=source, force=True)

    def observe_homecoming(self, *, gap_seconds: float, source: str = "startup") -> QuipEvent | None:
        transition = self._scene_state.observe_homecoming(gap_seconds=gap_seconds)
        if transition is None:
            return None
        return self._emit_for_transition(transition, source=source, force=True)

    def observe_game_event(self, category: QuipCategory, *, source: str = "screen_watcher") -> QuipEvent | None:
        if category == QuipCategory.GAME_ENTER:
            return self.observe_game_enter(game_name="unspecified", source=source)
        if category == QuipCategory.GAME_EXIT:
            return self.observe_game_exit(source=source)
        return self._maybe_emit_category(category, source=source, force=True)

    def tick(self) -> QuipEvent | None:
        transition = self._scene_state.tick()
        if transition is None:
            return None
        if transition.current == Scene.AFK:
            return self._emit_for_transition(transition, source="tick/afk")
        return self._emit_for_transition(transition, source="tick")

    def force_emit(self, category: QuipCategory, *, source: str = "manual") -> QuipEvent:
        event = self._build_event_for_category(category, source=source)
        self._dispatch(event)
        return event

    def _emit_for_transition(
        self,
        transition: SceneTransition,
        *,
        source: str,
        force: bool = False,
    ) -> QuipEvent | None:
        category = SCENE_TO_CATEGORY.get(transition.current, QuipCategory.GENERAL)
        repetition = self._consult_repetition(transition.current)
        return self._maybe_emit_scene(
            source=source,
            fallback_category=category,
            scene_override=transition.current,
            repetition=repetition,
            force=force,
        )

    def _consult_repetition(self, scene: Scene) -> bool:
        if self._repetition_advisor is None:
            return False
        try:
            return bool(self._repetition_advisor(scene, self._scene_state.current_game))
        except Exception:
            logger.exception("repetition_advisor_failed scene=%s", scene)
            return False

    def _maybe_emit_scene(
        self,
        *,
        source: str,
        fallback_category: QuipCategory,
        scene_override: Scene | None = None,
        repetition: bool = False,
        force: bool = False,
    ) -> QuipEvent | None:
        scene = scene_override or self._scene_state.scene
        if not self._reserve_slot(force=force):
            return None
        text = pick_scene_quip(scene, use_repetition=repetition, rng=self._rng)
        event = QuipEvent(
            text=text,
            category=fallback_category,
            emitted_at=time.time(),
            source=source,
            scene=scene,
            repetition=repetition,
        )
        self._dispatch(event)
        return event

    def _maybe_emit_category(self, category: QuipCategory, *, source: str, force: bool) -> QuipEvent | None:
        if not self._reserve_slot(force=force):
            return None
        event = self._build_event_for_category(category, source=source)
        self._dispatch(event)
        return event

    def _reserve_slot(self, *, force: bool) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._muted:
                return False
            if not force:
                gated_probability = self._governor.gate(self._emit_probability)
                if gated_probability <= 0.0:
                    return False
                if self._rng.random() > gated_probability:
                    return False
            if now - self._last_emit_at < self._cooldown_seconds:
                return False
            self._last_emit_at = now
            self._governor.record_emission(at=now)
            return True

    def _build_event_for_category(self, category: QuipCategory, *, source: str) -> QuipEvent:
        text = pick_quip(category, rng=self._rng)
        return QuipEvent(
            text=text,
            category=category,
            emitted_at=time.time(),
            source=source,
            scene=self._scene_state.scene,
        )

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
