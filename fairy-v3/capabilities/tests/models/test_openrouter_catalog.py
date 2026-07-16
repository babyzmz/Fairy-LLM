from __future__ import annotations

import httpx
import pytest
from fairy_core.model_catalog.models import ProviderCredentialStatus
from fairy_core.model_catalog.ports import ModelCatalogSourceError
from fairy_core.providers import SecretValue

from fairy_capabilities.models.openrouter_catalog import OpenRouterCatalogSource


def test_catalog_source_merges_dedicated_endpoint_metadata_without_unknown_models() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer test-secret"
        path = request.url.path
        query = request.url.query.decode()
        if path == "/api/v1/key":
            return httpx.Response(200, json={"data": {"is_free_tier": False}})
        if path == "/api/v1/models" and query == "output_modalities=text":
            return httpx.Response(
                200,
                json={
                    "data": [
                        _chat_model("deepseek/deepseek-v4-pro"),
                        _chat_model("z-ai/glm-5.2"),
                        _chat_model("moonshotai/kimi-k2.7-code"),
                        _chat_model("nvidia/nemotron-3-ultra-550b-a55b:free"),
                        _chat_model("qwen/qwen3-coder:free"),
                        _chat_model("untrusted/extra-model"),
                    ]
                },
            )
        if path == "/api/v1/images/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/gemini-3.1-flash-lite-image",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "supported_parameters": {
                                "resolution": {"type": "enum", "values": ["1K"]},
                                "aspect_ratio": {
                                    "type": "enum",
                                    "values": ["1:1", "16:9"],
                                },
                            },
                            "supports_streaming": False,
                            "endpoints": (
                                "/api/v1/images/models/google/gemini-3.1-flash-lite-image/endpoints"
                            ),
                        }
                    ]
                },
            )
        if path.endswith("gemini-3.1-flash-lite-image/endpoints"):
            return httpx.Response(
                200,
                json={
                    "id": "google/gemini-3.1-flash-lite-image",
                    "endpoints": [
                        {
                            "pricing": [
                                {
                                    "billable": "output_image",
                                    "unit": "image",
                                    "cost_usd": 0.05,
                                }
                            ]
                        }
                    ],
                },
            )
        if path == "/api/v1/models" and query == "output_modalities=audio":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/lyria-3-pro-preview",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["audio"],
                            },
                            "pricing": {"request": "0.08"},
                        }
                    ]
                },
            )
        if path == "/api/v1/videos/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "bytedance/seedance-2.0",
                            "supported_resolutions": ["720p", "1080p"],
                            "supported_aspect_ratios": ["16:9", "9:16"],
                            "pricing_skus": {"per-video-second": "0.1"},
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    source = OpenRouterCatalogSource(
        base_url="https://openrouter.ai/api/v1",
        secret=SecretValue.from_text("test-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = source.fetch()

    assert result.credential_status is ProviderCredentialStatus.CONFIGURED
    assert len(result.entries) == 8
    assert "untrusted/extra-model" not in {entry.model_id for entry in result.entries}
    image = next(
        entry for entry in result.entries if entry.model_id == "google/gemini-3.1-flash-lite-image"
    )
    assert image.supported_resolutions == ("1K",)
    assert image.supported_aspect_ratios == ("1:1", "16:9")
    assert image.prices[0].cost_usd == "0.05"
    video = next(entry for entry in result.entries if entry.model_id == "bytedance/seedance-2.0")
    assert video.supported_resolutions == ("720p", "1080p")
    assert len(requests) == 6


def test_catalog_source_classifies_invalid_credentials_without_exposing_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, text="secret diagnostic")
        )
    )
    source = OpenRouterCatalogSource(
        base_url="https://openrouter.ai/api/v1",
        secret=SecretValue.from_text("test-secret"),
        client=client,
    )

    with pytest.raises(ModelCatalogSourceError) as error:
        source.fetch()
    assert error.value.error_code == "CREDENTIAL_INVALID"
    assert error.value.credential_status is ProviderCredentialStatus.INVALID
    assert "secret diagnostic" not in str(error.value)


def test_catalog_source_requires_official_openrouter_origin() -> None:
    with pytest.raises(ValueError, match="official HTTPS"):
        OpenRouterCatalogSource(
            base_url="http://localhost:8080/api/v1",
            secret=SecretValue.from_text("test-secret"),
        )


def test_catalog_source_rejects_oversized_response_before_json_decode() -> None:
    oversized = b'{"data":[' + b" " * (4 * 1024 * 1024) + b"]}"
    source = OpenRouterCatalogSource(
        base_url="https://openrouter.ai/api/v1",
        secret=SecretValue.from_text("test-secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=oversized))
        ),
    )

    with pytest.raises(ModelCatalogSourceError) as error:
        source.fetch()
    assert error.value.error_code == "MODEL_CATALOG_RESPONSE_TOO_LARGE"


def _chat_model(model_id: str) -> dict[str, object]:
    return {
        "id": model_id,
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
        "context_length": 131_072,
        "top_provider": {"max_completion_tokens": 16_384},
        "supported_parameters": ["tools", "response_format"],
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    }
