from __future__ import annotations

from collections.abc import Iterator
from threading import Event, Lock
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


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderContextLengthError(ProviderError):
    pass


class ProviderContentRejectedError(ProviderError):
    pass


class ProviderNetworkError(ProviderError):
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
    __slots__ = ("_event", "_lock", "_reason")

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def is_interrupted(self) -> bool:
        with self._lock:
            return self._reason == "worker_interrupted"

    def cancel(self) -> None:
        self._set_reason("user_cancelled")

    def interrupt(self) -> None:
        self._set_reason("worker_interrupted")

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ProviderCancelledError("model request was cancelled")

    def _set_reason(self, reason: str) -> None:
        with self._lock:
            if self._reason is not None:
                return
            self._reason = reason
            self._event.set()


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
