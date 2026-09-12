from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fairy_core.assistant.evidence import evidence_context
from fairy_core.assistant.models import ToolInvocation, ToolInvocationStatus
from fairy_core.assistant.tools import tool_message_content
from fairy_core.providers import ModelMessage, ModelRole, ModelToolCall

_MAX_TOOL_CONTEXT_CHARACTERS = 40_000
_MAX_SKILL_RESULT_CHARACTERS = 6_000
_MAX_READ_RESULT_CHARACTERS = 10_000
_MAX_OTHER_RESULT_CHARACTERS = 4_000
_MIN_RESULT_CHARACTERS = 512
_LARGE_ARGUMENT_KEYS = frozenset({"content", "data", "patch", "text"})


@dataclass(frozen=True, slots=True)
class ToolContextProjection:
    messages: tuple[ModelMessage, ...]
    source_characters: int
    projected_characters: int
    truncated_items: int


def durable_tool_context(
    invocations: tuple[ToolInvocation, ...],
) -> tuple[ModelMessage, ...]:
    terminal = tuple(
        invocation
        for invocation in invocations
        if invocation.status
        in {
            ToolInvocationStatus.COMPLETED,
            ToolInvocationStatus.FAILED,
            ToolInvocationStatus.REJECTED,
        }
    )
    messages: list[ModelMessage] = []
    rounds = sorted({invocation.model_round for invocation in terminal})
    for model_round in rounds:
        grouped = tuple(
            invocation for invocation in terminal if invocation.model_round == model_round
        )
        messages.append(
            ModelMessage.create(
                role=ModelRole.ASSISTANT,
                content="",
                tool_calls=tuple(
                    ModelToolCall.create(
                        tool_call_id=invocation.provider_call_id,
                        name=invocation.tool_name,
                        arguments=json.dumps(
                            invocation.arguments,
                            ensure_ascii=True,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                    for invocation in grouped
                ),
            )
        )
        for invocation in grouped:
            rejected = invocation.status is not ToolInvocationStatus.COMPLETED
            content = invocation.model_content or (
                f"Tool execution failed ({invocation.error_code or 'TOOL_REJECTED'})."
            )
            # Every new model node and restart reconstructs this projection.
            # Receipt identities are Core-owned facts, not optional model text.
            if invocation.evidence_receipts:
                content = evidence_context(invocation.evidence_receipts) + "\n" + content
            messages.append(
                ModelMessage.create(
                    role=ModelRole.TOOL,
                    content=tool_message_content(
                        tool_name=invocation.tool_name,
                        tool_call_id=invocation.provider_call_id,
                        content=content,
                        rejected=rejected,
                    ),
                    name=invocation.tool_name,
                    tool_call_id=invocation.provider_call_id,
                )
            )
    return project_tool_context(tuple(messages)).messages


def project_tool_context(
    messages: tuple[ModelMessage, ...],
    *,
    max_characters: int = _MAX_TOOL_CONTEXT_CHARACTERS,
) -> ToolContextProjection:
    """Build a bounded provider projection without mutating durable tool records."""
    if max_characters < 1:
        raise ValueError("tool context character budget must be positive")
    source_characters = sum(_message_characters(message) for message in messages)
    truncated_items = 0
    projected: list[ModelMessage] = []
    for message in messages:
        if message.role is ModelRole.ASSISTANT and message.tool_calls:
            calls: list[ModelToolCall] = []
            for call in message.tool_calls:
                arguments, truncated = _project_arguments(call.arguments)
                truncated_items += int(truncated)
                calls.append(
                    ModelToolCall.create(
                        tool_call_id=call.id,
                        name=call.name,
                        arguments=arguments,
                    )
                )
            projected.append(
                ModelMessage.create(
                    role=message.role,
                    content=message.content,
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                    tool_calls=tuple(calls),
                    images=message.images,
                )
            )
            continue
        if message.role is ModelRole.TOOL:
            limit = _tool_result_limit(message.name)
            content, truncated = _bounded_text(message.content, limit)
            truncated_items += int(truncated)
            projected.append(
                ModelMessage.create(
                    role=message.role,
                    content=content,
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                )
            )
            continue
        projected.append(message)

    while _messages_characters(projected) > max_characters:
        reduced = False
        for index, message in enumerate(projected):
            if message.role is not ModelRole.TOOL or len(message.content) <= _MIN_RESULT_CHARACTERS:
                continue
            content, _truncated = _bounded_text(message.content, _MIN_RESULT_CHARACTERS)
            projected[index] = ModelMessage.create(
                role=message.role,
                content=content,
                name=message.name,
                tool_call_id=message.tool_call_id,
            )
            truncated_items += 1
            reduced = True
            if _messages_characters(projected) <= max_characters:
                break
        if not reduced:
            break

    if _messages_characters(projected) > max_characters:
        for index, message in enumerate(projected):
            if message.role is not ModelRole.ASSISTANT or not message.tool_calls:
                continue
            calls: list[ModelToolCall] = []
            changed = False
            for call in message.tool_calls:
                if len(call.arguments) <= 256:
                    calls.append(call)
                    continue
                calls.append(
                    ModelToolCall.create(
                        tool_call_id=call.id,
                        name=call.name,
                        arguments=_json({"context_projection": _projection_marker(call.arguments)}),
                    )
                )
                changed = True
                truncated_items += 1
            if changed:
                projected[index] = ModelMessage.create(
                    role=message.role,
                    content=message.content,
                    tool_calls=tuple(calls),
                )
            if _messages_characters(projected) <= max_characters:
                break

    return ToolContextProjection(
        messages=tuple(projected),
        source_characters=source_characters,
        projected_characters=_messages_characters(projected),
        truncated_items=truncated_items,
    )


def _project_arguments(arguments: str) -> tuple[str, bool]:
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        return _json({_projection_key(arguments): _projection_marker(arguments)}), True
    projected, truncated = _project_value(value)
    return _json(projected), truncated


def _project_value(value: Any, *, key: str | None = None) -> tuple[Any, bool]:
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        truncated = False
        for child_key, child_value in value.items():
            result, child_truncated = _project_value(child_value, key=str(child_key))
            projected[str(child_key)] = result
            truncated = truncated or child_truncated
        return projected, truncated
    if isinstance(value, list):
        projected_items: list[Any] = []
        truncated = False
        for item in value:
            result, child_truncated = _project_value(item, key=key)
            projected_items.append(result)
            truncated = truncated or child_truncated
        return projected_items, truncated
    if isinstance(value, str):
        limit = 256 if key in _LARGE_ARGUMENT_KEYS else 2_000
        if len(value) > limit:
            return _projection_marker(value), True
    return value, False


def _tool_result_limit(name: str | None) -> int:
    if (name or "").startswith("skill."):
        return _MAX_SKILL_RESULT_CHARACTERS
    if name in {"artifact.read", "knowledge.read", "project.read"}:
        return _MAX_READ_RESULT_CHARACTERS
    return _MAX_OTHER_RESULT_CHARACTERS


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    marker = f"\n[context projection truncated; {_projection_marker(value)}]"
    head_limit = max(1, limit - len(marker))
    return f"{value[:head_limit].rstrip()}{marker}", True


def _projection_marker(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"omitted sha256={digest} characters={len(value)}"


def _projection_key(value: str) -> str:
    return "unparsed_arguments" if value else "empty_arguments"


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _message_characters(message: ModelMessage) -> int:
    return len(message.content) + sum(len(call.arguments) for call in message.tool_calls)


def _messages_characters(messages: list[ModelMessage]) -> int:
    return sum(_message_characters(message) for message in messages)


__all__ = ["ToolContextProjection", "durable_tool_context", "project_tool_context"]
