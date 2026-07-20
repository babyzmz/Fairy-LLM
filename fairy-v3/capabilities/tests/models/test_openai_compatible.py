from __future__ import annotations

import json
import re
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest
from fairy_core.providers import (
    CancellationToken,
    ModelDeltaKind,
    ModelImage,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelTool,
    ModelToolCall,
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    SecretValue,
)

from fairy_capabilities.models.openai_compatible import OpenAICompatibleProvider


def _profile(
    *,
    credential_ref: str | None = "primary",
    capabilities: frozenset[ProviderCapability] | None = None,
) -> ProviderProfile:
    return ProviderProfile.create(
        profile_id="fixture",
        display_name="Fixture",
        kind=ProviderKind.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:18080/v1",
        model_id="fixture-model",
        capabilities=capabilities or frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        credential_ref=credential_ref,
        fallback_profile_id=None,
        timeout_seconds=2,
        enabled=True,
    )


def _request() -> ModelRequest:
    return ModelRequest.create(
        profile_id="fixture",
        messages=(ModelMessage.create(role=ModelRole.USER, content="Hello"),),
        tools=(
            ModelTool.create(
                name="weather.get",
                description="Get current weather",
                input_schema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ),
        required_capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        max_output_tokens=128,
    )


def _sse(*payloads: object) -> bytes:
    lines: list[str] = [": keep-alive", ""]
    for payload in payloads:
        value = payload if isinstance(payload, str) else json.dumps(payload)
        lines.extend((f"data: {value}", ""))
    return "\n".join(lines).encode()


def test_stream_normalizes_text_tools_usage_and_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["model"] == "fixture-model"
        assert payload["tool_choice"] == "required"
        provider_tool_name = payload["tools"][0]["function"]["name"]
        assert provider_tool_name != "weather.get"
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", provider_tool_name)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "lo",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {
                                            "name": provider_tool_name,
                                            "arguments": '{"city":',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"Paris"}'},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 4,
                        "total_tokens": 11,
                    },
                },
                "[DONE]",
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        profile=_profile(),
        secret=SecretValue.from_text("fixture-secret"),
        client=client,
    )

    deltas = tuple(provider.stream(_request(), CancellationToken()))

    assert [delta.sequence for delta in deltas] == list(range(1, len(deltas) + 1))
    assert "".join(delta.text or "" for delta in deltas) == "Hello"
    tool_deltas = [delta for delta in deltas if delta.kind is ModelDeltaKind.TOOL_CALL]
    assert [delta.tool_call_id for delta in tool_deltas] == ["call-1", "call-1"]
    assert [delta.tool_name for delta in tool_deltas] == ["weather.get", "weather.get"]
    assert (
        "".join(delta.tool_arguments_fragment or "" for delta in tool_deltas) == '{"city":"Paris"}'
    )
    usage = next(delta for delta in deltas if delta.kind is ModelDeltaKind.USAGE)
    assert usage.usage["total_tokens"] == 11
    assert deltas[-1].kind is ModelDeltaKind.DONE
    assert deltas[-1].finish_reason == "tool_calls"


def test_openrouter_request_enforces_structured_privacy_and_cost_accounting() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {
                    "choices": [{"delta": {"content": '{"ok":true}'}}],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 3,
                        "total_tokens": 11,
                        "cost": 10.0,
                    },
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ),
        )

    profile = ProviderProfile.create(
        profile_id="openrouter",
        display_name="OpenRouter",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://openrouter.ai/api/v1",
        model_id="deepseek/deepseek-v4-pro",
        capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.STRUCTURED_OUTPUT}),
        credential_ref="openrouter",
        fallback_profile_id=None,
        timeout_seconds=30,
        enabled=True,
    )
    request = ModelRequest.create(
        profile_id=profile.id,
        messages=(ModelMessage.create(role=ModelRole.USER, content="Classify"),),
        tools=(),
        required_capabilities=frozenset(
            {ProviderCapability.TEXT, ProviderCapability.STRUCTURED_OUTPUT}
        ),
        max_output_tokens=384,
        response_schema_name="route",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        require_parameters=True,
        deny_data_collection=True,
        zero_data_retention=True,
    )
    provider = OpenAICompatibleProvider(
        profile=profile,
        secret=SecretValue.from_text("fixture-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    deltas = tuple(provider.stream(request, CancellationToken()))

    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "route",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }
    assert captured["provider"] == {
        "allow_fallbacks": True,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    assert "stream_options" not in captured
    usage = next(delta for delta in deltas if delta.kind is ModelDeltaKind.USAGE)
    assert usage.usage_cost == "10"


def test_stream_emits_one_done_when_upstream_repeats_finish_reason() -> None:
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=_sse(
                        {"choices": [{"delta": {"content": "done"}}]},
                        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"total_tokens": 7},
                        },
                        "[DONE]",
                    ),
                )
            )
        ),
    )

    deltas = tuple(provider.stream(_request(), CancellationToken()))

    assert [delta.kind for delta in deltas].count(ModelDeltaKind.DONE) == 1
    assert [delta.kind for delta in deltas].count(ModelDeltaKind.USAGE) == 1


