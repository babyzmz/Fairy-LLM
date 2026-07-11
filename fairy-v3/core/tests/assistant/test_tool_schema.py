from __future__ import annotations

import math

import pytest

from fairy_core.assistant.tools import (
    ToolCandidateError,
    sanitize_model_arguments,
    validate_tool_arguments,
)
from fairy_core.commanding.registry import build_default_registry


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


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_model_argument_sanitizer_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ToolCandidateError, match="finite"):
        sanitize_model_arguments({"value": value})
