from __future__ import annotations

import pytest
from fairy_core.mcp.models import McpConnection, McpTransport
from fairy_core.mcp.ports import McpError

from fairy_cloud.mcp import CloudMcpConnector


def test_cloud_mcp_connector_rejects_stdio_before_starting_a_process() -> None:
    connector = CloudMcpConnector()
    connection = McpConnection(
        server_id="local-only",
        display_name="Local only",
        transport=McpTransport.STDIO,
        command="untrusted-command",
    )
    try:
        assert connector.credential_configured(connection) is False
        with pytest.raises(McpError) as error:
            connector.discover(connection)
        assert error.value.error_code == "MCP_TRANSPORT_NOT_ALLOWED"
    finally:
        connector.close()


def test_cloud_mcp_connector_resolves_only_deployment_credential_references() -> None:
    connector = CloudMcpConnector({"vault:docs": "deployment-secret"})
    configured = McpConnection(
        server_id="docs",
        display_name="Docs",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test/mcp",
        credential_ref="vault:docs",
    )
    missing = McpConnection(
        server_id="missing",
        display_name="Missing",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test/mcp",
        credential_ref="vault:missing",
    )
    try:
        assert connector.credential_configured(configured) is True
        assert connector.credential_configured(missing) is False
    finally:
        connector.close()


def test_cloud_mcp_connector_rejects_remote_host_outside_deployment_allowlist() -> None:
    connector = CloudMcpConnector(allowed_hosts=("approved.example.test",))
    connection = McpConnection(
        server_id="unapproved",
        display_name="Unapproved",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test/mcp",
    )
    try:
        with pytest.raises(McpError) as error:
            connector.discover(connection)
        assert error.value.error_code == "MCP_DESTINATION_BLOCKED"
    finally:
        connector.close()
