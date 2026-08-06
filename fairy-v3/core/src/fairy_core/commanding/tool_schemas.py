from __future__ import annotations

from typing import Any


def execution_plan_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "minItems": 1,
                "maxItems": 200,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1_024,
                            "description": (
                                "Canonical Workspace-relative path without ./ or /workspace."
                            ),
                        },
                        "purpose": {"type": "string", "minLength": 1, "maxLength": 500},
                        "batch": {"type": "integer", "minimum": 1, "maximum": 200},
                        "expected_hash": {
                            "type": ["string", "null"],
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "required": ["path", "purpose", "batch"],
                    "additionalProperties": False,
                },
            },
            "entrypoints": {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "string", "maxLength": 1_024},
            },
            "dependencies": {
                "type": "array",
                "maxItems": 128,
                "items": {"type": "string", "maxLength": 255},
            },
            "validation_commands": {
                "type": "array",
                "maxItems": 32,
                "description": (
                    "Finite validation commands only. Do not include servers, watchers, npm dev, "
                    "Vite, http.server, or Preview startup."
                ),
                "items": {"type": "string", "maxLength": 1_024},
            },
        },
        "required": ["files"],
        "additionalProperties": False,
    }


def project_read_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 1_024},
            "start_line": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
            "end_line": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def project_list_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "prefix": {"type": "string", "minLength": 1, "maxLength": 1_024},
            "glob": {"type": "string", "minLength": 1, "maxLength": 512},
            "cursor": {"type": "string", "minLength": 1, "maxLength": 2_048},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    }


def project_search_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 512},
            "glob": {"type": "string", "minLength": 1, "maxLength": 512},
            "case_sensitive": {"type": "boolean"},
            "cursor": {"type": "string", "minLength": 1, "maxLength": 2_048},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def artifact_read_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"artifact_id": {"type": "string", "format": "uuid"}},
        "required": ["artifact_id"],
        "additionalProperties": False,
    }


__all__ = [
    "artifact_read_tool_schema",
    "execution_plan_tool_schema",
    "project_list_tool_schema",
    "project_read_tool_schema",
    "project_search_tool_schema",
]
