from __future__ import annotations

import hmac
import re
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

_TOKEN = re.compile(r"^[a-z2-7]{52}$")
_INTERNAL_PATH = re.compile(
    r"^/internal/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/$"
)
_BLOCKED_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "cookie",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_BLOCKED_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "server",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_RESPONSE_BYTES = 128 * 1024 * 1024
_GATEWAY_HEADER = "X-Fairy-Runtime-Gateway-Key"


class PreviewRouteResolver(Protocol):
    def resolve(self, token: str) -> str: ...


class InternalRuntimeResolver(Protocol):
    async def resolve_internal(self, runtime_id: str) -> str: ...


class CloudPreviewProxy:
    def __init__(
        self,
        *,
        base_domain: str,
        routes: PreviewRouteResolver,
        gateway_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_domain = base_domain.strip().lower().strip(".")
        if (
            not normalized_domain
            or ":" in normalized_domain
            or normalized_domain == "localhost"
            or "." not in normalized_domain
        ):
            raise ValueError("Preview base domain must be a DNS suffix")
        self._base_domain = normalized_domain
        self._routes = routes
        self._gateway_key = _gateway_key(gateway_key)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=5),
            follow_redirects=False,
        )

    def install(self, app: FastAPI) -> None:
        @app.middleware("http")
        async def preview_proxy(request: Request, call_next):
            token = self._token_for_host(request.url.hostname)
            if token is None:
                return await call_next(request)
            return await self.forward(request, token)

    async def forward(self, request: Request, token: str) -> Response:
        if request.method not in _ALLOWED_METHODS:
            return _error(405, "PREVIEW_METHOD_NOT_ALLOWED")
        try:
            internal_base = await run_in_threadpool(self._routes.resolve, token)
            target = _target_url(internal_base, request)
        except Exception:
            return _error(502, "PREVIEW_ROUTE_UNAVAILABLE")
        body = await _bounded_body(request, _MAX_REQUEST_BYTES)
        if body is None:
            return _error(413, "PREVIEW_REQUEST_TOO_LARGE")
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _BLOCKED_REQUEST_HEADERS
            and not name.lower().startswith("x-fairy-")
        }
        headers[_GATEWAY_HEADER] = self._gateway_key
        try:
            upstream = await self._client.send(
                self._client.build_request(
                    request.method,
                    target,
                    content=body,
                    headers=headers,
                ),
                stream=True,
            )
        except httpx.HTTPError:
            return _error(502, "PREVIEW_UPSTREAM_UNAVAILABLE")
        content = await _bounded_response(upstream, _MAX_RESPONSE_BYTES)
        if content is None:
            return _error(502, "PREVIEW_RESPONSE_TOO_LARGE")
        response_headers = _response_headers(
            upstream.headers,
            public_origin=f"https://{token}.{self._base_domain}",
        )
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    def _token_for_host(self, host: str | None) -> str | None:
        if host is None:
            return None
        suffix = f".{self._base_domain}"
        normalized = host.lower().rstrip(".")
        if not normalized.endswith(suffix):
            return None
        token = normalized.removesuffix(suffix)
        return token if _TOKEN.fullmatch(token) is not None else None


class RuntimeInternalGateway:
    def __init__(
        self,
        *,
        resolver: InternalRuntimeResolver,
        gateway_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._resolver = resolver
        self._gateway_key = _gateway_key(gateway_key)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=2),
            follow_redirects=False,
        )

    def install(self, app: FastAPI) -> None:
        @app.api_route(
            "/internal/{runtime_id}/{path:path}",
            methods=sorted(_ALLOWED_METHODS),
        )
        async def internal_proxy(runtime_id: str, path: str, request: Request) -> Response:
            supplied = request.headers.get(_GATEWAY_HEADER)
            if supplied is None or not hmac.compare_digest(supplied, self._gateway_key):
                return _error(404, "RUNTIME_NOT_AVAILABLE")
            try:
                base = await self._resolver.resolve_internal(runtime_id)
                target = _local_target_url(base, path, request.url.query)
            except Exception:
                return _error(404, "RUNTIME_NOT_AVAILABLE")
            body = await _bounded_body(request, _MAX_REQUEST_BYTES)
            if body is None:
                return _error(413, "PREVIEW_REQUEST_TOO_LARGE")
            headers = {
                name: value
                for name, value in request.headers.items()
                if name.lower() not in _BLOCKED_REQUEST_HEADERS
                and not name.lower().startswith("x-fairy-")
            }
            try:
                upstream = await self._client.send(
                    self._client.build_request(
                        request.method,
                        target,
                        content=body,
                        headers=headers,
                    ),
                    stream=True,
                )
            except httpx.HTTPError:
                return _error(502, "RUNTIME_UPSTREAM_UNAVAILABLE")
            content = await _bounded_response(upstream, _MAX_RESPONSE_BYTES)
            if content is None:
                return _error(502, "PREVIEW_RESPONSE_TOO_LARGE")
            response_headers = _response_headers(upstream.headers)
            return Response(
                content=content,
                status_code=upstream.status_code,
                headers=response_headers,
            )


def _target_url(internal_base: str, request: Request) -> str:
    parsed = urlsplit(internal_base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "runtime"
        or parsed.port != 8082
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _INTERNAL_PATH.fullmatch(parsed.path) is None
    ):
        raise ValueError("Preview internal route is invalid")
    relative_path = request.url.path.lstrip("/")
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{internal_base}{relative_path}{query}"


def _local_target_url(base: str, path: str, query: str) -> str:
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/"
        or parsed.query
        or parsed.fragment
        or ".." in path.split("/")
    ):
        raise ValueError("Runtime local endpoint is invalid")
    suffix = f"?{query}" if query else ""
    return f"{base}{path.lstrip('/')}{suffix}"


async def _bounded_body(request: Request, limit: int) -> bytes | None:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            return None
        content.extend(chunk)
    return bytes(content)


async def _bounded_response(response: httpx.Response, limit: int) -> bytes | None:
    content = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > limit:
                return None
            content.extend(chunk)
        return bytes(content)
    finally:
        await response.aclose()


def _response_headers(
    headers: httpx.Headers,
    *,
    public_origin: str | None = None,
) -> dict[str, str]:
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in _BLOCKED_RESPONSE_HEADERS
    }
    location = result.get("location")
    if location is not None and public_origin is not None:
        parsed = urlsplit(location)
        if parsed.hostname in {"127.0.0.1", "localhost", "runtime"}:
            path = parsed.path or "/"
            query = f"?{parsed.query}" if parsed.query else ""
            fragment = f"#{parsed.fragment}" if parsed.fragment else ""
            result["location"] = f"{public_origin}{path}{query}{fragment}"
    return result


def _gateway_key(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) < 32:
        raise ValueError("Runtime gateway key must contain at least 32 bytes")
    return value


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code}})


__all__ = [
    "CloudPreviewProxy",
    "InternalRuntimeResolver",
    "PreviewRouteResolver",
    "RuntimeInternalGateway",
]
