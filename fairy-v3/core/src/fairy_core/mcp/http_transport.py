from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from fairy_core.mcp.ports import McpError


class McpHttpResolver(Protocol):
    def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class SocketMcpHttpResolver:
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            answers = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise McpError(
                "MCP endpoint DNS resolution failed",
                error_code="MCP_UNAVAILABLE",
            ) from error
        addresses: list[str] = []
        for answer in answers:
            value = str(answer[4][0]).split("%", 1)[0]
            if value not in addresses:
                addresses.append(value)
        return tuple(addresses)


@dataclass(frozen=True, slots=True)
class AuthorizedMcpHttpEndpoint:
    endpoint: str
    scheme: str
    host: str
    port: int
    authority: str
    addresses: tuple[str, ...]


def authorize_mcp_http_endpoint(
    endpoint: str,
    *,
    resolver: McpHttpResolver | None = None,
) -> AuthorizedMcpHttpEndpoint:
    parsed = urlsplit(endpoint)
    hostname = parsed.hostname
    if hostname is None:
        raise McpError("MCP endpoint has no host", error_code="MCP_DESTINATION_BLOCKED")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    loopback_name = hostname.casefold().rstrip(".") in {"127.0.0.1", "localhost", "::1"}
    raw_addresses = (resolver or SocketMcpHttpResolver()).resolve(hostname, port)
    if not raw_addresses:
        raise McpError("MCP endpoint DNS resolution failed", error_code="MCP_UNAVAILABLE")

    addresses: list[str] = []
    try:
        for raw_address in raw_addresses:
            address = ipaddress.ip_address(raw_address.split("%", 1)[0])
            allowed = address.is_loopback if loopback_name else _is_public_unicast(address)
            if not allowed:
                raise McpError(
                    "MCP endpoint resolves outside the allowed network boundary",
                    error_code="MCP_DESTINATION_BLOCKED",
                )
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
    except ValueError as error:
        raise McpError(
            "MCP endpoint DNS resolution failed",
            error_code="MCP_UNAVAILABLE",
        ) from error

    default_port = 443 if parsed.scheme == "https" else 80
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = display_host if port == default_port else f"{display_host}:{port}"
    return AuthorizedMcpHttpEndpoint(
        endpoint=endpoint,
        scheme=parsed.scheme,
        host=hostname,
        port=port,
        authority=authority,
        addresses=tuple(addresses),
    )


class PinnedMcpHttpTransport(httpx.AsyncBaseTransport):
    """Connects an HTTPX request to one pre-authorized address without another DNS lookup."""

    def __init__(
        self,
        target: AuthorizedMcpHttpEndpoint,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._target = target
        self._address = target.addresses[0]
        self._transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url != httpx.URL(self._target.endpoint):
            raise McpError(
                "MCP request escaped its authorized endpoint",
                error_code="MCP_DESTINATION_BLOCKED",
            )
        headers = request.headers.copy()
        headers["Host"] = self._target.authority
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self._target.host
        pinned_request = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=self._address),
            headers=headers.raw,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self._transport.handle_async_request(pinned_request)
        try:
            peer_address = _connected_peer(response)
            peer = ipaddress.ip_address(peer_address.split("%", 1)[0])
        except (TypeError, ValueError) as error:
            await response.aclose()
            raise McpError(
                "MCP connected peer address is unavailable",
                error_code="MCP_DESTINATION_BLOCKED",
            ) from error
        if str(peer) != self._address:
            await response.aclose()
            raise McpError(
                "MCP connected peer does not match the authorized endpoint",
                error_code="MCP_DESTINATION_BLOCKED",
            )
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


def _connected_peer(response: httpx.Response) -> str:
    network_stream = response.extensions.get("network_stream")
    getter = getattr(network_stream, "get_extra_info", None)
    if getter is None:
        raise TypeError("HTTP transport did not expose its connected peer")
    server_address = getter("server_addr")
    if isinstance(server_address, str):
        return server_address
    if isinstance(server_address, tuple) and server_address:
        return str(server_address[0])
    raise TypeError("HTTP transport did not expose its connected peer")


def _is_public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
    )


__all__ = [
    "AuthorizedMcpHttpEndpoint",
    "McpHttpResolver",
    "PinnedMcpHttpTransport",
    "SocketMcpHttpResolver",
    "authorize_mcp_http_endpoint",
]
