from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from app.models.tool_result import ToolResult
from app.tool_registry import ToolRegistry


logger = logging.getLogger(__name__)
ToolEventCallback = Callable[[str, dict[str, Any]], None]


class MCPClientLayer:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_callback: ToolEventCallback | None = None,
    ) -> None:
        self.registry = registry
        self.event_callback = event_callback

    def emit_event(self, event: str, payload: dict[str, Any]) -> None:
        if self.event_callback is not None:
            self.event_callback(event, payload)

    def call_tool(
        self,
        tool_name: str,
        *,
        allowed_tools: Iterable[str],
        **kwargs: Any,
    ) -> ToolResult:
        allowed = set(allowed_tools)
        if tool_name not in allowed:
            error = f"Tool '{tool_name}' not allowed for current skill."
            logger.warning("tool_call_failed tool=%s error=%s", tool_name, error)
            return ToolResult(tool_name=tool_name, ok=False, error=error)

        if not self.registry.has(tool_name):
            error = f"Tool '{tool_name}' is not registered."
            logger.warning("tool_call_failed tool=%s error=%s", tool_name, error)
            return ToolResult(tool_name=tool_name, ok=False, error=error)

        self.emit_event("tool_call_start", {"tool_name": tool_name, "kwargs": kwargs})
        logger.info("tool_call_start tool=%s", tool_name)

        try:
            payload = self.registry.get(tool_name).handler(**kwargs) or {}
            result = ToolResult(
                tool_name=tool_name,
                ok=True,
                data=dict(payload),
                metadata={"allowed": True},
            )
            logger.info("tool_call_done tool=%s", tool_name)
            self.emit_event("tool_call_done", {"tool_name": tool_name, "result": result.data})
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool_call_failed tool=%s", tool_name)
            self.emit_event("tool_call_failed", {"tool_name": tool_name, "error": str(exc), "kwargs": kwargs})
            return ToolResult(tool_name=tool_name, ok=False, error=str(exc))
