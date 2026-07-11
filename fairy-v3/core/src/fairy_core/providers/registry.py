from __future__ import annotations

from collections.abc import Iterable, Iterator

from fairy_core.providers.models import (
    ModelDelta,
    ModelRequest,
    ProviderHealth,
    PublicProviderProfile,
)
from fairy_core.providers.ports import (
    CancellationToken,
    ModelProvider,
    ProviderUnavailableError,
)


class ProviderRegistry:
    def __init__(self, providers: Iterable[ModelProvider] = ()) -> None:
        provider_list = tuple(providers)
        self._providers = {provider.profile.id: provider for provider in provider_list}
        if len(self._providers) != len(provider_list):
            raise ValueError("duplicate provider profile id")
        self._validate_fallbacks()

    def list_public(self) -> tuple[PublicProviderProfile, ...]:
        return tuple(
            provider.profile.public(
                credential_configured=provider.credential_configured,
            )
            for provider in self._providers.values()
        )

    def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def health(self, profile_id: str | None = None) -> tuple[ProviderHealth, ...]:
        if profile_id is not None:
            return (self._require_provider(profile_id).health(),)
        return tuple(provider.health() for provider in self._providers.values())

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        cancellation.raise_if_cancelled()
        provider = self._require_provider(request.profile_id)
        self._validate_request(provider, request)
        emitted = False
        try:
            for delta in provider.stream(request, cancellation):
                emitted = True
                yield delta
        except ProviderUnavailableError:
            if emitted or provider.profile.fallback_profile_id is None:
                raise
            cancellation.raise_if_cancelled()
            fallback = self._require_provider(provider.profile.fallback_profile_id)
            fallback_request = request.for_profile(fallback.profile.id)
            self._validate_request(fallback, fallback_request)
            yield from fallback.stream(fallback_request, cancellation)

    def _require_provider(self, profile_id: str) -> ModelProvider:
        try:
            return self._providers[profile_id]
        except KeyError as error:
            raise ProviderUnavailableError(
                f"provider profile {profile_id!r} is unavailable"
            ) from error

    @staticmethod
    def _validate_request(provider: ModelProvider, request: ModelRequest) -> None:
        profile = provider.profile
        if not profile.enabled:
            raise ProviderUnavailableError(f"provider profile {profile.id!r} is disabled")
        missing = request.required_capabilities - profile.capabilities
        if missing:
            values = ", ".join(sorted(capability.value for capability in missing))
            raise ProviderUnavailableError(
                f"provider profile {profile.id!r} lacks capabilities: {values}"
            )
        if profile.credential_ref is not None and not provider.credential_configured:
            raise ProviderUnavailableError(
                f"provider profile {profile.id!r} has no configured credential"
            )

    def _validate_fallbacks(self) -> None:
        for profile_id, provider in self._providers.items():
            fallback_id = provider.profile.fallback_profile_id
            if fallback_id is None:
                continue
            fallback = self._providers.get(fallback_id)
            if fallback is None:
                raise ValueError(f"fallback profile {fallback_id!r} does not exist")
            if not provider.profile.capabilities.issubset(fallback.profile.capabilities):
                raise ValueError(f"fallback profile {fallback_id!r} has incompatible capabilities")
            self._validate_no_cycle(profile_id)

    def _validate_no_cycle(self, start: str) -> None:
        visited: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in visited:
                raise ValueError("provider fallback cycle is not allowed")
            visited.add(current)
            current = self._providers[current].profile.fallback_profile_id
