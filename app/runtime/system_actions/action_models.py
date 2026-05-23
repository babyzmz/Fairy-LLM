from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SystemActionCategory = Literal["backend_action", "desktop_action", "desktop_automation_compatibility", "unknown"]


@dataclass(slots=True)
class SystemActionResolution:
    name: str
    category: SystemActionCategory
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
