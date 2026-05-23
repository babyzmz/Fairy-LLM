from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from app.companion.scene import (
    AFK_DEEP_THRESHOLD_SECONDS,
    AFK_LONG_THRESHOLD_SECONDS,
    AFK_THRESHOLD_SECONDS,
    DEFEAT_REGROUP_SECONDS,
    GAME_SCENES,
    GAME_WARMING_SECONDS,
    Scene,
    VICTORY_AFTERGLOW_SECONDS,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SceneEvent:
    kind: str
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SceneTransition:
    previous: Scene
    current: Scene
    reason: str
    at: float


SceneListener = Callable[[SceneTransition], None]


COMBAT_KEYWORDS = ("低血量", "残血", "回防", "low hp", "low health", "撤")
BOSS_KEYWORDS = ("boss", "BOSS", "首领", "头目", "精英怪")
VICTORY_KEYWORDS = ("通关", "胜利", "victory", "win", "击败")
DEFEAT_KEYWORDS = ("失败", "败北", "团灭", "defeat", "game over", "you died", "你死了")


def _text_hits(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


class SceneStateMachine:
    def __init__(
        self,
        *,
        afk_seconds: float = AFK_THRESHOLD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._scene = Scene.IDLE
        self._afk_seconds = afk_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners: list[SceneListener] = []
        self._last_event_at = clock()
        self._scene_entered_at = clock()
        self._current_game: str | None = None
        self._scene_expires_at: float | None = None
        self._afk_entered_at: float | None = None

    def afk_depth_seconds(self) -> float:
        with self._lock:
            if self._scene != Scene.AFK or self._afk_entered_at is None:
                return 0.0
            return max(0.0, self._clock() - self._afk_entered_at)

    def afk_tier(self) -> str:
        if self._scene != Scene.AFK:
            return "none"
        depth = self.afk_depth_seconds()
        if depth >= AFK_LONG_THRESHOLD_SECONDS:
            return "long"
        if depth >= AFK_DEEP_THRESHOLD_SECONDS:
            return "deep"
        return "shallow"

    @property
    def scene(self) -> Scene:
        return self._scene

    @property
    def current_game(self) -> str | None:
        return self._current_game

    def subscribe(self, listener: SceneListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def tick(self) -> SceneTransition | None:
        now = self._clock()
        target: Scene | None = None
        reason = ""
        with self._lock:
            if self._scene_expires_at is not None and now >= self._scene_expires_at:
                if self._scene == Scene.GAME_WARMING:
                    target = Scene.GAME_ACTIVE
                    reason = "warming_elapsed"
                elif self._scene in (Scene.VICTORY_AFTERGLOW, Scene.DEFEAT_REGROUP):
                    target = Scene.GAME_ACTIVE if self._current_game else Scene.IDLE
                    reason = "post_combat_settled"
                self._scene_expires_at = None
            elif self._scene not in (Scene.AFK, Scene.HOMECOMING):
                if now - self._last_event_at > self._afk_seconds:
                    target = Scene.AFK
                    reason = "idle_timeout"
        if target is None:
            return None
        return self._transition_to(target, reason=reason, payload={})

    def observe_assistant_text(self, text: str) -> SceneTransition | None:
        if not text:
            return None
        with self._lock:
            self._last_event_at = self._clock()
        if _text_hits(text, BOSS_KEYWORDS):
            return self._transition_to(Scene.BOSS, reason="assistant_boss_keyword", payload={"text_preview": text[:40]})
        if _text_hits(text, COMBAT_KEYWORDS):
            return self._transition_to(Scene.COMBAT, reason="assistant_combat_keyword", payload={"text_preview": text[:40]})
        if _text_hits(text, VICTORY_KEYWORDS):
            return self._enter_victory(reason="assistant_victory_keyword")
        if _text_hits(text, DEFEAT_KEYWORDS):
            return self._enter_defeat(reason="assistant_defeat_keyword")
        return self._wake_from_afk(reason="assistant_text")

    def observe_game_enter(self, game_name: str) -> SceneTransition | None:
        with self._lock:
            previous_game = self._current_game
            self._current_game = game_name
            self._last_event_at = self._clock()
        return self._transition_to(
            Scene.GAME_WARMING,
            reason="game_enter" if previous_game is None else "game_switch",
            payload={"game": game_name, "previous_game": previous_game},
            duration=GAME_WARMING_SECONDS,
            force=True,
        )

    def observe_game_exit(self) -> SceneTransition | None:
        with self._lock:
            previous_game = self._current_game
            self._current_game = None
            self._last_event_at = self._clock()
        return self._transition_to(
            Scene.IDLE,
            reason="game_exit",
            payload={"previous_game": previous_game},
            force=True,
        )

    def observe_homecoming(self, *, gap_seconds: float) -> SceneTransition | None:
        return self._transition_to(
            Scene.HOMECOMING,
            reason="long_gap_return",
            payload={"gap_seconds": gap_seconds},
            duration=20.0,
            force=True,
        )

    def _enter_victory(self, *, reason: str) -> SceneTransition | None:
        return self._transition_to(
            Scene.VICTORY_AFTERGLOW,
            reason=reason,
            payload={},
            duration=VICTORY_AFTERGLOW_SECONDS,
        )

    def _enter_defeat(self, *, reason: str) -> SceneTransition | None:
        return self._transition_to(
            Scene.DEFEAT_REGROUP,
            reason=reason,
            payload={},
            duration=DEFEAT_REGROUP_SECONDS,
        )

    def _wake_from_afk(self, *, reason: str) -> SceneTransition | None:
        with self._lock:
            if self._scene != Scene.AFK:
                return None
            target = Scene.GAME_ACTIVE if self._current_game else Scene.IDLE
        return self._transition_to(target, reason=reason, payload={})

    def _transition_to(
        self,
        target: Scene,
        *,
        reason: str,
        payload: dict[str, object],
        duration: float | None = None,
        force: bool = False,
    ) -> SceneTransition | None:
        now = self._clock()
        with self._lock:
            if target == self._scene and not force:
                return None
            previous = self._scene
            self._scene = target
            self._scene_entered_at = now
            self._scene_expires_at = now + duration if duration else None
            self._afk_entered_at = now if target == Scene.AFK else None
            listeners = list(self._listeners)
        transition = SceneTransition(previous=previous, current=target, reason=reason, at=now)
        for listener in listeners:
            try:
                listener(transition)
            except Exception:
                logger.exception("scene_listener_failed reason=%s", reason)
        return transition

    def force_scene(self, target: Scene, *, reason: str = "forced") -> SceneTransition | None:
        return self._transition_to(target, reason=reason, payload={}, force=True)


_singleton: SceneStateMachine | None = None
_singleton_lock = threading.Lock()


def get_scene_state_machine() -> SceneStateMachine:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = SceneStateMachine()
        return _singleton
