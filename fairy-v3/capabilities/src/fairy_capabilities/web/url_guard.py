from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from fairy_core.research.models import canonical_http_url


class UnsafeUrlError(ValueError):
    error_code = "NETWORK_TARGET_BLOCKED"


class Resolver(Protocol):
    def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class SocketResolver:
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            answers = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise UnsafeUrlError("network target did not resolve") from error
        values: list[str] = []
        for answer in answers:
            value = str(answer[4][0])
            if value not in values:
                values.append(value)
        return tuple(values)


@dataclass(frozen=True, slots=True)
class AuthorizedUrl:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


class UrlGuard:
    def __init__(self, *, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or SocketResolver()

    def authorize(self, value: str) -> AuthorizedUrl:
        try:
            canonical = canonical_http_url(value)
        except ValueError as error:
            raise UnsafeUrlError(str(error)) from error
        parsed = urlsplit(canonical)
        host = parsed.hostname
        assert host is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if host.casefold() == "localhost" or host.casefold().endswith(
            (".localhost", ".local", ".internal")
        ):
            raise UnsafeUrlError("network target must resolve to public addresses")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            raw_addresses = self._resolver.resolve(host, port)
            if not raw_addresses:
                raise UnsafeUrlError("network target did not resolve") from None
        else:
            raw_addresses = (str(literal),)
        addresses: list[str] = []
        for raw_address in raw_addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise UnsafeUrlError("resolver returned an invalid address") from error
            if not _is_public_unicast(address):
                raise UnsafeUrlError("network target must use public addresses")
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
        return AuthorizedUrl(
            url=canonical,
            scheme=parsed.scheme,
            host=host,
            port=port,
            addresses=tuple(addresses),
        )

    @staticmethod
    def validate_peer(target: AuthorizedUrl, peer_address: str) -> None:
        try:
            peer = ipaddress.ip_address(peer_address)
        except ValueError as error:
            raise UnsafeUrlError("connected peer address is invalid") from error
        normalized = str(peer)
        if not _is_public_unicast(peer) or normalized not in target.addresses:
            raise UnsafeUrlError("connected peer does not match the authorized public target")


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
    "AuthorizedUrl",
    "Resolver",
    "SocketResolver",
    "UnsafeUrlError",
    "UrlGuard",
]
