from __future__ import annotations

import json
from dataclasses import dataclass, field

from fairy_core.assistant.tools import (
    ToolCandidateError,
    sanitize_model_arguments,
    sanitize_public_intent,
    validate_tool_arguments,
)
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.mcp.schema import contains_reserved_arguments
from fairy_core.providers import ModelDelta

_MAX_TOOL_ARGUMENT_CHARACTERS = 64_000


@dataclass(slots=True)
class ToolCandidate:
    call_id: str
    name: str | None = None
    argument_fragments: list[str] = field(default_factory=list)
    argument_characters: int = 0

    def append(self, delta: ModelDelta) -> None:
        if delta.tool_name is not None:
            if self.name is not None and self.name != delta.tool_name:
                raise ToolCandidateError("tool candidate changed its name")
            self.name = delta.tool_name
        fragment = delta.tool_arguments_fragment or ""
        self.argument_characters += len(fragment)
        if self.argument_characters > _MAX_TOOL_ARGUMENT_CHARACTERS:
            raise ToolCandidateError("tool candidate arguments are too large")
        self.argument_fragments.append(fragment)

    def arguments(self) -> dict[str, object]:
        raw = self.raw_arguments()
        raw.pop("public_intent", None)
        return sanitize_model_arguments(raw)

    def public_intent(self) -> str | None:
        value = self.raw_arguments().get("public_intent")
        return sanitize_public_intent(value)

    def raw_arguments(self) -> dict[str, object]:
        if self.name is None:
            raise ToolCandidateError("tool candidate has no name")
        try:
            value = json.loads("".join(self.argument_fragments) or "{}")
        except json.JSONDecodeError as error:
            raise ToolCandidateError("tool candidate arguments are invalid JSON") from error
        if not isinstance(value, dict):
            raise ToolCandidateError("tool candidate arguments must be an object")
        return value


def arguments_for_definition(
    candidate: ToolCandidate,
    definition: ToolDefinition,
) -> dict[str, object]:
    if definition.source == "mcp" and contains_reserved_arguments(candidate.raw_arguments()):
        raise ToolCandidateError("MCP arguments contain Core-reserved identity or transport fields")
    arguments = candidate.arguments()
    validate_tool_arguments(definition, arguments)
    return arguments


__all__ = ["ToolCandidate", "arguments_for_definition"]
