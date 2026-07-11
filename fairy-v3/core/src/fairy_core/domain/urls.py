from __future__ import annotations

import posixpath
from urllib.parse import quote, urlsplit, urlunsplit

_MAX_URL_LENGTH = 2_048


def canonical_http_url(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_URL_LENGTH:
        raise ValueError("url is required and cannot exceed 2,048 characters")
    if "\\" in normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError("url contains forbidden characters")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as error:
        raise ValueError("url authority is invalid") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url credentials are not allowed")
    host = parsed.hostname
    if host is None or "%" in parsed.netloc:
        raise ValueError("url host is invalid")
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("url host is invalid") from error
    if not ascii_host or any(character.isspace() for character in ascii_host):
        raise ValueError("url host is invalid")
    default_port = 443 if scheme == "https" else 80
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("url port is invalid")
    host_display = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    authority = host_display if port in {None, default_port} else f"{host_display}:{port}"
    raw_path = parsed.path or "/"
    normalized_path = posixpath.normpath("/".join(raw_path.split("/")))
    if raw_path.startswith("/") and not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    if normalized_path in {".", ""}:
        normalized_path = "/"
    if raw_path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"
    path = quote(normalized_path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&;%:+,/?@!$'()*-._~")
    return urlunsplit((scheme, authority, path, query, ""))


__all__ = ["canonical_http_url"]
