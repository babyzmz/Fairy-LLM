from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.tool_result import ToolResult


@dataclass(slots=True)
class SkillRoute:
    chosen_skill: str
    reason: str
    allowed_tools: list[str]


@dataclass(slots=True)
class SkillResult:
    skill_name: str
    success: bool
    summary: str
    structured: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    response_text: str = ""
    changed_files: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
    tool_lock: bool = False  # NEW: Prevents LLM rewriting of factual tool outputs
