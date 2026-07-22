from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fairy_core.persona import (
    AmbientContextSnapshot,
    AmbientDialoguePreferences,
    AmbientDialogueState,
    AmbientSurface,
    FairyDialogueDirector,
    load_default_dialogue_catalog,
    load_default_persona_authority,
)

NOW = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)


def _director() -> FairyDialogueDirector:
    return FairyDialogueDirector(
        catalog=load_default_dialogue_catalog(),
        persona=load_default_persona_authority(),
    )


def _context(**overrides: object) -> AmbientContextSnapshot:
    values: dict[str, object] = {
        "observed_at": NOW,
        "locale": "zh-CN",
        "surface": AmbientSurface.PET,
        "user_idle_seconds": 0,
        "startup_eligible": False,
        "user_returned": False,
        "network_restored": False,
        "battery_percent": None,
        "charging": None,
        "charging_started": False,
        "locked": False,
        "do_not_disturb": False,
        "typing": False,
        "input_open": False,
        "microphone_active": False,
        "fullscreen": False,
        "realtime_active": False,
        "active_turn": False,
        "approval_waiting": False,
        "severe_error": False,
        "tts_active": False,
    }
    values.update(overrides)
    return AmbientContextSnapshot(**values)


def test_startup_greeting_is_emitted_once_per_local_day() -> None:
    director = _director()
    preferences = AmbientDialoguePreferences()
    state = AmbientDialogueState.empty()

    first = director.evaluate(_context(startup_eligible=True), preferences, state)
    assert first.projection is not None
    assert first.projection.dialogue_id == "startup.daily.01"

    second = director.evaluate(
        _context(startup_eligible=True),
        preferences,
        first.next_state,
    )
    assert second.projection is None
    assert second.reason == "startup_already_presented"


def test_idle_threshold_and_global_cooldown_are_enforced() -> None:
    director = _director()
    preferences = AmbientDialoguePreferences()

    too_early = director.evaluate(
        _context(user_idle_seconds=239),
        preferences,
        AmbientDialogueState.empty(),
    )
    assert too_early.projection is None

    first = director.evaluate(
        _context(user_idle_seconds=240),
        preferences,
        AmbientDialogueState.empty(),
    )
    assert first.projection is not None

    cooling_down = director.evaluate(
        _context(observed_at=NOW + timedelta(minutes=11), user_idle_seconds=900),
        preferences,
        first.next_state,
    )
    assert cooling_down.projection is None
    assert cooling_down.reason == "global_cooldown"


def test_every_hard_suppression_blocks_dialogue() -> None:
    director = _director()
    preferences = AmbientDialoguePreferences()
    blockers = (
        "locked",
        "do_not_disturb",
        "typing",
        "input_open",
        "microphone_active",
        "fullscreen",
        "realtime_active",
        "active_turn",
        "approval_waiting",
        "severe_error",
        "tts_active",
    )

    for blocker in blockers:
        decision = director.evaluate(
            _context(user_idle_seconds=900, **{blocker: True}),
            preferences,
            AmbientDialogueState.empty(),
        )
        assert decision.projection is None
        assert decision.reason == f"suppressed:{blocker}"


def test_generated_dialogue_is_opt_in_and_capped() -> None:
    director = _director()
    state = AmbientDialogueState.empty()

    authored = director.evaluate(
        _context(user_idle_seconds=900),
        AmbientDialoguePreferences(generated_enabled=False),
        state,
    )
    assert authored.generation_request is None

    generated = director.evaluate(
        _context(user_idle_seconds=900),
        AmbientDialoguePreferences(generated_enabled=True),
        state,
    )
    assert generated.generation_request is not None
    assert generated.generation_request.max_output_tokens == 160
    assert generated.generation_request.tools == ()
    assert generated.generation_request.history == ()


def test_return_and_self_commentary_triggers_are_reachable() -> None:
    director = _director()
    preferences = AmbientDialoguePreferences()

    returned = director.evaluate(
        _context(user_idle_seconds=15, user_returned=True),
        preferences,
        AmbientDialogueState.empty(),
    )
    assert returned.projection is not None
    assert returned.projection.trigger.value == "user_returned"

    commentary = director.evaluate(
        _context(user_idle_seconds=240),
        preferences,
        AmbientDialogueState(local_date="2026-07-23", daily_text_count=3),
    )
    assert commentary.projection is not None
    assert commentary.projection.trigger.value == "self_commentary"
