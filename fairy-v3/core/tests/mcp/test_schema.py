from __future__ import annotations

import pytest

from fairy_core.mcp.models import McpDiscovery
from fairy_core.mcp.schema import McpSchemaError, sanitize_mcp_tool


def test_mcp_tool_names_are_namespaced_and_schemas_are_closed_and_bounded() -> None:
    tool = sanitize_mcp_tool(
        server_id="Issue-Tracker",
        name="Get Issue",
        title="Get issue",
        description="Read one issue.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        output_schema=None,
    )

    assert tool.imported_name == "mcp.issue-tracker.get_issue"
    assert tool.input_schema["additionalProperties"] is False
    assert tool.input_schema["properties"]["id"]["maxLength"] == 32_000
    assert len(tool.schema_digest) == 64


def test_root_schema_dialect_metadata_is_accepted_but_not_projected() -> None:
    tool = sanitize_mcp_tool(
        server_id="docs",
        name="query",
        title=None,
        description="Query current documentation.",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema=None,
    )

    assert "$schema" not in tool.input_schema
    with pytest.raises(McpSchemaError, match="unsupported"):
        sanitize_mcp_tool(
            server_id="docs",
            name="nested",
            title=None,
            description="Reject nested dialect metadata.",
            input_schema={
                "type": "object",
                "properties": {"query": {"$schema": "https://example.test", "type": "string"}},
            },
            output_schema=None,
        )


@pytest.mark.parametrize(
    "schema,match",
    [
        ({"type": "object", "$ref": "https://evil.test/schema"}, "unsupported"),
        (
            {
                "type": "object",
                "properties": {"scope_digest": {"type": "string"}},
            },
            "reserved",
        ),
        (
            {
                "type": "object",
                "properties": {"credential": {"type": "string"}},
            },
            "reserved",
        ),
        (
            {
                "type": "object",
                "properties": {"values": {"type": "array", "maxItems": 100_000}},
            },
            "maxItems",
        ),
    ],
)
def test_malicious_or_unbounded_mcp_schemas_fail_closed(
    schema: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(McpSchemaError, match=match):
        sanitize_mcp_tool(
            server_id="unsafe",
            name="attack",
            title=None,
            description="Untrusted tool",
            input_schema=schema,
            output_schema=None,
        )


def test_sanitized_name_collisions_are_rejected_by_discovery() -> None:
    first = sanitize_mcp_tool(
        server_id="issues",
        name="get issue",
        title=None,
        description="first",
        input_schema={"type": "object"},
        output_schema=None,
    )
    second = sanitize_mcp_tool(
        server_id="issues",
        name="get-issue",
        title=None,
        description="second",
        input_schema={"type": "object"},
        output_schema=None,
    )

    assert first.imported_name == second.imported_name
    with pytest.raises(ValueError, match="collide"):
        McpDiscovery.create(
            server_name="issues",
            protocol_version="2025-11-25",
            tools=(first, second),
        )
