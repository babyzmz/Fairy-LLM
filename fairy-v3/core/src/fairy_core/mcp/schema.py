from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from fairy_core.mcp.models import McpToolDescriptor

_SERVER_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_MAX_SCHEMA_BYTES = 32 * 1024
_MAX_DEPTH = 8
_MAX_PROPERTIES = 64
_MAX_STRING_LENGTH = 32_000
_MAX_ARRAY_ITEMS = 100
_ALLOWED_KEYS = frozenset(
    {
        "additionalProperties",
        "description",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "properties",
        "required",
        "title",
        "type",
    }
)
_RESERVED_ARGUMENTS = frozenset(
    {
        "_meta",
        "allowed_write_paths",
        "api_key",
        "arguments",
        "base_version_id",
        "capabilities",
        "capability",
        "command",
        "conversation_id",
        "credential",
        "credential_ref",
        "credentials",
        "endpoint",
        "environment",
        "execution_target",
        "forbidden_write_paths",
        "headers",
        "memory_read_scope",
        "memory_snapshot_hash",
        "memory_snapshot_id",
        "memory_write_scope",
        "network_policy",
        "project_id",
        "project_root",
        "scope",
        "scope_digest",
        "server_id",
        "target_version_id",
        "task_id",
        "token",
        "transport",
        "version_id",
        "workspace_type",
    }
)


class McpSchemaError(ValueError):
    pass


def sanitize_mcp_tool(
    *,
    server_id: str,
    name: str,
    title: str | None,
    description: str,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any] | None,
) -> McpToolDescriptor:
    normalized_server = server_id.strip().lower()
    if _SERVER_ID.fullmatch(normalized_server) is None or "--" in normalized_server:
        raise McpSchemaError("MCP server ID is invalid")
    normalized_tool = _sanitize_tool_name(name)
    sanitized_input = _sanitize_schema(input_schema, path="input", depth=0, root=True)
    sanitized_output = (
        _sanitize_schema(output_schema, path="output", depth=0, root=True)
        if output_schema is not None
        else None
    )
    imported_name = f"mcp.{normalized_server}.{normalized_tool}"
    bounded_description = description.strip()
    if not bounded_description or len(bounded_description) > 4_096:
        raise McpSchemaError("MCP tool description is invalid")
    bounded_title = title.strip() if title else None
    if bounded_title is not None and len(bounded_title) > 200:
        raise McpSchemaError("MCP tool title is invalid")
    payload = {
        "name": name,
        "imported_name": imported_name,
        "title": bounded_title,
        "description": bounded_description,
        "input_schema": sanitized_input,
        "output_schema": sanitized_output,
    }
    digest = hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()
    return McpToolDescriptor.bound(
        name=name,
        title=bounded_title,
        description=bounded_description,
        input_schema=sanitized_input,
        output_schema=sanitized_output,
        imported_name=imported_name,
        schema_digest=digest,
    )


