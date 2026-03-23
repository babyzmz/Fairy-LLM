from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionContext:
    session_id: str
    active_city: str = ""
    active_weather_location: str = ""
    active_location_target: str = ""
    active_topic: str = ""
    last_capability: str = ""
    last_generic_query: str = ""
    last_explanation_topic: str = ""
    last_card_topics: list[str] = field(default_factory=list)
    last_clarification_target: str = ""
    last_clarification_message: str = ""
    last_structured_type: str = ""
    last_structured: dict[str, Any] = field(default_factory=dict)
    last_execution_plan: list[dict[str, Any]] = field(default_factory=list)
    last_step_capability: str = ""
    active_execution_plan_state: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    turn_window: list[str] = field(default_factory=list)

    def remember_turn(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if cleaned:
            self.turn_window.append(cleaned)
            self.turn_window = self.turn_window[-8:]
