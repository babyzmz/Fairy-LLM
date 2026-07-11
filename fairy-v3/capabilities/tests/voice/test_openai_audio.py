from __future__ import annotations

import json

import httpx
import pytest
from fairy_core.providers import (
    CancellationToken,
    ProviderCancelledError,
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
    ProviderProtocolError,
    ProviderUnavailableError,
    SecretValue,
)
from fairy_core.voice import (
    AudioMediaType,
    TranscriptionRequest,
    VoiceSynthesisRequest,
)

from fairy_capabilities.voice.openai_audio import OpenAIAudioAdapter


def test_transcription_uses_bounded_multipart_and_normalizes_segments() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "text": "Hello Fairy",
                "language": "en",
                "segments": [
                    {"id": 0, "text": "Hello", "start": 0.0, "end": 0.4},
                    {"id": 1, "text": "Fairy", "start": 0.4, "end": 0.8},
                ],
            },
        )

    adapter = audio_adapter(handler)
    result = adapter.transcribe(
        TranscriptionRequest.create(
            profile_id="voice",
            media_type=AudioMediaType.WEBM,
            audio=b"webm-audio",
            language="en",
        ),
        CancellationToken(),
    )

    assert result.text == "Hello Fairy"
    assert tuple(segment.index for segment in result.segments) == (0, 1)
    request = requests[0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer test-secret"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.read()
    assert b'name="model"' in body
    assert b"audio-model" in body
    assert b'name="file"; filename="recording.webm"' in body
    assert b"webm-audio" in body
    assert b"test-secret" not in body


def test_synthesis_requires_pcm_wav_and_reports_exact_metadata() -> None:
    wav = pcm_wav(samples=b"\x00\x00\x01\x00", sample_rate=24_000, channels=1)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=wav, headers={"Content-Type": "audio/wav"})

    result = audio_adapter(handler).synthesize(
        VoiceSynthesisRequest.create(
            profile_id="voice",
            voice="alloy",
            text="Hello Fairy.",
        ),
        CancellationToken(),
    )

    assert (result.sample_rate, result.channels, result.frames) == (24_000, 1, 2)
    request = requests[0]
    assert request.url.path == "/v1/audio/speech"
    assert json.loads(request.read()) == {
        "model": "audio-model",
        "voice": "alloy",
        "input": "Hello Fairy.",
        "response_format": "wav",
    }


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"not-wav", headers={"Content-Type": "audio/wav"}), "RIFF"),
        (
            httpx.Response(200, content=b"audio", headers={"Content-Type": "audio/mpeg"}),
            "media type",
        ),
        (httpx.Response(429, json={"error": {"message": "limited"}}), "429"),
    ],
)
def test_synthesis_rejects_invalid_or_failed_responses(
    response: httpx.Response,
    message: str,
) -> None:
    adapter = audio_adapter(lambda _request: response)
    request = VoiceSynthesisRequest.create(
        profile_id="voice",
        voice="alloy",
        text="Hello.",
    )

    error_type = ProviderProtocolError if response.status_code == 200 else ProviderUnavailableError
    with pytest.raises(error_type, match=message):
        adapter.synthesize(request, CancellationToken())


def test_audio_requests_honor_cancellation_timeout_and_missing_credentials() -> None:
    cancellation = CancellationToken()

    def cancelling_handler(_request: httpx.Request) -> httpx.Response:
        cancellation.cancel()
        return httpx.Response(
            200,
            json={"text": "ignored", "language": "en", "segments": []},
        )

    request = TranscriptionRequest.create(
        profile_id="voice",
        media_type=AudioMediaType.WAV,
        audio=pcm_wav(samples=b"\x00\x00", sample_rate=16_000, channels=1),
        language=None,
    )
    with pytest.raises(ProviderCancelledError):
        audio_adapter(cancelling_handler).transcribe(request, cancellation)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailableError, match="timed out"):
        audio_adapter(timeout_handler).transcribe(request, CancellationToken())

    adapter = OpenAIAudioAdapter(
        profile=voice_profile(),
        secret=None,
        client=httpx.Client(transport=httpx.MockTransport(cancelling_handler)),
    )
    with pytest.raises(ProviderUnavailableError, match="credential"):
        adapter.transcribe(request, CancellationToken())


def test_transcription_rejects_malformed_or_out_of_order_payloads() -> None:
    malformed = audio_adapter(
        lambda _request: httpx.Response(
            200,
            json={
                "text": "Bad",
                "language": "en",
                "segments": [
                    {"id": 2, "text": "Bad", "start": 0, "end": 1},
                ],
            },
        )
    )
    with pytest.raises(ProviderProtocolError, match="contiguous"):
        malformed.transcribe(
            TranscriptionRequest.create(
                profile_id="voice",
                media_type=AudioMediaType.OGG,
                audio=b"ogg-audio",
                language=None,
            ),
            CancellationToken(),
        )


def audio_adapter(handler) -> OpenAIAudioAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIAudioAdapter(
        profile=voice_profile(),
        secret=SecretValue.from_text("test-secret"),
        client=client,
    )


def voice_profile() -> ProviderProfile:
    return ProviderProfile.create(
        profile_id="voice",
        display_name="Voice",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://voice.example.test/v1",
        model_id="audio-model",
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.STT,
                ProviderCapability.TTS,
            }
        ),
        credential_ref="voice-secret",
        fallback_profile_id=None,
        timeout_seconds=30,
        enabled=True,
    )


def pcm_wav(*, samples: bytes, sample_rate: int, channels: int) -> bytes:
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    fmt = (
        (1).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + (16).to_bytes(2, "little")
    )
    body = b"fmt " + len(fmt).to_bytes(4, "little") + fmt
    body += b"data" + len(samples).to_bytes(4, "little") + samples
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WAVE" + body
