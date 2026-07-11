from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.domain.errors import DomainError
from fairy_core.mcp.models import (
    McpCallContext,
    McpCallResult,
    McpConnection,
    McpDiscovery,
)


class McpError(DomainError):
    def __init__(self, message: str, *, error_code: str = "MCP_UNAVAILABLE") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.code = error_code


class McpTransportInterrupted(McpError):
    def __init__(self, message: str, *, response_started: bool) -> None:
        super().__init__(message, error_code="MCP_TRANSPORT_INTERRUPTED")
        self.response_started = response_started


class McpCancelledError(Exception):
    pass


class McpConnector(Protocol):
    def discover(self, connection: McpConnection) -> McpDiscovery: ...

    def call_tool(
        self,
        connection: McpConnection,
        tool_name: str,
        arguments: dict[str, object],
        context: McpCallContext,
    ) -> McpCallResult: ...

    def cancel(self, command_run_id: UUID) -> None: ...

    def credential_configured(self, connection: McpConnection) -> bool: ...

    def close(self) -> None: ...


__all__ = [
    "McpCancelledError",
    "McpConnector",
    "McpError",
    "McpTransportInterrupted",
]
