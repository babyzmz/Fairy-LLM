from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import jwt

from fairy_cloud.auth.models import AuthenticationError


class KeyProvider(Protocol):
    async def key_for(self, key_id: str) -> Any: ...


class StaticKeyProvider:
    def __init__(self, keys: Mapping[str, Any]) -> None:
        self._keys = dict(keys)

    async def key_for(self, key_id: str) -> Any:
        try:
            return self._keys[key_id]
        except KeyError as error:
            raise AuthenticationError("token signing key is unknown") from error


class RemoteJwksProvider:
    """OIDC discovery and JWKS cache with fail-closed refresh behavior."""

    def __init__(
        self,
        *,
        issuer: str,
        client: httpx.AsyncClient | None = None,
        cache_seconds: int = 300,
        allow_insecure_http: bool = False,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
        self._owns_client = client is None
        self._cache_seconds = cache_seconds
        self._allow_insecure_http = allow_insecure_http
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        _validate_endpoint(self._issuer, allow_insecure_http=allow_insecure_http)

    async def key_for(self, key_id: str) -> Any:
        if not key_id:
            raise AuthenticationError("token key id is required")
        if time.monotonic() >= self._expires_at or key_id not in self._keys:
            async with self._lock:
                if time.monotonic() >= self._expires_at or key_id not in self._keys:
                    await self._refresh()
        try:
            return self._keys[key_id]
        except KeyError as error:
            raise AuthenticationError("token signing key is unknown") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _refresh(self) -> None:
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            discovery_response = await self._client.get(discovery_url)
            discovery_response.raise_for_status()
            discovery = discovery_response.json()
            if discovery.get("issuer") != self._issuer:
                raise AuthenticationError("OIDC discovery issuer mismatch")
            jwks_uri = str(discovery["jwks_uri"])
            _validate_endpoint(
                jwks_uri,
                allow_insecure_http=self._allow_insecure_http,
            )
            jwks_response = await self._client.get(jwks_uri)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationError("unable to refresh OIDC signing keys") from error

        keys: dict[str, Any] = {}
        for raw_key in jwks.get("keys", []):
            key_id = raw_key.get("kid") if isinstance(raw_key, dict) else None
            if isinstance(key_id, str) and key_id:
                try:
                    keys[key_id] = jwt.PyJWK.from_dict(raw_key).key
                except (jwt.PyJWTError, ValueError) as error:
                    raise AuthenticationError("OIDC provider returned an invalid key") from error
        if not keys:
            raise AuthenticationError("OIDC provider returned no signing keys")
        self._keys = keys
        self._expires_at = time.monotonic() + self._cache_seconds


def _validate_endpoint(url: str, *, allow_insecure_http: bool) -> None:
    parsed = urlparse(url)
    allowed_schemes = {"https"}
    if allow_insecure_http:
        allowed_schemes.add("http")
    if parsed.scheme not in allowed_schemes or not parsed.netloc:
        raise ValueError("OIDC endpoints must use an allowed absolute URL")
