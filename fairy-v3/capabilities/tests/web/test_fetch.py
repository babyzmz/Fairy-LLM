from __future__ import annotations

import gzip
import hashlib
from datetime import UTC, datetime

import pytest
from fairy_core.research.models import FetchRequest

from fairy_capabilities.web.fetch import FetchError, SafeWebFetcher
from fairy_capabilities.web.url_guard import UnsafeUrlError, UrlGuard

from .support import PUBLIC, ScriptedResponse, ScriptedTransport, StaticResolver


def _fetcher(
    responses: list[ScriptedResponse],
    *,
    answers: dict[str, tuple[str, ...]] | None = None,
    max_body_bytes: int = 1_024,
    max_decoded_bytes: int = 2_048,
) -> tuple[SafeWebFetcher, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    guard = UrlGuard(
        resolver=StaticResolver(
            answers
            or {
                "example.com": (PUBLIC,),
                "cdn.example.com": ("8.8.8.8",),
            }
        )
    )
    return (
        SafeWebFetcher(
            guard=guard,
            transport=transport,
            clock=lambda: datetime(2026, 7, 11, 3, 0, tzinfo=UTC),
            max_body_bytes=max_body_bytes,
            max_decoded_bytes=max_decoded_bytes,
        ),
        transport,
    )


def test_fetch_normalizes_html_and_retains_hash_and_redirect_provenance() -> None:
    body = (
        b"<!doctype html><html><head><title> Fairy  V3 </title>"
        b"<script>ignore()</script></head><body><h1>Architecture</h1>"
        b"<p>Project-first research.</p></body></html>"
    )
    fetcher, transport = _fetcher(
        [
            ScriptedResponse(
                302,
                {"location": "https://cdn.example.com/article"},
            ),
            ScriptedResponse(
                200,
                {"content-type": "text/html; charset=utf-8"},
                body,
                peer_ip="8.8.8.8",
            ),
        ]
    )

    document = fetcher.fetch(FetchRequest.create(url="https://example.com/start"))

    assert document.requested_url == "https://example.com/start"
    assert document.final_url == "https://cdn.example.com/article"
    assert document.redirect_chain == (
        "https://example.com/start",
        "https://cdn.example.com/article",
    )
    assert document.title == "Fairy V3"
    assert document.media_type == "text/html"
    assert document.text == "Fairy V3 Architecture Project-first research."
    assert document.content_hash == hashlib.sha256(body).hexdigest()
    assert document.byte_length == len(body)
    assert [request.addresses for request in transport.requests] == [
        (PUBLIC,),
        ("8.8.8.8",),
    ]


def test_fetch_rejects_redirect_loop_and_public_to_private_redirect() -> None:
    looping, _ = _fetcher(
        [
            ScriptedResponse(302, {"location": "/b"}),
            ScriptedResponse(302, {"location": "/a"}),
        ]
    )
    with pytest.raises(FetchError, match="loop"):
        looping.fetch(FetchRequest.create(url="https://example.com/a"))

    private, transport = _fetcher([ScriptedResponse(302, {"location": "http://127.0.0.1/admin"})])
    with pytest.raises(UnsafeUrlError):
        private.fetch(FetchRequest.create(url="https://example.com/start"))
    assert len(transport.requests) == 1


def test_fetch_rejects_dns_peer_change_binary_and_oversized_body() -> None:
    changed, _ = _fetcher(
        [
            ScriptedResponse(
                200,
                {"content-type": "text/plain"},
                b"secret",
                peer_ip="8.8.4.4",
            )
        ]
    )
    with pytest.raises(UnsafeUrlError, match="peer"):
        changed.fetch(FetchRequest.create(url="https://example.com/"))

    binary, _ = _fetcher(
        [ScriptedResponse(200, {"content-type": "application/octet-stream"}, b"bin")]
    )
    with pytest.raises(FetchError, match="media"):
        binary.fetch(FetchRequest.create(url="https://example.com/file"))

    oversized, _ = _fetcher(
        [
            ScriptedResponse(
                200,
                {"content-type": "text/plain"},
                (b"a" * 600, b"b" * 600),
            )
        ],
        max_body_bytes=1_000,
    )
    with pytest.raises(FetchError, match="large"):
        oversized.fetch(FetchRequest.create(url="https://example.com/large"))


def test_fetch_enforces_decompression_limit_and_rejects_unknown_encoding() -> None:
    compressed = gzip.compress(b"x" * 4_000)
    fetcher, _ = _fetcher(
        [
            ScriptedResponse(
                200,
                {"content-type": "text/plain", "content-encoding": "gzip"},
                compressed,
            )
        ],
        max_decoded_bytes=2_000,
    )
    with pytest.raises(FetchError, match="decompressed"):
        fetcher.fetch(FetchRequest.create(url="https://example.com/gzip"))

    unknown, _ = _fetcher(
        [
            ScriptedResponse(
                200,
                {"content-type": "text/plain", "content-encoding": "br"},
                b"encoded",
            )
        ]
    )
    with pytest.raises(FetchError, match="encoding"):
        unknown.fetch(FetchRequest.create(url="https://example.com/br"))


def test_fetch_rejects_ambiguous_concatenated_compression_members() -> None:
    concatenated = gzip.compress(b"first") + gzip.compress(b"second")
    fetcher, _ = _fetcher(
        [
            ScriptedResponse(
                200,
                {"content-type": "text/plain", "content-encoding": "gzip"},
                concatenated,
            )
        ]
    )

    with pytest.raises(FetchError, match="compression"):
        fetcher.fetch(FetchRequest.create(url="https://example.com/gzip"))


def test_fetch_request_cache_key_is_stable_and_sensitive_to_limits() -> None:
    first = FetchRequest.create(url="https://EXAMPLE.com:443/a#fragment")
    replay = FetchRequest.create(url="https://example.com/a")
    shorter = FetchRequest.create(url="https://example.com/a", max_text_characters=1_000)

    assert first.cache_key == replay.cache_key
    assert first.cache_key != shorter.cache_key
