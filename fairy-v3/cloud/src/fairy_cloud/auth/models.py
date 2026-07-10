from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AuthenticationError(RuntimeError):
    code = "UNAUTHENTICATED"


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    user_id: str
    device_id: str
    scopes: frozenset[str]


class Authenticator(Protocol):
    async def authenticate(
        self,
        *,
        authorization: str | None,
        device_id: str | None,
    ) -> RequestIdentity: ...
