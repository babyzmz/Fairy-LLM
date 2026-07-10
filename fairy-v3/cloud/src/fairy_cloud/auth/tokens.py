from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any

import jwt

from fairy_cloud.auth.jwks import KeyProvider
from fairy_cloud.auth.models import AuthenticationError, RequestIdentity


class DenyAllAuthenticator:
    async def authenticate(
        self,
        *,
        authorization: str | None,
        device_id: str | None,
    ) -> RequestIdentity:
        del authorization, device_id
        raise AuthenticationError("authentication is required")


class StaticTokenAuthenticator:
    """Explicit deterministic authenticator for contract tests and local Compose."""

    def __init__(self, identities: Mapping[str, RequestIdentity]) -> None:
        self._identities = dict(identities)

    async def authenticate(
        self,
        *,
        authorization: str | None,
        device_id: str | None,
    ) -> RequestIdentity:
        token = _bearer_token(authorization)
        identity = next(
            (
                candidate
                for expected, candidate in self._identities.items()
                if hmac.compare_digest(token, expected)
            ),
            None,
        )
        if (
            identity is None
            or not device_id
            or not hmac.compare_digest(identity.device_id, device_id)
        ):
            raise AuthenticationError("token or device identity is invalid")
        return identity


class OidcTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_provider: KeyProvider,
        required_scopes: frozenset[str] = frozenset(),
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        leeway_seconds: int = 30,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._key_provider = key_provider
        self._required_scopes = required_scopes
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds

    async def authenticate(
        self,
        *,
        authorization: str | None,
        device_id: str | None,
    ) -> RequestIdentity:
        token = _bearer_token(authorization)
        if len(token) > 16_384:
            raise AuthenticationError("access token is too large")
        try:
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg", ""))
            key_id = str(header.get("kid", ""))
            if algorithm not in self._algorithms or not key_id:
                raise AuthenticationError("token signing metadata is invalid")
            signing_key = await self._key_provider.key_for(key_id)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "device_id"]},
            )
        except AuthenticationError:
            raise
        except jwt.PyJWTError as error:
            raise AuthenticationError("access token validation failed") from error

        user_id = _required_string_claim(claims, "sub")
        token_device_id = _required_string_claim(claims, "device_id")
        if not device_id or not hmac.compare_digest(token_device_id, device_id):
            raise AuthenticationError("token is bound to a different device")
        scopes = _scopes(claims)
        if not self._required_scopes.issubset(scopes):
            raise AuthenticationError("access token is missing required scopes")
        return RequestIdentity(
            user_id=user_id,
            device_id=token_device_id,
            scopes=scopes,
        )


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError("Bearer token is required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authorization must use Bearer authentication")
    return token.strip()


def _required_string_claim(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise AuthenticationError(f"token claim is required: {name}")
    return value


def _scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    raw_scope = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw_scope, str):
        return frozenset(raw_scope.split())
    if isinstance(raw_scope, list) and all(isinstance(item, str) for item in raw_scope):
        return frozenset(raw_scope)
    raise AuthenticationError("token scopes are malformed")
