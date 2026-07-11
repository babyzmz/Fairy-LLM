from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Protocol

from fairy_core.providers.models import (
    ModelDelta,
    ModelRequest,
    ProviderHealth,
    ProviderProfile,
)


class ProviderError(RuntimeError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass


class ProviderCancelledError(ProviderError):
    pass


class SecretValue:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    @classmethod
    def from_text(cls, value: str) -> SecretValue:
        normalized = value.strip()
        if not normalized:
            raise ValueError("secret value is required")
        return cls(normalized)

    def reveal(self) -> str:
        return self.__value

    def __str__(self) -> str:
        return "<redacted>"

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


class CancellationToken:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ProviderCancelledError("model request was cancelled")


class ModelProvider(Protocol):
    @property
    def profile(self) -> ProviderProfile: ...

    @property
    def credential_configured(self) -> bool: ...

    def health(self) -> ProviderHealth: ...

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]: ...
