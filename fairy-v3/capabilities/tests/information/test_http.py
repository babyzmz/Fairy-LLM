from __future__ import annotations

import httpx
import pytest

from fairy_capabilities.information.http import BoundedJsonClient, InformationProviderError


def test_information_http_rejects_oversized_body_before_json_parse() -> None:
    client = BoundedJsonClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"{" + b"x" * 2_048)
            )
        ),
        max_response_bytes=1_024,
        retries=0,
    )

    with pytest.raises(InformationProviderError) as captured:
        client.get_json("https://example.com/data", params={})

    assert captured.value.error_code == "RESPONSE_TOO_LARGE"


def test_information_http_does_not_retry_rate_limit_response() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "private detail"})

    client = BoundedJsonClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retries=2,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(InformationProviderError) as captured:
        client.get_json("https://example.com/data", params={})

    assert captured.value.error_code == "RATE_LIMITED"
    assert calls == 1
    assert "private detail" not in str(captured.value)
