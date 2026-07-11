from __future__ import annotations

from typing import Protocol

from fairy_core.providers import CancellationToken, ProviderProfile
from fairy_core.voice.models import (
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    VoiceSynthesisRequest,
)


class VoiceProvider(Protocol):
    @property
    def profile(self) -> ProviderProfile: ...

    @property
    def credential_configured(self) -> bool: ...

    def transcribe(
        self,
        request: TranscriptionRequest,
        cancellation: CancellationToken,
    ) -> TranscriptionResult: ...

    def synthesize(
        self,
        request: VoiceSynthesisRequest,
        cancellation: CancellationToken,
    ) -> SynthesizedAudio: ...


__all__ = ["VoiceProvider"]
