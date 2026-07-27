from __future__ import annotations

from fairy_core.assistant.tools import ToolExecutionUnavailableError, ToolExecutor, ToolResult
from fairy_core.commanding import CommandRun
from fairy_core.commanding.registry import SideEffect, ToolDefinition
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory

_BROWSER_INPUT_TOOLS = frozenset(
    {
        "browser.click",
        "browser.fill",
        "browser.press",
        "browser.scroll",
    }
)
_PROHIBITED_NAME_PARTS = frozenset(
    {
        "account",
        "email",
        "login",
        "message",
        "payment",
        "purchase",
        "secret",
    }
)


class RealtimeAssistanceCapabilityError(ToolExecutionUnavailableError):
    error_code = "REALTIME_ASSISTANCE_CAPABILITY_DENIED"


class RealtimeAssistanceToolExecutor:
    """Adds the Realtime-only denylist without creating a second tool authority."""

    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        delegate: ToolExecutor,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._delegate = delegate

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()

    def execute(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        self._authorize(definition, scope)
        return self._delegate.execute(definition, scope, arguments)

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        self._authorize(definition, scope)
        execute_command = getattr(self._delegate, "execute_command", None)
        if callable(execute_command):
            return execute_command(
                definition,
                scope,
                arguments,
                command_run=command_run,
            )
        return self._delegate.execute(definition, scope, arguments)

    def _authorize(self, definition: ToolDefinition, scope: ScopeContract) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            assistance = unit_of_work.realtime.assistance_for_task(scope.task_id)
        if assistance is None:
            return
        name_parts = frozenset(definition.name.replace("-", ".").replace("_", ".").split("."))
        if definition.name.startswith("system.") or name_parts & _PROHIBITED_NAME_PARTS:
            raise RealtimeAssistanceCapabilityError(
                "Realtime Assistance cannot execute external account or system actions"
            )
        if definition.name in _BROWSER_INPUT_TOOLS:
            raise RealtimeAssistanceCapabilityError(
                "Realtime Assistance cannot control keyboard, pointer, or forms"
            )
        if definition.source == "mcp" and definition.side_effect in {
            SideEffect.WRITE,
            SideEffect.EXECUTE,
        }:
            raise RealtimeAssistanceCapabilityError(
                "Realtime Assistance permits only read-only MCP capabilities"
            )
        needs_network = (
            definition.name == "research.build"
            or definition.name.startswith("browser.")
            or definition.source == "mcp"
        )
        if needs_network and not assistance.allow_network:
            raise RealtimeAssistanceCapabilityError(
                "Online Assistance is disabled for this Realtime request"
            )


__all__ = [
    "RealtimeAssistanceCapabilityError",
    "RealtimeAssistanceToolExecutor",
]
