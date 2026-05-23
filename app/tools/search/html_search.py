from __future__ import annotations

import time
import warnings
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from app.config import llm_config
from utils.browser_automation import browser_automation


SearchDiagnostics = list[dict[str, Any]]
SEARCH_ENGINE_DOMAINS = (
    "bing.com",
    "duckduckgo.com",
    "google.com",
)
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
}
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "form",
    "spm",
    "from",
    "source",
    "scm",
    "campaign",
    "ref",
}


def _is_search_engine_host(host: str) -> bool:
    lowered = host.lower()
    if "searx" in lowered:
        return True
    return any(lowered == domain or lowered.endswith(f".{domain}") for domain in SEARCH_ENGINE_DOMAINS)


def _looks_like_search_engine_url(url: str) -> bool:
    parsed = urlparse(url)
    if not _is_search_engine_host(parsed.netloc):
        return False
    path = parsed.path.lower()
    return path in {"", "/"} or path.startswith("/search") or "html" in path or path.startswith("/lite")


def _strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    query = parse_qs(parsed.query, keep_blank_values=False)
    clean_pairs: list[tuple[str, str]] = []
    for key, values in query.items():
        if key.lower() in TRACKING_QUERY_KEYS:
            continue
        for value in values:
            clean_pairs.append((key, value))
    clean_query = urlencode(clean_pairs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, ""))


def normalize_search_result_url(result: dict[str, Any] | str) -> str:
    raw_url = str(result.get("url", "") if isinstance(result, dict) else result).strip()
    if not raw_url.startswith(("http://", "https://")):
        return ""

    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)

    if "duckduckgo.com" in parsed.netloc:
        uddg = query.get("uddg")
        if uddg:
            raw_url = unquote(uddg[0])
            parsed = urlparse(raw_url)
            query = parse_qs(parsed.query)

    if "bing.com" in parsed.netloc:
        for key in ("u", "url", "target", "r"):
            value = query.get(key)
            if value:
                candidate = unquote(value[0])
                if candidate.startswith(("http://", "https://")):
                    raw_url = candidate
                    parsed = urlparse(raw_url)
                    query = parse_qs(parsed.query)
                    break

    if "google." in parsed.netloc:
        for key in ("q", "url"):
            value = query.get(key)
            if value:
                candidate = unquote(value[0])
                if candidate.startswith(("http://", "https://")):
                    raw_url = candidate
                    parsed = urlparse(raw_url)
                    query = parse_qs(parsed.query)
                    break

    raw_url = _strip_tracking_params(raw_url)
    if _looks_like_search_engine_url(raw_url):
        return ""
    return raw_url


def _parse_duckduckgo_results(html: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for node in soup.select(".result"):
        anchor = node.select_one(".result__a") or node.select_one("a.result-link")
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()
        if not title or not href:
            continue
        url = normalize_search_result_url(href)
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        snippet_node = (
            node.select_one(".result__snippet")
            or node.select_one(".result-snippet")
            or node.select_one(".result__body")
        )
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "provider": "duckduckgo-html"})
        if len(results) >= max_results:
            break
    return results


def _parse_bing_results(html: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    candidate_nodes = soup.select("li.b_algo") or soup.select(".algo") or soup.select("main li")
    for node in candidate_nodes:
        anchor = node.select_one("h2 a") or node.select_one("a[href]")
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()
        if not title or not href or href in seen_urls:
            continue
        if not href.startswith(("http://", "https://")):
            continue
        href = normalize_search_result_url(href)
        if not href:
            continue
        snippet_node = node.select_one(".b_caption p") or node.select_one("p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        seen_urls.add(href)
        results.append({"title": title, "url": href, "snippet": snippet, "provider": "bing-html"})
        if len(results) >= max_results:
            break
    return results


def _parse_bing_rss_results(xml_text: str, max_results: int) -> list[dict[str, Any]]:
    raw_xml = str(xml_text or "").strip()
    if not raw_xml:
        return []
    try:
        root = ET.fromstring(raw_xml)
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for node in root.findall(".//item"):
        title = str(node.findtext("title") or "").strip()
        href = str(node.findtext("link") or "").strip()
        description = str(node.findtext("description") or "").strip()
        snippet = BeautifulSoup(description, "html.parser").get_text(" ", strip=True) if description else ""
        url = normalize_search_result_url(href)
        if not title or not url or url in seen_urls:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "provider": "bing-rss"})
        if len(results) >= max_results:
            break
    return results


