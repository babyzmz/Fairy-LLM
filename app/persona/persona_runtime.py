from __future__ import annotations

from app.app_preferences import AppPreferences


PERSONA_MODE_OFF = "off"
PERSONA_MODE_LIGHTWEIGHT = "lightweight"
PERSONA_MODE_FULL = "full"
VALID_PERSONA_MODES = {PERSONA_MODE_OFF, PERSONA_MODE_LIGHTWEIGHT, PERSONA_MODE_FULL}


def get_effective_persona_mode(preferences: AppPreferences | None, active_mode: str) -> str:
    prefs = preferences or AppPreferences()
    if not prefs.persona_enabled:
        return PERSONA_MODE_OFF
    if active_mode == "game_mode" and prefs.game_mode_force_disable_persona:
        return PERSONA_MODE_OFF
    mode = prefs.persona_mode if prefs.persona_mode in VALID_PERSONA_MODES else PERSONA_MODE_FULL
    if active_mode == "game_mode" and mode == PERSONA_MODE_FULL and not prefs.game_mode_force_disable_persona:
        return PERSONA_MODE_LIGHTWEIGHT
    return mode


def is_persona_enabled(preferences: AppPreferences | None, active_mode: str) -> bool:
    return get_effective_persona_mode(preferences, active_mode) != PERSONA_MODE_OFF


def is_full_persona_enabled(preferences: AppPreferences | None, active_mode: str) -> bool:
    return get_effective_persona_mode(preferences, active_mode) == PERSONA_MODE_FULL
