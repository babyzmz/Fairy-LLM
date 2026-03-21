from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SkillSpec:
    name: str
    description: str
    trigger_hints: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    execution_steps: tuple[str, ...]
    output_schema: tuple[str, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
