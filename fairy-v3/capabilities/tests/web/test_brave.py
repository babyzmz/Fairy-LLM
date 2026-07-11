from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fairy_core.providers import SecretValue
from fairy_core.research.models import SearchKind, SearchRequest

from fairy_capabilities.web.brave import (
    BraveSearchAdapter,
    BraveSearchError,
    SearchUnavailableError,
)


def test_web_search_sends_auth_paging_freshness_and_normalizes_hits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/res/v1/web/search"
        assert request.headers["x-subscription-token"] == "fixture-brave-secret"
        assert request.url.params["q"] == "Fairy V3"
        assert request.url.params["count"] == "2"
        assert request.url.params["offset"] == "3"
        assert request.url.params["freshness"] == "pw"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "<strong>Fairy</strong> V3",
                            "url": "https://EXAMPLE.com:443/fairy#fragment",
                            "description": "Project-first <em>assistant</em>",
                            "language": "en",
                            "page_age": "2026-07-10T01:02:03Z",
                            "profile": {"long_name": "Example"},
                        },
                        {
                            "title": "Architecture",
                            "url": "https://docs.example.com/v3",
                            "description": "Command Bus",
                        },
                    ]
                }
            },
            headers={"x-ratelimit-remaining": "1999"},
        )

    adapter = BraveSearchAdapter(
        secret=SecretValue.from_text("fixture-brave-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 7, 11, 3, 0, tzinfo=UTC),
    )

    hits = adapter.search(
        SearchRequest.create(
            query="Fairy V3",
            count=2,
            offset=3,
            freshness="pw",
        )
    )

    assert [(hit.rank, hit.title, hit.url) for hit in hits] == [
        (7, "Fairy V3", "https://example.com/fairy"),
        (8, "Architecture", "https://docs.example.com/v3"),
    ]
    assert hits[0].description == "Project-first assistant"
    assert hits[0].published_at == datetime(2026, 7, 10, 1, 2, 3, tzinfo=UTC)
    assert hits[0].source == "Example"
    assert adapter.health().status == "available"
    assert adapter.health().diagnostics == ("rate_limit_remaining=1999",)


def test_news_search_uses_news_endpoint_and_top_level_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/res/v1/news/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Fairy released",
                        "url": "https://news.example.com/fairy",
                        "description": "Release notes",
                        "age": "2 hours ago",
                        "meta_url": {"hostname": "news.example.com"},
                    }
                ]
            },
        )

    adapter = BraveSearchAdapter(
        secret=SecretValue.from_text("fixture-brave-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    hits = adapter.search(SearchRequest.create(query="Fairy", kind=SearchKind.NEWS, count=1))

    assert len(hits) == 1
    assert hits[0].provider == "brave_news"
    assert hits[0].source == "news.example.com"
    assert hits[0].published_at is None


def test_no_key_is_unavailable_without_making_a_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    adapter = BraveSearchAdapter(
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    health = adapter.health()

    assert health.status == "unavailable"
    assert health.error_code == "CAPABILITY_NOT_AVAILABLE"
    with pytest.raises(SearchUnavailableError, match="unavailable"):
        adapter.search(SearchRequest.create(query="Fairy"))
    assert called is False


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(401, json={"message": "fixture-brave-secret"}), "AUTH_REJECTED"),
        (httpx.Response(429, json={"message": "fixture-brave-secret"}), "RATE_LIMITED"),
        (httpx.Response(503, json={"message": "fixture-brave-secret"}), "UPSTREAM_ERROR"),
        (
            httpx.Response(
                200,
                content=json.dumps({"web": {"results": "wrong"}}).encode(),
            ),
            "PROTOCOL_ERROR",
        ),
    ],
)
def test_search_sanitizes_provider_and_malformed_payload_errors(
    response: httpx.Response,
    expected_code: str,
) -> None:
    adapter = BraveSearchAdapter(
        secret=SecretValue.from_text("fixture-brave-secret"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: response)),
    )

    with pytest.raises(BraveSearchError) as captured:
        adapter.search(SearchRequest.create(query="Fairy"))

    assert captured.value.error_code == expected_code
    assert "fixture-brave-secret" not in str(captured.value)


def test_search_rejects_malformed_result_without_returning_partial_hits() -> None:
    adapter = BraveSearchAdapter(
        secret=SecretValue.from_text("fixture-brave-secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "web": {
                            "results": [
                                {
                                    "title": "Valid",
                                    "url": "https://example.com/valid",
                                    "description": "valid",
                                },
                                {
                                    "title": "Invalid",
                                    "url": "file:///secret",
                                    "description": "invalid",
                                },
                            ]
                        }
                    },
                )
            )
        ),
    )

    with pytest.raises(BraveSearchError) as captured:
        adapter.search(SearchRequest.create(query="Fairy"))

    assert captured.value.error_code == "PROTOCOL_ERROR"


def test_search_rejects_response_body_above_hard_limit() -> None:
    adapter = BraveSearchAdapter(
        secret=SecretValue.from_text("fixture-brave-secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    content=b"{" + (b"x" * 2_048),
                )
            )
        ),
        max_response_bytes=1_024,
    )

    with pytest.raises(BraveSearchError) as captured:
        adapter.search(SearchRequest.create(query="Fairy"))

    assert captured.value.error_code == "RESPONSE_TOO_LARGE"
