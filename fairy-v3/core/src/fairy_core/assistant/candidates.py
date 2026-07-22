from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

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
    return _normalize_builtin_arguments(definition.name, arguments)


def deduplicate_tool_candidates(
    candidates: list[ToolCandidate],
    definitions: Mapping[str, ToolDefinition],
) -> tuple[tuple[ToolCandidate, ...], int]:
    unique: list[ToolCandidate] = []
    seen: set[tuple[str, str]] = set()
    suppressed = 0
    for candidate in candidates:
        definition = definitions.get(candidate.name or "")
        if definition is None:
            unique.append(candidate)
            continue
        try:
            arguments = arguments_for_definition(candidate, definition)
        except ToolCandidateError:
            unique.append(candidate)
            continue
        key = (
            definition.name,
            json.dumps(
                arguments,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        if key in seen:
            suppressed += 1
            continue
        seen.add(key)
        unique.append(candidate)
    return tuple(unique), suppressed


def _normalize_builtin_arguments(
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    if tool_name not in {"edit.propose_changeset", "execution.plan", "project.read"}:
        return arguments
    normalized = dict(arguments)
    if tool_name == "project.read":
        path = normalized.get("path")
        if isinstance(path, str):
            normalized["path"] = _relative_path(path)
        return normalized
    raw_files = normalized.get("files")
    if not isinstance(raw_files, list):
        return normalized
    files: list[object] = []
    seen_paths: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            files.append(raw_file)
            continue
        file = dict(raw_file)
        path = file.get("path")
        if isinstance(path, str):
            canonical = _relative_path(path)
            if canonical in seen_paths:
                raise ToolCandidateError(
                    f"tool candidate contains the same canonical path twice: {canonical}"
                )
            seen_paths.add(canonical)
            file["path"] = canonical
            content = file.get("content")
            if tool_name == "edit.propose_changeset" and isinstance(content, str):
                _reject_obvious_placeholder(canonical, content)
        files.append(file)
    normalized["files"] = files
    if tool_name == "execution.plan":
        commands = normalized.get("validation_commands")
        if isinstance(commands, list):
            normalized["validation_commands"] = [
                command
                for command in commands
                if not isinstance(command, str) or not _is_runtime_command(command)
            ]
    return normalized


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ToolCandidateError(f"tool path is outside the Workspace: {value}")
    canonical = path.as_posix()
    if canonical in {"", "."}:
        raise ToolCandidateError("tool path must identify a Workspace file")
    return canonical


def _reject_obvious_placeholder(path: str, content: str) -> None:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix in {".html", ".htm"}:
        body = re.search(r"<body(?:\s[^>]*)?>(.*?)</body\s*>", content, re.I | re.S)
        if body is not None and not _without_comments(body.group(1), html=True).strip():
            raise ToolCandidateError(
                f"generated HTML has an empty body: {path}; submit the complete planned file"
            )
        if body is None and not _without_comments(content, html=True).strip():
            raise ToolCandidateError(f"generated HTML is only comments or whitespace: {path}")
    if suffix in {".css", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"} and not (
        _without_comments(content, html=False).strip()
    ):
        raise ToolCandidateError(
            f"generated source is only comments or whitespace: {path}; submit complete content"
        )


def _without_comments(content: str, *, html: bool) -> str:
    if html:
        return re.sub(r"<!--.*?-->", "", content, flags=re.S)
    without_blocks = re.sub(r"/\*.*?\*/", "", content, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\r\n]*(?:\r?\n|$)", "", without_blocks)


def _is_runtime_command(command: str) -> bool:
    normalized = " ".join(command.casefold().split())
    patterns = (
        r"(?:^|\s)python(?:3)?(?:\.exe)?\s+-m\s+http\.server(?:\s|$)",
        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve)(?:\s|$)",
        r"(?:^|\s)(?:next\s+dev|vite|uvicorn|gunicorn|flask\s+run|nodemon)(?:\s|$)",
        r"(?:^|\s)--watch(?:\s|$)",
    )
    return any(re.search(pattern, normalized) is not None for pattern in patterns)


__all__ = [
    "ToolCandidate",
    "arguments_for_definition",
    "deduplicate_tool_candidates",
]
