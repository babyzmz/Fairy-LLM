from __future__ import annotations

import json
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx
from fairy_core.providers import SecretValue
from fairy_core.research.models import (
    ResearchCapabilityHealth,
    ResearchCapabilityStatus,
    SearchHit,
    SearchKind,
    SearchRequest,
)


class BraveSearchError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class SearchUnavailableError(BraveSearchError):
    def __init__(self) -> None:
        super().__init__(
            "CAPABILITY_NOT_AVAILABLE",
            "Brave search capability is unavailable",
        )


class BraveSearchAdapter:
    def __init__(
        self,
        *,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
        endpoint: str = "https://api.search.brave.com/res/v1",
        clock=lambda: datetime.now(UTC),
        timeout_seconds: float = 10,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        normalized_endpoint = endpoint.strip().rstrip("/")
        if not normalized_endpoint.startswith("https://"):
            raise ValueError("Brave endpoint must use HTTPS")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Brave timeout must be between 0 and 30 seconds")
        if isinstance(max_response_bytes, bool) or not 1_024 <= max_response_bytes <= 4_194_304:
            raise ValueError("Brave response limit must be between 1 KiB and 4 MiB")
        self._secret = secret
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._endpoint = normalized_endpoint
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._diagnostics: tuple[str, ...] = ()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> ResearchCapabilityHealth:
        available = self._secret is not None
        return ResearchCapabilityHealth.create(
            provider="brave",
            status=(
                ResearchCapabilityStatus.AVAILABLE
                if available
                else ResearchCapabilityStatus.UNAVAILABLE
            ),
            observed_at=self._clock(),
            error_code=None if available else "CAPABILITY_NOT_AVAILABLE",
            diagnostics=self._diagnostics,
        )

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        if self._secret is None:
            raise SearchUnavailableError()
        endpoint_name = "news" if request.kind is SearchKind.NEWS else "web"
        params: dict[str, str | int] = {
            "q": request.query,
            "count": request.count,
            "offset": request.offset,
        }
        if request.freshness is not None:
            params["freshness"] = request.freshness
        try:
            with self._client.stream(
                "GET",
                f"{self._endpoint}/{endpoint_name}/search",
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._secret.reveal(),
                    "User-Agent": "Fairy-V3/0.1",
                },
                timeout=self._timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise BraveSearchError(
                        _status_error_code(response.status_code),
                        "Brave search request was rejected",
                    )
                body = _read_bounded(response, self._max_response_bytes)
                remaining = response.headers.get("x-ratelimit-remaining")
        except httpx.TimeoutException as error:
            raise BraveSearchError("TIMEOUT", "Brave search timed out") from error
        except httpx.HTTPError as error:
            raise BraveSearchError(
                "TRANSPORT_ERROR",
                "Brave search transport failed",
            ) from error
        try:
            payload = json.loads(body)
            results = _results(payload, request.kind)
            hits = tuple(
                _hit(
                    item,
                    provider=f"brave_{request.kind.value}",
                    rank=request.offset * request.count + index,
                )
                for index, item in enumerate(results[: request.count], start=1)
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            raise BraveSearchError(
                "PROTOCOL_ERROR",
                "Brave search returned an invalid payload",
            ) from error
        self._diagnostics = (
            (f"rate_limit_remaining={remaining}",)
            if remaining is not None and remaining.isdigit()
            else ()
        )
        return hits


def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > maximum:
            raise BraveSearchError(
                "RESPONSE_TOO_LARGE",
                "Brave search response exceeded the byte limit",
            )
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > maximum:
            raise BraveSearchError(
                "RESPONSE_TOO_LARGE",
                "Brave search response exceeded the byte limit",
            )
    return bytes(body)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _results(payload: Any, kind: SearchKind) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if kind is SearchKind.WEB:
        group = payload.get("web")
        if not isinstance(group, dict):
            raise ValueError("web results are missing")
        values = group.get("results")
    else:
        values = payload.get("results")
        if values is None:
            group = payload.get("news")
            values = group.get("results") if isinstance(group, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError("results must be a list of objects")
    return values


def _hit(item: dict[str, Any], *, provider: str, rank: int) -> SearchHit:
    profile = item.get("profile")
    meta_url = item.get("meta_url")
    source = item.get("source")
    if source is None and isinstance(profile, dict):
        source = profile.get("long_name")
    if source is None and isinstance(meta_url, dict):
        source = meta_url.get("hostname")
    return SearchHit.create(
        provider=provider,
        rank=rank,
        title=_plain_text(item["title"]),
        url=item["url"],
        description=_plain_text(item.get("description", "")),
        published_at=_published_at(item),
        language=item.get("language"),
        source=source,
    )


def _plain_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("search text must be a string")
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _published_at(item: dict[str, Any]) -> datetime | None:
    for name in ("page_age", "published_time", "date"):
        value = item.get(name)
        if not isinstance(value, str):
            continue
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed
    return None


def _status_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "AUTH_REJECTED"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code >= 500:
        return "UPSTREAM_ERROR"
    return "REQUEST_REJECTED"


__all__ = [
    "BraveSearchAdapter",
    "BraveSearchError",
    "SearchUnavailableError",
]
