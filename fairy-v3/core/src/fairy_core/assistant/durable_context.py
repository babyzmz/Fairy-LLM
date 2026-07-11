from __future__ import annotations

import json

from fairy_core.assistant.models import ToolInvocation, ToolInvocationStatus
from fairy_core.assistant.tools import tool_message_content
from fairy_core.providers import ModelMessage, ModelRole, ModelToolCall


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
    return tuple(messages)


__all__ = ["durable_tool_context"]
