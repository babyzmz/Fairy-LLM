from __future__ import annotations

from fairy_core.assistant.context import AssistantContext
from fairy_core.assistant.routing import RoutingDecision
from fairy_core.providers import (
    ModelMessage,
    ModelRole,
    ProviderCapability,
    ProviderUnavailableError,
)


def constrain_context_for_media(
    context: AssistantContext,
    decision: RoutingDecision | None,
    *,
    output_completed: bool = False,
) -> AssistantContext:
    if decision is None or decision.media_tool_name is None:
        return context

    if output_completed:
        policy = ModelMessage.create(
            role=ModelRole.SYSTEM,
            content=(
                "The planned media output is already durable. Do not call any tool or request "
                "another media output. Return one concise final answer that refers to the "
                "generated Workspace artifact."
            ),
        )
        return AssistantContext(
            messages=(context.messages[0], policy, *context.messages[1:]),
            tools=(),
            tool_definitions=(),
            required_capabilities=(
                context.required_capabilities - frozenset({ProviderCapability.TOOLS})
            ),
            diagnostics=context.diagnostics,
        )

    tool_definitions = tuple(
        definition
        for definition in context.tool_definitions
        if definition.name == decision.media_tool_name
    )
    tools = tuple(tool for tool in context.tools if tool.name == decision.media_tool_name)
    if len(tool_definitions) != 1 or len(tools) != 1:
        raise ProviderUnavailableError("The selected media generation capability is unavailable")

    policy = ModelMessage.create(
        role=ModelRole.SYSTEM,
        content=(
            f"This Turn is routed to {decision.task_kind.value} generation. "
            f"Before a successful tool result, call {decision.media_tool_name} "
            "exactly once with a bounded specification derived from the user request. "
            "Do not call another tool or emit user-visible text in that round. After a "
            "successful tool result, do not call the tool again; return one concise final "
            "answer that refers to the generated Workspace artifact."
        ),
    )
    return AssistantContext(
        messages=(context.messages[0], policy, *context.messages[1:]),
        tools=tools,
        tool_definitions=tool_definitions,
        required_capabilities=context.required_capabilities,
        diagnostics=context.diagnostics,
    )


__all__ = ["constrain_context_for_media"]
