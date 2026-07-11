from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from fairy_core.mcp.models import (
    McpCallContext,
    McpCallResult,
    McpConnection,
    McpDiscovery,
    McpToolDescriptor,
)
from fairy_core.mcp.ports import McpTransportInterrupted


@dataclass(slots=True)
class FakeMcpConnector:
    tools: tuple[McpToolDescriptor, ...]
    result: McpCallResult = field(
        default_factory=lambda: McpCallResult.create(
            text="Issue FAIRY-42 is open.",
            structured_content={"id": "FAIRY-42", "state": "open"},
        )
    )
    discover_calls: list[McpConnection] = field(default_factory=list)
    calls: list[tuple[McpConnection, str, dict[str, object], McpCallContext]] = field(
        default_factory=list
    )
    cancelled: list[UUID] = field(default_factory=list)
    failures: list[McpTransportInterrupted] = field(default_factory=list)
    closed: bool = False

    def discover(self, connection: McpConnection) -> McpDiscovery:
        self.discover_calls.append(connection)
        return McpDiscovery.create(
            server_name=connection.server_id,
            protocol_version="2025-11-25",
            tools=self.tools,
        )

    def call_tool(
        self,
        connection: McpConnection,
        tool_name: str,
        arguments: dict[str, object],
        context: McpCallContext,
    ) -> McpCallResult:
        self.calls.append((connection, tool_name, arguments, context))
        if self.failures:
            raise self.failures.pop(0)
        return self.result

    def cancel(self, command_run_id: UUID) -> None:
        self.cancelled.append(command_run_id)

    def credential_configured(self, connection: McpConnection) -> bool:
        return connection.credential_ref is None or connection.credential_ref == "env:TEST_TOKEN"

    def close(self) -> None:
        self.closed = True


def issue_tools() -> tuple[McpToolDescriptor, ...]:
    return (
        McpToolDescriptor.create(
            name="get_issue",
            title="Get issue",
            description="Read one issue by identifier.",
            input_schema={
                "type": "object",
                "properties": {"issue_id": {"type": "string", "minLength": 1, "maxLength": 64}},
                "required": ["issue_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "maxLength": 64},
                    "state": {"type": "string", "enum": ["open", "closed"]},
                },
                "required": ["id", "state"],
                "additionalProperties": False,
            },
        ),
        McpToolDescriptor.create(
            name="create_issue",
            title="Create issue",
            description="Create one issue.",
            input_schema={
                "type": "object",
                "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["title"],
                "additionalProperties": False,
            },
            output_schema=None,
        ),
    )
