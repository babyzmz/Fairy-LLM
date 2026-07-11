from __future__ import annotations

import pytest

from fairy_core.providers import (
    CancellationToken,
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
    ProviderUnavailableError,
)
from fairy_core.voice import (
    AudioMediaType,
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
    VoiceRegistry,
    VoiceSynthesisRequest,
)


def test_registry_negotiates_stt_tts_and_credentials() -> None:
    provider = FixtureVoiceProvider()
    registry = VoiceRegistry((provider,))
    cancellation = CancellationToken()
    transcript = registry.transcribe(
        TranscriptionRequest.create(
            profile_id="voice",
            media_type=AudioMediaType.WEBM,
            audio=b"recording",
            language=None,
        ),
        cancellation,
    )
    audio = registry.synthesize(
        VoiceSynthesisRequest.create(
            profile_id="voice",
            voice="alloy",
            text="Hello.",
        ),
        cancellation,
    )

    assert transcript.text == "Hello"
    assert audio.frames == 2
    assert provider.transcription_calls == 1
    assert provider.synthesis_calls == 1

    provider.credential_configured = False
    with pytest.raises(ProviderUnavailableError, match="credential"):
        registry.transcribe(
            TranscriptionRequest.create(
                profile_id="voice",
                media_type=AudioMediaType.WEBM,
                audio=b"recording",
                language=None,
            ),
            cancellation,
        )


def test_registry_rejects_unknown_or_incompatible_profiles() -> None:
    provider = FixtureVoiceProvider(
        capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.STT})
    )
    registry = VoiceRegistry((provider,))

    with pytest.raises(ProviderUnavailableError, match="tts"):
        registry.synthesize(
            VoiceSynthesisRequest.create(
                profile_id="voice",
                voice="alloy",
                text="Hello.",
            ),
            CancellationToken(),
        )
    with pytest.raises(ProviderUnavailableError, match="missing"):
        registry.transcribe(
            TranscriptionRequest.create(
                profile_id="missing",
                media_type=AudioMediaType.WEBM,
                audio=b"recording",
                language=None,
            ),
            CancellationToken(),
        )


class FixtureVoiceProvider:
    def __init__(
        self,
        *,
        capabilities: frozenset[ProviderCapability] = frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.STT,
                ProviderCapability.TTS,
            }
        ),
    ) -> None:
        self.profile = ProviderProfile.create(
            profile_id="voice",
            display_name="Voice",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://voice.example.test/v1",
            model_id="audio-model",
            capabilities=capabilities,
            credential_ref="voice-secret",
            fallback_profile_id=None,
            timeout_seconds=30,
            enabled=True,
        )
        self.credential_configured = True
        self.transcription_calls = 0
        self.synthesis_calls = 0

    def transcribe(
        self,
        request: TranscriptionRequest,
        cancellation: CancellationToken,
    ) -> TranscriptionResult:
        cancellation.raise_if_cancelled()
        self.transcription_calls += 1
        return TranscriptionResult.create(
            profile_id=request.profile_id,
            text="Hello",
            language="en",
            segments=(
                TranscriptSegment(
                    index=0,
                    text="Hello",
                    start_seconds=0,
                    end_seconds=0.5,
                ),
            ),
        )

    def synthesize(
        self,
        request: VoiceSynthesisRequest,
        cancellation: CancellationToken,
    ) -> SynthesizedAudio:
        cancellation.raise_if_cancelled()
        self.synthesis_calls += 1
        return SynthesizedAudio.create(
            profile_id=request.profile_id,
            wav=pcm_wav(samples=b"\x00\x00\x01\x00", sample_rate=24_000, channels=1),
            sample_rate=24_000,
            channels=1,
            frames=2,
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
