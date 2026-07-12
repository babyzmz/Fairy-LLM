from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


class TokenRejectedError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredToken:
    digest: str
    expires_at: float


class OneTimeTokenRegistry:
    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        if not 1.0 <= ttl_seconds <= 300.0:
            raise ValueError("voice token TTL is invalid")
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, RegisteredToken] = {}
        self._lock = threading.Lock()

    def register(self, token: str) -> None:
        digest = _token_digest(token)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if digest in self._tokens:
                raise TokenRejectedError("voice token is already registered")
            self._tokens[digest] = RegisteredToken(
                digest=digest,
                expires_at=now + self._ttl_seconds,
            )

    def consume(self, token: str) -> None:
        digest = _token_digest(token)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            registered = self._tokens.pop(digest, None)
        if registered is None or registered.expires_at <= now:
            raise TokenRejectedError("voice token is missing, expired, or already used")

    def _prune(self, now: float) -> None:
        expired = [digest for digest, token in self._tokens.items() if token.expires_at <= now]
        for digest in expired:
            del self._tokens[digest]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def bearer_token(header: str | None) -> str:
    if header is None or not header.startswith("Bearer "):
        raise TokenRejectedError("voice bearer token is required")
    return header.removeprefix("Bearer ").strip()


def constant_time_token_matches(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _token_digest(token: str) -> str:
    if not 32 <= len(token) <= 256 or any(character.isspace() for character in token):
        raise TokenRejectedError("voice token format is invalid")
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "OneTimeTokenRegistry",
    "TokenRejectedError",
    "bearer_token",
    "constant_time_token_matches",
    "new_token",
]