def _import_ddgs() -> Any | None:
    try:
        from ddgs import DDGS  # type: ignore
        return DDGS
    except Exception:
        return None


def _search_via_ddgs(query: str, max_results: int) -> list[dict[str, Any]]:
    DDGS = _import_ddgs()
    if DDGS is None:
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=max_results):
                    url = normalize_search_result_url(str(item.get("href", "")).strip())
                    title = str(item.get("title", "")).strip()
                    snippet = str(item.get("body", "")).strip()
                    if not title or not url or url in seen_urls:
                        continue
                    if not url.startswith(("http://", "https://")):
                        continue
                    seen_urls.add(url)
                    results.append({"title": title, "url": url, "snippet": snippet, "provider": "ddgs"})
                    if len(results) >= max_results:
                        break
    except Exception:
        raise
    return results


def _search_via_searxng(query: str, max_results: int, timeout_sec: int) -> list[dict[str, Any]]:
    base_url = llm_config.search_api_url.strip().rstrip("/")
    if not base_url:
        return []

    headers = {"User-Agent": "Mozilla/5.0"}
    if llm_config.search_api_key:
        headers["Authorization"] = f"Bearer {llm_config.search_api_key}"

    response = requests.get(
        f"{base_url}/search",
        params={"q": query, "format": "json"},
        headers=headers,
        timeout=timeout_sec,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in payload.get("results", [])[:max_results]:
        url = normalize_search_result_url(str(item.get("url", "")).strip())
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("content", "")).strip()
        if not title or not url or url in seen_urls:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "provider": "searxng"})
    return results


def _sort_preferred(results: list[dict[str, Any]], preferred_domains: Iterable[str] | None) -> list[dict[str, Any]]:
    preferred = tuple(domain.lower() for domain in (preferred_domains or []))
    if not preferred:
        return results

    def score(item: dict[str, Any]) -> tuple[int, str]:
        domain = urlparse(str(item.get("url", ""))).netloc.lower()
        preferred_hit = 0 if any(hint in domain for hint in preferred) else 1
        return (preferred_hit, domain)

    return sorted(results, key=score)


def _record_attempt(
    diagnostics: SearchDiagnostics,
    provider: str,
    *,
    query: str,
    preferred_domains: Iterable[str] | None,
    max_results: int,
    request_started: bool,
    request_succeeded: bool,
    latency_ms: float,
    raw_results: list[dict[str, Any]] | None = None,
    error_type: str = "",
    error_message: str = "",
) -> None:
    raw_results = list(raw_results or [])
    diagnostics.append(
        {
            "provider": provider,
            "provider_name": provider,
            "request_query": str(query or "").strip(),
            "preferred_domains": [str(item).strip() for item in list(preferred_domains or []) if str(item).strip()],
            "max_results": int(max_results),
            "provider_request_started": bool(request_started),
            "provider_request_succeeded": bool(request_succeeded),
            "provider_latency_ms": round(float(latency_ms or 0.0), 2),
            "provider_error_type": str(error_type or "").strip(),
            "provider_error_message": str(error_message or "").strip(),
            "raw_provider_results_count": len(raw_results),
            "raw_provider_results": raw_results[: min(5, len(raw_results))],
            "search_provider_failed": bool(error_type or error_message),
        }
    )