def contains_reserved_arguments(value: Mapping[str, object]) -> bool:
    def visit(item: object) -> bool:
        if isinstance(item, Mapping):
            return any(
                str(key).casefold() in _RESERVED_ARGUMENTS or visit(child)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return any(visit(child) for child in item)
        return False

    return visit(value)


def sanitize_extension_input_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded closed schema accepted for any untrusted extension tool."""

    return _sanitize_schema(schema, path="input", depth=0, root=True)


def _sanitize_tool_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 128 or not normalized.isascii():
        raise McpSchemaError("MCP tool name is invalid")
    sanitized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    sanitized = re.sub(r"_+", "_", sanitized)
    if not sanitized or len(sanitized) > 64:
        raise McpSchemaError("MCP tool name cannot be safely imported")
    return sanitized


def _sanitize_schema(
    schema: Mapping[str, Any],
    *,
    path: str,
    depth: int,
    root: bool = False,
) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise McpSchemaError(f"{path} schema must be an object")
    if depth > _MAX_DEPTH:
        raise McpSchemaError("MCP schema nesting is too deep")
    metadata_keys = {"$schema"} if root else set()
    unknown = set(schema) - _ALLOWED_KEYS - metadata_keys
    if unknown:
        raise McpSchemaError(f"MCP schema uses unsupported keywords: {', '.join(sorted(unknown))}")
    if "$schema" in schema:
        dialect = schema["$schema"]
        if not isinstance(dialect, str) or not 1 <= len(dialect) <= 200:
            raise McpSchemaError("MCP schema dialect metadata is invalid")
    schema_type = schema.get("type", "object" if root else None)
    if not isinstance(schema_type, str) or schema_type not in {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
    }:
        raise McpSchemaError(f"{path} schema type is unsupported")
    if root and schema_type != "object":
        raise McpSchemaError("MCP tool root schema must describe an object")
    result: dict[str, Any] = {"type": schema_type}
    for key in ("title", "description"):
        if key in schema:
            value = schema[key]
            if not isinstance(value, str) or len(value) > 1_024:
                raise McpSchemaError(f"MCP schema {key} is invalid")
            result[key] = value
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, (list, tuple)) or not 1 <= len(enum) <= 100:
            raise McpSchemaError("MCP schema enum is invalid")
        try:
            encoded_enum = json.loads(_canonical(list(enum)))
        except (TypeError, ValueError) as error:
            raise McpSchemaError("MCP schema enum is not JSON") from error
        if len(_canonical(encoded_enum).encode("utf-8")) > 8_192:
            raise McpSchemaError("MCP schema enum is too large")
        result["enum"] = encoded_enum
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping) or len(properties) > _MAX_PROPERTIES:
            raise McpSchemaError("MCP schema properties are invalid")
        sanitized_properties: dict[str, Any] = {}
        for raw_name, child in properties.items():
            name = str(raw_name)
            if not name or len(name) > 128:
                raise McpSchemaError("MCP schema property name is invalid")
            if name.casefold() in _RESERVED_ARGUMENTS:
                raise McpSchemaError(f"MCP schema property is reserved: {name}")
            if not isinstance(child, Mapping):
                raise McpSchemaError("MCP schema property must be an object")
            sanitized_properties[name] = _sanitize_schema(
                child,
                path=f"{path}.{name}",
                depth=depth + 1,
            )
        additional = schema.get("additionalProperties", False)
        if additional is not False:
            raise McpSchemaError("MCP object schemas must disable additionalProperties")
        required = schema.get("required", [])
        if not isinstance(required, (list, tuple)) or any(
            not isinstance(name, str) for name in required
        ):
            raise McpSchemaError("MCP schema required is invalid")
        if len(required) != len(set(required)) or not set(required).issubset(sanitized_properties):
            raise McpSchemaError("MCP schema required references unknown properties")
        result.update(
            {
                "properties": sanitized_properties,
                "required": list(required),
                "additionalProperties": False,
            }
        )
    elif schema_type == "array":
        minimum = _bounded_integer(schema.get("minItems", 0), "minItems", 0, _MAX_ARRAY_ITEMS)
        maximum = _bounded_integer(
            schema.get("maxItems", _MAX_ARRAY_ITEMS),
            "maxItems",
            0,
            _MAX_ARRAY_ITEMS,
        )
        if minimum > maximum:
            raise McpSchemaError("MCP schema minItems exceeds maxItems")
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise McpSchemaError("MCP array schema requires one item schema")
        result.update(
            {
                "items": _sanitize_schema(items, path=f"{path}[]", depth=depth + 1),
                "minItems": minimum,
                "maxItems": maximum,
            }
        )
    elif schema_type == "string":
        minimum = _bounded_integer(schema.get("minLength", 0), "minLength", 0, _MAX_STRING_LENGTH)
        maximum = _bounded_integer(
            schema.get("maxLength", _MAX_STRING_LENGTH),
            "maxLength",
            0,
            _MAX_STRING_LENGTH,
        )
        if minimum > maximum:
            raise McpSchemaError("MCP schema minLength exceeds maxLength")
        result.update({"minLength": minimum, "maxLength": maximum})
    elif schema_type in {"integer", "number"}:
        for key in ("minimum", "maximum"):
            if key in schema:
                value = schema[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise McpSchemaError(f"MCP schema {key} must be numeric")
                if isinstance(value, float) and not math.isfinite(value):
                    raise McpSchemaError(f"MCP schema {key} must be finite")
                result[key] = value
        if "minimum" in result and "maximum" in result and result["minimum"] > result["maximum"]:
            raise McpSchemaError("MCP schema minimum exceeds maximum")
    encoded = _canonical(result).encode("utf-8")
    if len(encoded) > _MAX_SCHEMA_BYTES:
        raise McpSchemaError("MCP schema is too large")
    return result


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise McpSchemaError(f"MCP schema {name} is outside its allowed bound")
    return value


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "McpSchemaError",
    "contains_reserved_arguments",
    "sanitize_extension_input_schema",
    "sanitize_mcp_tool",
]