def test_stream_rejects_usage_only_completion_as_unusable() -> None:
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=_sse(
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"total_tokens": 7},
                        },
                        "[DONE]",
                    ),
                )
            )
        ),
    )

    with pytest.raises(ProviderProtocolError, match="usable response"):
        tuple(provider.stream(_request(), CancellationToken()))


def test_stream_serializes_structured_tool_protocol_for_follow_up_round() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        provider_tool_name = payload["tools"][0]["function"]["name"]
        assistant = payload["messages"][1]
        tool = payload["messages"][2]
        assert assistant == {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": provider_tool_name,
                        "arguments": '{"city":"Paris"}',
                    },
                }
            ],
        }
        assert tool == {
            "role": "tool",
            "content": "18 C",
            "name": provider_tool_name,
            "tool_call_id": "call-1",
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"choices": [{"delta": {"content": "It is 18 C"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ),
        )

    request = ModelRequest.create(
        profile_id="fixture",
        messages=(
            ModelMessage.create(role=ModelRole.USER, content="Weather in Paris"),
            ModelMessage.create(
                role=ModelRole.ASSISTANT,
                content="",
                tool_calls=(
                    ModelToolCall.create(
                        tool_call_id="call-1",
                        name="weather.get",
                        arguments='{"city":"Paris"}',
                    ),
                ),
            ),
            ModelMessage.create(
                role=ModelRole.TOOL,
                content="18 C",
                name="weather.get",
                tool_call_id="call-1",
            ),
        ),
        tools=_request().tools,
        required_capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        max_output_tokens=128,
    )
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    deltas = tuple(provider.stream(request, CancellationToken()))

    assert "".join(delta.text or "" for delta in deltas) == "It is 18 C"
    assert deltas[-1].kind is ModelDeltaKind.DONE


def test_stream_round_trips_namespaced_tools_through_safe_unique_aliases() -> None:
    canonical_names = (
        "skill.design-taste-frontend",
        "execution.plan",
        "mcp.playwright.browser_navigate",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        provider_names = [item["function"]["name"] for item in payload["tools"]]
        assert len(set(provider_names)) == len(canonical_names)
        assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in provider_names)
        assert not set(provider_names).intersection(canonical_names)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "functions.skill.design-taste-frontend:0",
                                        "function": {
                                            "name": provider_names[0],
                                            "arguments": '{"public_intent":"Use design guidance"}',
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "functions.execution.plan:1",
                                        "function": {
                                            "name": provider_names[1],
                                            "arguments": '{"files":[]}',
                                        },
                                    },
                                    {
                                        "index": 2,
                                        "id": "functions.mcp.playwright.browser_navigate:2",
                                        "function": {
                                            "name": provider_names[2],
                                            "arguments": '{"url":"https://example.com"}',
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                "[DONE]",
            ),
        )

    request = ModelRequest.create(
        profile_id="fixture",
        messages=(ModelMessage.create(role=ModelRole.USER, content="Create a web page"),),
        tools=tuple(
            ModelTool.create(
                name=name,
                description=f"Use {name}",
                input_schema={"type": "object", "properties": {}},
            )
            for name in canonical_names
        ),
        required_capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        max_output_tokens=128,
    )
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    deltas = tuple(provider.stream(request, CancellationToken()))
    tool_deltas = [delta for delta in deltas if delta.kind is ModelDeltaKind.TOOL_CALL]

    assert [delta.tool_name for delta in tool_deltas] == list(canonical_names)


def test_stream_serializes_task_bound_png_as_an_inline_multimodal_part() -> None:
    png = bytearray(b"\x89PNG\r\n\x1a\nfixture")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this state"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgpmaXh0dXJl",
                            "detail": "auto",
                        },
                    },
                ],
            }
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"choices": [{"delta": {"content": "Visible"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ),
        )

    image = ModelImage.create(
        task_id=UUID("0198f4de-0114-7000-8000-000000000003"),
        media_type="image/png",
        data=memoryview(png),
        content_hash="a" * 64,
        width=1,
        height=1,
        label="untrusted_screen_content",
        untrusted_data=True,
    )
    request = ModelRequest.create(
        profile_id="fixture",
        messages=(
            ModelMessage.create(
                role=ModelRole.USER,
                content="Inspect this state",
                images=(image,),
            ),
        ),
        tools=(),
        required_capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.VISION}),
        max_output_tokens=128,
    )
    provider = OpenAICompatibleProvider(
        profile=_profile(
            credential_ref=None,
            capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.VISION}),
        ),
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        "".join(delta.text or "" for delta in provider.stream(request, CancellationToken()))
        == "Visible"
    )


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (
            httpx.Response(
                429,
                json={"error": {"message": "upstream-secret-detail"}},
            ),
            ProviderRateLimitError,
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"data: {not-json}\n\n",
            ),
            ProviderProtocolError,
        ),
    ],
)
def test_stream_sanitizes_upstream_and_malformed_frame_errors(
    response: httpx.Response,
    expected_error: type[Exception],
) -> None:
    provider = OpenAICompatibleProvider(
        profile=_profile(),
        secret=SecretValue.from_text("fixture-secret"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: response)),
    )

    with pytest.raises(expected_error) as captured:
        tuple(provider.stream(_request(), CancellationToken()))

    message = str(captured.value)
    assert "fixture-secret" not in message
    assert "upstream-secret-detail" not in message


