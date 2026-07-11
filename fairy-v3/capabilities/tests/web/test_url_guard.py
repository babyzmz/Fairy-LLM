from __future__ import annotations

import pytest

from fairy_capabilities.web.url_guard import UnsafeUrlError, UrlGuard

from .support import StaticResolver

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://192.0.2.1/",
    ],
)
def test_guard_rejects_every_non_global_literal_address(url: str) -> None:
    with pytest.raises(UnsafeUrlError, match="public"):
        UrlGuard(resolver=StaticResolver({})).authorize(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "https://user:secret@example.com/",
        "https://%31%32%37.0.0.1/",
        "https://example.com%2f@127.0.0.1/",
        "https://example.com:0/",
        "https://example.com:65536/",
        "https://example.com\\@127.0.0.1/",
    ],
)
def test_guard_rejects_ambiguous_encoded_or_credentialed_authorities(
    url: str,
) -> None:
    with pytest.raises(UnsafeUrlError):
        UrlGuard(resolver=StaticResolver({"example.com": (PUBLIC,)})).authorize(url)


def test_guard_canonicalizes_public_targets_and_pins_dns_answers() -> None:
    resolver = StaticResolver({"example.com": ("8.8.8.8", PUBLIC, PUBLIC)})
    guard = UrlGuard(resolver=resolver)

    target = guard.authorize("HTTPS://Example.COM:443/a/../b?q=hello%20world#ignored")

    assert target.url == "https://example.com/b?q=hello%20world"
    assert target.host == "example.com"
    assert target.port == 443
    assert target.addresses == ("8.8.8.8", PUBLIC)
    assert resolver.calls == [("example.com", 443)]
    guard.validate_peer(target, PUBLIC)


@pytest.mark.parametrize("peer", ["127.0.0.1", "10.0.0.1", "8.8.4.4"])
def test_guard_rejects_private_or_changed_connected_peer(peer: str) -> None:
    guard = UrlGuard(resolver=StaticResolver({"example.com": (PUBLIC,)}))
    target = guard.authorize("https://example.com/")

    with pytest.raises(UnsafeUrlError, match="peer"):
        guard.validate_peer(target, peer)


def test_guard_rejects_empty_or_private_dns_answers() -> None:
    guard = UrlGuard(
        resolver=StaticResolver(
            {
                "empty.example": (),
                "mixed.example": (PUBLIC, "127.0.0.1"),
            }
        )
    )

    with pytest.raises(UnsafeUrlError, match="resolve"):
        guard.authorize("https://empty.example/")
    with pytest.raises(UnsafeUrlError, match="public"):
        guard.authorize("https://mixed.example/")
