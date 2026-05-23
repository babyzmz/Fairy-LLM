from __future__ import annotations

import mimetypes
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from app.ai.llm_client_file_processor import FileProcessor
from app.config import llm_config
from utils.web_content_extractor import extract_web_content


WEB_FETCH_CACHE_TTL_SEC = 15 * 60
MAX_URL_LENGTH = 2000
MAX_REDIRECTS = 10
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
}
_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/javascript",
    "application/x-javascript",
)
_BINARY_DOWNLOAD_DIR = Path("data") / "web_fetch"
_FETCH_CACHE_LOCK = threading.Lock()
_FETCH_CACHE: dict[str, tuple[float, "FetchedWebContent"]] = {}


@dataclass(slots=True)
class RedirectInfo:
    original_url: str
    redirect_url: str
    status_code: int


@dataclass(slots=True)
class FetchedWebContent:
    requested_url: str
    final_url: str
    status_code: int | None
    content_type: str
    html: str = ""
    text_content: str = ""
    title: str = ""
    bytes: int = 0
    blocked_reason: str = ""
    persisted_path: str = ""
    persisted_size: int | None = None
    redirect_url: str = ""
    redirect_status_code: int | None = None
    cache_hit: bool = False


def clear_web_fetch_cache() -> None:
    with _FETCH_CACHE_LOCK:
        _FETCH_CACHE.clear()


def validate_fetch_url(url: str) -> tuple[bool, str, str]:
    raw = str(url or "").strip()
    if not raw:
        return False, "empty_url", ""
    if len(raw) > MAX_URL_LENGTH:
        return False, "url_too_long", raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "invalid_url", raw

    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported_scheme", raw
    if parsed.username or parsed.password:
        return False, "credentialed_url_blocked", raw
    if not parsed.netloc or not parsed.hostname:
        return False, "invalid_host", raw
    if any(ch.isspace() for ch in parsed.netloc):
        return False, "invalid_host", raw

    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )
    return True, "", normalized


def is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    try:
        original = urlparse(original_url)
        redirect = urlparse(redirect_url)
    except Exception:
        return False

    if redirect.scheme not in {"http", "https"}:
        return False
    if redirect.username or redirect.password:
        return False
    if redirect.scheme != original.scheme:
        return False
    if (redirect.port or _default_port(redirect.scheme)) != (
        original.port or _default_port(original.scheme)
    ):
        return False

    return _strip_www(redirect.hostname or "") == _strip_www(original.hostname or "")


def fetch_http_resource(url: str, timeout_sec: int = 12) -> FetchedWebContent:
    valid, reason, normalized_url = validate_fetch_url(url)
    if not valid:
        return FetchedWebContent(
            requested_url=str(url or "").strip(),
            final_url=str(url or "").strip(),
            status_code=None,
            content_type="",
            blocked_reason=reason,
        )

    cached = _get_cached(normalized_url)
    if cached is not None:
        return replace(cached, cache_hit=True)

    try:
        response_or_redirect = _request_with_permitted_redirects(
            normalized_url,
            timeout_sec=timeout_sec,
            verify_ssl=True,
            depth=0,
        )
    except requests.RequestException as exc:
        return FetchedWebContent(
            requested_url=normalized_url,
            final_url=normalized_url,
            status_code=None,
            content_type="",
            blocked_reason=f"request_failed:{type(exc).__name__}",
        )
    except Exception as exc:  # noqa: BLE001
        return FetchedWebContent(
            requested_url=normalized_url,
            final_url=normalized_url,
            status_code=None,
            content_type="",
            blocked_reason=f"request_failed:{type(exc).__name__}",
        )

    if isinstance(response_or_redirect, RedirectInfo):
        return FetchedWebContent(
            requested_url=normalized_url,
            final_url=normalized_url,
            status_code=response_or_redirect.status_code,
            content_type="",
            blocked_reason="redirect_blocked",
            redirect_url=response_or_redirect.redirect_url,
            redirect_status_code=response_or_redirect.status_code,
        )

    response = response_or_redirect
    raw_bytes = response.content or b""
    content_type = _normalize_content_type(response.headers.get("content-type", ""))
    final_url = str(response.url or normalized_url)

    html = ""
    text_content = ""
    title = ""
    persisted_path = ""
    persisted_size: int | None = None

    if _is_html_content_type(content_type):
        html = _decode_text_bytes(raw_bytes, response.encoding)
        try:
            extracted = extract_web_content(html)
        except Exception:
            extracted = None
        if extracted is not None:
            title = extracted.title
            text_content = _merge_text_sections(
                extracted.meta_description,
                extracted.body_text,
            )
    elif _is_textual_content_type(content_type):
        text_content = _decode_text_bytes(raw_bytes, response.encoding).strip()
        html = text_content
    else:
        persisted_path, persisted_size = persist_binary_content(
            raw_bytes,
            content_type=content_type,
            source_url=final_url,
        )
        if persisted_path:
            text_content = extract_persisted_file_text(Path(persisted_path))
        html = text_content

    result = FetchedWebContent(
        requested_url=normalized_url,
        final_url=final_url,
        status_code=response.status_code,
        content_type=content_type,
        html=html,
        text_content=text_content.strip(),
        title=title.strip(),
        bytes=len(raw_bytes),
        persisted_path=persisted_path,
        persisted_size=persisted_size,
    )
    _set_cached(normalized_url, result)
    return result


