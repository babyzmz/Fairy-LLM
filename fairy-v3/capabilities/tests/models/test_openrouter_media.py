from __future__ import annotations

import base64
import json

import httpx
import pytest
from fairy_core.media.ports import (
    ImageGenerationRequest,
    MediaProviderVideoStatus,
    MusicGenerationRequest,
    VideoGenerationRequest,
)
from fairy_core.providers import (
    CancellationToken,
    ProviderProtocolError,
    ProviderUnavailableError,
    SecretValue,
)

from fairy_capabilities.models.openrouter_media import OpenRouterMediaProvider


def _provider(handler) -> OpenRouterMediaProvider:
    return OpenRouterMediaProvider(
        base_url="https://openrouter.ai/api/v1",
        secret=SecretValue.from_text("test-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_image_generation_accepts_only_inline_trusted_bytes() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/images"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.headers["idempotency-key"] == "image:1"
        payload = json.loads(request.content)
        assert payload["model"] == "google/gemini-3.1-flash-lite-image"
        assert payload["provider"]["data_collection"] == "deny"
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
                "usage": {"cost": "0.02"},
            },
        )

    result = _provider(handler).generate_image(
        ImageGenerationRequest(
            model_id="google/gemini-3.1-flash-lite-image",
            prompt="A precise image",
            size="1024x1024",
            aspect_ratio="1:1",
            seed=7,
            idempotency_key="image:1",
            zero_data_retention=True,
        ),
        CancellationToken(),
    )

    assert result.content == png
    assert result.media_type == "image/png"
    assert result.usage_cost == "0.02"


def test_image_generation_rejects_provider_urls() -> None:
    provider = _provider(
        lambda _request: httpx.Response(
            200,
            json={"data": [{"url": "https://untrusted.invalid/image.png"}]},
        )
    )

    with pytest.raises(ProviderProtocolError, match="inline"):
        provider.generate_image(
            ImageGenerationRequest(
                model_id="google/gemini-3.1-flash-lite-image",
                prompt="A precise image",
                size="1024x1024",
                aspect_ratio="1:1",
                seed=None,
                idempotency_key="image:url",
                zero_data_retention=False,
            ),
            CancellationToken(),
        )


def test_music_generation_preserves_stream_frame_order() -> None:
    first = base64.b64encode(b"RIFFfirst").decode("ascii")
    second = base64.b64encode(b"second").decode("ascii")
    stream = "\n".join(
        (
            f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{first}"}}}}}}]}}',
            f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{second}"}}}}}}],'
            '"usage":{"cost":"0.11"}}',
            "data: [DONE]",
            "",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "google/lyria-3-pro-preview"
        assert payload["modalities"] == ["text", "audio"]
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    result = _provider(handler).generate_music(
        MusicGenerationRequest(
            model_id="google/lyria-3-pro-preview",
            prompt="A short instrumental cue",
            output_format="wav",
            seed=None,
            idempotency_key="music:1",
            zero_data_retention=False,
        ),
        CancellationToken(),
    )

    assert result.content == b"RIFFfirstsecond"
    assert result.media_type == "audio/wav"
    assert result.usage_cost == "0.11"


def test_video_start_poll_and_download_use_fixed_openrouter_paths() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode("ascii").split("?", 1)[0])
        if request.method == "POST":
            return httpx.Response(202, json={"id": "video/job 1", "status": "pending"})
        if request.url.path.endswith("/content"):
            return httpx.Response(
                200,
                content=b"video-content",
                headers={"content-type": "video/mp4"},
            )
        return httpx.Response(200, json={"id": "video/job 1", "status": "completed"})

    provider = _provider(handler)
    started = provider.start_video(
        VideoGenerationRequest(
            model_id="bytedance/seedance-2.0",
            prompt="A five second motion study",
            duration_seconds=5,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=True,
            seed=2,
            idempotency_key="video:1",
            zero_data_retention=False,
        ),
        CancellationToken(),
    )
    polled = provider.get_video(started.provider_job_id, CancellationToken())
    content = provider.download_video(started.provider_job_id, CancellationToken())

    assert started.status is MediaProviderVideoStatus.PENDING
    assert polled.status is MediaProviderVideoStatus.COMPLETED
    assert content.content == b"video-content"
    assert paths == [
        "/api/v1/videos",
        "/api/v1/videos/video%2Fjob%201",
        "/api/v1/videos/video%2Fjob%201/content",
    ]


def test_video_generation_rejects_zdr_before_network() -> None:
    provider = _provider(lambda _request: pytest.fail("network must not be used"))

    with pytest.raises(ProviderUnavailableError, match="does not support ZDR"):
        provider.start_video(
            VideoGenerationRequest(
                model_id="bytedance/seedance-2.0",
                prompt="A five second motion study",
                duration_seconds=5,
                resolution="720p",
                aspect_ratio="16:9",
                generate_audio=True,
                seed=None,
                idempotency_key="video:zdr",
                zero_data_retention=True,
            ),
            CancellationToken(),
        )


def test_media_provider_requires_configured_credential() -> None:
    provider = OpenRouterMediaProvider(
        base_url="https://openrouter.ai/api/v1",
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: pytest.fail())),
    )

    with pytest.raises(ProviderUnavailableError, match="credential"):
        provider.generate_image(
            ImageGenerationRequest(
                model_id="google/gemini-3.1-flash-lite-image",
                prompt="A precise image",
                size="1024x1024",
                aspect_ratio="1:1",
                seed=None,
                idempotency_key="image:no-secret",
                zero_data_retention=False,
            ),
            CancellationToken(),
        )
