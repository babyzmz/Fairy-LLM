from __future__ import annotations

import logging

from app.companion.memory_advisor import RepetitionAdvisor, get_repetition_advisor
from app.companion.observer import get_companion_observer
from app.companion.passive_screen_watcher import get_passive_screen_watcher
from app.companion.persistent_memory import get_persistent_memory
from app.companion.scene_state_machine import SceneTransition, get_scene_state_machine


logger = logging.getLogger(__name__)


_advisor_unsubscribe: callable | None = None


def start_companion() -> None:
    global _advisor_unsubscribe
    observer = get_companion_observer()
    scene_state = get_scene_state_machine()
    advisor = get_repetition_advisor()
    persistent = get_persistent_memory()
    persistent.load()

    observer.set_repetition_advisor(advisor)
    if _advisor_unsubscribe is None:
        _advisor_unsubscribe = scene_state.subscribe(_on_scene_transition)

    should_home, gap = persistent.should_emit_homecoming()
    if should_home:
        try:
            observer.observe_homecoming(gap_seconds=gap)
        except Exception:
            logger.exception("companion_homecoming_emit_failed")

    get_passive_screen_watcher().start()
    logger.info("companion_started homecoming=%s gap_seconds=%.0f", should_home, gap)


def stop_companion() -> None:
    global _advisor_unsubscribe
    try:
        get_passive_screen_watcher().stop()
    except Exception:
        logger.exception("companion_watcher_stop_failed")
    try:
        get_persistent_memory().note_session_end()
    except Exception:
        logger.exception("companion_persistent_memory_save_failed")
    if _advisor_unsubscribe is not None:
        try:
            _advisor_unsubscribe()
        except Exception:
            logger.exception("companion_advisor_unsubscribe_failed")
        _advisor_unsubscribe = None


def _on_scene_transition(transition: SceneTransition) -> None:
    advisor = get_repetition_advisor()
    try:
        advisor.record_scene_entry(transition.current, get_scene_state_machine().current_game)
    except Exception:
        logger.exception("companion_advisor_record_failed scene=%s", transition.current)