def extract_persisted_file_text(path: Path) -> str:
    processor = FileProcessor(llm_config)
    text = processor.extract_file_text(path).strip()
    if not text or text.startswith("[Unsupported file format"):
        return ""
    return text


def persist_binary_content(
    raw_bytes: bytes,
    *,
    content_type: str,
    source_url: str,
) -> tuple[str, int | None]:
    if not raw_bytes:
        return "", None

    _BINARY_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    extension = _guess_extension(content_type, source_url)
    filename = f"webfetch-{int(time.time() * 1000)}-{threading.get_ident()}{extension}"
    output_path = _BINARY_DOWNLOAD_DIR / filename
    output_path.write_bytes(raw_bytes)
    return str(output_path), len(raw_bytes)


def _request_with_permitted_redirects(
    url: str,
    *,
    timeout_sec: int,
    verify_ssl: bool,
    depth: int,
) -> requests.Response | RedirectInfo:
    if depth > MAX_REDIRECTS:
        raise RuntimeError("too_many_redirects")

    try:
        response = requests.get(
            url,
            headers=_FETCH_HEADERS,
            timeout=timeout_sec,
            allow_redirects=False,
            verify=verify_ssl,
        )
    except requests.exceptions.SSLError:
        if verify_ssl:
            return _request_with_permitted_redirects(
                url,
                timeout_sec=timeout_sec,
                verify_ssl=False,
                depth=depth,
            )
        raise

    if response.status_code in {301, 302, 303, 307, 308}:
        location = str(response.headers.get("location") or "").strip()
        if not location:
            response.raise_for_status()
        redirect_url = urljoin(url, location)
        if is_permitted_redirect(url, redirect_url):
            return _request_with_permitted_redirects(
                redirect_url,
                timeout_sec=timeout_sec,
                verify_ssl=verify_ssl,
                depth=depth + 1,
            )
        return RedirectInfo(
            original_url=url,
            redirect_url=redirect_url,
            status_code=response.status_code,
        )

    response.raise_for_status()
    return response


def _decode_text_bytes(raw_bytes: bytes, response_encoding: str | None) -> str:
    encodings = [response_encoding, "utf-8", "utf-16", "gb18030"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return raw_bytes.decode(encoding)
        except Exception:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _guess_extension(content_type: str, source_url: str) -> str:
    parsed = urlparse(source_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    if "markdown" in content_type:
        return ".md"
    guessed = mimetypes.guess_extension(content_type) or ""
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def _is_html_content_type(content_type: str) -> bool:
    return "text/html" in content_type


def _is_textual_content_type(content_type: str) -> bool:
    if not content_type:
        return False
    return any(content_type.startswith(prefix) for prefix in _TEXTUAL_CONTENT_TYPES)


def _normalize_content_type(raw: str) -> str:
    content_type = str(raw or "").split(";", 1)[0].strip().lower()
    return content_type


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _strip_www(hostname: str) -> str:
    lowered = hostname.lower().strip()
    return lowered[4:] if lowered.startswith("www.") else lowered


def _merge_text_sections(*sections: str) -> str:
    return "\n".join(section.strip() for section in sections if section and section.strip()).strip()


def _get_cached(url: str) -> FetchedWebContent | None:
    now = time.time()
    with _FETCH_CACHE_LOCK:
        _prune_cache(now)
        item = _FETCH_CACHE.get(url)
        if item is None:
            return None
        cached_at, value = item
        if now - cached_at > WEB_FETCH_CACHE_TTL_SEC:
            _FETCH_CACHE.pop(url, None)
            return None
        return value


def _set_cached(url: str, content: FetchedWebContent) -> None:
    now = time.time()
    with _FETCH_CACHE_LOCK:
        _prune_cache(now)
        _FETCH_CACHE[url] = (now, replace(content, cache_hit=False))


def _prune_cache(now: float) -> None:
    expired = [
        key
        for key, (cached_at, _value) in _FETCH_CACHE.items()
        if now - cached_at > WEB_FETCH_CACHE_TTL_SEC
    ]
    for key in expired:
        _FETCH_CACHE.pop(key, None)
