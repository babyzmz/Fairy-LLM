from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    _tool,
)
from fairy_core.commanding.types import PermissionProfile


def build_information_definitions(
    profiles: frozenset[PermissionProfile],
) -> list[ToolDefinition]:
    shared = {
        "effect": SideEffect.READ,
        "risk": RiskLevel.LOW,
        "approval": ApprovalPolicy.NEVER,
        "profiles": profiles,
        "executor": "information_tools",
    }
    return [
        _tool(
            "info.weather",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get current weather for an explicitly resolved location.",
            input_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "minLength": 2, "maxLength": 500},
                    "country_code": {"type": "string", "minLength": 2, "maxLength": 2},
                    "candidate_index": {"type": "integer", "minimum": 1, "maximum": 5},
                    "units": {"type": "string", "enum": ["metric", "imperial"]},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.news",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Search current news sources with publication provenance.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    "freshness": {"type": "string", "maxLength": 64},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.time",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Convert the current instant to an IANA time zone.",
            input_schema={
                "type": "object",
                "properties": {"timezone": {"type": "string", "minLength": 3, "maxLength": 255}},
                "required": ["timezone"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.map",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Generate an encoded OpenStreetMap search link.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 1_000}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.stock",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get a delayed or last-close stock quote.",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string", "minLength": 1, "maxLength": 16}},
                "required": ["symbol"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.fx",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Convert currencies using a dated central-bank reference rate.",
            input_schema={
                "type": "object",
                "properties": {
                    "base": {"type": "string", "minLength": 3, "maxLength": 3},
                    "quote": {"type": "string", "minLength": 3, "maxLength": 3},
                    "amount": {"type": "number", "minimum": 0, "maximum": 1e15},
                },
                "required": ["base", "quote"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.crypto",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get a dated cryptocurrency close in the requested market currency.",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "minLength": 1, "maxLength": 16},
                    "market_currency": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 3,
                    },
                },
                "required": ["symbol", "market_currency"],
                "additionalProperties": False,
            },
        ),
    ]


__all__ = ["build_information_definitions"]
