from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from fairy_core.providers.models import (
    ModelDelta,
    ModelDeltaKind,
    ModelRequest,
    ProviderAttemptEvent,
    ProviderAttemptStatus,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderProfile,
    PublicProviderProfile,
)
from fairy_core.providers.ports import (
    CancellationToken,
    ModelProvider,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

AttemptObserver = Callable[[ProviderAttemptEvent], None]
AttemptValidator = Callable[[tuple[ModelDelta, ...]], None]


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

    def profile(self, profile_id: str) -> ProviderProfile:
        return self._require_provider(profile_id).profile

    def profile_for_model(self, model_id: str) -> ProviderProfile:
        matches = tuple(
            provider.profile
            for provider in self._providers.values()
            if provider.profile.model_id == model_id
        )
        if len(matches) != 1:
            raise ProviderUnavailableError(
                f"model {model_id!r} does not resolve to one provider profile"
            )
        return matches[0]

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
        *,
        on_attempt: AttemptObserver | None = None,
        attempt_validator: AttemptValidator | None = None,
    ) -> Iterator[ModelDelta]:
        cancellation.raise_if_cancelled()
        primary = self._require_provider(request.profile_id)
        candidates = [primary, primary]
        fallback_ids = request.fallback_profile_ids
        if (
            request.allow_profile_fallback
            and not fallback_ids
            and primary.profile.fallback_profile_id is not None
        ):
            fallback_ids = (primary.profile.fallback_profile_id,)
        candidates.extend(self._require_provider(profile_id) for profile_id in fallback_ids)
        last_error: ProviderError | None = None
        for attempt_number, provider in enumerate(candidates, start=1):
            cancellation.raise_if_cancelled()
            attempt_request = request.for_profile(provider.profile.id)
            self._validate_request(provider, attempt_request)
            _notify_attempt(
                on_attempt,
                ProviderAttemptEvent(
                    profile_id=provider.profile.id,
                    model_id=provider.profile.model_id,
                    endpoint_kind="chat",
                    model_role=request.model_role,
                    attempt_number=attempt_number,
                    status=ProviderAttemptStatus.STARTED,
                ),
            )
            substantive_emitted = False
            buffered_deltas: list[ModelDelta] | None = [] if attempt_validator is not None else None
            buffered_tool_deltas: list[ModelDelta] = []
            buffered_done_deltas: list[ModelDelta] = []
            usage: dict[str, int] = {}
            usage_cost: str | None = None
            try:
                for delta in self._validated_stream(provider, attempt_request, cancellation):
                    if delta.kind is ModelDeltaKind.TEXT:
                        substantive_emitted = True
                    if delta.kind is ModelDeltaKind.USAGE:
                        for key, value in delta.usage.items():
                            usage[key] = usage.get(key, 0) + value
                        if delta.usage_cost is not None:
                            usage_cost = delta.usage_cost
                    if buffered_deltas is None:
                        if delta.kind is ModelDeltaKind.TOOL_CALL:
                            # Tool calls are streamed as argument fragments. Do not expose a
                            # candidate until the provider has completed the attempt; otherwise a
                            # transport failure would make retry concatenate two different calls.
                            buffered_tool_deltas.append(delta)
                        elif delta.kind is ModelDeltaKind.DONE:
                            buffered_done_deltas.append(delta)
                        else:
                            yield delta
                    else:
                        buffered_deltas.append(delta)
                if attempt_validator is not None:
                    assert buffered_deltas is not None
                    attempt_validator(tuple(buffered_deltas))
                _notify_attempt(
                    on_attempt,
                    ProviderAttemptEvent(
                        profile_id=provider.profile.id,
                        model_id=provider.profile.model_id,
                        endpoint_kind="chat",
                        model_role=request.model_role,
                        attempt_number=attempt_number,
                        status=ProviderAttemptStatus.SUCCEEDED,
                        usage=usage,
                        usage_cost=usage_cost,
                    ),
                )
                if buffered_deltas is not None:
                    yield from buffered_deltas
                else:
                    yield from buffered_tool_deltas
                    yield from buffered_done_deltas
                return
            except ProviderCancelledError:
                _notify_attempt(
                    on_attempt,
                    ProviderAttemptEvent(
                        profile_id=provider.profile.id,
                        model_id=provider.profile.model_id,
                        endpoint_kind="chat",
                        model_role=request.model_role,
                        attempt_number=attempt_number,
                        status=ProviderAttemptStatus.FAILED,
                        error_category=ProviderErrorCategory.CANCELLED,
                        usage=usage,
                        usage_cost=usage_cost,
                    ),
                )
                raise
            except ProviderError as error:
                last_error = error
                category = _error_category(error)
                _notify_attempt(
                    on_attempt,
                    ProviderAttemptEvent(
                        profile_id=provider.profile.id,
                        model_id=provider.profile.model_id,
                        endpoint_kind="chat",
                        model_role=request.model_role,
                        attempt_number=attempt_number,
                        status=ProviderAttemptStatus.FAILED,
                        error_category=category,
                        usage=usage,
                        usage_cost=usage_cost,
                    ),
                )
                if (substantive_emitted and attempt_validator is None) or not _retryable(category):
                    raise
        assert last_error is not None
        raise last_error

    def stream_once(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        """Run exactly one provider attempt without fallback or automatic retry."""

        cancellation.raise_if_cancelled()
        provider = self._require_provider(request.profile_id)
        self._validate_request(provider, request)
        yield from self._validated_stream(provider, request, cancellation)

    @staticmethod
    def _validated_stream(
        provider: ModelProvider,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        expected_sequence = 1
        for delta in provider.stream(request, cancellation):
            cancellation.raise_if_cancelled()
            if delta.profile_id != provider.profile.id:
                raise ProviderProtocolError(
                    "provider delta profile does not match the selected profile"
                )
            if delta.sequence != expected_sequence:
                raise ProviderProtocolError("provider delta sequence is not contiguous")
            expected_sequence += 1
            yield delta

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


def _notify_attempt(observer: AttemptObserver | None, event: ProviderAttemptEvent) -> None:
    if observer is not None:
        observer(event)


def _error_category(error: ProviderError) -> ProviderErrorCategory:
    if isinstance(error, ProviderAuthenticationError):
        return ProviderErrorCategory.AUTHENTICATION
    if isinstance(error, ProviderRateLimitError):
        return ProviderErrorCategory.RATE_LIMIT
    if isinstance(error, ProviderTimeoutError):
        return ProviderErrorCategory.TIMEOUT
    if isinstance(error, ProviderProtocolError):
        return ProviderErrorCategory.PROTOCOL
    if isinstance(error, ProviderContextLengthError):
        return ProviderErrorCategory.CONTEXT_LENGTH
    if isinstance(error, ProviderContentRejectedError):
        return ProviderErrorCategory.CONTENT_REJECTED
    if isinstance(error, ProviderNetworkError):
        return ProviderErrorCategory.NETWORK
    if isinstance(error, ProviderUnavailableError):
        return ProviderErrorCategory.UNAVAILABLE
    return ProviderErrorCategory.UNKNOWN


def _retryable(category: ProviderErrorCategory) -> bool:
    return category in {
        ProviderErrorCategory.RATE_LIMIT,
        ProviderErrorCategory.TIMEOUT,
        ProviderErrorCategory.PROTOCOL,
        ProviderErrorCategory.NETWORK,
        ProviderErrorCategory.UNAVAILABLE,
    }