def search_web_detailed(
    query: str,
    max_results: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> dict[str, Any]:
    diagnostics: SearchDiagnostics = []
    preferred = [str(item).strip() for item in list(preferred_domains or []) if str(item).strip()]
    backend = str(llm_config.search_backend or "auto").strip().lower() or "auto"

    def _complete(results: list[dict[str, Any]], provider_name: str) -> dict[str, Any]:
        sorted_results = _sort_preferred(results, preferred)[:max_results]
        return {
            "results": sorted_results,
            "diagnostics": diagnostics,
            "provider_name": provider_name,
            "provider_chain": [str(item.get("provider_name") or "") for item in diagnostics if str(item.get("provider_name") or "").strip()],
        }

    if backend in {"auto", "searxng"}:
        started = time.perf_counter()
        try:
            searxng_results = _search_via_searxng(query, max_results=max_results, timeout_sec=timeout_sec)
            _record_attempt(
                diagnostics,
                "searxng",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=True,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                raw_results=searxng_results,
            )
            if searxng_results:
                return _complete(searxng_results, "searxng")
        except Exception as exc:
            _record_attempt(
                diagnostics,
                "searxng",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    if backend == "ddgs":
        started = time.perf_counter()
        try:
            ddgs_results = _search_via_ddgs(query, max_results=max_results)
            _record_attempt(
                diagnostics,
                "ddgs",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=True,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                raw_results=ddgs_results,
            )
            if ddgs_results:
                return _complete(ddgs_results, "ddgs")
        except Exception as exc:
            _record_attempt(
                diagnostics,
                "ddgs",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    if llm_config.browser_automation_enabled and backend == "browser":
        if browser_automation.available():
            started = time.perf_counter()
            try:
                browser_results = browser_automation.search(
                    query,
                    max_results=max_results,
                    timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                    wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    preferred_domains=preferred,
                )
                normalized_results = [
                    {
                        "title": item.title,
                        "url": item.url,
                        "snippet": item.snippet,
                        "provider": item.provider,
                    }
                    for item in browser_results[:max_results]
                ]
                _record_attempt(
                    diagnostics,
                    "browser",
                    query=query,
                    preferred_domains=preferred,
                    max_results=max_results,
                    request_started=True,
                    request_succeeded=True,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    raw_results=normalized_results,
                )
                if browser_results:
                    return _complete(normalized_results, "browser")
            except Exception as exc:
                _record_attempt(
                    diagnostics,
                    "browser",
                    query=query,
                    preferred_domains=preferred,
                    max_results=max_results,
                    request_started=True,
                    request_succeeded=False,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        else:
            _record_attempt(
                diagnostics,
                "browser",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=False,
                request_succeeded=False,
                latency_ms=0.0,
                error_type="BrowserUnavailable",
                error_message="browser_automation_unavailable",
            )

    search_urls = [
        ("duckduckgo-html", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
        ("duckduckgo-html", f"https://duckduckgo.com/html/?q={quote_plus(query)}"),
    ]

    for provider, search_url in search_urls:
        started = time.perf_counter()
        try:
            response = requests.get(search_url, headers=_SEARCH_HEADERS, timeout=timeout_sec)
            response.raise_for_status()
            results = _parse_duckduckgo_results(response.text, max_results=max_results)
            _record_attempt(
                diagnostics,
                provider,
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=True,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                raw_results=results,
                error_message="http_202_empty" if response.status_code == 202 and not results else "",
            )
            if results:
                return _complete(results, provider)
        except Exception as exc:
            _record_attempt(
                diagnostics,
                provider,
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    started = time.perf_counter()
    try:
        response = requests.get(
            f"https://www.bing.com/search?q={quote_plus(query)}",
            headers=_SEARCH_HEADERS,
            timeout=timeout_sec,
        )
        response.raise_for_status()
        results = _parse_bing_results(response.text, max_results=max_results)
        _record_attempt(
            diagnostics,
            "bing-html",
            query=query,
            preferred_domains=preferred,
            max_results=max_results,
            request_started=True,
            request_succeeded=True,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            raw_results=results,
        )
        if results:
            return _complete(results, "bing-html")
    except Exception as exc:
        _record_attempt(
            diagnostics,
            "bing-html",
            query=query,
            preferred_domains=preferred,
            max_results=max_results,
            request_started=True,
            request_succeeded=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    started = time.perf_counter()
    try:
        response = requests.get(
            f"https://www.bing.com/search?format=rss&q={quote_plus(query)}",
            headers=_SEARCH_HEADERS,
            timeout=timeout_sec,
        )
        response.raise_for_status()
        results = _parse_bing_rss_results(response.text, max_results=max_results)
        _record_attempt(
            diagnostics,
            "bing-rss",
            query=query,
            preferred_domains=preferred,
            max_results=max_results,
            request_started=True,
            request_succeeded=True,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            raw_results=results,
        )
        if results:
            return _complete(results, "bing-rss")
    except Exception as exc:
        _record_attempt(
            diagnostics,
            "bing-rss",
            query=query,
            preferred_domains=preferred,
            max_results=max_results,
            request_started=True,
            request_succeeded=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    if backend == "auto":
        started = time.perf_counter()
        try:
            ddgs_results = _search_via_ddgs(query, max_results=max_results)
            _record_attempt(
                diagnostics,
                "ddgs",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=True,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                raw_results=ddgs_results,
            )
            if ddgs_results:
                return _complete(ddgs_results, "ddgs")
        except Exception as exc:
            _record_attempt(
                diagnostics,
                "ddgs",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=True,
                request_succeeded=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    if llm_config.browser_automation_enabled and backend == "auto":
        if browser_automation.available():
            started = time.perf_counter()
            try:
                browser_results = browser_automation.search(
                    query,
                    max_results=max_results,
                    timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                    wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    preferred_domains=preferred,
                )
                normalized_results = [
                    {
                        "title": item.title,
                        "url": item.url,
                        "snippet": item.snippet,
                        "provider": item.provider,
                    }
                    for item in browser_results[:max_results]
                ]
                _record_attempt(
                    diagnostics,
                    "browser",
                    query=query,
                    preferred_domains=preferred,
                    max_results=max_results,
                    request_started=True,
                    request_succeeded=True,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    raw_results=normalized_results,
                )
                if browser_results:
                    return _complete(normalized_results, "browser")
            except Exception as exc:
                _record_attempt(
                    diagnostics,
                    "browser",
                    query=query,
                    preferred_domains=preferred,
                    max_results=max_results,
                    request_started=True,
                    request_succeeded=False,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        else:
            _record_attempt(
                diagnostics,
                "browser",
                query=query,
                preferred_domains=preferred,
                max_results=max_results,
                request_started=False,
                request_succeeded=False,
                latency_ms=0.0,
                error_type="BrowserUnavailable",
                error_message="browser_automation_unavailable",
            )

    return {
        "results": [],
        "diagnostics": diagnostics,
        "provider_name": "",
        "provider_chain": [str(item.get("provider_name") or "") for item in diagnostics if str(item.get("provider_name") or "").strip()],
    }


def search_web(
    query: str,
    max_results: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    return search_web_detailed(
        query,
        max_results=max_results,
        timeout_sec=timeout_sec,
        preferred_domains=preferred_domains,
    )["results"]


def search_web_queries(
    queries: Iterable[str],
    *,
    max_results_per_query: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for order, query in enumerate(queries):
        q = query.strip()
        if not q:
            continue
        try:
            results = search_web(
                q,
                max_results=max_results_per_query,
                timeout_sec=timeout_sec,
                preferred_domains=preferred_domains,
            )
        except Exception:
            continue
        for rank, item in enumerate(results):
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(
                {
                    **item,
                    "query": q,
                    "query_order": order,
                    "result_rank": rank,
                }
            )
    return merged
