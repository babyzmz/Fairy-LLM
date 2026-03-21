from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import BASE_DIR, voice_config


PREFERENCES_FILE = BASE_DIR / "config" / "app_preferences.json"


@dataclass(slots=True)
class AppPreferences:
    ui_language: str = "zh_CN"
    speak_responses: bool = False
    stream_responses: bool = False
    persona_enabled: bool = False
    persona_mode: str = "full"
    game_mode_force_disable_persona: bool = True
    presence_quiet_mode: bool = False
    presence_position_x: int | None = None
    presence_position_y: int | None = None
    presence_avatar_size: int = 80


def load_app_preferences() -> AppPreferences:
    if not PREFERENCES_FILE.exists():
        return AppPreferences()
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return AppPreferences()
    return AppPreferences(
        ui_language=str(data.get("ui_language", "zh_CN") or "zh_CN"),
        speak_responses=bool(data.get("speak_responses", False)),
        stream_responses=bool(data.get("stream_responses", False)),
        persona_enabled=bool(data.get("persona_enabled", False)),
        persona_mode=str(data.get("persona_mode", "full") or "full"),
        game_mode_force_disable_persona=bool(data.get("game_mode_force_disable_persona", True)),
        presence_quiet_mode=bool(data.get("presence_quiet_mode", False)),
        presence_position_x=_optional_int(data.get("presence_position_x")),
        presence_position_y=_optional_int(data.get("presence_position_y")),
        presence_avatar_size=max(64, min(112, int(data.get("presence_avatar_size", 80) or 80))),
    )


def save_app_preferences(preferences: AppPreferences) -> None:
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_FILE.write_text(
        json.dumps(asdict(preferences), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_app_preferences(preferences: AppPreferences) -> None:
    voice_config.speak_responses = preferences.speak_responses
    voice_config.stream_responses = preferences.stream_responses


def load_and_apply_app_preferences() -> AppPreferences:
    preferences = load_app_preferences()
    apply_app_preferences(preferences)
    return preferences


def _optional_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
