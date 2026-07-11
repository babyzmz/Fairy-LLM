from __future__ import annotations

import hashlib
import http.client
import re
import socket
import ssl
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from fairy_core.research.models import FetchedDocument, FetchRequest

from fairy_capabilities.web.url_guard import AuthorizedUrl, UnsafeUrlError, UrlGuard

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TEXT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/xhtml+xml",
        "application/xml",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/xml",
    }
)
_WHITESPACE = re.compile(r"\s+")


class FetchError(RuntimeError):
    error_code = "CAPABILITY_FETCH_FAILED"


@dataclass(slots=True)
class RawHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Iterator[bytes]
    peer_ip: str


class HttpTransport(Protocol):
    def request(
        self,
        target: AuthorizedUrl,
        *,
        timeout_seconds: float,
    ) -> RawHttpResponse: ...


class PinnedHttpTransport:
    def __init__(self, *, user_agent: str = "Fairy-V3/0.1") -> None:
        self._user_agent = user_agent
        self._ssl_context = ssl.create_default_context()

    def request(
        self,
        target: AuthorizedUrl,
        *,
        timeout_seconds: float,
    ) -> RawHttpResponse:
        network_socket: socket.socket | ssl.SSLSocket | None = None
        try:
            network_socket = socket.create_connection(
                (target.addresses[0], target.port),
                timeout=timeout_seconds,
            )
            if target.scheme == "https":
                network_socket = self._ssl_context.wrap_socket(
                    network_socket,
                    server_hostname=target.host,
                )
            peer_ip = str(network_socket.getpeername()[0])
            parsed = urlsplit(target.url)
            request_target = parsed.path or "/"
            if parsed.query:
                request_target = f"{request_target}?{parsed.query}"
            default_port = 443 if target.scheme == "https" else 80
            display_host = f"[{target.host}]" if ":" in target.host else target.host
            host_header = (
                display_host if target.port == default_port else f"{display_host}:{target.port}"
            )
            request_bytes = (
                f"GET {request_target} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                f"User-Agent: {self._user_agent}\r\n"
                "Accept: text/html,text/plain,text/markdown,application/json,"
                "application/xml;q=0.8,*/*;q=0.1\r\n"
                "Accept-Encoding: gzip, deflate, identity\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            network_socket.sendall(request_bytes)
            response = http.client.HTTPResponse(network_socket)
            response.begin()
            headers = {name.lower(): value for name, value in response.getheaders()}
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            if network_socket is not None:
                network_socket.close()
            raise FetchError("network fetch transport failed") from error

        def chunks() -> Iterator[bytes]:
            try:
                while True:
                    chunk = response.read(64 * 1_024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                response.close()
                network_socket.close()

        return RawHttpResponse(
            status_code=response.status,
            headers=headers,
            body=chunks(),
            peer_ip=peer_ip,
        )


class SafeWebFetcher:
    def __init__(
        self,
        *,
        guard: UrlGuard | None = None,
        transport: HttpTransport | None = None,
        clock=lambda: datetime.now(UTC),
        max_redirects: int = 5,
        max_body_bytes: int = 2 * 1_024 * 1_024,
        max_decoded_bytes: int = 4 * 1_024 * 1_024,
    ) -> None:
        if not 0 <= max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")
        if max_body_bytes < 1 or max_decoded_bytes < 1:
            raise ValueError("fetch byte limits must be positive")
        self._guard = guard or UrlGuard()
        self._transport = transport or PinnedHttpTransport()
        self._clock = clock
        self._max_redirects = max_redirects
        self._max_body_bytes = max_body_bytes
        self._max_decoded_bytes = max_decoded_bytes

    def fetch(self, request: FetchRequest) -> FetchedDocument:
        target = self._guard.authorize(request.url)
        requested = target.url
        chain: list[str] = []
        seen: set[str] = set()
        for redirect_count in range(self._max_redirects + 1):
            if target.url in seen:
                raise FetchError("redirect loop detected")
            seen.add(target.url)
            chain.append(target.url)
            try:
                response = self._transport.request(
                    target,
                    timeout_seconds=request.timeout_seconds,
                )
            except (FetchError, UnsafeUrlError):
                raise
            except (OSError, TimeoutError) as error:
                raise FetchError("network fetch transport failed") from error
            try:
                self._guard.validate_peer(target, response.peer_ip)
            except Exception:
                _close_body(response.body)
                raise
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            if response.status_code in _REDIRECT_STATUSES:
                location = headers.get("location", "").strip()
                if not location:
                    _close_body(response.body)
                    raise FetchError("redirect response is missing a location")
                if redirect_count >= self._max_redirects:
                    _close_body(response.body)
                    raise FetchError("redirect limit exceeded")
                next_url = urljoin(target.url, location)
                _close_body(response.body)
                target = self._guard.authorize(next_url)
                continue
            if not 200 <= response.status_code < 300:
                _close_body(response.body)
                raise FetchError("upstream returned a non-success status")
            try:
                media_type, charset = _content_type(headers.get("content-type"))
            except FetchError:
                _close_body(response.body)
                raise
            if media_type not in _TEXT_MEDIA_TYPES:
                _close_body(response.body)
                raise FetchError("response media type is not allowed")
            raw = self._read_bounded(response.body, headers.get("content-length"))
            decoded = _decode_content(
                raw,
                headers.get("content-encoding", "identity"),
                maximum=self._max_decoded_bytes,
            )
            text = _decode_text(decoded, charset)
            title, normalized = _normalize_document(media_type, text, target.host)
            truncated = len(normalized) > request.max_text_characters
            if truncated:
                normalized = normalized[: request.max_text_characters]
            return FetchedDocument.create(
                requested_url=requested,
                final_url=target.url,
                redirect_chain=tuple(chain),
                media_type=media_type,
                byte_length=len(decoded),
                content_hash=hashlib.sha256(decoded).hexdigest(),
                title=title,
                text=normalized,
                fetched_at=self._clock(),
                truncated=truncated,
            )
        raise FetchError("redirect limit exceeded")

    def _read_bounded(
        self,
        chunks: Iterator[bytes],
        content_length: str | None,
    ) -> bytes:
        try:
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as error:
                    raise FetchError("response content length is invalid") from error
                if declared < 0 or declared > self._max_body_bytes:
                    raise FetchError("response body is too large")
            body = bytearray()
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise FetchError("response body chunk is invalid")
                body.extend(chunk)
                if len(body) > self._max_body_bytes:
                    raise FetchError("response body is too large")
            if not body:
                raise FetchError("response body is empty")
            return bytes(body)
        finally:
            _close_body(chunks)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.title: list[str] = []
        self._ignored_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag.casefold() in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        if tag.casefold() == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if tag.casefold() == "title":
            self._title_depth = max(0, self._title_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._title_depth:
            self.title.append(data)


def _content_type(value: str | None) -> tuple[str, str]:
    if value is None:
        raise FetchError("response media type is missing")
    parts = [part.strip() for part in value.split(";")]
    media_type = parts[0].lower()
    charset = "utf-8"
    for parameter in parts[1:]:
        name, separator, raw_value = parameter.partition("=")
        if separator and name.strip().casefold() == "charset":
            charset = raw_value.strip().strip('"').lower()
    return media_type, charset


def _decode_content(value: bytes, encoding: str, *, maximum: int) -> bytes:
    normalized = encoding.strip().casefold()
    if normalized in {"", "identity"}:
        if len(value) > maximum:
            raise FetchError("decompressed response is too large")
        return value
    if normalized == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif normalized == "deflate":
        decompressor = zlib.decompressobj()
    else:
        raise FetchError("response content encoding is not allowed")
    output = bytearray()
    try:
        for offset in range(0, len(value), 64 * 1_024):
            remaining = maximum - len(output)
            chunk = value[offset : offset + 64 * 1_024]
            output.extend(decompressor.decompress(chunk, remaining + 1))
            if len(output) > maximum or decompressor.unconsumed_tail:
                raise FetchError("decompressed response is too large")
        output.extend(decompressor.flush(maximum - len(output) + 1))
    except zlib.error as error:
        raise FetchError("response compression is invalid") from error
    if not decompressor.eof or decompressor.unused_data:
        raise FetchError("response compression is invalid or ambiguous")
    if len(output) > maximum:
        raise FetchError("decompressed response is too large")
    return bytes(output)


def _decode_text(value: bytes, charset: str) -> str:
    if charset not in {"ascii", "iso-8859-1", "latin-1", "utf-8", "windows-1252"}:
        raise FetchError("response charset is not allowed")
    try:
        return value.decode(charset, errors="replace")
    except LookupError as error:
        raise FetchError("response charset is invalid") from error


def _normalize_document(media_type: str, value: str, host: str) -> tuple[str, str]:
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        try:
            parser.feed(value)
            parser.close()
        except Exception as error:
            raise FetchError("HTML response is malformed") from error
        normalized = _WHITESPACE.sub(" ", " ".join(parser.text)).strip()
        title = _WHITESPACE.sub(" ", " ".join(parser.title)).strip()
    else:
        normalized = _WHITESPACE.sub(" ", value).strip()
        title = ""
    if not normalized:
        raise FetchError("response contains no usable text")
    if not title:
        title = normalized[:200].strip() or host
    return title[:1_000], normalized


def _close_body(body: Iterator[bytes]) -> None:
    close = getattr(body, "close", None)
    if callable(close):
        close()


__all__ = [
    "FetchError",
    "HttpTransport",
    "PinnedHttpTransport",
    "RawHttpResponse",
    "SafeWebFetcher",
]
