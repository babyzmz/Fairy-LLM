from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.mcp.models import (
    McpCallContext,
    McpCallResult,
    McpConnection,
    McpDiscovery,
    McpTransport,
)
from fairy_core.mcp.ports import McpError
from fairy_core.mcp.sdk import MappingCredentialResolver, OfficialMcpConnector


class CloudMcpConnector:
    """Cloud composition that permits deployment-trusted Streamable HTTP only."""

    def __init__(
        self,
        credentials: Mapping[str, str] | None = None,
        *,
        allowed_hosts: Iterable[str] = (),
    ) -> None:
        self._delegate = OfficialMcpConnector(
            credentials=MappingCredentialResolver(credentials or {}),
        )
        self._allowed_hosts = frozenset(_host(value) for value in allowed_hosts)

    def discover(self, connection: McpConnection) -> McpDiscovery:
        self._validate(connection)
        return self._delegate.discover(connection)

    def call_tool(
        self,
        connection: McpConnection,
        tool_name: str,
        arguments: dict[str, object],
        context: McpCallContext,
    ) -> McpCallResult:
        self._validate(connection)
        return self._delegate.call_tool(connection, tool_name, arguments, context)

    def cancel(self, command_run_id: UUID) -> None:
        self._delegate.cancel(command_run_id)

    def credential_configured(self, connection: McpConnection) -> bool:
        return (
            connection.transport is McpTransport.STREAMABLE_HTTP
            and self._delegate.credential_configured(connection)
        )

    def close(self) -> None:
        self._delegate.close()

    def _validate(self, connection: McpConnection) -> None:
        if connection.transport is not McpTransport.STREAMABLE_HTTP:
            raise McpError(
                "Cloud MCP requires Streamable HTTP",
                error_code="MCP_TRANSPORT_NOT_ALLOWED",
            )
        hostname = urlsplit(connection.endpoint or "").hostname
        if hostname is None or _host(hostname) not in self._allowed_hosts:
            raise McpError(
                "Cloud MCP endpoint is not deployment allowlisted",
                error_code="MCP_DESTINATION_BLOCKED",
            )


def _host(value: str) -> str:
    normalized = value.strip().rstrip(".").casefold()
    if not normalized or any(character in normalized for character in "/:@[]"):
        raise ValueError("Cloud MCP allowed host is invalid")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Cloud MCP allowed host is invalid") from error


__all__ = ["CloudMcpConnector"]
