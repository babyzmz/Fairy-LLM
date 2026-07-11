from __future__ import annotations

import hashlib

import pytest

from fairy_core.providers import ProviderCapability, ProviderKind, ProviderProfile
from fairy_core.voice import (
    AudioMediaType,
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
    VoiceSynthesisRequest,
)


def test_transcription_models_require_ordered_bounded_segments() -> None:
    request = TranscriptionRequest.create(
        profile_id="voice",
        media_type=AudioMediaType.WEBM,
        audio=b"recording",
        language="en",
    )
    result = TranscriptionResult.create(
        profile_id="voice",
        text="Hello Fairy",
        language="en",
        segments=(
            TranscriptSegment(index=0, text="Hello", start_seconds=0.0, end_seconds=0.4),
            TranscriptSegment(index=1, text="Fairy", start_seconds=0.4, end_seconds=0.8),
        ),
    )

    assert request.audio == b"recording"
    assert result.text == "Hello Fairy"
    with pytest.raises(ValueError, match="contiguous"):
        TranscriptionResult.create(
            profile_id="voice",
            text="bad",
            language=None,
            segments=(
                TranscriptSegment(
                    index=1,
                    text="bad",
                    start_seconds=0.0,
                    end_seconds=0.1,
                ),
            ),
        )
    with pytest.raises(ValueError, match="overlap"):
        TranscriptionResult.create(
            profile_id="voice",
            text="bad",
            language=None,
            segments=(
                TranscriptSegment(index=0, text="a", start_seconds=0.0, end_seconds=1.0),
                TranscriptSegment(index=1, text="b", start_seconds=0.5, end_seconds=1.5),
            ),
        )


def test_synthesis_models_bind_text_and_validate_pcm_wav_metadata() -> None:
    wav = pcm_wav(samples=b"\x00\x00\x01\x00", sample_rate=24_000, channels=1)
    request = VoiceSynthesisRequest.create(
        profile_id="voice",
        voice="alloy",
        text="Hello.",
    )
    result = SynthesizedAudio.create(
        profile_id="voice",
        wav=wav,
        sample_rate=24_000,
        channels=1,
        frames=2,
    )

    assert request.text == "Hello."
    assert result.content_hash == hashlib.sha256(wav).hexdigest()
    with pytest.raises(ValueError, match="RIFF"):
        SynthesizedAudio.create(
            profile_id="voice",
            wav=b"not-wav",
            sample_rate=24_000,
            channels=1,
            frames=2,
        )
    with pytest.raises(ValueError, match="metadata"):
        SynthesizedAudio.create(
            profile_id="voice",
            wav=wav,
            sample_rate=16_000,
            channels=1,
            frames=2,
        )


def test_voice_profile_requires_the_requested_modality() -> None:
    profile = ProviderProfile.create(
        profile_id="voice",
        display_name="Voice",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://voice.example.test/v1",
        model_id="audio-model",
        capabilities=frozenset(
            {ProviderCapability.STT, ProviderCapability.TTS, ProviderCapability.TEXT}
        ),
        credential_ref="voice-secret",
        fallback_profile_id=None,
        timeout_seconds=30,
        enabled=True,
    )

    assert ProviderCapability.STT in profile.capabilities
    assert ProviderCapability.TTS in profile.capabilities


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
