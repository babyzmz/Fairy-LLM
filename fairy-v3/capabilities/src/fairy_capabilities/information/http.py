from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx


class InformationProviderError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class CapabilityUnavailableError(InformationProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            "CAPABILITY_NOT_AVAILABLE",
            f"{provider} capability is unavailable",
        )


class BoundedJsonClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10,
        max_response_bytes: int = 2 * 1024 * 1024,
        retries: int = 1,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("information timeout must be between 0 and 30 seconds")
        if isinstance(max_response_bytes, bool) or not 1_024 <= max_response_bytes <= 4_194_304:
            raise ValueError("information response limit must be between 1 KiB and 4 MiB")
        if isinstance(retries, bool) or not 0 <= retries <= 2:
            raise ValueError("information retries must be between 0 and 2")
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._retries = retries
        self._sleeper = sleeper

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float],
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        for attempt in range(self._retries + 1):
            try:
                with self._client.stream(
                    "GET",
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout_seconds,
                ) as response:
                    if response.status_code == 429:
                        raise InformationProviderError(
                            "RATE_LIMITED",
                            "information provider rate limit was reached",
                        )
                    if response.status_code >= 500 and attempt < self._retries:
                        self._sleeper(0.05 * (attempt + 1))
                        continue
                    if not 200 <= response.status_code < 300:
                        raise InformationProviderError(
                            "UPSTREAM_ERROR" if response.status_code >= 500 else "REQUEST_REJECTED",
                            "information provider rejected the request",
                        )
                    body = _read_bounded(response, self._max_response_bytes)
            except InformationProviderError:
                raise
            except httpx.TimeoutException as error:
                if attempt < self._retries:
                    self._sleeper(0.05 * (attempt + 1))
                    continue
                raise InformationProviderError(
                    "TIMEOUT",
                    "information provider timed out",
                ) from error
            except httpx.HTTPError as error:
                if attempt < self._retries:
                    self._sleeper(0.05 * (attempt + 1))
                    continue
                raise InformationProviderError(
                    "TRANSPORT_ERROR",
                    "information provider transport failed",
                ) from error
            try:
                return json.loads(body)
            except json.JSONDecodeError as error:
                raise InformationProviderError(
                    "PROTOCOL_ERROR",
                    "information provider returned invalid JSON",
                ) from error
        raise AssertionError("bounded retry loop did not return")


def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > maximum:
            raise InformationProviderError(
                "RESPONSE_TOO_LARGE",
                "information provider response exceeded the byte limit",
            )
    output = bytearray()
    for chunk in response.iter_bytes():
        output.extend(chunk)
        if len(output) > maximum:
            raise InformationProviderError(
                "RESPONSE_TOO_LARGE",
                "information provider response exceeded the byte limit",
            )
    return bytes(output)


__all__ = [
    "BoundedJsonClient",
    "CapabilityUnavailableError",
    "InformationProviderError",
]
