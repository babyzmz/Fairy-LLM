from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ExecutionLayout = Literal["single", "vertical_list", "grid", "masonry"]


@dataclass(slots=True)
class ExecutionStep:
    name: str
    description: str
    tool_name: str = ""
    blocking: bool = False
    emits_progress: bool = True
    allows_streaming: bool = False


@dataclass(slots=True)
class ExecutionPlan:
    intent: str
    capability: str
    response_mode: str
    speech_mode: str
    layout_mode: ExecutionLayout
    card_types: list[str] = field(default_factory=list)
    steps: list[ExecutionStep] = field(default_factory=list)
    use_tools: bool = False
    allow_streaming: bool = False
    fallback_rules: list[str] = field(default_factory=list)
