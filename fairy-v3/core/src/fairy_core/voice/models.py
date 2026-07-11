from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise

MAX_INPUT_AUDIO_BYTES = 20 * 1024 * 1024
MAX_SYNTHESIZED_AUDIO_BYTES = 20 * 1024 * 1024
MAX_SYNTHESIS_TEXT_CHARACTERS = 4_096
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VOICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class AudioMediaType(StrEnum):
    WEBM = "audio/webm"
    WAV = "audio/wav"
    MPEG = "audio/mpeg"
    MP4 = "audio/mp4"
    OGG = "audio/ogg"


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    index: int
    text: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("transcript segment index must be non-negative")
        if not self.text.strip() or len(self.text) > 20_000:
            raise ValueError("transcript segment text is invalid")
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("transcript segment timing must be finite")
        if self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ValueError("transcript segment timing is invalid")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    profile_id: str
    media_type: AudioMediaType
    audio: bytes
    language: str | None

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        media_type: AudioMediaType,
        audio: bytes,
        language: str | None,
    ) -> TranscriptionRequest:
        normalized_profile = _profile_id(profile_id)
        normalized_audio = bytes(audio)
        if not normalized_audio:
            raise ValueError("transcription audio is required")
        if len(normalized_audio) > MAX_INPUT_AUDIO_BYTES:
            raise ValueError("transcription audio exceeds the size limit")
        normalized_language = _language(language)
        return cls(
            profile_id=normalized_profile,
            media_type=media_type,
            audio=normalized_audio,
            language=normalized_language,
        )

    def for_profile(self, profile_id: str) -> TranscriptionRequest:
        return replace(self, profile_id=_profile_id(profile_id))


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    profile_id: str
    text: str
    language: str | None
    segments: tuple[TranscriptSegment, ...]

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        text: str,
        language: str | None,
        segments: tuple[TranscriptSegment, ...],
    ) -> TranscriptionResult:
        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > 100_000:
            raise ValueError("transcription text is invalid")
        expected_indexes = tuple(range(len(segments)))
        if tuple(segment.index for segment in segments) != expected_indexes:
            raise ValueError("transcript segment indexes must be contiguous")
        for previous, current in pairwise(segments):
            if current.start_seconds < previous.end_seconds:
                raise ValueError("transcript segments must not overlap")
        return cls(
            profile_id=_profile_id(profile_id),
            text=normalized_text,
            language=_language(language),
            segments=tuple(segments),
        )


@dataclass(frozen=True, slots=True)
class VoiceSynthesisRequest:
    profile_id: str
    voice: str
    text: str

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        voice: str,
        text: str,
    ) -> VoiceSynthesisRequest:
        normalized_voice = voice.strip()
        if not _VOICE_NAME.fullmatch(normalized_voice):
            raise ValueError("voice name is invalid")
        if not text.strip() or len(text) > MAX_SYNTHESIS_TEXT_CHARACTERS:
            raise ValueError("synthesis text is invalid")
        return cls(
            profile_id=_profile_id(profile_id),
            voice=normalized_voice,
            text=text,
        )

    def for_profile(self, profile_id: str) -> VoiceSynthesisRequest:
        return replace(self, profile_id=_profile_id(profile_id))


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    profile_id: str
    wav: bytes
    sample_rate: int
    channels: int
    frames: int
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        wav: bytes,
        sample_rate: int,
        channels: int,
        frames: int,
    ) -> SynthesizedAudio:
        content = bytes(wav)
        metadata = parse_pcm_wav(content)
        expected = (sample_rate, channels, frames)
        if metadata != expected:
            raise ValueError("WAV metadata does not match the declared metadata")
        return cls(
            profile_id=_profile_id(profile_id),
            wav=content,
            sample_rate=sample_rate,
            channels=channels,
            frames=frames,
            content_hash=hashlib.sha256(content).hexdigest(),
        )


def parse_pcm_wav(content: bytes) -> tuple[int, int, int]:
    if len(content) < 44 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise ValueError("audio must be a RIFF WAVE file")
    if len(content) > MAX_SYNTHESIZED_AUDIO_BYTES:
        raise ValueError("synthesized WAV exceeds the size limit")
    declared_size = int.from_bytes(content[4:8], "little") + 8
    if declared_size != len(content):
        raise ValueError("RIFF size does not match WAV content")
    offset = 12
    fmt: bytes | None = None
    data: bytes | None = None
    while offset + 8 <= len(content):
        chunk_id = content[offset : offset + 4]
        chunk_size = int.from_bytes(content[offset + 4 : offset + 8], "little")
        start = offset + 8
        end = start + chunk_size
        if end > len(content):
            raise ValueError("WAV chunk exceeds RIFF bounds")
        if chunk_id == b"fmt " and fmt is None:
            fmt = content[start:end]
        elif chunk_id == b"data" and data is None:
            data = content[start:end]
        offset = end + (chunk_size % 2)
    if offset != len(content) or fmt is None or data is None or len(fmt) < 16:
        raise ValueError("WAV requires bounded fmt and data chunks")
    audio_format = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    sample_rate = int.from_bytes(fmt[4:8], "little")
    byte_rate = int.from_bytes(fmt[8:12], "little")
    block_align = int.from_bytes(fmt[12:14], "little")
    bits_per_sample = int.from_bytes(fmt[14:16], "little")
    if audio_format != 1 or bits_per_sample != 16:
        raise ValueError("WAV must contain 16-bit PCM audio")
    if channels not in {1, 2} or not 8_000 <= sample_rate <= 48_000:
        raise ValueError("WAV channel count or sample rate is unsupported")
    expected_align = channels * 2
    if block_align != expected_align or byte_rate != sample_rate * expected_align:
        raise ValueError("WAV PCM rate metadata is inconsistent")
    if len(data) % block_align != 0:
        raise ValueError("WAV data is not aligned to complete frames")
    return sample_rate, channels, len(data) // block_align


def _profile_id(value: str) -> str:
    normalized = value.strip()
    if not _PROFILE_ID.fullmatch(normalized):
        raise ValueError("voice profile id is invalid")
    return normalized


def _language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not _LANGUAGE.fullmatch(normalized):
        raise ValueError("transcription language is invalid")
    return normalized


__all__ = [
    "MAX_INPUT_AUDIO_BYTES",
    "MAX_SYNTHESIS_TEXT_CHARACTERS",
    "MAX_SYNTHESIZED_AUDIO_BYTES",
    "AudioMediaType",
    "SynthesizedAudio",
    "TranscriptSegment",
    "TranscriptionRequest",
    "TranscriptionResult",
    "VoiceSynthesisRequest",
    "parse_pcm_wav",
]
