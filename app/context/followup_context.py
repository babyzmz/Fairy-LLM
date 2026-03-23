from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FollowUpContext:
    target: str = ""
    reused: bool = False
    reason: str = ""
    focus_value: str = ""