def test_stream_translates_timeout_without_leaking_request() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request included fixture-secret", request=request)

    provider = OpenAICompatibleProvider(
        profile=_profile(),
        secret=SecretValue.from_text("fixture-secret"),
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )

    with pytest.raises(ProviderTimeoutError, match="timed out") as captured:
        tuple(provider.stream(_request(), CancellationToken()))
    assert "fixture-secret" not in str(captured.value)


@pytest.mark.parametrize(
    "content",
    [
        b'{"error":{"message":"not actually an SSE response"}}',
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
    ],
)
def test_stream_rejects_non_sse_and_truncated_success_responses(
    content: bytes,
) -> None:
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
        ),
    )

    with pytest.raises(ProviderProtocolError, match="completion"):
        tuple(provider.stream(_request(), CancellationToken()))


class _TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def test_stream_reads_streaming_error_before_safe_classification() -> None:
    body = _TrackingStream(
        (b'{"error":{"message":"upstream-secret-detail"}}',)
    )
    provider = OpenAICompatibleProvider(
        profile=_profile(),
        secret=SecretValue.from_text("fixture-secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(429, stream=body)
            )
        ),
    )

    with pytest.raises(ProviderRateLimitError, match="rate limit") as captured:
        tuple(provider.stream(_request(), CancellationToken()))

    assert "upstream-secret-detail" not in str(captured.value)
    assert body.closed is True


def test_mid_stream_cancellation_closes_response_without_more_deltas() -> None:
    body = _TrackingStream(
        (
            b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
        )
    )
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=body,
                )
            )
        ),
    )
    cancellation = CancellationToken()
    stream = provider.stream(_request(), cancellation)

    assert next(stream).text == "first"
    cancellation.cancel()
    with pytest.raises(Exception, match="cancelled"):
        next(stream)
    assert body.closed is True


def test_health_does_not_require_a_generation_request() -> None:
    provider = OpenAICompatibleProvider(
        profile=_profile(credential_ref=None),
        secret=None,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200 if request.url.path == "/v1/models" else 404,
                    json={"data": []},
                )
            )
        ),
    )

    health = provider.health()

    assert health.status.value == "available"
    assert health.error_code is None
