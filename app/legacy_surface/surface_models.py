from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LegacySurfaceDecision:
    action_name: str
    reason: str
    explicit_intent: str = ""
    enabled: bool = False

    @property
    def can_delegate(self) -> bool:
        return self.enabled and self.explicit_intent == "desktop_automation"
