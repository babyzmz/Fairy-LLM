from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from fairy_core.providers import (
    CancellationToken,
    ProviderCapability,
    ProviderProtocolError,
    ProviderUnavailableError,
)
from fairy_core.voice.models import (
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    VoiceSynthesisRequest,
)
from fairy_core.voice.ports import VoiceProvider

RequestT = TypeVar("RequestT", TranscriptionRequest, VoiceSynthesisRequest)
ResultT = TypeVar("ResultT", TranscriptionResult, SynthesizedAudio)


class VoiceRegistry:
    def __init__(self, providers: Iterable[VoiceProvider] = ()) -> None:
        provider_list = tuple(providers)
        self._providers = {provider.profile.id: provider for provider in provider_list}
        if len(self._providers) != len(provider_list):
            raise ValueError("duplicate voice provider profile id")

    def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def transcribe(
        self,
        request: TranscriptionRequest,
        cancellation: CancellationToken,
    ) -> TranscriptionResult:
        return self._execute(
            request=request,
            capability=ProviderCapability.STT,
            cancellation=cancellation,
            operation=lambda provider, selected: provider.transcribe(
                selected,
                cancellation,
            ),
        )

    def synthesize(
        self,
        request: VoiceSynthesisRequest,
        cancellation: CancellationToken,
    ) -> SynthesizedAudio:
        return self._execute(
            request=request,
            capability=ProviderCapability.TTS,
            cancellation=cancellation,
            operation=lambda provider, selected: provider.synthesize(
                selected,
                cancellation,
            ),
        )

    def _execute(
        self,
        *,
        request: RequestT,
        capability: ProviderCapability,
        cancellation: CancellationToken,
        operation: Callable[[VoiceProvider, RequestT], ResultT],
    ) -> ResultT:
        cancellation.raise_if_cancelled()
        provider = self._require_provider(request.profile_id)
        self._validate_provider(provider, capability)
        try:
            result = operation(provider, request)
        except ProviderUnavailableError:
            fallback_id = provider.profile.fallback_profile_id
            if fallback_id is None:
                raise
            cancellation.raise_if_cancelled()
            provider = self._require_provider(fallback_id)
            self._validate_provider(provider, capability)
            result = operation(provider, request.for_profile(fallback_id))
        cancellation.raise_if_cancelled()
        if result.profile_id != provider.profile.id:
            raise ProviderProtocolError("voice result profile does not match the selected provider")
        return result

    def _require_provider(self, profile_id: str) -> VoiceProvider:
        try:
            return self._providers[profile_id]
        except KeyError as error:
            raise ProviderUnavailableError(
                f"voice provider profile {profile_id!r} is unavailable"
            ) from error

    @staticmethod
    def _validate_provider(
        provider: VoiceProvider,
        capability: ProviderCapability,
    ) -> None:
        profile = provider.profile
        if not profile.enabled:
            raise ProviderUnavailableError(f"voice provider profile {profile.id!r} is disabled")
        if capability not in profile.capabilities:
            raise ProviderUnavailableError(
                f"voice provider profile {profile.id!r} lacks {capability.value}"
            )
        if profile.credential_ref is not None and not provider.credential_configured:
            raise ProviderUnavailableError(
                f"voice provider profile {profile.id!r} has no configured credential"
            )


__all__ = ["VoiceRegistry"]
