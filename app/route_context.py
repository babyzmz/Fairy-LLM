from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RouteContext:
    previous_skill: str = ""
    previous_structured: dict[str, Any] = field(default_factory=dict)
    screen_followup_remaining: int = 0
    session_id: str = ""
    perception_intent: str = ""
    forced_bundle: str = ""
    web_task_type: str = ""
    web_intent_plan: dict[str, Any] = field(default_factory=dict)
    web_context: dict[str, Any] = field(default_factory=dict)
    preferred_routes: list[str] = field(default_factory=list)
    preferred_modalities: list[str] = field(default_factory=list)
    active_focus: dict[str, str] = field(default_factory=dict)
