from __future__ import annotations

import json
import math

import pytest

from fairy_core.assistant.candidates import (
    ToolCandidate,
    arguments_for_definition,
    deduplicate_tool_candidates,
)
from fairy_core.assistant.tools import (
    ToolCandidateError,
    model_tools_for_definitions,
    sanitize_model_arguments,
    sanitize_public_intent,
    validate_tool_arguments,
)
from fairy_core.commanding.registry import build_default_registry
from fairy_core.providers import ModelDelta


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "Fairy", "kind": "unsupported"},
        {"query": "Fairy", "count": 0},
        {"query": "Fairy", "count": 21},
        {"query": "Fairy", "offset": -1},
    ],
)
def test_web_search_schema_enforces_enum_and_numeric_bounds(
    arguments: dict[str, object],
) -> None:
    definition = build_default_registry().get("web.search")
    assert definition is not None

    with pytest.raises(ToolCandidateError):
        validate_tool_arguments(definition, arguments)


@pytest.mark.parametrize("source_count", [0, 11])
def test_research_schema_enforces_source_count(source_count: int) -> None:
    definition = build_default_registry().get("research.build")
    assert definition is not None

    with pytest.raises(ToolCandidateError):
        validate_tool_arguments(
            definition,
            {
                "kind": "web_brief",
                "question": "Fairy",
                "sources": ["https://example.com"] * source_count,
            },
        )


def test_information_schemas_enforce_units_and_numeric_bounds() -> None:
    registry = build_default_registry()
    weather = registry.get("info.weather")
    fx = registry.get("info.fx")
    assert weather is not None and fx is not None

    validate_tool_arguments(weather, {"location": "Sydney", "units": "metric"})
    validate_tool_arguments(fx, {"base": "USD", "quote": "EUR", "amount": 10.5})

    with pytest.raises(ToolCandidateError, match="allowed"):
        validate_tool_arguments(weather, {"location": "Sydney", "units": "kelvin"})
    with pytest.raises(ToolCandidateError, match="minimum"):
        validate_tool_arguments(fx, {"base": "USD", "quote": "EUR", "amount": -1})


def test_execution_plan_schema_accepts_null_for_a_new_file_hash() -> None:
    definition = build_default_registry().get("execution.plan")
    assert definition is not None

    validate_tool_arguments(
        definition,
        {
            "files": [
                {
                    "path": "index.html",
                    "purpose": "Create the entrypoint",
                    "batch": 1,
                    "expected_hash": None,
                }
            ]
        },
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_model_argument_sanitizer_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ToolCandidateError, match="finite"):
        sanitize_model_arguments({"value": value})


def test_model_visible_tools_receive_a_bounded_public_intent_field() -> None:
    definition = build_default_registry().get("web.search")
    assert definition is not None

    direct_answer, web_search = model_tools_for_definitions((definition,))

    assert "public_intent" not in direct_answer.input_schema["properties"]
    assert web_search.input_schema["properties"]["public_intent"]["maxLength"] == 240
    assert "public_intent" not in definition.input_schema["properties"]
    assert sanitize_public_intent("Use Bearer very-secret-token to search") == (
        "Use [redacted] to search"
    )


def test_model_tool_adds_public_intent_when_properties_are_omitted() -> None:
    definition = next(
        item
        for item in build_default_registry().definitions()
        if item.model_visible and "properties" not in item.input_schema
    )

    tool = model_tools_for_definitions((definition,))[-1]

    assert tool.input_schema["properties"]["public_intent"]["maxLength"] == 240


def test_workspace_tool_arguments_are_canonical_and_drop_runtime_commands() -> None:
    definition = build_default_registry().get("execution.plan")
    assert definition is not None
    candidate = ToolCandidate(call_id="plan")
    candidate.append(
        ModelDelta.tool_call(
            profile_id="scripted",
            sequence=1,
            tool_call_id="plan",
            tool_name="execution.plan",
            arguments_fragment=json.dumps(
                {
                    "files": [
                        {
                            "path": "./index.html",
                            "purpose": "Create page",
                            "batch": 1,
                        }
                    ],
                    "validation_commands": [
                        "npm test",
                        "python3 -m http.server 8080",
                        "npm run dev",
                    ],
                }
            ),
        )
    )

    arguments = arguments_for_definition(candidate, definition)

    assert arguments["files"][0]["path"] == "index.html"
    assert arguments["validation_commands"] == ["npm test"]


def test_placeholder_changeset_is_rejected_before_command_dispatch() -> None:
    definition = build_default_registry().get("edit.propose_changeset")
    assert definition is not None
    candidate = ToolCandidate(call_id="edit")
    candidate.append(
        ModelDelta.tool_call(
            profile_id="scripted",
            sequence=1,
            tool_call_id="edit",
            tool_name="edit.propose_changeset",
            arguments_fragment=json.dumps(
                {
                    "files": [{"path": "./main.js", "content": "// TODO"}],
                    "reason": "Create page",
                }
            ),
        )
    )

    with pytest.raises(ToolCandidateError, match="only comments"):
        arguments_for_definition(candidate, definition)


def test_equivalent_tool_candidates_are_coalesced_before_execution() -> None:
    definition = build_default_registry().get("artifact.list")
    assert definition is not None
    candidates: list[ToolCandidate] = []
    for call_id in ("first", "second"):
        candidate = ToolCandidate(call_id=call_id)
        candidate.append(
            ModelDelta.tool_call(
                profile_id="scripted",
                sequence=1,
                tool_call_id=call_id,
                tool_name="artifact.list",
                arguments_fragment="{}",
            )
        )
        candidates.append(candidate)

    unique, suppressed = deduplicate_tool_candidates(
        candidates,
        {definition.name: definition},
    )

    assert [candidate.call_id for candidate in unique] == ["first"]
    assert suppressed == 1
